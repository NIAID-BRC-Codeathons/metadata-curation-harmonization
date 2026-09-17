# AI-Driven Metadata Curation - LangChain Implementation

LangChain-based implementation of the Plan → Retrieve → Resolve pipeline for mapping biological sample metadata to ontology terms (UBERON, MONDO, ENVO).

## Quick Start

### 1. Install Dependencies

```bash
# From the src/ontology_selection/ directory
mamba env create -f environment.yaml   # or: conda env create -f environment.yaml
conda activate metadata-curation-langchain
```

### 2. Configure Environment

```bash
# Copy example env file
cp .env.example .env

# Edit .env and set your ARGO_USER
nano .env
```

Example `.env`:
```bash
ARGO_USER=ac.yourname
```

### 3. Test with Mock Data

```bash
# Run on 5 handcrafted test records with mock RAG
python scripts/02_run_pipeline.py --test
```

This will process test records and save results to `data/out/proposals.jsonl`.

### 4. Run on Real Data

```bash
# Process BV-BRC records (first 100)
python scripts/02_run_pipeline.py \
    --input ../../data/inputs/sample.input.jsonl \
    --output data/out/proposals.jsonl \
    --limit 100
```

## Architecture

```
RecordInput (BV-BRC JSON)
    ↓
┌────────────────────┐
│   PLAN AGENT       │  LLM decides: which ontologies? what queries?
│   (claudesonnet5)  │
└────────┬───────────┘
         │ PlanOutput {mappings: [{ontology, fields, query_texts}]}
         ↓
┌────────────────────┐
│   RAG CLIENT       │  Calls external rag.py (or mock)
│   (subprocess)     │  Embeds queries, searches ontology DBs
└────────┬───────────┘
         │ RAGOutput {buckets: [{ontology, candidates[]}]}
         ↓
┌────────────────────┐
│  RESOLVE AGENT     │  LLM picks best terms, assigns roles/confidence
│  (claudesonnet5)   │
└────────┬───────────┘
         │
         ↓
ResolveOutput {terms: [{curie, label, role, confidence}]}
```

## Configuration

Edit `config.yaml` to customize:

- **LLM settings** (model, temperature, max_tokens)
- **RAG command** (path to rag.py, parameters)
- **Field selection** (stop list, consider list)
- **Parallelization** (max_workers)
- **Logging** (level, file path)

Key settings:

```yaml
llm:
  model: claudesonnet5
  temperature: 1

rag:
  command: "python scripts/00_mock_rag.py"  # Change to real rag.py path
  top_k: 10
  low_score_threshold: 0.70

parallelism:
  max_workers: 1  # Increase for parallel processing
```

## Pipeline Stages

### Stage 1: Plan Agent

**Input:** `RecordInput` (normalized BV-BRC metadata)  
**Output:** `PlanOutput` (ontology mappings + query texts)

The Plan agent analyzes metadata fields and decides:
- Which ontologies are relevant (UBERON, MONDO, ENVO)
- Which input fields map to each ontology
- What search queries to generate (1-3 per ontology)

Example:
```json
{
  "record_id": "SAMN123",
  "mappings": [
    {
      "ontology": "UBERON",
      "fields": ["isolation_source"],
      "query_texts": ["blood"]
    },
    {
      "ontology": "MONDO",
      "fields": ["note"],
      "query_texts": ["sepsis", "bloodstream infection"]
    }
  ],
  "flags": []
}
```

### Stage 2: RAG Retrieval

**Input:** `PlanOutput`  
**Output:** `RAGOutput` (candidate terms)

Calls external RAG system (or mock) to:
- Embed query texts
- Search pre-indexed ontology databases
- Return top-k candidates per ontology

Example:
```json
{
  "record_id": "SAMN123",
  "buckets": [
    {
      "ontology": "UBERON",
      "candidates": [
        {
          "curie": "UBERON:0000178",
          "label": "blood",
          "definition": "A fluid connective tissue...",
          "score": 0.95,
          "rank": 0
        }
      ]
    }
  ]
}
```

### Stage 3: Resolve Agent

**Input:** `RAGOutput` + original metadata  
**Output:** `ResolveOutput` (final selected terms)

