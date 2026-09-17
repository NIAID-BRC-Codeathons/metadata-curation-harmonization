"""
Generates reproducible embedding-ready text descriptions for ontology terms.

Reads term entries from the OBO files in data/ontology/, restricts each file to
its own terms (OBO files import terms from other ontologies, e.g. uberon_ext.obo
imports BFO), and concatenates each term's name and definition into a single
lowercase sentence suitable for embedding.

Usage:
    python scripts/generate_term_descriptions.py
    python scripts/generate_term_descriptions.py --ontology uberon_ext mondo
    python scripts/generate_term_descriptions.py -o data/ontology/term_descriptions.parquet

Author: Parker Hicks
Date: 2026-09-16
"""

import sys
from argparse import ArgumentParser
from pathlib import Path

import polars as pl

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ontology_rag.ontology import OboEntry, Ontology  # noqa: E402

ONTOLOGY_DIR = Path(__file__).parent.parent / "dataset" / "ontology"
DEFAULT_OUTFILE = ONTOLOGY_DIR / "term_descriptions.parquet"

# OBO files import terms from other ontologies, so each file's own terms must be
# picked out by ID prefix rather than taking entries in file order.
ID_PREFIXES: dict[str, str] = {
    "uberon_ext": "UBERON",
    "mondo": "MONDO",
    "envo": "ENVO",
}


def build_description(entry: OboEntry, include_def: bool = False) -> str:
    """Concatenate a term's name and definition into a single lowercase sentence."""
    if include_def:
        text = f"{entry.name}: {entry.definition}" if entry.definition else entry.name
    else:
        text = entry.name
    return text.strip().lower()


def build_term_descriptions(obo_file: Path) -> pl.DataFrame:
    """Build a DataFrame of term IDs paired with name + definition descriptions."""
    onto = Ontology.from_obo(obo_file)
    prefix = ID_PREFIXES[obo_file.name.split(".")[0]]
    entries = [entry for entry in onto.entries if entry.id_prefix == prefix]

    return pl.DataFrame(
        {
            "id": [entry.id for entry in entries],
            "description": [build_description(entry) for entry in entries],
        }
    )


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument(
        "--ontology",
        nargs="+",
        default=list(ID_PREFIXES),
        choices=list(ID_PREFIXES),
        help="Which OBO file(s) in data/ontology/ to pull term descriptions from.",
    )
    parser.add_argument(
        "--use-def",
        action="store_true",
        help="Apply flag to include term definitions in the description."
        " Otherwise, only names are included.",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        type=Path,
        default=DEFAULT_OUTFILE,
        help="Path to write the combined term descriptions parquet file.",
    )
    args = parser.parse_args()

    dfs = [
        build_term_descriptions(ONTOLOGY_DIR / f"{name}.obo.gz")
        for name in args.ontology
    ]
    df = pl.concat(dfs)

    args.outfile.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(args.outfile)

    print(f"Wrote {len(df)} term descriptions to {args.outfile}")


if __name__ == "__main__":
    main()
