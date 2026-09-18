#!/usr/bin/env python3

import json
import math
from collections import defaultdict
from pathlib import Path

import pandas as pd
from oaklib import get_adapter
from oaklib.datamodels.similarity import TermPairwiseSimilarity
from oaklib.datamodels.vocabulary import IS_A, OWL_THING, PART_OF
from oaklib.utilities.semsim.similarity_utils import setwise_jaccard_similarity

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

# CURIEs per information-content query, under SQLite's bound-parameter limit
IC_BATCH = 900

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
        self._gold_index = None
        # graph lookups shared by every pair in a run; see _similarity
        self._ancestors = {}
        self._ic = defaultdict(dict)

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

    def _ancestors_of(self, ontology, curie):
        key = (ontology, curie)

        if key not in self._ancestors:
            self._ancestors[key] = list(
                self.adapter(ontology).ancestors(curie, predicates=self.config[ontology][1])
            )

        return self._ancestors[key]

    def _common_ancestors(self, ontology, predicted, target):
        common = self.adapter(ontology).common_ancestors(
            predicted,
            target,
            self.config[ontology][1],
            subject_ancestors=self._ancestors_of(ontology, predicted),
            object_ancestors=self._ancestors_of(ontology, target),
        )

        return [curie for curie in common if curie != OWL_THING]

    # oaklib's IC query scans the ontology's whole entailed_edge table, after
    # re-counting every node in it, however few CURIEs it is given, so a batch
    # costs what a single lookup does. A CURIE the graph holds no score for is
    # kept as None so it is not asked about again.
    def _load_information_content(self, ontology, curies):
        known = self._ic[ontology]
        missing = sorted(set(curies).difference(known))

        for start in range(0, len(missing), IC_BATCH):
            batch = missing[start:start + IC_BATCH]
            scores = dict(self.adapter(ontology).information_content_scores(
                batch, object_closure_predicates=self.config[ontology][1]
            ))

            for curie in batch:
                known[curie] = scores.get(curie)

    def _information_content(self, ontology, curies):
        # what oaklib's sqlite adapter answers for an empty list
        if not curies:
            return {OWL_THING: 0.0}

        self._load_information_content(ontology, curies)
        known = self._ic[ontology]

        # oaklib's query groups by CURIE, which SQLite returns in sorted order;
        # keeping that order keeps oaklib's tie-break when it picks the MICA
        return {curie: known[curie] for curie in sorted(curies) if known[curie] is not None}

    # oaklib 0.7.4's pairwise_similarity, minus the work score_pair never reads;
    # recheck it against oaklib's if that version changes. oaklib also looks up
    # the subject's and object's own IC, and each of its three IC lookups
    # re-counts every node in the ontology: about 1.7 s a pair, nearly all of a
    # run. Here ancestors and IC come from per-run caches that _prefetch fills
    # for a whole batch at once. TermPairwiseSimilarity is still built because
    # its CURIE validation is what makes a malformed ID comparable=False.
    def _similarity(self, ontology, predicted, target):
        jaccard = setwise_jaccard_similarity(
            self._ancestors_of(ontology, predicted), self._ancestors_of(ontology, target)
        )
        ics = self._information_content(
            ontology, self._common_ancestors(ontology, predicted, target)
        )

        if ics:
            max_ic = max(ics.values())
            ancestor = next(curie for curie, ic in ics.items()
                            if math.isclose(ic, max_ic, rel_tol=0.001))
        else:
            max_ic, ancestor = 0.0, None

        sim = TermPairwiseSimilarity(
            subject_id=predicted,
            object_id=target,
            ancestor_id=ancestor,
            ancestor_information_content=max_ic,
            jaccard_similarity=jaccard,
        )
        # as oaklib does: keep the plain float, not the constructor's NegativeLogValue
        sim.ancestor_information_content = max_ic

        if sim.ancestor_information_content and sim.jaccard_similarity:
            sim.phenodigm_score = math.sqrt(
                sim.jaccard_similarity * sim.ancestor_information_content
            )

        return sim

    # every branch returns the same keys so callers can average without key checks;
    # comparable=False means the pair never reached the graph, which is not the
    # same as a real jaccard of 0.0
    def score_pair(self, ontology: str, predicted: str, target: str) -> dict:
        # not reported on its own any more (f1_pred covers exact matching); it
        # still decides what an unreachable pair is worth
        exact = predicted == target

        try:
            sim = self._similarity(ontology, predicted, target)
        except Exception:  # invalid / out-of-ontology ID
            return {
                "jaccard": 1.0 if exact else 0.0,
                "resnik": None,
                "phenodigm": None,
                "mica": predicted if exact else None,
                "comparable": False,
            }

        return {
            "jaccard": sim.jaccard_similarity,
            "resnik": sim.ancestor_information_content,
            "phenodigm": sim.phenodigm_score,
            "mica": sim.ancestor_id,  # useful for error analysis
            "comparable": True,
        }

    # first row and row count per join key, built once rather than filtering the
    # whole gold frame for every record; rebuilt if .gold is replaced
    def _gold_rows(self):
        if self._gold_index is None or self._gold_index[0] is not self.gold:
            first, counts = {}, defaultdict(int)

            for position, key in enumerate(self.gold[self.join_key]):
                if pd.isna(key):  # == never matches a missing key
                    continue

                first.setdefault(key, position)
                counts[key] += 1

            self._gold_index = (self.gold, first, counts)

        return self._gold_index[1:]

    def gold_for(self, record_id):
        first, counts = self._gold_rows()

        if record_id not in first:
            return None, 0

        row = self.gold.iloc[first[record_id]]
        gold = {}

        for ontology in self.config:
            if ontology in self.gold.columns and not pd.isna(row[ontology]):
                gold[ontology] = row[ontology]

        return gold, counts[record_id]

    @staticmethod
    def _predicted_terms(eval_set):
        predicted = defaultdict(list)

        for term in eval_set.get("terms", []):
            if term.get("term_id"):
                predicted[term["ontology"]].append(term["term_id"])

        return predicted

    # speed only: finds every pair score_record is about to score and loads their
    # IC in one query per ontology instead of one per pair. Anything that fails
    # here is skipped and left for score_pair to report as before.
    def _prefetch(self, eval_sets):
        common = defaultdict(set)

        for eval_set in eval_sets:
            gold, _ = self.gold_for(eval_set.get("record_id"))

            if gold is None:
                continue

            for ontology, term_ids in self._predicted_terms(eval_set).items():
                target = gold.get(ontology)

                if target is None:
                    continue

                for term_id in term_ids:
                    try:
                        common[ontology].update(self._common_ancestors(ontology, term_id, target))
                    except Exception:
                        continue

        for ontology, curies in common.items():
            try:
                self._load_information_content(ontology, curies)
            except Exception:
                continue

    def score_record(self, eval_set):
        result = dict(eval_set)
        gold, n_rows = self.gold_for(eval_set.get("record_id"))

        if gold is None:
            result["score"] = {}
            result["score_status"] = "no_matching_record"
            return result

        predicted = self._predicted_terms(eval_set)
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

        # read twice, so a generator must not be spent by the prefetch pass
        eval_sets = list(eval_sets)
        self._prefetch(eval_sets)
        results = [self.score_record(eval_set) for eval_set in eval_sets]

        if output_path is not None:
            output_path = Path(output_path)

            if not output_path.suffix:
                output_path = output_path / OUTPUT_JSON.name

            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(results, indent=2), encoding="utf-8")

        return results

    # results already returned by score_records are passed straight through, so
    # nothing is scored twice on the way to a frame or an average
    def _as_results(self, eval_sets):
        if isinstance(eval_sets, dict):
            eval_sets = [eval_sets]
        else:
            eval_sets = list(eval_sets)

        if eval_sets and "score" in eval_sets[0]:
            return eval_sets

        return self.score_records(eval_sets)

    # flattens to one row per (record, ontology) for aggregate analysis
    def score_frame(self, eval_sets):
        rows = []

        for result in self._as_results(eval_sets):
            for ontology, scored in result["score"].items():
                row = {
                    "record_id": result.get("record_id"),
                    "ontology": ontology,
                    "status": scored["status"],
                    "gold": scored.get("gold"),
                    "n_terms": scored.get("n_terms"),
                }
                row.update({metric: scored.get(metric) for metric in self.averaged})
                rows.append(row)

        return pd.DataFrame(rows)

    # corpus-level view: one row per ontology, one column per metric, plus an
    # "all" row pooling every scored pair. A None metric (the pair never reached
    # the graph) is left out of its mean rather than counted as 0.0, so n_scored
    # is the ceiling on how many values any one column averaged, not the count.
    def average_scores(self, scored):
        frame = scored if isinstance(scored, pd.DataFrame) else self.score_frame(scored)
        metrics = list(self.averaged)
        columns = metrics + ["n_scored", "n_records"]

        if frame.empty:
            return pd.DataFrame(columns=columns)

        # no_gold_term rows hold no metrics, so they fall out of every mean on
        # their own; grouping the whole frame keeps an ontology the gold standard
        # could never judge visible as a row of n_scored 0 instead of dropping it
        graded = frame[frame["status"] == "scored"]

        summary = frame.groupby("ontology")[metrics].mean()
        summary["n_scored"] = (graded.groupby("ontology").size()
                               .reindex(summary.index, fill_value=0))
        summary["n_records"] = frame.groupby("ontology").size()

        order = [ontology for ontology in self.config if ontology in summary.index]
        order += [ontology for ontology in summary.index if ontology not in order]
        summary = summary.loc[order]

        summary.loc["all"] = [*frame[metrics].mean(), len(graded), len(frame)]
        summary[["n_scored", "n_records"]] = summary[["n_scored", "n_records"]].astype(int)

        return summary[columns]

    # both tables in one call for downstream users: the record-by-record frame and
    # the dataset report built from it, scored once. The individual methods stay
    # available for callers that want only one of the two.
    def evaluate(self, eval_sets, output_path=None):
        frame = self.score_frame(self.score_records(eval_sets, output_path=output_path))

        return frame, self.average_scores(frame)


if __name__ == "__main__":
    scorer = OntologyScorer(GOLD_STD)
    frame, report = scorer.evaluate(eval_set, output_path=OUTPUT_JSON)
    print(frame)
    print()
    print(report.round(3))
