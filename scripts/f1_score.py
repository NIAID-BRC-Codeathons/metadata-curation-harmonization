#!/usr/bin/env python3

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd
from sklearn import metrics

REPO_ROOT = Path(__file__).resolve().parents[1]

GOLD_STD = REPO_ROOT / "dataset" / "curated_metadata_expanded.json"

ONTOLOGIES = ["MONDO", "UBERON", "ENVO"]

# gold IRI column -> the ontology column its CURIEs are written to
IRI_COLUMNS = {
    "curated_disease_iri": "MONDO",
    "curated_tissue_iri": "UBERON",
    "curated_env_iri": "ENVO",
}

JOIN_KEY = "Assembly Accessions"

# stand-in label for "no term", so an abstention is still a comparable value;
# it is deliberately kept out of the sklearn label set (see prf1)
ABSENT = ""

test_records = [{
  "record_id": "GCA_030168515.1",
  "outcome": "proposed",
  "terms": [
    {
      "field": "isolation_source",
      "ontology": "UBERON",
      "term_id": "UBERON:0002097",
      "label": "skin of body",
      "definition": "The organ covering the body that consists of dermis and epidermis.",
      "role": "primary",
      "confidence": 0.8
    },
    {
      "field": "isolation_source",
      "ontology": "MONDO",
      "term_id": "MONDO:0004485",
      "label": "wound infection",
      "definition": "An infection that involves a wound.",
      "role": "secondary",
      "confidence": 0.7
    }
  ],
  "candidate_curies": ["UBERON:0002097", "UBERON:000122", "MONDO:0004485"],
  "mappings": [
    {
      "ontology": "UBERON",
      "fields": ["isolation_source"],
      "query_texts": ["wound infection", "wound"]
    },
    {
      "ontology": "MONDO",
      "fields": ["isolation_source"],
      "query_texts": ["wound infection", "infection"]
    }
  ],
  "flags": [],
  "abstain_reason": None
},
{
  "record_id": "GCA_021491695.1",
  "outcome": "proposed",
  "terms": [
    {
      "field": "isolation_source",
      "ontology": "MONDO",
      "term_id": "MONDO:0021178",
      "label": "skin of body",
      "definition": "The organ covering the body that consists of dermis and epidermis.",
      "role": "primary",
      "confidence": 0.8
    },
    {
      "field": "isolation_source",
      "ontology": "UBERON",
      "term_id": "UBERON:0002097",
      "label": "wound infection",
      "definition": "An infection that involves a wound.",
      "role": "secondary",
      "confidence": 0.7
    }
  ],
  "candidate_curies": ["MONDO:0021178", "UBERON:0002097", "MONDO:0004485"],
  "mappings": [
    {
      "ontology": "UBERON",
      "fields": ["isolation_source"],
      "query_texts": ["wound infection", "wound"]
    },
    {
      "ontology": "MONDO",
      "fields": ["isolation_source"],
      "query_texts": ["wound infection", "infection"]
    }
  ],
  "flags": [],
  "abstain_reason": None
}]


