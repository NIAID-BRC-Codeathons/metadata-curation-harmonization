#!/usr/bin/env python3
"""
This script generate embedding from unique words in the corpus.

This script works with the gpu-linux-cuda118 environment using CUDA/11.8 on a100 GPUs.
If using CPUs, reinstall torch that is compatable with CPU-only machines.

Authors: Parker Hicks, Hao Yuan
Date: 2024-11-28

Last updated: 2026-02-16 by Parker Hicks
"""

import sys
from argparse import ArgumentParser
from pathlib import Path

import numpy as np
import numpy.typing as npt
import polars as pl

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from ontology_rag.embeddings import SUPPORTED_LLMS
from ontology_rag.embeddings import (
    EmbeddingGenerator as BaseEmbeddingGenerator,
)  # noqa: E402


class EmbeddingGenerator(BaseEmbeddingGenerator):
    """EmbeddingGenerator with support for document-level parquet I/O and ids.

    Adds an optional ``ids`` array (e.g. ontology term IDs) that is carried
    alongside document-level data and written as a leading "id" column when
    saving to parquet.
    """

    def __init__(self, data, level, model: str = "biomedbert", ids=None):
        self.ids: npt.NDArray | None = np.array(ids) if ids is not None else None

        if self.ids is not None and len(self.ids) != len(data):
            raise ValueError(
                f"Got {len(self.ids)} ids for {len(data)} documents. "
                "ids must align 1:1 with data."
            )

        super().__init__(data, level, model)

    def to_parquet(self, file: str | Path):
        """Save the embedding matrix as a parquet file.

        Word-level embeddings are saved wide, with each word as its own column
        and embedding dimensions as rows.

        Document-level embeddings are saved long, with one row per document,
        one column per embedding dimension, and (if ids were provided) a
        leading "id" column labeling each row's term ID.
        """
        if self.level == "word":
            super().to_parquet(file)
        elif self.level == "document":
            columns = [f"dim_{i}" for i in range(self.embedding_size)]
            df = pl.DataFrame(self.embeddings, schema=columns)
            if self.ids is not None:
                df = df.insert_column(0, pl.Series("id", self.ids))
            df.write_parquet(file)


def load_documents(
    input_path: Path, id_column: str, text_column: str
) -> tuple[list[str], npt.NDArray | None]:
    """Load documents (and their ids, if available) from a text or parquet file.

    A .parquet file is expected to have a text column (default "description")
    and, optionally, an id column (default "id") labeling each row's term.
    Any other file is treated as plain text with one document per line.
    """
    if input_path.suffix == ".parquet":
        df = pl.read_parquet(input_path)
        if text_column not in df.columns:
            raise ValueError(
                f"Column '{text_column}' not found in {input_path}. "
                f"Available columns: {df.columns}"
            )
        documents = df[text_column].to_list()
        ids = df[id_column].to_numpy() if id_column in df.columns else None
        return documents, ids

    with open(input_path, "r", encoding="utf-8") as f:
        documents = [line.strip() for line in f.readlines()]
    return documents, None


def main():
    parser = ArgumentParser()
    parser.add_argument(
        "-i",
        "--input",
        help="Path to a free-text document-per-line file, or a .parquet file "
        "with a text column (see --text-column) and, optionally, an id "
        "column (see --id-column).",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "-l",
        "--level",
        help="Word or doument level. If word, will extract unique words to embed.",
        type=str,
        choices=["word", "document"],
        default="word",
    )
    parser.add_argument(
        "-m",
        "--model",
        help="LLM to use for embedding.",
        choices=list(SUPPORTED_LLMS.keys()),
        default="sapbert",
    )
    parser.add_argument(
        "--id-column",
        help="Column in a .parquet --input holding each document's ID, "
        "e.g. an ontology term ID. Ignored for text-file input.",
        type=str,
        default="id",
    )
    parser.add_argument(
        "--text-column",
        help="Column in a .parquet --input holding each document's text.",
        type=str,
        default="description",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        help="Path to embeddings.parquet",
        type=Path,
        default="emebddings.parquet",
    )
    args = parser.parse_args()

    documents, ids = load_documents(args.input, args.id_column, args.text_column)

    generator = EmbeddingGenerator(
        documents, level=args.level, model=args.model, ids=ids
    )
    generator.generate_embeddings()
    generator.to_parquet(args.outfile)


if __name__ == "__main__":
    main()
