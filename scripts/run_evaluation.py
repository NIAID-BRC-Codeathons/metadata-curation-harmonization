#!/usr/bin/env python3
"""Score one result set with both evaluators and write the combined rows to JSONL.

Edit the parameter block below, or import run_evaluation() and pass your own.
"""

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

from distance_metric_eval import OntologyScorer  # noqa: E402
from f1_score import F1Evaluator, test_records  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]

# --- parameters -------------------------------------------------------------

GOLD_STD = REPO_ROOT / "data" / "inputs" / "Clays_test" / "onto_gold_std.csv"

# a .json / .jsonl path, or a list of records in the shape of test_records
RESULTS = r"C:\Users\Clayg\OneDrive\Desktop\RaviLab\Codeathon\metadata-curation-harmonization\data\out\proposals.jsonl"

RUN_ID = "two_hundred_first_run"

# a directory (the file lands at <OUTPUT_PATH>/<RUN_ID>.jsonl) or a full file name
OUTPUT_PATH = REPO_ROOT / "data" / "out" / "evaluation"

# ----------------------------------------------------------------------------

MERGE_KEYS = ["record_id", "ontology"]


def load_results(results):
    """A list of records passes through; a path is read as .json or .jsonl."""
    if isinstance(results, (str, Path)):
        return F1Evaluator.load_records(results)

    if isinstance(results, dict):
        return [results]

    return list(results)


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
def combine(distance_df, f1_df, run_id):
    if distance_df.empty:
        distance_df = pd.DataFrame(columns=MERGE_KEYS)

    renamed = {column: f"distance_{column}" for column in distance_df.columns
               if column not in MERGE_KEYS}
    combined = distance_df.rename(columns=renamed).merge(f1_df, on=MERGE_KEYS, how="outer")
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

    Writes the combined frame to <output_path>/<run_id>.jsonl.
    """
    records = load_results(results)
    # read once, so both evaluators judge against exactly the same table
    gold_df = gold if isinstance(gold, pd.DataFrame) else pd.read_csv(gold)

    scorer = OntologyScorer(gold_df)
    evaluator = F1Evaluator(gold_df)

    distance_df, distance_report = scorer.evaluate(records)
    f1_df, f1_report = evaluator.evaluate(records, verbose=False)

    combined = combine(distance_df, f1_long_frame(f1_df, evaluator.ontologies), run_id)
    path = write_jsonl(combined, output_path, run_id)

    print(f"{len(f1_df)} of {len(records)} records matched the gold standard")

    if f1_df.empty:
        print(f"  nothing matched: record_id is joined against "
              f"'{evaluator.join_key}' in the gold standard")

    print(f"wrote {len(combined)} rows to {path}")

    return combined, distance_report, f1_report


if __name__ == "__main__":
    combined, distance_report, f1_report = run_evaluation()

    print("\n== distance report ==")
    print(distance_report.round(3))

    print("\n== F1 report ==")
    print(f1_report.round(3))
