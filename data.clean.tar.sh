#!/usr/bin/env bash
#
# Clean out background files from copies of data.*/ run directories,
# then tar them up.  Skips directories whose tarball is already newer
# than every file inside the directory.
#
set -euo pipefail

cat > rm.txt <<EOF
./inputs/.gitignore
./intermediate/test100_embeddings.parquet
./intermediate/test100_plans.jsonl
./intermediate/test100_rag.jsonl
./out/evaluation/demo.jsonl
./out/plans.jsonl
./out/plans_rules.jsonl
./out/test100_proposals.jsonl
./raw/records.jsonl
EOF

DIRLIST=$(ls -1d data.v* 2>/dev/null | grep -v '\.tar' || true)
if [ -z "$DIRLIST" ]; then
    echo "No data.v* directories found."
    rm -f rm.txt
    exit 0
fi
echo "DIRLIST=$DIRLIST"

for x in $DIRLIST; do
    tarball="${x}.tar.gz"

    # Skip if tarball exists and no file in the directory is newer.
    if [ -f "$tarball" ]; then
        newer=$(find "$x" -newer "$tarball" -type f 2>/dev/null | head -1)
        if [ -z "$newer" ]; then
            echo "### $x — up to date, skipping"
            continue
        fi
    fi

    echo "### $x — creating $tarball"
    pushd "$x" > /dev/null
    cat ../rm.txt | xargs rm -f
    popd > /dev/null
    tar czf "$tarball" "$x"
done

rm -f rm.txt
