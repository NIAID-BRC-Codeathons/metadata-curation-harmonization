#!/usr/bin/env python3
"""
Reads the retrieval stage's output into the RAGOutput contract that Resolve consumes.

The RAG tool reports its top k matches per query text (engine.md section 6.3 keeps
provenance at that granularity), while Resolve reasons over one ranked candidate
list per ontology. This module bridges the two: it flattens every query text's
matches into a single bucket, keeps the best score where queries overlap, and
re-ranks what survives.

Definitions are left null. The vector database carries term ids and names only,
and engine.md is explicit that a missing definition is reported, never invented.

Author: Andrew LaPointe
Date: 2026-09-17
"""

import json
import logging
from pathlib import Path
from typing import Any

from .models import Candidate, RAGBucket, RAGOutput, SourceField

logger = logging.getLogger(__name__)


def load_rag_results(file: str | Path, top_k: int = 10) -> list[RAGOutput]:
    """Load retrieval results from the RAG stage's JSONL output.

    Arguments:
        file (str | Path):
            Path to the retrieval output, one RecordResult per line.
        top_k (int):
            Maximum candidates to keep per ontology bucket after merging.

    Returns:
        (list[RAGOutput]): One RAGOutput per record, in file order.
    """
    outputs = []
    with open(file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                outputs.append(_parse_record(json.loads(line), top_k))

    logger.info(f"Loaded {len(outputs)} RAG results from {file}")
    return outputs


def _parse_record(entry: dict[str, Any], top_k: int) -> RAGOutput:
    """Convert one RecordResult into a RAGOutput."""
    record_id = entry["record_id"]
    flags = list(entry.get("flags", []))
    buckets = []

    for bucket in entry.get("buckets", []):
        ontology = bucket["ontology"]
        query_texts = [q.get("text", "") for q in bucket.get("query_texts", [])]
        candidates = _merge_matches(bucket.get("query_texts", []), ontology, top_k)

        if not candidates:
            flag = f"empty_rag_{ontology}"
            if flag not in flags:
                flags.append(flag)

        buckets.append(
            RAGBucket(
                ontology=ontology,
                src_fields=[SourceField(**sf) for sf in bucket.get("src_fields", [])],
                query_texts=query_texts,
                candidates=candidates,
            )
        )

    return RAGOutput(record_id=record_id, buckets=buckets, flags=flags)


def _merge_matches(
    query_texts: list[dict[str, Any]], ontology: str, top_k: int
) -> list[Candidate]:
    """Merge every query text's matches into one ranked candidate list.

    A term reached by several query texts keeps its best score, since the query
    texts are alternative phrasings of the same field rather than separate asks.
    """
    best: dict[str, float] = {}
    labels: dict[str, str] = {}

    for query_text in query_texts:
        for match in query_text.get("matches", []):
            term_id = match["term_id"]
            score = float(match["score"])
            if score > best.get(term_id, float("-inf")):
                best[term_id] = score
                labels[term_id] = match.get("label") or match.get("term_name", "")

    ranked = sorted(best.items(), key=lambda item: (-item[1], item[0]))[:top_k]

    return [
        Candidate(
            curie=term_id,
            label=labels[term_id],
            definition=None,
            ontology=ontology,
            score=score,
            rank=rank,
        )
        for rank, (term_id, score) in enumerate(ranked)
    ]
