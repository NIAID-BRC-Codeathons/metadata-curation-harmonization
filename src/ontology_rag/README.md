# ontology_rag

Retrieves the top k ontology terms most similar to free-text metadata fields, using FAISS
similarity search over precomputed term embeddings.

## Two environments

Embedding generation (`torch`/`transformers`) and FAISS search (`faiss`) must run in
**separate environments**. On at least macOS + conda, loading both in one process crashes
the interpreter (`Fatal Python error: Aborted`) because pip-installed `torch` bundles its
own `libomp.dylib` while conda's `faiss-cpu` links `llvm-openmp` — two copies of the same
OpenMP runtime in one process. `ontology_rag.rag` (and everything it imports) never imports
`torch`, so as long as each script below runs in its intended environment, this can't happen.

- **faiss env** — `environment.yaml` in the repo root (`conda env create -f environment.yaml`).
  Used to run `ontology_rag.rag`.
- **torch env** — not included in this repo; needs `torch` and `transformers` installed
  (e.g. the `gpu-linux-cuda118` environment used elsewhere in this project). Used to run
  `scripts/embed_query_records.py`.

## Running the pipeline

Input is a Plan-output JSONL file of metadata records to link (see
`records.load_records` and `src/ontology_selection/engine.md` section 6.2 for the exact
schema: one JSON object per line, each with a `record_id` plus a list of `mappings`, where
each mapping is `{"ontology": ..., "src_fields": [{"path": ..., "name": ..., "value": ...}],
"query_texts": [...]}`).

```bash
# 1. torch env: embed every mapping's query_texts
python scripts/embed_query_records.py -i plan_outputs.jsonl -o query_embeddings.parquet

# 2. faiss env: run FAISS search using the precomputed embeddings
python -m ontology_rag.rag \
  -i plan_outputs.jsonl \
  -e query_embeddings.parquet \
  -v path/to/vector_db_dir \
  -o rag_results.jsonl
```

Output is JSONL, one record per line, each holding a `buckets` list (one bucket per ontology
mapping, echoing its `src_fields`/`query_texts` for provenance) where each `query_texts` entry
carries its own top k `matches`.

`vector_db_dir` must contain one `<ontology>.parquet` file per ontology queried (e.g.
`uberon.parquet`), each with an `id` column, a `name` column, and one column per embedding
dimension — see `scripts/make_example_vector_db.py` for an example builder.

## Module layout

- `records.py` — `MetadataRecord`/`OntologyMapping`/`SrcField` data model and
  `load_records()`. No faiss/torch dependency; shared by both stages.
- `query_embeddings.py` — reads/writes the intermediate embeddings parquet
  (`write_query_embeddings`/`read_query_embeddings`), keyed by
  `(record_id, ontology, query_text)`. No faiss/torch dependency; shared by both stages.
- `embeddings.py` — `EmbeddingGenerator` (torch/transformers). Only imported by
  `scripts/embed_query_records.py`.
- `vector_db.py` — `Faiss` wrapper (faiss). Only imported by `rag.py`.
- `rag.py` — `OntologyRag`/`OntologyVectorStore`, the FAISS retrieval stage. Produces one
  `RecordResult` (with `OntologyBucket`/`QueryTextResult`/`TermMatch`) per input record. Never
  imports `embeddings.py`.
