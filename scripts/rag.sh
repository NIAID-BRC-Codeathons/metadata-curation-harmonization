#!/usr/bin/env bash
#
# Run the full ontology-linking RAG pipeline for a Plan-output JSONL file
# (see src/ontology_selection/engine.md): embed each mapping's query_texts,
# then run FAISS-based retrieval against the prebuilt ontology vector database.
#
# Query embedding and retrieval are split into two conda environments (see
# scripts/embed_query_records.py) since loading both torch and faiss in the
# same process can abort the interpreter on some platforms.
#
# Usage:
#   ./scripts/rag.sh <step_1_sample_records.jsonl> <step_2_term_rankings.jsonl> [vector_db_dir]
#
# Author: Parker Hicks
# Date: 2026-09-17

set -euo pipefail

if [[ $# -ne 2 && $# -ne 3 ]]; then
  echo "Usage: $0 <step_1_sample_records.jsonl> <step_2_term_rankings.jsonl> [vector_db_dir]" >&2
  exit 1
fi

RECORDS="$1"
OUTPUT="$2"
VECTOR_DB_DIR="${3:-dataset/ontology/vector_db}"

source "$(conda info --base)/etc/profile.d/conda.sh"

conda deactivate
conda activate embeddings

python scripts/embed_query_records.py -i "$RECORDS" -o query_embeddings.parquet -m sapbert

conda deactivate
conda activate metadata-curation
PYTHONPATH="src${PYTHONPATH:+:$PYTHONPATH}" python -m ontology_rag.rag \
  -i "$RECORDS" \
  -e query_embeddings.parquet \
  -v "$VECTOR_DB_DIR" \
  -o "$OUTPUT"
