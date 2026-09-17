#!/usr/bin/env python3
"""
Builds ontology_rag vector database parquet files from document-level term embeddings.

Reads each <ontology>.parquet in dataset/ontology/document_level_embeddings/ (an "id"
column plus one column per embedding dimension), looks up each term's canonical name
from the corresponding OBO file via ontology_rag.ontology.Ontology, and writes an
"id"/"name" + embedding-dimension parquet per ontology into dataset/ontology/vector_db/,
matching the schema ontology_rag.rag.OntologyVectorStore expects.

Usage:
    python scripts/build_vector_db.py
    python scripts/build_vector_db.py --ontology uberon mondo
    python scripts/build_vector_db.py -o dataset/ontology/vector_db

Author: Parker Hicks
Date: 2026-09-17
"""

import sys
from argparse import ArgumentParser
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ontology_rag.ontology import Ontology  # noqa: E402

ONTOLOGY_DIR = Path(__file__).parent.parent / "dataset" / "ontology"
DEFAULT_EMBEDDINGS_DIR = ONTOLOGY_DIR / "document_level_embeddings"
DEFAULT_OUTDIR = ONTOLOGY_DIR / "vector_db"

# document_level_embeddings files are named after the ontology, not the OBO file that
# defines its terms (e.g. uberon.parquet vs. uberon_ext.obo.gz), and OBO files import
# terms from other ontologies (e.g. uberon_ext.obo.gz imports BFO), so each ontology's
# own terms must be picked out of its OBO file by ID prefix.
OBO_FILES: dict[str, str] = {
    "envo": "envo.obo.gz",
    "uberon": "uberon_ext.obo.gz",
    "mondo": "mondo.obo.gz",
}
ID_PREFIXES: dict[str, str] = {
    "envo": "ENVO",
    "uberon": "UBERON",
    "mondo": "MONDO",
}


def build_vector_db(embeddings_file: Path, obo_file: Path, id_prefix: str) -> pl.DataFrame:
    """Attach term names to a document-level embeddings table by ID."""
    embeddings = pl.read_parquet(embeddings_file)

    onto = Ontology.from_obo(obo_file)
    names = {
        entry.id: entry.name for entry in onto.entries if entry.id_prefix == id_prefix
    }

    missing = sorted(set(embeddings["id"]) - names.keys())
    if missing:
        raise ValueError(
            f"{obo_file} has no name for {len(missing)} term ID(s) present in "
            f"{embeddings_file}, e.g. {missing[:5]}."
        )

    id_map = pl.DataFrame({"id": list(names.keys()), "name": list(names.values())})
    dim_columns = [column for column in embeddings.columns if column != "id"]

    return embeddings.join(id_map, on="id", how="left").select(["id", "name", *dim_columns])


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument(
        "--ontology",
        nargs="+",
        default=list(OBO_FILES),
        choices=list(OBO_FILES),
        help="Which ontologies to build vector databases for.",
    )
    parser.add_argument(
        "--embeddings-dir",
        type=Path,
        default=DEFAULT_EMBEDDINGS_DIR,
        help="Directory containing one <ontology>.parquet document-level embeddings "
        "table per ontology.",
    )
    parser.add_argument(
        "--ontology-dir",
        type=Path,
        default=ONTOLOGY_DIR,
        help="Directory containing the OBO files (<name>.obo.gz) to pull term names from.",
    )
    parser.add_argument(
        "-o",
        "--outdir",
        type=Path,
        default=DEFAULT_OUTDIR,
        help="Directory to write <ontology>.parquet vector databases into.",
    )
    args = parser.parse_args()

    args.outdir.mkdir(parents=True, exist_ok=True)

    for ontology in args.ontology:
        embeddings_file = args.embeddings_dir / f"{ontology}.parquet"
        obo_file = args.ontology_dir / OBO_FILES[ontology]

        df = build_vector_db(embeddings_file, obo_file, ID_PREFIXES[ontology])

        outfile = args.outdir / f"{ontology}.parquet"
        df.write_parquet(outfile)
        print(f"Wrote {len(df)} terms x {len(df.columns) - 2} dims to {outfile}")


if __name__ == "__main__":
    main()
