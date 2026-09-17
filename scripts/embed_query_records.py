#!/usr/bin/env python3
"""
Generate embeddings for every ontology-linking query_text in a Plan-output
JSONL file (see ontology_rag.records.load_records for the input format).

Run this in an environment with torch/transformers installed (e.g. the
gpu-linux-cuda118 environment referenced in ontology_rag.embeddings), separate
from the faiss-based `metadata-curation` conda environment used by
ontology_rag.rag. Loading both faiss and torch in the same process can abort
the interpreter on some platforms due to duplicate OpenMP runtimes, so the two
stages are kept as separate scripts/processes rather than one combined step.

Usage:
    python scripts/embed_query_records.py -i plan_outputs.jsonl -o query_embeddings.parquet

Author: Parker Hicks
Date: 2026-09-17
"""

import sys
from argparse import ArgumentParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ontology_rag.embeddings import SUPPORTED_LLMS, EmbeddingGenerator  # noqa: E402
from ontology_rag.query_embeddings import write_query_embeddings  # noqa: E402
from ontology_rag.records import load_records  # noqa: E402


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        required=True,
        help="Path to input Plan-output JSONL file of metadata records to link "
        "(see ontology_rag.records.load_records for the expected format).",
    )
    parser.add_argument(
        "-m",
        "--model",
        choices=list(SUPPORTED_LLMS),
        default="sapbert",
        help="LLM to use for embedding the query text.",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        type=Path,
        default="query_embeddings.parquet",
        help="Path to write the precomputed query embeddings as parquet.",
    )
    args = parser.parse_args()

    records = load_records(args.input)

    record_ids: list[str] = []
    ontologies: list[str] = []
    query_texts: list[str] = []
    for record in records:
        for mapping in record.mappings:
            for query_text in mapping.query_texts:
                record_ids.append(record.record_id)
                ontologies.append(mapping.ontology)
                query_texts.append(query_text)

    generator = EmbeddingGenerator(query_texts, level="document", model=args.model)
    vectors = generator.embeddings.astype("float32")

    write_query_embeddings(record_ids, ontologies, query_texts, vectors, args.outfile)
    print(f"Wrote {len(record_ids)} query embeddings to {args.outfile}")


if __name__ == "__main__":
    main()