The Resolve agent:
- Picks best terms from RAG candidates
- Assigns roles (primary/secondary/alternate)
- Assigns confidence scores
- Can select multiple terms across ontologies
- Can abstain if no good matches

Example:
```json
{
  "record_id": "SAMN123",
  "outcome": "proposed",
  "terms": [
    {
      "ontology": "UBERON",
      "term_id": "UBERON:0000178",
      "label": "blood",
      "role": "primary",
      "confidence": 0.95
    },
    {
      "ontology": "MONDO",
      "term_id": "MONDO:0005015",
      "label": "sepsis",
      "role": "primary",
      "confidence": 0.88
    }
  ],
  "flags": []
}
```

## Project Structure

All paths below are relative to the repository root.

```
src/ontology_selection/          # The package — and the working directory for all commands
├── config.yaml              # Main configuration
├── .env                     # Credentials (gitignored)
├── environment.yaml         # Conda environment definition
│
├── models.py                # Pydantic schemas
├── plan_agent.py            # Plan agent (LLM)
├── rag_client.py            # RAG wrapper
├── resolve_agent.py         # Resolve agent (LLM)
├── checks.py                # Soft validation
├── pipeline.py              # Orchestration
├── ingest.py                # BV-BRC JSON normalization
├── utils.py                 # Helpers
│
├── prompts/                 # System prompts for the Plan and Resolve agents
│
├── scripts/
│   ├── 00_mock_rag.py       # Mock RAG for testing
│   └── 02_run_pipeline.py   # Main entry point
│
├── data/
│   ├── intermediate/        # Plan/RAG outputs (optional)
│   ├── out/                 # Final proposals.jsonl, pipeline.log
│   └── test/
│
└── data_examples/           # Checked-in sample outputs for reference
```

Input data lives outside the package, at the repository root:

```
data/
├── inputs/                  # Source datasets (sample.input.jsonl, ...)
└── raw/                     # Working rows (records.jsonl)
```

## CLI Usage

### Run Pipeline

```bash
# Test mode (5 handcrafted records, mock RAG)
python scripts/02_run_pipeline.py --test

# Real data, first 100 records
python scripts/02_run_pipeline.py \
    --input ../../data/raw/records.jsonl \
    --limit 100

# Full dataset, 4 parallel workers
python scripts/02_run_pipeline.py \
    --input ../../data/raw/records.jsonl \
    --workers 4

# Custom config
python scripts/02_run_pipeline.py \
    --input ../../data/raw/records.jsonl \
    --config my_config.yaml

# Dry run (don't save outputs)
python scripts/02_run_pipeline.py \
    --input ../../data/raw/records.jsonl \
    --no-save
```

### Options

