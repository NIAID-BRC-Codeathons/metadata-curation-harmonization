# omics-mcp

An MCP server that lets an AI agent query local NCBI genome extracts and the
public [OmicIDX](https://github.com/seandavi/omicidx) datasets together, in one
SQL statement, using DuckDB.

Local Parquet covers what OmicIDX does not carry:

| Table | Source | Notes |
| --- | --- | --- |
| `genome` | NCBI Datasets assembly reports | hosted Parquet, 158k rows |
| `bvbrc` | BV-BRC genome metadata | hosted Parquet, 26k rows |

Both are published at
`https://ftp.ncbi.nlm.nih.gov/pub/datasets/.argonne/parquet/` and read over
HTTPS, so there is nothing to build or download to get started.

Everything else is read straight from OmicIDX over HTTPS, with no download and
no credentials: `omicidx_bioprojects`, `omicidx_biosamples`, `omicidx_sra_runs`,
`omicidx_sra_experiments`, `omicidx_sra_samples`, `omicidx_sra_studies`,
`omicidx_geo_samples`, `omicidx_geo_series`, `omicidx_geo_platforms`.

## Install

```bash
pip install -e .
```

## Register with an MCP client

Copy `mcp.json.example` to `.vscode/mcp.json` in the workspace you want it in,
reload the window, then open Copilot Chat in Agent mode. No other setup is
needed.

## Rebuilding the hosted Parquet

Only needed when the upstream extracts change. Point the builder at the
directory holding `genome.jsonl` and `bvbrc_staph.jsonl`:

```bash
omics-mcp-build --data-dir /path/to/extracts --out-dir /path/to/parquet
```

The conversion is small and fast: 612 MB of `genome.jsonl` becomes roughly
17 MB of Parquet, and the whole run takes a few seconds. Upload the result to
the hosted location, or set `OMICS_PARQUET_DIR` to use it locally.

## Tools

| Tool | Purpose |
| --- | --- |
| `list_datasets` | Every dataset, whether local or OmicIDX, and whether it is available |
| `describe_dataset` | Column names and types for one dataset |
| `query` | Read-only SQL across local and OmicIDX tables |
| `get_genome` | Everything known about one assembly accession, assembled |

`get_genome` resolves BV-BRC with the same ordered fallbacks as `combine.py`
(`assembly_accession`, then `biosample_accession`, then a WGS-prefix match on
`genbank_accessions`) and reports which one hit under `matched_by`.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `OMICS_PARQUET_BASE` | NCBI FTP `pub/datasets/.argonne/parquet` | Hosted `genome`/`bvbrc` Parquet |
| `OMICS_PARQUET_DIR` | unset | Local Parquet directory; used when the files exist there |
| `OMICS_OMICIDX_DIR` | unset | Local cached OmicIDX Parquet directory |
| `OMICS_DATA_DIR` | `.` | Where the source JSONL files live |
| `OMICS_OMICIDX_BASE` | OmicIDX `latest` | Override to pin a dated snapshot |
| `OMICS_MAX_ROWS` | `500` | Hard cap on rows returned |

Set `OMICS_PARQUET_DIR` to prefer local files, which are noticeably faster than
the hosted copies for repeated full scans.

## Notes

`query` accepts a single `SELECT`, `WITH`, or `DESCRIBE` statement and caps the
row count. It is a usability guard for agent-written SQL, not a security
boundary: anyone who can reach the server can read every registered dataset.

The local Parquet is a point-in-time snapshot while OmicIDX refreshes daily, so
rerun `omics-mcp-build` and republish when the extracts change.

## Download all Parquet locally

For repeated full exports, download the hosted files once instead of scanning
OmicIDX over HTTPS for every export:

```bash
./scripts/download_parquet.sh /data/omics-parquet
export OMICS_PARQUET_DIR=/data/omics-parquet
export OMICS_OMICIDX_DIR=/data/omics-parquet
```

The script downloads the two NCBI-hosted local tables and all OmicIDX tables,
using four concurrent downloads by default. Set `OMICS_DOWNLOAD_WORKERS` to
change that concurrency. It prints the environment settings when complete.

Then run the full combined export locally:

```bash
omics-mcp-export /data/combined.jsonl.gz
```

To run fixed-size exports in parallel:

```bash
./scripts/run_parallel_export.sh 8 10000 /data/chunks
```

This starts 8 jobs with 10,000 genome records per job. Each job writes a
`chunk-NNNN.jsonl.gz` file and a matching log. The launcher waits for all jobs
and exits nonzero if any job fails. Merge successful chunks with:

```bash
gzip -cd /data/chunks/chunk-*.jsonl.gz > /data/chunks/combined.jsonl
```
