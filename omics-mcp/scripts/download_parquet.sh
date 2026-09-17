#!/usr/bin/env bash
set -euo pipefail

output_dir="${1:-${OMICS_PARQUET_DIR:-$PWD/parquet}}"
workers="${OMICS_DOWNLOAD_WORKERS:-4}"

parquet_base="${OMICS_PARQUET_BASE:-https://ftp.ncbi.nlm.nih.gov/pub/datasets/.argonne/parquet}"
omicidx_base="${OMICS_OMICIDX_BASE:-https://data.omicidx.cancerdatasci.org/latest}"

mkdir -p "$output_dir"

# Local-only extracts plus all OmicIDX tables exposed by the MCP.
urls=(
    "$parquet_base/genome.parquet"
    "$parquet_base/bvbrc.parquet"
    "$omicidx_base/sra_runs.parquet"
    "$omicidx_base/sra_experiments.parquet"
    "$omicidx_base/sra_samples.parquet"
    "$omicidx_base/sra_studies.parquet"
    "$omicidx_base/biosamples.parquet"
    "$omicidx_base/bioprojects.parquet"
    "$omicidx_base/geo_samples.parquet"
    "$omicidx_base/geo_series.parquet"
    "$omicidx_base/geo_platforms.parquet"
)

export output_dir
printf '%s\n' "${urls[@]}" | xargs -P "$workers" -n 1 bash -c '
    set -euo pipefail
    url="$1"
    file="${url##*/}"
    target="$output_dir/$file"
    echo "Downloading $file"
    curl --fail --location --retry 5 --retry-delay 2 --continue-at - \
        --output "$target.part" "$url"
    mv "$target.part" "$target"
    echo "Finished $file ($(du -h "$target" | cut -f1))"
' _

echo
echo "Downloaded Parquet files to: $output_dir"
echo
echo "Set these environment variables before running the MCP or exporter:"
echo "  export OMICS_PARQUET_DIR=$output_dir"
echo "  export OMICS_OMICIDX_DIR=$output_dir"
echo
echo "The exporter can then run locally with:"
echo "  omics-mcp-export /path/to/combined.jsonl.gz"
