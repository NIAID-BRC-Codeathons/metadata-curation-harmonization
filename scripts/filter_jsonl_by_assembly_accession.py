#!/usr/bin/env python3
"""
Filter a combined JSONL(.gz) file to only records whose assembly accession
appears in a curated accession list.

The accession list can be either:
  - A TSV file  (e.g. curated_assembly_accessions.tsv) — specify the 1-based
    column with ``--column`` (default 2).
  - A JSON file (e.g. curated_metadata_expanded.json) — a column-oriented dict
    whose ``--key`` entry (default "Assembly Accessions") maps index strings to
    accession values.

The file type is detected from the extension (.json vs .tsv/.txt/anything else).

Matches against both ``genome.accession`` (typically GCF_) and
``genome.pairedAccession`` (typically GCA_) so that either prefix convention
hits.

Usage:
    # from the JSON gold-standard file (default)
    python scripts/filter_jsonl_by_assembly_accession.py

    # from a TSV instead
    python scripts/filter_jsonl_by_assembly_accession.py \
        -a dataset/curated_assembly_accessions.tsv --column 2

    # explicit arguments
    python scripts/filter_jsonl_by_assembly_accession.py \
        -a dataset/curated_metadata_expanded.json \
        --key "Assembly Accessions" \
        -i dataset/combined.jsonl \
        -o dataset/curated_combined.jsonl
"""

import argparse
import gzip
import json
import sys


def load_accessions_tsv(path: str, column: int) -> set[str]:
    """Load accession IDs from a TSV file.

    Args:
        path:   Path to the TSV file (first row is a header).
        column: 1-based column number containing the accession.

    Returns:
        Set of accession strings.
    """
    idx = column - 1
    ids: set[str] = set()
    with open(path) as f:
        next(f)  # skip header
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) > idx and parts[idx]:
                ids.add(parts[idx])
    return ids


def load_accessions_json(path: str, key: str) -> set[str]:
    """Load accession IDs from a column-oriented JSON file.

    The file is a dict of ``{column_name: {index: value, ...}, ...}``.
    We extract the values under *key*.

    Args:
        path: Path to the JSON file.
        key:  Key whose values are the accessions.

    Returns:
        Set of accession strings.
    """
    with open(path) as f:
        data = json.load(f)
    column = data.get(key)
    if column is None:
        available = ", ".join(sorted(data.keys()))
        raise KeyError(f"Key {key!r} not found in {path}. Available keys: {available}")
    return {str(v) for v in column.values() if v}


def load_accessions(path: str, column: int, key: str) -> set[str]:
    """Auto-detect file type and load accessions."""
    if path.endswith(".json"):
        return load_accessions_json(path, key)
    return load_accessions_tsv(path, column)


def filter_jsonl(input_path: str, output_path: str, ids: set[str]) -> tuple[int, int]:
    """Stream *input_path* and write matching lines to *output_path*.

    A record matches when ``genome.accession`` or
    ``genome.pairedAccession`` is in *ids*.

    Returns:
        (total_lines, kept_lines)
    """
    opener = gzip.open if input_path.endswith(".gz") else open
    total = 0
    kept = 0
    with opener(input_path, "rt", encoding="utf-8") as fin, \
         open(output_path, "w") as fout:
        for line in fin:
            total += 1
            d = json.loads(line)
            g = d.get("genome", {})
            if g.get("accession") in ids or g.get("pairedAccession") in ids:
                fout.write(line)
                kept += 1
    return total, kept


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Filter combined JSONL to curated assembly accessions.",
    )
    parser.add_argument(
        "-a", "--accessions",
        default="dataset/curated_metadata_expanded.json",
        help="TSV or JSON file listing curated accessions (default: %(default)s)",
    )
    parser.add_argument(
        "-c", "--column",
        type=int,
        default=2,
        help="1-based column when using a TSV file (default: %(default)s)",
    )
    parser.add_argument(
        "-k", "--key",
        default="Assembly Accessions",
        help="JSON dict key when using a JSON file (default: %(default)s)",
    )
    parser.add_argument(
        "-i", "--input",
        default="dataset/combined.jsonl",
        help="Input JSONL or JSONL.GZ file (default: %(default)s)",
    )
    parser.add_argument(
        "-o", "--output",
        default="dataset/curated_combined.jsonl",
        help="Output JSONL file (default: %(default)s)",
    )
    args = parser.parse_args()

    ids = load_accessions(args.accessions, args.column, args.key)
    print(f"Loaded {len(ids)} accessions from {args.accessions}")

    total, kept = filter_jsonl(args.input, args.output, ids)
    print(f"Kept {kept} of {total} records -> {args.output}")

    missing = len(ids) - kept
    if missing:
        print(f"Note: {missing} accessions from the list had no matching record", file=sys.stderr)


if __name__ == "__main__":
    main()
