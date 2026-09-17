# ontology_selection

The LLM stages of the engine: **Plan** (step A) decides which ontologies to search and
with what query texts; **Resolve** (step C) picks the final term set from the retrieved
candidates. Retrieval itself (step B) is `src/ontology_rag`.

Both stages are batch CLIs over JSONL. They are normally driven by the orchestrator
(`python scripts/run_pipeline.py`), which runs them in order with the retrieval stages in
between - see `pipeline.yaml` and `src/orchestrator/README.md`. The contracts are
defined in `../engine.md`.

## Setup

```bash
conda env create -f environment.yaml
conda activate ontology-selection

cp .env.example .env     # then set ARGO_USER=ac.yourname
```

`ARGO_USER` is read from `.env` beside this package, or from the shell, which wins.
It never lives in `config.yaml`.

## Running a stage directly

Both need `src/` on the path; the orchestrator sets this for you.

```bash
export PYTHONPATH=$PWD/src

# A. Plan
python -m ontology_selection.select_ontology \
    --input data/raw/records.jsonl \
    --output data/intermediate/plan_outputs.jsonl \
    --config src/ontology_selection/config.yaml \
    --workers 4 [--limit N]

# C. Resolve
python -m ontology_selection.resolve \
    --rag-results data/intermediate/rag_results.jsonl \
    --records data/raw/records.jsonl \
    --output data/out/proposals.jsonl \
    --config src/ontology_selection/config.yaml \
    --top-k 10 --workers 4
```

Both preserve input order, so every intermediate file lines up row for row with
`records.jsonl`.

## Input

`ingest_jsonl` accepts two shapes and detects which per line:

- **Flat engine rows** (`engine.md` section 6.1) - a top-level `record_id` plus the
  searchable fields. Fields the Plan agent can use are lifted out of
  `extras.attributes` when the top level leaves them unset.
- **Nested BV-BRC/NCBI records** - flattened by `normalize_bvbrc_record`.

## The Plan → Retrieve boundary

`PlanOutput` serialized to JSONL *is* the retrieval stage's input format. No adapter
sits between them: `scripts/embed_query_records.py` and `ontology_rag.rag` both read
`plan_outputs.jsonl` directly.

```json
{"record_id": "SAMN50884510",
 "mappings": [{"ontology": "UBERON",
               "src_fields": [{"path": "bvbrc.isolation_source",
                               "name": "isolation_source", "value": "Groin"}],
               "query_texts": ["groin", "inguinal region"]}],
 "flags": []}
```

## The Retrieve → Resolve boundary

Here an adapter *is* needed, and it is `rag_results.py`. Retrieval reports top k
matches **per query text**; Resolve reasons over one ranked candidate list **per
ontology**. `load_rag_results()` flattens the former into the latter: matches from
every query text in a bucket are merged, a term reached by several queries keeps its
best score, and what survives is re-sorted and re-ranked to `top_k`.

`definition` is left `null`. The vector database carries term ids and names only, and
`engine.md` is explicit that a missing definition is reported, never invented. The
`missing_definition` soft check is disabled for the same reason - with definitions
uniformly absent it would fire on every record and collapse `outcome` to a constant.
Restore it when the vector database is rebuilt with definitions.

## Configuration

`config.yaml` holds what belongs to these two stages: the `llm` block (Argo model,
temperature, base URL), `field_selection`, `parallelism` and `logging`.

Stage commands, artifact paths and the retrieval settings live one level up in
`pipeline.yaml`, because the orchestrator owns them.

## Module layout

| File | Job |
|---|---|
| `models.py` | The pydantic contracts from `engine.md` section 6 |
| `ingest.py` | Raw JSONL -> `RecordInput`, either input shape |
| `plan_agent.py` | Step A: the LLM call and its rules-based fallback |
| `select_ontology.py` | Step A CLI |
| `rag_results.py` | Retrieval output -> `RAGOutput` (the adapter) |
| `resolve_agent.py` | Step C: the LLM call, the invented-CURIE guard, the fallback |
| `resolve.py` | Step C CLI |
| `checks.py` | Soft checkers - flags, never exceptions |
| `summary.py` | End-of-stage run summaries |
| `utils.py` | Argo client, config, logging, JSONL I/O |
| `prompts/` | The system prompts, as markdown |

## Flags

Flags accumulate and travel to review rather than stopping the run. Plan flags reach
the final output through retrieval, which copies them onto its own result.

| Flag | Meaning |
|---|---|
| `no_mappings` | Plan found nothing worth searching |
| `empty_record` | No usable fields on the record |
| `ambiguous_source` | Source text too vague to map confidently |
| `looks_like_host` | Host-like text in `isolation_source` (`engine.md` section 8) |
| `<ONTOLOGY>: top match score ... below threshold` | Weak retrieval, from `ontology_rag` |
| `empty_rag_<ONTOLOGY>` | A bucket came back with no candidates |
| `invented_curie_removed` | Resolve named a CURIE that was not retrieved; dropped |
| `plan_agent_error` / `resolve_agent_error` | LLM call failed; heuristic fallback used |

## Troubleshooting

**`ModuleNotFoundError: No module named 'ontology_selection'`** - set
`PYTHONPATH=$PWD/src`, or run through the orchestrator, which sets it.

**`RuntimeError: ARGO_USER is not set`** - copy `.env.example` to `.env` and set it.

**`Invalid json output: ... ACCESS DENIED ... FROM ARGO`** - the gateway returned an
error body with HTTP 200. Seen transiently at roughly 1 call in 400; the agent falls
back to its heuristic and flags the record. If it is every call, the account is not
authorized for the Argo API.
