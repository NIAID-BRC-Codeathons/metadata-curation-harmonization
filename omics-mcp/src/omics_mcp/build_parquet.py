#!/usr/bin/env python3
"""Convert the local JSONL extracts to Parquet for fast querying."""

import argparse
from pathlib import Path

import duckdb

from omics_mcp.config import SOURCES, data_dir, parquet_dir


def sql_literal(path):
    return str(path).replace("'", "''")


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory holding the JSONL extracts (default: $OMICS_DATA_DIR)",
    )

    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Directory for Parquet output (default: $OMICS_PARQUET_DIR)",
    )

    parser.add_argument(
        "--only",
        nargs="*",
        choices=sorted(SOURCES),
        help="Convert only these tables",
    )

    args = parser.parse_args()

    source_dir = args.data_dir or data_dir()
    out_dir = args.out_dir or parquet_dir() or Path("parquet")
    out_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    converted = 0

    for name, filename in SOURCES.items():
        if args.only and name not in args.only:
            continue

        source = source_dir / filename

        if not source.exists():
            print(f"Skipping {name}: {source} not found", flush=True)
            continue

        target = out_dir / f"{name}.parquet"

        print(f"Converting {name}: {source} -> {target}", flush=True)

        con.execute(
            f"""
            COPY (
                SELECT * FROM read_json_auto(
                    '{sql_literal(source)}',
                    format = 'newline_delimited',
                    union_by_name = true,
                    sample_size = -1
                )
            ) TO '{sql_literal(target)}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )

        rows = con.execute(
            f"SELECT count(*) FROM read_parquet('{sql_literal(target)}')"
        ).fetchone()[0]

        print(
            f"  {rows:,} rows, {target.stat().st_size / 1e6:,.1f} MB",
            flush=True,
        )

        converted += 1

    if not converted:
        print(
            f"\nNo sources found in {source_dir}. "
            f"Set --data-dir or $OMICS_DATA_DIR.",
            flush=True,
        )


if __name__ == "__main__":
    main()
