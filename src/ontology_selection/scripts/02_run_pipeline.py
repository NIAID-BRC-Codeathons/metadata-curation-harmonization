#!/usr/bin/env python3
"""
Main pipeline script: Run full Plan → RAG → Resolve pipeline.

Usage:
    # Run on test data (mock RAG)
    python scripts/02_run_pipeline.py --test

    # Run on real data
    python scripts/02_run_pipeline.py \\
        --input data/raw/bvbrc_records.jsonl \\
        --output data/out/proposals.jsonl \\
        --limit 100

    # Run with custom config
    python scripts/02_run_pipeline.py \\
        --input data/raw/bvbrc_records.jsonl \\
        --config config.yaml \\
        --workers 4
"""

import argparse
import sys
from pathlib import Path

# Add src/ (the parent of the ontology_selection package) to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from ontology_selection.models import RecordInput
from ontology_selection.pipeline import OntologyPipeline
from ontology_selection.ingest import ingest_jsonl, create_test_records
from ontology_selection.utils import load_config, setup_logging, save_jsonl


def main():
    parser = argparse.ArgumentParser(description="Run ontology selection pipeline")
    parser.add_argument("--input", help="Input JSONL file (BV-BRC records)")
    parser.add_argument("--output", help="Output JSONL file (proposals)")
    parser.add_argument("--config", default="config.yaml", help="Configuration file")
    parser.add_argument("--limit", type=int, help="Process first N records only")
    parser.add_argument("--workers", type=int, help="Override max_workers from config")
    parser.add_argument("--test", action="store_true", help="Run on test data (ignore --input)")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--no-save", action="store_true", help="Don't save outputs (dry run)")
    
    args = parser.parse_args()
    
    # Load configuration
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"ERROR: Config file not found: {args.config}")
        print("Run this script from the src/ontology_selection/ directory")
        sys.exit(1)
    
    # Override config values from CLI args
    if args.workers:
        config.setdefault('parallelism', {})['max_workers'] = args.workers
    
    if args.output:
        config.setdefault('output', {})['final_proposals'] = args.output
    
    # Setup logging
    config.setdefault('logging', {})['level'] = args.log_level
    setup_logging(config)
    
    # Load records
    if args.test:
        print("Running in TEST mode with handcrafted records")
        records = create_test_records()
    elif args.input:
        print(f"Loading records from {args.input}")
        records = ingest_jsonl(args.input, config, limit=args.limit)
    else:
        print("ERROR: Must specify --input or --test")
        sys.exit(1)
    
    if not records:
        print("ERROR: No records loaded")
        sys.exit(1)
    
    print(f"Loaded {len(records)} records")
    
    # Initialize pipeline
    print(f"Initializing pipeline with config: {args.config}")
    pipeline = OntologyPipeline(config)
    
    # Run pipeline
    print("\nStarting pipeline...")
    results = pipeline.process_batch(
        records,
        save_outputs=not args.no_save,
        show_progress=True
    )
    
    # Summary printed by pipeline
    print(f"\nProcessing complete: {len(results)} results")
    
    if args.no_save:
        print("\n(Outputs not saved - dry run mode)")
    else:
        output_path = config.get('output', {}).get('final_proposals', 'data/out/proposals.jsonl')
        print(f"\nResults saved to: {output_path}")
    
    # Exit with error code if all failed
    successful = sum(1 for r in results if r.terms)
    if successful == 0:
        print("\nWARNING: No records produced terms!")
        sys.exit(1)


if __name__ == "__main__":
    main()
