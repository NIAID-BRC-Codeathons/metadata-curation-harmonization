#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
    echo "Usage: $0 JOBS RECORDS_PER_FILE OUTPUT_DIR [EXPORT_COMMAND]" >&2
    exit 2
fi

jobs="$1"
records_per_file="$2"
output_dir="$3"
export_command="${4:-omics-mcp-export}"

if [[ "$jobs" -lt 1 || "$records_per_file" -lt 1 ]]; then
    echo "JOBS and RECORDS_PER_FILE must be positive integers" >&2
    exit 2
fi

mkdir -p "$output_dir"

pids=()
for ((job=0; job<jobs; job++)); do
    offset=$((job * records_per_file))
    output="$output_dir/chunk-$(printf '%04d' "$job").jsonl.gz"

    echo "Starting job $job: offset=$offset limit=$records_per_file -> $output"

    "$export_command" \
        --offset "$offset" \
        --limit "$records_per_file" \
        --omit-unmatched-bvbrc \
        "$output" \
        >"$output.log" 2>&1 &
    pids+=("$!")
done

failed=0
for ((job=0; job<jobs; job++)); do
    if wait "${pids[$job]}"; then
        echo "Finished job $job"
    else
        echo "Failed job $job; see $output_dir/chunk-$(printf '%04d' "$job").jsonl.gz.log" >&2
        failed=1
    fi
done

if [[ "$failed" -ne 0 ]]; then
    exit 1
fi

echo "All $jobs jobs completed. Output directory: $output_dir"
echo "Combine chunks with:"
echo "  gzip -cd $output_dir/chunk-*.jsonl.gz > $output_dir/combined.jsonl"
