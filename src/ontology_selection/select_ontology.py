#!/usr/bin/env python3
"""
Plan stage: decide which ontologies to search for each record, and with what
query texts.

Reads normalized metadata records and writes one PlanOutput per line. That output
is the retrieval stage's input as-is - no adapter sits between them.

Usage:
    python -m ontology_selection.select_ontology \
        --input data/raw/records.jsonl \
        --output data/intermediate/plan_outputs.jsonl \
        --config src/ontology_selection/config.yaml

Author: Andrew LaPointe
Date: 2026-09-17
"""

import logging
import sys
from argparse import ArgumentParser
from concurrent.futures import ThreadPoolExecutor
from typing import List

from .checks import run_checks
from .ingest import ingest_jsonl
from .models import PlanOutput, RecordInput
from .plan_agent import PlanAgent
from .summary import print_plan_summary
from .utils import load_config, save_jsonl, setup_logging

logger = logging.getLogger(__name__)


def plan_records(
    records: List[RecordInput], agent: PlanAgent, workers: int = 1
) -> List[PlanOutput]:
    """
    Plan every record, preserving input order.

    Order matters here in a way it did not when the pipeline was per-record: this
    output is the next stage's input file, and keeping it aligned with the input
    makes the two trivially diffable.

    Args:
        records: Normalized input records
        agent: Plan agent
        workers: Number of records to plan concurrently

    Returns:
        List of PlanOutput, one per record, in input order
    """
    def plan_one(record: RecordInput) -> PlanOutput:
        return run_checks(agent.plan(record), stage="plan")

    if workers <= 1:
        return [plan_one(record) for record in records]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(plan_one, records))


def main():
    parser = ArgumentParser(description="Plan ontology searches for metadata records.")
    parser.add_argument(
        "-i", "--input", required=True, help="Input JSONL file of metadata records."
    )
    parser.add_argument(
        "-o", "--output", required=True, help="Output JSONL file of plans."
    )
    parser.add_argument(
        "-c", "--config", default="config.yaml", help="Configuration file."
    )
    parser.add_argument(
        "--limit", type=int, help="Plan only the first N records."
    )
    parser.add_argument(
        "--workers", type=int, help="Records to plan concurrently."
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

    records = ingest_jsonl(args.input, config, limit=args.limit)
    if not records:
        print(f"ERROR: No records loaded from {args.input}", file=sys.stderr)
        sys.exit(1)

    workers = args.workers or config.get("parallelism", {}).get("max_workers", 1)
    logger.info(f"Planning {len(records)} records (workers={workers})")

    plans = plan_records(records, PlanAgent(config), workers=workers)

    save_jsonl(plans, args.output)
    logger.info(f"Saved {len(plans)} plans to {args.output}")
    print_plan_summary(plans)


if __name__ == "__main__":
    main()
