#!/usr/bin/env python3
"""
Resolve stage: pick the final ontology term set from the retrieved candidates.

Reads the retrieval stage's output, rejoins it with the original metadata (the
retrieval output carries provenance and candidates, not the source record), and
writes one proposal per record.

Usage:
    python -m ontology_selection.resolve \
        --rag-results data/intermediate/rag_results.jsonl \
        --records data/raw/records.jsonl \
        --output data/out/proposals.jsonl \
        --config src/ontology_selection/config.yaml

Author: Andrew LaPointe
Date: 2026-09-17
"""

import logging
import sys
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from .checks import run_checks
from .ingest import ingest_jsonl
from .models import RAGOutput, ResolveOutput
from .rag_results import load_rag_results
from .resolve_agent import ResolveAgent
from .summary import print_resolve_summary
from .utils import load_config, setup_logging, save_jsonl

logger = logging.getLogger(__name__)


def resolve_records(
    rag_outputs: List[RAGOutput],
    metadata_by_id: Dict[str, Dict[str, Any]],
    agent: ResolveAgent,
    workers: int = 1,
) -> List[ResolveOutput]:
    """
    Resolve every record, preserving input order.

    Flags from the earlier stages are merged in after the resolve checks have run,
    so an upstream flag records provenance without downgrading this record's
    outcome - only what Resolve itself found does that.

    Args:
        rag_outputs: Retrieval results, one per record
        metadata_by_id: Original record metadata, keyed by record_id
        agent: Resolve agent
        workers: Number of records to resolve concurrently

    Returns:
        List of ResolveOutput, one per record, in input order
    """
    def resolve_one(rag_output: RAGOutput) -> ResolveOutput:
        metadata = metadata_by_id.get(rag_output.record_id)
        if metadata is None:
            logger.warning(
                f"No source metadata for {rag_output.record_id}; "
                "resolving on candidates alone"
            )
            metadata = {}

        resolved = agent.resolve(rag_output, metadata)
        resolved = run_checks(resolved, stage="resolve")
        resolved.flags = sorted(set(resolved.flags) | set(rag_output.flags))
        return resolved

    if workers <= 1:
        return [resolve_one(rag_output) for rag_output in rag_outputs]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(resolve_one, rag_outputs))


def main():
    parser = ArgumentParser(description="Resolve ontology terms from RAG candidates.")
    parser.add_argument(
        "-r", "--rag-results", required=True, help="Retrieval stage JSONL output."
    )
    parser.add_argument(
        "--records", required=True, help="Original metadata records JSONL."
    )
    parser.add_argument(
        "-o", "--output", required=True, help="Output JSONL file of proposals."
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml", help="Configuration file."
    )
    parser.add_argument(
        "--top-k", type=int, default=10, help="Candidates to keep per ontology."
    )
    parser.add_argument(
        "--workers", type=int, help="Records to resolve concurrently."
    )
    parser.add_argument(
        "--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="INFO"
    )
    args = parser.parse_args()

    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"ERROR: Config file not found: {args.config}", file=sys.stderr)
        sys.exit(2)

    config.setdefault("logging", {})["level"] = args.log_level
    setup_logging(config)

    rag_outputs = load_rag_results(args.rag_results, top_k=args.top_k)
    if not rag_outputs:
        print(f"ERROR: No RAG results in {args.rag_results}", file=sys.stderr)
        sys.exit(1)

    metadata_by_id = {
        record.record_id: record.model_dump(exclude={"extras"}, exclude_none=True)
        for record in ingest_jsonl(args.records, config)
    }

    workers = args.workers or config.get("parallelism", {}).get("max_workers", 1)
    logger.info(f"Resolving {len(rag_outputs)} records (workers={workers})")

    results = resolve_records(
        rag_outputs, metadata_by_id, ResolveAgent(config), workers=workers
    )

    save_jsonl(results, args.output)
    logger.info(f"Saved {len(results)} proposals to {args.output}")
    print_resolve_summary(results)


if __name__ == "__main__":
    main()