- `--input FILE`: Input JSONL file (BV-BRC records)
- `--output FILE`: Output JSONL file (proposals)
- `--config FILE`: Configuration file (default: config.yaml)
- `--limit N`: Process first N records only
- `--workers N`: Override max_workers from config
- `--test`: Run on test data (ignores --input)
- `--log-level`: DEBUG, INFO, WARNING, ERROR
- `--no-save`: Dry run (don't write outputs)

## Testing Mock RAG

The mock RAG system (`scripts/00_mock_rag.py`) simulates the real RAG interface for testing.

```bash
# Test mock RAG directly
python scripts/00_mock_rag.py \
    --input test_input.json \
    --outfile test_output.json \
    --top-k 10 \
    --low-score-threshold 0.70
```

Input format (`test_input.json`):
```json
{
  "record_id": "TEST001",
  "ontologies": [
    {
      "ontology": "UBERON",
      "fields": ["isolation_source"],
      "query_texts": ["blood"]
    }
  ]
}
```

## Switching to Real RAG

1. Update `config.yaml`:

```yaml
rag:
  command: "python /path/to/real_rag.py"
  top_k: 10
  # ... other rag settings
```

2. Ensure real `rag.py` accepts the same CLI arguments:

```bash
rag.py --input in.json --top-k 10 --model biomedbert \
       --metric cosine --device gpu --low-score-threshold 0.70 \
       --outfile out.json
```

## Output Files

### Intermediate Outputs (for debugging)

When `save_intermediate: true` in `config.yaml` (default), the pipeline saves:

**1. Plan outputs** (`data/intermediate/plan_outputs.jsonl`):
```json
{
  "record_id": "TEST001",
  "mappings": [
    {"ontology": "UBERON", "fields": ["isolation_source"], "query_texts": ["blood"]},
    {"ontology": "MONDO", "fields": ["note"], "query_texts": ["bloodstream infection"]}
  ],
  "flags": []
}
```

**2. RAG outputs** (`data/intermediate/rag_results.jsonl`):
```json
{
  "record_id": "TEST001",
  "buckets": [
    {
      "ontology": "UBERON",
      "fields": ["isolation_source"],
      "query_texts": ["blood"],
      "candidates": [
        {"curie": "UBERON:0000178", "label": "blood", "score": 0.95, "rank": 0}
      ]
    }
  ]
}
```

These files show exactly what was passed between pipeline stages, making debugging easier.

### Final Output Format

The pipeline produces `proposals.jsonl` with one JSON object per line:

```json
{
  "record_id": "SAMN02603524",
  "outcome": "proposed",
  "terms": [
    {
      "ontology": "UBERON",
      "term_id": "UBERON:0000178",
      "label": "blood",
      "role": "primary",
      "confidence": 0.95
    }
  ],
  "candidate_curies": ["UBERON:0000178", "UBERON:0001977", ...],
  "flags": [],
  "abstain_reason": null,
  "original_metadata": { ... }
}
```

## Flags

The pipeline adds flags to track edge cases:

**Plan stage:**
- `looks_like_host`: isolation_source appears to be a species name
- `empty_record`: All key fields are null/empty
- `no_mappings`: Plan agent produced no ontology mappings
- `long_query_text`: Query text exceeds 200 characters
- `plan_agent_error`: LLM failed, using fallback

**RAG stage:**
- `empty_rag_UBERON`: No candidates returned for UBERON
- `low_scores_MONDO`: All candidates have score < 0.5
- `missing_definition`: Some candidates lack definitions

**Resolve stage:**
- `invented_curie_removed`: LLM tried to invent a CURIE
- `low_confidence_primary`: Primary term has confidence < 0.5
- `multiple_primary_same_ontology`: Multiple primary terms for one ontology
- `resolve_agent_error`: LLM failed, using fallback

## Troubleshooting

### ARGO_USER not set

```
WARNING: ARGO_USER not set in environment, using default 'ac.yourname'
```

**Fix:** Set `ARGO_USER` in `.env` or export in shell:
```bash
export ARGO_USER=ac.yourname
```

### Config file not found

```
ERROR: Config file not found: config.yaml
```

**Fix:** Run script from the `src/ontology_selection/` directory:
```bash
cd src/ontology_selection/
python scripts/02_run_pipeline.py --test
```

### Mock RAG not found

```
FileNotFoundError: [Errno 2] No such file or directory: 'python'
```

**Fix:** Update RAG command in config.yaml to use full path:
```yaml
rag:
  command: "python3 scripts/00_mock_rag.py"
```

### LLM returns invalid JSON

The pipeline includes fallback logic for LLM failures. Check logs for:
```
Plan agent failed for SAMN123: JSON decode error
```

This triggers heuristic-based fallback. If frequent, try:
- Adjusting prompts in `plan_agent.py` or `resolve_agent.py`
- Increasing `max_tokens` in config.yaml
- Using a different model (e.g., `claudeopus5`)

## Next Steps

### Day 2 (Tomorrow)
- [ ] Integrate real RAG system (replace mock)
- [ ] Build Web UI for human review (Streamlit)
- [ ] Test on 100+ real BV-BRC records

### Day 3
- [ ] Process full 6k dataset
- [ ] Evaluation framework (vs gold standard)
- [ ] Error analysis and prompt tuning
- [ ] Documentation and demo

## References

- [engine.md](../engine.md): Pipeline specification
- [Argo Quickstart](../../../ANL-Argo-Quickstart/README.md): LLM access documentation
- [BV-BRC](https://www.bv-brc.org/): Bacterial/viral resource center
