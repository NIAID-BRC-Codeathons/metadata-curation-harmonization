#!/usr/bin/env python3

import json
from collections import defaultdict
from pathlib import Path

import pandas as pd
from oaklib import get_adapter
from oaklib.datamodels.vocabulary import IS_A, PART_OF

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = REPO_ROOT / "data" / "inputs" / "Clays_test"

GOLD_STD = TEST_DIR / "onto_gold_std.csv"
OUTPUT_JSON = TEST_DIR / "scored_eval.json"

CONFIG = {
    "MONDO":  ("sqlite:obo:mondo",  [IS_A]),
    "UBERON": ("sqlite:obo:uberon", [IS_A, PART_OF]),
    "ENVO":   ("sqlite:obo:envo",   [IS_A, PART_OF]),
}

JOIN_KEY = "Assembly Accessions"
AVERAGED = ["jaccard", "resnik", "phenodigm"]

eval_set = {
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
  "candidate_curies": ["UBERON:0002097", "MONDO:0004485"],
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
}


class OntologyScorer:
    def __init__(self, gold=GOLD_STD, config=CONFIG, join_key=JOIN_KEY, averaged=AVERAGED):
        self.config = config
        self.join_key = join_key
        self.averaged = averaged
        self.gold = self._load_gold(gold)
        self._adapters = {}

    @staticmethod
    def _load_gold(gold):
        if isinstance(gold, pd.DataFrame):
            return gold

        path = Path(gold)

        if path.suffix == ".json":
            return pd.read_json(path)

        return pd.read_csv(path)

    # built on first use so importing this module, or scoring a single ontology,
    # does not trigger every sqlite download
    def adapter(self, ontology):
        if ontology not in self._adapters:
            self._adapters[ontology] = get_adapter(self.config[ontology][0])

        return self._adapters[ontology]

    # every branch returns the same keys so callers can average without key checks;
    # comparable=False means the pair never reached the graph, which is not the
    # same as a real jaccard of 0.0
    def score_pair(self, ontology: str, predicted: str, target: str) -> dict:
        exact = int(predicted == target)

        try:
            sim = self.adapter(ontology).pairwise_similarity(
                predicted, target, predicates=self.config[ontology][1]
            )
        except Exception:  # invalid / out-of-ontology ID
            return {
                "exact": exact,
                "jaccard": 1.0 if exact else 0.0,
                "resnik": None,
                "phenodigm": None,
                "mica": predicted if exact else None,
                "comparable": False,
            }

        return {
            "exact": exact,
            "jaccard": sim.jaccard_similarity,
            "resnik": sim.ancestor_information_content,
            "phenodigm": sim.phenodigm_score,
            "mica": sim.ancestor_id,  # useful for error analysis
            "comparable": True,
        }

    def gold_for(self, record_id):
        rows = self.gold[self.gold[self.join_key] == record_id]

        if rows.empty:
            return None, 0

        row = rows.iloc[0]
        gold = {}

        for ontology in self.config:
            if ontology in self.gold.columns and not pd.isna(row[ontology]):
                gold[ontology] = row[ontology]

        return gold, len(rows)

    def score_record(self, eval_set):
        result = dict(eval_set)
        gold, n_rows = self.gold_for(eval_set.get("record_id"))

        if gold is None:
            result["score"] = {}
            result["score_status"] = "no_matching_record"
            return result

        predicted = defaultdict(list)

        for term in eval_set.get("terms", []):
            if term.get("term_id"):
                predicted[term["ontology"]].append(term["term_id"])

        scores = {}

        for ontology, term_ids in predicted.items():
            target = gold.get(ontology)

            if target is None:
                scores[ontology] = {"status": "no_gold_term", "n_terms": len(term_ids)}
                continue

            per_term = []

            for term_id in term_ids:
                scored = self.score_pair(ontology, term_id, target)
                scored["term_id"] = term_id
                per_term.append(scored)

            aggregate = {
                "status": "scored",
                "gold": target,
                "n_terms": len(per_term),
                "exact": max(term["exact"] for term in per_term),
            }

            for metric in self.averaged:
                values = [term[metric] for term in per_term if term[metric] is not None]
                aggregate[metric] = sum(values) / len(values) if values else None

            aggregate["terms"] = per_term
            scores[ontology] = aggregate

        result["score"] = scores
        result["score_status"] = "scored"
        result["gold_rows_matched"] = n_rows

        return result

    def score_records(self, eval_sets, output_path=None):
        if isinstance(eval_sets, dict):
            eval_sets = [eval_sets]

        results = [self.score_record(eval_set) for eval_set in eval_sets]

        if output_path is not None:
            output_path = Path(output_path)

            if not output_path.suffix:
                output_path = output_path / OUTPUT_JSON.name

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

        return results

    # flattens to one row per (record, ontology) for aggregate analysis
    def score_frame(self, eval_sets):
        rows = []

        for result in self.score_records(eval_sets):
            for ontology, scored in result["score"].items():
                row = {
                    "record_id": result.get("record_id"),
                    "ontology": ontology,
                    "status": scored["status"],
                    "gold": scored.get("gold"),
                    "n_terms": scored.get("n_terms"),
                    "exact": scored.get("exact"),
                }
                row.update({metric: scored.get(metric) for metric in self.averaged})
                rows.append(row)

        return pd.DataFrame(rows)


if __name__ == "__main__":
    scorer = OntologyScorer(GOLD_STD)
    scorer.score_records(eval_set, output_path=OUTPUT_JSON)
    print(scorer.score_frame(eval_set))
