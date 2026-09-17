#!/usr/bin/env python3
"""Export a full genome/BV-BRC/OmicIDX linkage file as compressed JSONL."""

import argparse
import gzip
import json
import re
from pathlib import Path

import duckdb

from omics_mcp.config import omicidx_source, table_source


def sql_literal(value):
    return str(value).replace("'", "''")


def add_unique(target, key, value):
    if value is not None and key not in target:
        target[key] = value


def load_json_map(con, table, key):
    rows = con.execute(
        f"SELECT {key}, to_json(list(t)) FROM {table} t GROUP BY {key}"
    ).fetchall()

    return {row[0]: json.loads(row[1]) for row in rows}


def combine_records(maps, keys):
    result = []
    seen = set()

    for key in keys:
        if key is None:
            continue

        for record in maps.get(key, []):
            identity = json.dumps(record, sort_keys=True, default=str)

            if identity not in seen:
                seen.add(identity)
                result.append(record)

    return result


def master_wgs_accession(genome):
    url = (genome.get("wgsInfo") or {}).get("masterWgsUrl")

    if not url:
        return None

    match = re.search(r"/nuccore/([^.?]+)", url)

    return match.group(1) if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="Output .jsonl.gz file")
    parser.add_argument(
        "--include-sra-runs",
        action="store_true",
        help="Include SRA runs in addition to SRA experiments",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of genome records to skip",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of genome records to process",
    )
    parser.add_argument(
        "--omit-unmatched-bvbrc",
        action="store_true",
        help="Do not emit BV-BRC records unmatched to this genome slice",
    )
    args = parser.parse_args()

    if args.offset < 0:
        parser.error("--offset must be non-negative")

    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")

    print(
        f"Genome slice: offset={args.offset:,}, "
        f"limit={args.limit if args.limit is not None else 'all'}",
        flush=True,
    )

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")

    genome_path, _ = table_source("genome")
    bvbrc_path, _ = table_source("bvbrc")
    con.execute(
        f"CREATE VIEW genome AS SELECT * FROM read_parquet('{sql_literal(genome_path)}')"
    )
    con.execute(
        f"CREATE VIEW bvbrc AS SELECT * FROM read_parquet('{sql_literal(bvbrc_path)}')"
    )
    con.execute(
        f"""
        CREATE TEMP TABLE selected_genome AS
        SELECT * FROM genome
        ORDER BY accession
        LIMIT {args.limit if args.limit is not None else 'ALL'}
        OFFSET {args.offset}
        """
    )

    for name in ("biosamples", "bioprojects", "sra_experiments"):
        location, _ = omicidx_source(name)
        con.execute(
            f"CREATE VIEW {name} AS SELECT * FROM "
            f"read_parquet('{sql_literal(location)}')"
        )

    if args.include_sra_runs:
        location, _ = omicidx_source("sra_runs")
        con.execute(
            f"CREATE VIEW sra_runs AS SELECT * FROM "
            f"read_parquet('{sql_literal(location)}')"
        )

    # Stage only reachable remote records before generating the export.
    con.execute("""
        CREATE TEMP TABLE relevant_biosample_keys AS
        SELECT DISTINCT accession FROM (
            SELECT assemblyInfo.biosample.accession AS accession FROM selected_genome
            UNION ALL
            SELECT biosample_accession AS accession FROM bvbrc
        ) WHERE accession IS NOT NULL
    """)
    con.execute("""
        CREATE TEMP TABLE relevant_bioproject_keys AS
        SELECT DISTINCT accession FROM (
            SELECT assemblyInfo.bioprojectAccession AS accession FROM selected_genome
            UNION ALL
            SELECT bioproject_accession AS accession FROM bvbrc
        ) WHERE accession IS NOT NULL
    """)
    con.execute("""
        CREATE TEMP TABLE linked_biosamples AS
        SELECT s.* FROM biosamples s
        JOIN relevant_biosample_keys k ON k.accession = s.accession
    """)
    con.execute("""
        CREATE TEMP TABLE linked_bioprojects AS
        SELECT p.* FROM bioprojects p
        JOIN relevant_bioproject_keys k ON k.accession = p.accession
    """)
    con.execute("""
        CREATE TEMP TABLE relevant_sra_samples AS
        SELECT DISTINCT sra_sample FROM linked_biosamples
        WHERE sra_sample IS NOT NULL
    """)
    con.execute("""
        CREATE TEMP TABLE linked_sra_experiments AS
        SELECT e.* FROM sra_experiments e
        JOIN relevant_sra_samples k ON k.sra_sample = e.sample_accession
    """)

    if args.include_sra_runs:
        con.execute("""
            CREATE TEMP TABLE relevant_sra_runs AS
            SELECT DISTINCT sra_accession AS accession FROM bvbrc
            WHERE sra_accession IS NOT NULL
        """)
        con.execute("""
            CREATE TEMP TABLE linked_sra_runs AS
            SELECT r.* FROM sra_runs r
            JOIN relevant_sra_runs k ON k.accession = r.accession
        """)

    bioproject_map = load_json_map(con, "linked_bioprojects", "accession")
    biosample_map = load_json_map(con, "linked_biosamples", "accession")
    sra_experiment_map = load_json_map(
        con, "linked_sra_experiments", "sample_accession"
    )
    sra_run_map = {}

    if args.include_sra_runs:
        sra_run_map = load_json_map(con, "linked_sra_runs", "accession")

    match_sql = f"""
        WITH genome_records AS (
            SELECT row_number() OVER () AS genome_id, g.*
            FROM selected_genome g
        ),
        bvbrc_records AS (
            SELECT row_number() OVER () AS bvbrc_id, b.*
            FROM bvbrc b
        ),
        matches AS (
            SELECT g.genome_id, b.bvbrc_id, 'assembly_accession' AS matched_by
            FROM genome_records g JOIN bvbrc_records b
              ON b.assembly_accession IN
                 (g.accession, g.currentAccession, g.pairedAccession)
            UNION
            SELECT g.genome_id, b.bvbrc_id, 'biosample_accession'
            FROM genome_records g JOIN bvbrc_records b
              ON b.biosample_accession = g.assemblyInfo.biosample.accession
            UNION
            SELECT g.genome_id, b.bvbrc_id, 'bioproject_accession'
            FROM genome_records g JOIN bvbrc_records b
              ON b.bioproject_accession = g.assemblyInfo.bioprojectAccession
            UNION
            SELECT g.genome_id, b.bvbrc_id, 'genbank_accession'
            FROM genome_records g JOIN bvbrc_records b
              ON g.wgsInfo.masterWgsUrl IS NOT NULL
             AND b.genbank_accessions IS NOT NULL
             AND list_contains(
                 string_split(b.genbank_accessions, ','),
                 regexp_extract(
                     g.wgsInfo.masterWgsUrl,
                     '/nuccore/([^.?]+)',
                     1
                 )
             )
        ),
        best_matches AS (
            SELECT genome_id, bvbrc_id,
                   min(matched_by) AS matched_by
            FROM matches
            GROUP BY genome_id, bvbrc_id
        )
        SELECT g.genome_id, b.bvbrc_id,
                             to_json(g) AS genome_json,
                             to_json(b) AS bvbrc_json,
                             best_matches.matched_by,
                             g.assemblyInfo.bioprojectAccession AS genome_bioproject,
                             g.assemblyInfo.biosample.accession AS genome_biosample,
                             b.bioproject_accession,
                             b.biosample_accession,
                             b.sra_accession
        FROM genome_records g
                {"FULL OUTER JOIN" if not args.omit_unmatched_bvbrc else "LEFT JOIN"} best_matches
                    ON best_matches.genome_id = g.genome_id
                {"FULL OUTER JOIN" if not args.omit_unmatched_bvbrc else "LEFT JOIN"}
                    bvbrc_records b ON b.bvbrc_id = best_matches.bvbrc_id
    """

    args.output.parent.mkdir(parents=True, exist_ok=True)
    matched_bvbrc = set()
    genome_count = 0
    bvbrc_count = 0

    with gzip.open(args.output, "wt") as out:
        cursor = con.execute(match_sql)

        while rows := cursor.fetchmany(500):
            for (
                genome_id,
                bvbrc_id,
                genome_json,
                bvbrc_json,
                matched_by,
                genome_bioproject,
                genome_biosample,
                bioproject_accession,
                biosample_accession,
                sra_accession,
            ) in rows:
                biosample_keys = [genome_biosample, biosample_accession]
                bioproject_keys = [genome_bioproject, bioproject_accession]
                biosamples = combine_records(biosample_map, biosample_keys)
                bioprojects = combine_records(bioproject_map, bioproject_keys)
                sra_samples = [
                    sample.get("sra_sample")
                    for sample in biosamples
                ]
                sra_experiments = combine_records(
                    sra_experiment_map, sra_samples
                )
                sra_runs = combine_records(sra_run_map, [sra_accession])

                if genome_id is not None:
                    genome_count += 1
                if bvbrc_id is not None:
                    bvbrc_count += 1
                    matched_bvbrc.add(bvbrc_id)

                record = {
                    "genome": json.loads(genome_json) if genome_json else None,
                    "bvbrc": json.loads(bvbrc_json) if bvbrc_json else None,
                    "matched_by": matched_by,
                    "bioprojects": bioprojects,
                    "biosamples": biosamples,
                    "sra_experiments": sra_experiments,
                    "sra_runs": sra_runs,
                }
                out.write(json.dumps(record, separators=(",", ":")) + "\n")

    print(f"Wrote {args.output}", flush=True)
    print(f"Genome rows represented: {genome_count:,}", flush=True)
    print(f"BV-BRC rows represented: {bvbrc_count:,}", flush=True)
    print(f"BV-BRC rows matched to a genome: {len(matched_bvbrc):,}", flush=True)


if __name__ == "__main__":
    main()
