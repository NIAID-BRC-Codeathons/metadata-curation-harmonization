#!/usr/bin/env python3
"""Score one result set with both evaluators and write the combined rows to JSONL.

Edit the parameter block below, or import run_evaluation() and pass your own.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from distance_metric_eval import OntologyScorer  # noqa: E402
from f1_score import F1Evaluator, test_records  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- parameters -------------------------------------------------------------

GOLD_STD = REPO_ROOT / "data" / "inputs" / "Clays_test" / "onto_gold_std.csv"

# a .json / .jsonl path, or a list of records in the shape of test_records
RESULTS = r"C:\Users\Clayg\OneDrive\Desktop\RaviLab\Codeathon\metadata-curation-harmonization\data\clays\data.v2.232uniq.work40\proposals.jsonl"

RUN_ID = "data.v2.232uniq.work40"

# a directory (the file lands at <OUTPUT_PATH>/<RUN_ID>.jsonl) or a full file name
OUTPUT_PATH = REPO_ROOT / "data" / "out" / "evaluation"

# ----------------------------------------------------------------------------

MERGE_KEYS = ["record_id", "ontology"]

# the curators' key in the gold standard; align_gold adds a record_id column
# beside it for the evaluators to join on
GOLD_KEY = "Assembly Accessions"

ACCESSION = re.compile(r"(GC[AF])_(\d+)\.(\d+)")
OTHER_DATABASE = {"GCA": "GCF", "GCF": "GCA"}


def load_results(results):
    """A list of records passes through; a path is read as .json or .jsonl."""
    if isinstance(results, (str, Path)):
        return F1Evaluator.load_records(results)

    if isinstance(results, dict):
        return [results]

    return list(results)


# NCBI lists most assemblies twice, as the GenBank record (GCA_) and its RefSeq
# copy (GCF_) with the same number, linked by genome.pairedAccession. The
# pipeline keys a record by genome.currentAccession, the GCF_ one for a RefSeq
# record, while the curators keyed the gold standard by the accession they saw,
# nearly always GCA_. Joined literally, 83 of data.v1.309uniq's 309 records
# matched.
def gold_accession(record_id, gold_keys, by_number):
    """The gold standard's accession for record_id, or None if it has none.

    The record's own accession wins, then its twin at the same version, then
    the twin's only version in the gold standard. This reproduces
    genome.pairedAccession for all 6,008 curated NCBI records.
    """
    if record_id in gold_keys:
        return record_id

    match = ACCESSION.fullmatch(str(record_id))

    if match is None:
        return None

    database, number, version = match.groups()
    twin = f"{OTHER_DATABASE[database]}_{number}.{version}"

    if twin in gold_keys:
        return twin

    versions = by_number.get((OTHER_DATABASE[database], number), [])

    return versions[0] if len(versions) == 1 else None


def align_gold(gold, records, gold_key=GOLD_KEY):
    """Each record's gold rows, keyed by the record_id the record uses.

    Returns that frame (a record_id column ahead of every gold column, gold row
    order kept) and the record_id -> gold accession of every record that matched.
    """
    positions = defaultdict(list)

    for position, key in enumerate(gold[gold_key]):
        if not pd.isna(key):
            positions[key].append(position)

    by_number = defaultdict(list)

    for key in positions:
        match = ACCESSION.fullmatch(str(key))

        if match:
            by_number[(match[1], match[2])].append(key)

    matched = {}

    for record in records:
        accession = gold_accession(record.get("record_id"), positions, by_number)

        if accession is not None:
            matched[record.get("record_id")] = accession

    pairs = [(record_id, position) for record_id, accession in matched.items()
             for position in positions[accession]]
    aligned = gold.iloc[[position for _, position in pairs]].reset_index(drop=True)
    aligned.insert(0, "record_id", [record_id for record_id, _ in pairs])

    return aligned, matched


# the two evaluators disagree on shape: OntologyScorer emits one row per
# (record, ontology) while F1Evaluator emits one row per record, three ontologies
# wide. Melting the F1 frame is what gives them a key to join on.
def f1_long_frame(eval_df, ontologies):
    parts = []

    for ontology in ontologies:
        part = eval_df[["record_id", f"{ontology}_gold", f"{ontology}_pred",
                        f"{ontology}_match"]].copy()
        part.columns = ["record_id", "f1_gold", "f1_pred", "f1_match"]
        part.insert(1, "ontology", ontology)
        parts.append(part)

    if not parts:
        return pd.DataFrame(columns=MERGE_KEYS + ["f1_gold", "f1_pred", "f1_match"])

    return pd.concat(parts, ignore_index=True)


# both sides keep their own gold column on purpose: OntologyScorer reads it from
# the ontology column it was asked for, F1Evaluator keys off the CURIE's own
# prefix, so a CL term sitting in the UBERON column shows up in one and not the
# other. Seeing them side by side is the point.
#
# Every record gets one row per evaluated ontology whether or not either
# evaluator had anything to say, so the file always holds len(records) x
# len(ontologies) rows. A record the gold standard lacks, or one that proposed no
# terms, is the one with f1_match null; terms from other ontologies (CL, say) get
# no row.
def combine(distance_df, f1_df, run_id, record_ids, ontologies):
    if distance_df.empty:
        distance_df = pd.DataFrame(columns=MERGE_KEYS)

    renamed = {column: f"distance_{column}" for column in distance_df.columns
               if column not in MERGE_KEYS}
    rows = pd.DataFrame([(record_id, ontology) for record_id in record_ids
                         for ontology in ontologies], columns=MERGE_KEYS)
    combined = (rows.merge(distance_df.rename(columns=renamed), on=MERGE_KEYS, how="left")
                .merge(f1_df, on=MERGE_KEYS, how="left"))
    # nullable, so an unmatched record's null does not turn every 0/1 into 0.0/1.0
    combined["f1_match"] = combined["f1_match"].astype("Int64")
    combined.insert(0, "run_id", run_id)

    return combined.sort_values(MERGE_KEYS, ignore_index=True)


def write_jsonl(frame, output_path, run_id):
    """One JSON object per line, NaN written as null."""
    output_path = Path(output_path)
    path = output_path if output_path.suffix else output_path / f"{run_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = frame.to_json(orient="records", lines=True)

    if lines and not lines.endswith("\n"):
        lines += "\n"

    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(lines)

    return path


def run_evaluation(gold=GOLD_STD, results=RESULTS, run_id=RUN_ID, output_path=OUTPUT_PATH):
    """Returns (combined row-by-row frame, distance report, F1 report).

    Writes the combined frame to <output_path>/<run_id>.jsonl, one row per
    record and ontology, matched to the gold standard or not.
    """
    records = load_results(results)
    # read once, so both evaluators judge against exactly the same table
    gold_df = gold if isinstance(gold, pd.DataFrame) else pd.read_csv(gold)
    aligned, matched = align_gold(gold_df, records)

    scorer = OntologyScorer(aligned, join_key="record_id")
    evaluator = F1Evaluator(aligned, join_key="record_id")

    distance_df, distance_report = scorer.evaluate(records)
    f1_df, f1_report = evaluator.evaluate(records, verbose=False)

    record_ids = list(dict.fromkeys(record.get("record_id") for record in records))
    combined = combine(distance_df, f1_long_frame(f1_df, evaluator.ontologies), run_id,
                       record_ids, evaluator.ontologies)
    path = write_jsonl(combined, output_path, run_id)

    through_pair = sum(record_id != accession for record_id, accession in matched.items())
    print(f"{len(matched)} of {len(records)} records matched the gold standard, "
          f"{through_pair} of them through their paired GCA_/GCF_ accession")

    if len(matched) < len(record_ids):
        print(f"  the rest are written with f1_match null: record_id is joined "
              f"against '{GOLD_KEY}' in the gold standard, directly or paired")

    if f1_df.attrs.get("unproposed"):
        print(f"{f1_df.attrs['unproposed']} records proposed no terms and are left out "
              f"of the F1 report")

    print(f"wrote {len(combined)} rows to {path}")

    return combined, distance_report, f1_report


if __name__ == "__main__":
    combined, distance_report, f1_report = run_evaluation()

    print("\n== distance report ==")
    print(distance_report.round(3))

    print("\n== F1 report ==")
    print(f1_report.round(3))
