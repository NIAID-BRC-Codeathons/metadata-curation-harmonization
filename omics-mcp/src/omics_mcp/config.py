"""Dataset locations and limits, all overridable by environment variable."""

import os
from pathlib import Path

# Remote OmicIDX Parquet, refreshed daily and read over HTTPS.
OMICIDX_BASE = os.environ.get(
    "OMICS_OMICIDX_BASE",
    "https://data.omicidx.cancerdatasci.org/latest",
)

# Hosted copies of the local extracts, so no build step is required.
PARQUET_BASE = os.environ.get(
    "OMICS_PARQUET_BASE",
    "https://ftp.ncbi.nlm.nih.gov/pub/datasets/.argonne/parquet",
)

# Local extracts that OmicIDX does not carry: JSONL source -> table name.
SOURCES = {
    "genome": "genome.jsonl",
    "bvbrc": "bvbrc_staph.jsonl",
}

LOCAL_TABLES = {name: f"{name}.parquet" for name in SOURCES}

OMICIDX_TABLES = [
    "sra_runs",
    "sra_experiments",
    "sra_samples",
    "sra_studies",
    "biosamples",
    "bioprojects",
    "geo_samples",
    "geo_series",
    "geo_platforms",
]

MAX_ROWS = int(os.environ.get("OMICS_MAX_ROWS", "500"))


def parquet_dir():
    """Local Parquet directory, or None when the hosted copies should be used."""

    value = os.environ.get("OMICS_PARQUET_DIR")

    return Path(value).expanduser().resolve() if value else None


def table_source(name):
    """Return (location, is_local) for one local-extract table."""

    filename = LOCAL_TABLES[name]
    directory = parquet_dir()

    if directory:
        path = directory / filename

        if path.exists():
            return str(path), True

    return f"{PARQUET_BASE}/{filename}", False


def omicidx_source(name):
    """Return a local OmicIDX file when cached, otherwise its remote URL."""

    filename = f"{name}.parquet"
    directory = os.environ.get("OMICS_OMICIDX_DIR")

    if directory:
        path = Path(directory).expanduser().resolve() / filename

        if path.exists():
            return str(path), True

    return f"{OMICIDX_BASE}/{filename}", False


def data_dir():
    """Directory holding the source JSONL extracts."""

    return Path(
        os.environ.get("OMICS_DATA_DIR", ".")
    ).expanduser().resolve()