class F1Evaluator:
    def __init__(self, gold=GOLD_STD, ontologies=ONTOLOGIES, iri_columns=IRI_COLUMNS,
                 join_key=JOIN_KEY, prediction_key="terms", term_key="term_id",
                 absent=ABSENT):
        self.ontologies = list(ontologies)
        self.iri_columns = dict(iri_columns)
        self.join_key = join_key
        self.prediction_key = prediction_key
        self.term_key = term_key
        self.absent = absent
        self.gold = self._add_curie_columns(self._load_gold(gold))

    @staticmethod
    def _load_gold(gold):
        if isinstance(gold, pd.DataFrame):
            return gold.copy()

        path = Path(gold)

        if path.suffix == ".csv":
            return pd.read_csv(path)

        return pd.read_json(path)

    @staticmethod
    def iri_to_curie(iri):
        """http://purl.obolibrary.org/obo/MONDO_0005971 -> MONDO:0005971"""
        if pd.isna(iri):
            return pd.NA

        return str(iri).strip().rsplit("/", 1)[-1].replace("_", ":", 1)

    # a gold sheet that already carries CURIE columns is left as-is, so this also
    # accepts frames that never had IRIs
    def _add_curie_columns(self, gold):
        for source, ontology in self.iri_columns.items():
            if ontology in gold.columns:
                gold[ontology] = gold[ontology].astype("string")
            elif source in gold.columns:
                gold[ontology] = gold[source].map(self.iri_to_curie).astype("string")

        return gold

    @staticmethod
    def group_by_ontology(curies, container=list):
        """["UBERON:0002097", "MONDO:0004485"] -> {"UBERON": [...], "MONDO": [...]}

        Pass container=str to keep a single CURIE per ontology instead of a list.
        Prefixes outside the evaluated ontologies (CL, for instance) keep their own
        key, where lookups simply never find them.
        """
        grouped = defaultdict(container)

        for curie in curies:
            if pd.isna(curie):
                continue

            prefix = str(curie).split(":", 1)[0]

            if container is list:
                grouped[prefix].append(str(curie))
            else:
                grouped[prefix] = str(curie)

        return dict(grouped)

    def gold_for(self, record_id):
        """{"UBERON": "UBERON:0001242", ...} for one record, or None if unmatched."""
        rows = self.gold[self.gold[self.join_key] == record_id]

        if rows.empty:
            return None

        return self.group_by_ontology(rows.iloc[0][self.ontologies], container=str)

    # terms are dicts carrying term_id, so every term a record proposes is kept,
    # not just the primary one. A plain list of CURIEs (candidate_curies) still
    # works, which keeps the source a constructor argument rather than an edit.
    def predicted_curies(self, record):
        curies = []

        for entry in record.get(self.prediction_key) or []:
            curie = entry.get(self.term_key) if isinstance(entry, dict) else entry

            if curie:
                curies.append(curie)

        return curies

    # taking the gold term whenever it appears anywhere in the proposed terms scores
    # the term set, not its ranking; the first term proposed stands in otherwise
    def eval_row(self, record):
        record_id = record.get("record_id")
        gold = self.gold_for(record_id)

        if gold is None:
            return None

        predicted = self.group_by_ontology(self.predicted_curies(record))
        row = {"record_id": record_id}

        for ontology in self.ontologies:
            predicted_terms = predicted.get(ontology) or []
            gold_term = gold.get(ontology) or self.absent

            if gold_term and gold_term in predicted_terms:
                pred_term = gold_term
            else:
                pred_term = predicted_terms[0] if predicted_terms else self.absent

            row[f"{ontology}_gold"] = gold_term
            row[f"{ontology}_pred"] = pred_term
            row[f"{ontology}_match"] = int(gold_term != self.absent and pred_term == gold_term)

        return row

    def eval_frame(self, records, verbose=True):
        """One row per matched record: gold, predicted and hit per ontology."""
        if isinstance(records, dict):
            records = [records]

        rows = []

        for record in records:
            row = self.eval_row(record)

            if row is None:
                if verbose:
                    print(f"No gold standard found for record_id: {record.get('record_id')}")
                continue

            rows.append(row)

        columns = ["record_id"] + [f"{ontology}_{key}" for ontology in self.ontologies
                                   for key in ("gold", "pred", "match")]

        return pd.DataFrame(rows, columns=columns)

    def prf1(self, gold, pred):
        """Micro P/R/F1 over the real CURIEs seen in this slice.

        Passing labels= without ABSENT is what keeps a correct abstention neutral
        instead of a true positive.
        """
        labels = sorted({t for t in pd.concat([gold, pred]) if t != self.absent})

        if not labels:
            return {"precision": 0.0, "recall": 0.0, "f1": 0.0, "support": 0}

        kwargs = dict(labels=labels, average="micro", zero_division=0)

        return {
            "precision": metrics.precision_score(gold, pred, **kwargs),
            "recall": metrics.recall_score(gold, pred, **kwargs),
            "f1": metrics.f1_score(gold, pred, **kwargs),
            "support": int((gold != self.absent).sum()),
        }

    def f1_table(self, eval_df):
        """Per-ontology scores plus micro (pooled labels) and macro (mean) averages."""
        f1_df = pd.DataFrame({o: self.prf1(eval_df[f"{o}_gold"], eval_df[f"{o}_pred"])
                              for o in self.ontologies}).T

        # pooled: stack the gold/pred column pairs into one long label vector
        gold_all = pd.concat([eval_df[f"{o}_gold"] for o in self.ontologies], ignore_index=True)
        pred_all = pd.concat([eval_df[f"{o}_pred"] for o in self.ontologies], ignore_index=True)

        macro = f1_df.loc[self.ontologies, ["precision", "recall", "f1"]].mean()
        f1_df.loc["micro avg"] = self.prf1(gold_all, pred_all)
        f1_df.loc["macro avg"] = [*macro, f1_df.loc["micro avg", "support"]]
        f1_df["support"] = f1_df["support"].astype(int)

        return f1_df

    def evaluate(self, records, output_dir=None, verbose=True):
        """Both tables in one call: (per-record frame, F1 summary)."""
        eval_df = self.eval_frame(records, verbose=verbose)
        f1_df = self.f1_table(eval_df)

        if output_dir is not None:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            eval_df.to_csv(output_dir / "f1_eval.csv", index=False)
            f1_df.to_csv(output_dir / "f1_table.csv", index_label="ontology")

        return eval_df, f1_df

    @staticmethod
    def load_records(path):
        path = Path(path)

        if path.suffix == ".jsonl":
            with path.open(encoding="utf-8") as handle:
                return [json.loads(line) for line in handle if line.strip()]

        return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    evaluator = F1Evaluator(GOLD_STD)
    eval_df, f1_df = evaluator.evaluate(test_records)
    print(eval_df)
    print()
    print(f1_df.round(3))
