# AI-Driven Metadata Curation and Harmonization

**NIAID-BRCs AI Codeathon 2.0** · September 16–18, 2026 · Argonne National Laboratory

Improving the findability and reuse of pathogen datasets by completing, normalizing, and connecting sparse BioProject, BioSample, SRA, and BRC metadata.

Project page: https://niaid-brc-codeathons.github.io/projects/metadata-curation-harmonization/

---

> **This is a draft pitch, not a plan.**
>
> What follows is a one-slide proposal from the organizing team. It exists
> to seed a team, not to constrain one. Scope, methods, target organism,
> and success criteria are all still open — expect them to change
> substantially. Turning this into a real plan is the team's first job, and
> it lands in the project charter due August 28, 2026.

---

## Goal (proposed)

Improve the findability and reuse of pathogen datasets by completing, normalizing, and connecting sparse BioProject, BioSample, SRA, and BRC metadata.

## Three-Day MVP (proposed)

Select approximately 5,000 BioProjects or 10,000 pathogen records. Normalize host, isolation source, geography, collection date, disease, organism, sequencing strategy, and funding fields. Use linked publications and related records as evidence for proposed corrections.

Create a graph linking studies, samples, sequences, publications, organisms, diseases, repositories, and NIAID programs.

## Model and evaluation (proposed)

Train or calibrate ontology-linking and metadata-normalization components. Hide known metadata fields and measure exact match, hierarchical ontology match, confidence calibration, and unsupported-completion rate.

## Leads

- Parker Hicks
- Yang Lu

Team assignments are still being finalized. Participants can review their project, and request a reassignment, in the participant spreadsheet circulated by the organizing team.

## Members

- Curtis Hendrickson
- Andrew LaPointe

## Working here

This repository is the team's working space for the codeathon — code, notebooks, data pointers, and notes. Replace this README with the real thing once the charter is written. Team members get access through the [NIAID-BRC-Codeathons](https://github.com/NIAID-BRC-Codeathons) organization; accept the invitation if you have not already.

## Running the pipeline

The engine runs as four stages - `plan -> embed -> retrieve -> resolve` - each to
completion, each in its own conda environment, handing work to the next as a file.
Stage commands and all pipeline-wide settings live in `pipeline.yaml`.

```bash
conda run -n orchestrator python scripts/run_pipeline.py --dry-run   # print commands, run nothing
conda run -n orchestrator python scripts/run_pipeline.py             # run everything
conda run -n orchestrator python scripts/run_pipeline.py --only retrieve
conda run -n orchestrator python scripts/run_pipeline.py --from embed
```

| Stage | Environment | Reads | Writes |
|---|---|---|---|
| `plan` | `ontology-selection` | `data/raw/records.jsonl` | `data/intermediate/plan_outputs.jsonl` |
| `embed` | `embeddings` | the plans | `data/intermediate/query_embeddings.parquet` |
| `retrieve` | `metadata-curation` | plans + embeddings | `data/intermediate/rag_results.jsonl` |
| `resolve` | `ontology-selection` | RAG results + records | `data/out/proposals.jsonl` |

Four stages rather than one process because the retrieval stage is batched over the
whole corpus (each FAISS index is built once, not once per record), and because torch
and faiss cannot share an interpreter. See `src/orchestrator/README.md`.

The engine's data contracts are in `src/engine.md`.

## Environment

- We will use the FAISS (Facebook AI Similiarity Search) vector database package for the ontology RAG implementation. FAISS requires `conda` to install, so we will need to use a `conda` environment for that, at least. I would rather use `pixi`, but I assume most people are more familiar with `conda` anyways so this works.
  - See `environment.yaml` for the environment build.

Four environments, one per stage plus the orchestrator:

```bash
conda env create -f environment.yaml                    # metadata-curation (faiss, retrieval)
conda env create -f pytorch_environment.yaml            # embeddings (torch, embedding)
conda env create -f src/ontology_selection/environment.yaml   # ontology-selection (langchain)
conda env create -f src/orchestrator/environment.yaml   # orchestrator (pyyaml only)
```

`metadata-curation` also needs its PyPI dependencies:

```bash
conda activate metadata-curation
pip install -r requirements.txt
```

The LLM stages need an Argo username: copy `src/ontology_selection/.env.example` to
`.env` beside it and set `ARGO_USER`.
