#!/usr/bin/env python3
"""MCP server exposing local genome/BV-BRC extracts alongside OmicIDX via DuckDB."""

import json

import duckdb
from mcp.server.mcpserver import MCPServer

from omics_mcp.config import (
    LOCAL_TABLES,
    MAX_ROWS,
    OMICIDX_BASE,
    OMICIDX_TABLES,
    table_source,
)

mcp = MCPServer("omics")


def sql_literal(path):
    return str(path).replace("'", "''")


def connect():
    """Open a DuckDB connection with every dataset registered as a view."""

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")

    for name in LOCAL_TABLES:
        location, _ = table_source(name)

        con.execute(
            f"CREATE VIEW {name} AS "
            f"SELECT * FROM read_parquet('{sql_literal(location)}')"
        )

    for name in OMICIDX_TABLES:
        con.execute(
            f"CREATE VIEW omicidx_{name} AS SELECT * FROM read_parquet"
            f"('{OMICIDX_BASE}/{name}.parquet')"
        )

    return con


def rows_to_json(cursor):
    columns = [d[0] for d in cursor.description]
    rows = [dict(zip(columns, row)) for row in cursor.fetchall()]

    return json.dumps(rows, indent=2, default=str)


def records(cursor):
    return json.loads(cursor.fetchdf().to_json(orient="records"))


@mcp.tool()
def list_datasets() -> str:
    """List every queryable dataset, whether it is local or from OmicIDX."""

    datasets = []

    for name in LOCAL_TABLES:
        location, is_local = table_source(name)
        datasets.append(
            {
                "name": name,
                "source": "local file" if is_local else "hosted",
                "available": True,
                "path": location,
            }
        )

    for name in OMICIDX_TABLES:
        datasets.append(
            {
                "name": f"omicidx_{name}",
                "source": "omicidx",
                "available": True,
                "path": f"{OMICIDX_BASE}/{name}.parquet",
            }
        )

    return json.dumps(datasets, indent=2)


@mcp.tool()
def describe_dataset(name: str) -> str:
    """Return the column names and types for one dataset."""

    if name not in LOCAL_TABLES and name not in {
        f"omicidx_{table}" for table in OMICIDX_TABLES
    }:
        raise ValueError(f"Unknown dataset: {name}")

    con = connect()

    try:
        return rows_to_json(con.execute(f"DESCRIBE SELECT * FROM {name}"))
    finally:
        con.close()


@mcp.tool()
def query(sql: str, limit: int = 100) -> str:
    """Run a read-only SQL query across the local and OmicIDX datasets.

    Local tables: genome, bvbrc. OmicIDX tables are prefixed with omicidx_.
    Remote tables are read over HTTPS, so filter and project aggressively.
    """

    statement = sql.strip().rstrip(";")

    if not statement.lower().startswith(("select", "with", "describe")):
        raise ValueError(
            "Only SELECT, WITH, and DESCRIBE statements are allowed"
        )

    if ";" in statement:
        raise ValueError("Only a single statement is allowed")

    limit = max(1, min(limit, MAX_ROWS))

    con = connect()

    try:
        return rows_to_json(
            con.execute(f"SELECT * FROM ({statement}) LIMIT {limit}")
        )
    finally:
        con.close()


@mcp.tool()
def get_genome(accession: str, include_sra: bool = True) -> str:
    """Assemble everything known about one genome assembly accession.

    Accepts a GCA_ or GCF_ accession and gathers the local assembly report,
    the matching BV-BRC record, and BioProject/BioSample/SRA from OmicIDX.
    """

    con = connect()

    try:
        found = records(
            con.execute(
                """
                SELECT * FROM genome
                WHERE accession = ?
                   OR currentAccession = ?
                   OR pairedAccession = ?
                LIMIT 1
                """,
                [accession, accession, accession],
            )
        )

        if not found:
            return json.dumps({"error": f"No genome found for {accession}"})

        record = found[0]
        info = record.get("assemblyInfo") or {}
        biosample_accession = (info.get("biosample") or {}).get("accession")
        bioproject_accession = info.get("bioprojectAccession")
        wgs_prefix = (record.get("wgsInfo") or {}).get("wgsProjectAccession")

        result = {"genome": record, "matched_by": {}}

        # Same ordered fallbacks as combine.py.
        fallbacks = [
            (
                "assembly_accession",
                "SELECT * FROM bvbrc WHERE assembly_accession IN (?, ?, ?)",
                [
                    record.get("accession"),
                    record.get("currentAccession"),
                    record.get("pairedAccession"),
                ],
            ),
            (
                "biosample_accession",
                "SELECT * FROM bvbrc WHERE biosample_accession = ?",
                [biosample_accession],
            ),
            (
                "genbank_accessions",
                "SELECT * FROM bvbrc WHERE genbank_accessions LIKE ?",
                [f"{wgs_prefix[:4]}%" if wgs_prefix else None],
            ),
        ]

        for label, sql, params in fallbacks:
            if any(param is None for param in params):
                continue

            matches = records(con.execute(sql, params))

            if matches:
                result["bvbrc"] = matches
                result["matched_by"]["bvbrc"] = label
                break

        if bioproject_accession:
            matches = records(
                con.execute(
                    "SELECT * FROM omicidx_bioprojects WHERE accession = ?",
                    [bioproject_accession],
                )
            )

            if matches:
                result["bioproject"] = matches[0]

        if biosample_accession:
            matches = records(
                con.execute(
                    "SELECT * FROM omicidx_biosamples WHERE accession = ?",
                    [biosample_accession],
                )
            )

            if matches:
                result["biosample"] = matches[0]

                if include_sra and matches[0].get("sra_sample"):
                    experiments = records(
                        con.execute(
                            """
                            SELECT accession, title, library_strategy, platform,
                                   instrument_model, study_accession
                            FROM omicidx_sra_experiments
                            WHERE sample_accession = ?
                            LIMIT 50
                            """,
                            [matches[0]["sra_sample"]],
                        )
                    )

                    if experiments:
                        result["sra_experiments"] = experiments

        return json.dumps(result, indent=2, default=str)
    finally:
        con.close()


def main():
    mcp.run()


if __name__ == "__main__":
    main()
