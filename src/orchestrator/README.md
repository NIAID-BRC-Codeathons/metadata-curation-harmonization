# orchestrator

Runs the `plan → embed → retrieve → resolve` engine, one stage at a time, each to
completion, each in its own conda environment.

## Why stages, not one process

Two hard constraints shape this:

- **The retrieval stage is batched.** `ontology_rag.rag` groups every
  `(record_id, ontology, query_text)` triple by ontology so each FAISS index is built
  once and searched in a single call. The MONDO index alone is 27,990 × 768 floats.
  Retrieving per record would rebuild it per record.
- **Embedding and retrieval cannot share a process.** torch bundles its own `libomp`
  and conda's `faiss-cpu` links `llvm-openmp`; both in one interpreter aborts it (see
  `src/ontology_rag/README.md`). They must be separate processes in separate
  environments.

Together these mean the whole corpus has to clear Plan before any of it enters
Retrieve. Stages hand work to each other as files.

## Setup

Build the orchestrator environment once, from the repo root:

```bash
conda env create -f src/orchestrator/environment.yaml
```

It needs only Python and pyyaml - the heavy dependencies live in the stage
environments, which the orchestrator enters with `conda run` rather than importing
from. Rebuild it after changing `environment.yaml`:

```bash
conda env update -f src/orchestrator/environment.yaml --prune
```

The stages need their own environments before a full run will work:

```bash
conda env create -f environment.yaml                          # metadata-curation (faiss)
conda env create -f pytorch_environment.yaml                  # embeddings (torch)
conda env create -f src/ontology_selection/environment.yaml   # ontology-selection (langchain)
```

The orchestrator checks that each selected stage's environment exists before running
anything, and names the missing one if not.

## Usage

Run from the repo root:

```bash
conda run -n orchestrator python scripts/run_pipeline.py --dry-run   # print commands, run nothing
conda run -n orchestrator python scripts/run_pipeline.py             # run everything
conda run -n orchestrator python scripts/run_pipeline.py --only retrieve
conda run -n orchestrator python scripts/run_pipeline.py --from embed
conda run -n orchestrator python scripts/run_pipeline.py --to plan
```

`scripts/run_pipeline.py` is a launcher that puts `src/` on the path. The package
itself is `orchestrator`, so this is the same thing:

```bash
PYTHONPATH=src conda run -n orchestrator python -m orchestrator --dry-run
```

`--from`/`--to` slice the pipeline so a failed late stage can be rerun without
repeating the LLM stages. `--only` runs named stages and cannot be combined with them.

## Configuration

Everything lives in `pipeline.yaml` at the repo root: the artifacts passed between
stages, the settings stages share, and the command and conda environment for each
stage. Nothing about the pipeline is hardcoded here.

```yaml
artifacts:
  plans: data/intermediate/plan_outputs.jsonl

settings:
  top_k: 10

stages:
  - name: retrieve
    env: metadata-curation
    command: python -m ontology_rag.rag -i {plans} -k {top_k} ...
    inputs:  [plans, embeddings]
    outputs: [rag_results]
```

A `{token}` in a command is filled from `artifacts` and `settings`. `workdir` is
resolved relative to the config file and becomes each stage's working directory.

## What runs before anything else

The config is validated at load time and the run is checked before the first stage
starts, so mistakes surface immediately rather than part way through:

- duplicate stage names, and `inputs`/`outputs` naming unknown artifacts
- `{token}`s with nothing to fill them
- conda environments that do not exist, for the selected stages only
- input artifacts that are missing and that no earlier selected stage produces

Each stage then gets `PYTHONPATH=<workdir>/src` (both `ontology_rag` and
`ontology_selection` import as packages from `src/`), has its output directories
created, and streams its own stdout and stderr. A non-zero exit stops the pipeline;
so does a stage that exits 0 without writing a declared output.

## Module layout

- `config.py` - `Stage`/`PipelineConfig`, `load_pipeline_config()`, validation.
- `runner.py` - `run_stage()`/`run_pipeline()`, the `conda run` invocation and preflight.
- `__main__.py` - the CLI, reached through `scripts/run_pipeline.py`.
