"""
Read/write precomputed ontology-query embeddings passed between the embedding
generation stage (scripts/embed_query_records.py, run in a torch/transformers
environment) and the FAISS retrieval stage (ontology_rag.rag, run in a faiss
environment).

Deliberately free of faiss and torch imports so it can be shared by both stages.

Author: Parker Hicks
Date: 2026-09-17
"""

from pathlib import Path

import numpy as np
import numpy.typing as npt
import polars as pl

RECORD_ID_COLUMN = "record_id"
ONTOLOGY_COLUMN = "ontology"
QUERY_TEXT_COLUMN = "query_text"


def write_query_embeddings(
    record_ids: list[str],
    ontologies: list[str],
    query_texts: list[str],
    vectors: npt.NDArray[np.float32],
    outfile: str | Path,
) -> None:
    """Write one row per (record_id, ontology, query_text) query alongside its
    embedding vector.

    Arguments:
        record_ids (list[str]):
            Record ID for each query, one per row.
        ontologies (list[str]):
            Ontology searched by each query, one per row.
        query_texts (list[str]):
            Query text embedded for each row.
        vectors (npt.NDArray[np.float32]):
            Row-oriented 2D array of embedding vectors, one row per query.
        outfile (str | Path):
            Destination parquet file.
    """
    df = pl.DataFrame(
        {
            RECORD_ID_COLUMN: record_ids,
            ONTOLOGY_COLUMN: ontologies,
            QUERY_TEXT_COLUMN: query_texts,
            **{f"d{i}": vectors[:, i] for i in range(vectors.shape[1])},
        }
    )
    df.write_parquet(outfile)


def read_query_embeddings(
    file: str | Path,
) -> dict[tuple[str, str, str], npt.NDArray[np.float32]]:
    """Load precomputed query embeddings keyed by (record_id, ontology, query_text).

    Raises:
        ValueError: If the file is missing the record_id, ontology, or query_text
            column.
    """
    df = pl.read_parquet(file)
    for column in (RECORD_ID_COLUMN, ONTOLOGY_COLUMN, QUERY_TEXT_COLUMN):
        if column not in df.columns:
            raise ValueError(
                f"Query embeddings file {file} is missing the '{column}' column."
            )

    vectors = np.ascontiguousarray(
        df.drop(RECORD_ID_COLUMN, ONTOLOGY_COLUMN, QUERY_TEXT_COLUMN).to_numpy(),
        dtype=np.float32,
    )
    return {
        (record_id, ontology, query_text): vectors[i]
        for i, (record_id, ontology, query_text) in enumerate(
            zip(
                df[RECORD_ID_COLUMN].to_list(),
                df[ONTOLOGY_COLUMN].to_list(),
                df[QUERY_TEXT_COLUMN].to_list(),
            )
        )
    }
