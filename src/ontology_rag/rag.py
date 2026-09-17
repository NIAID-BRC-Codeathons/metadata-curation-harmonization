"""
RAG (retrieval augmented generation) system to retrieve the top k
ontology term entries associated with a particular free-text query.

This module only depends on faiss, not torch/transformers: query text must be
embedded ahead of time with scripts/embed_query_records.py (run in a separate
torch/transformers environment) and passed in via the `embeddings` argument to
OntologyRag.query, or read from disk with ontology_rag.query_embeddings.

Author: Parker Hicks
Date: 2026-09-16
"""

import json
from argparse import ArgumentParser
from dataclasses import asdict, dataclass, field
from pathlib import Path

import faiss
import numpy as np
import numpy.typing as npt
import polars as pl

from ontology_rag.query_embeddings import read_query_embeddings
from ontology_rag.records import MetadataRecord, OntologyMapping, SrcField, load_records
from ontology_rag.vector_db import Faiss, FaissDevice, FaissMetric

__all__ = [
    "MetadataRecord",
    "OntologyMapping",
    "SrcField",
    "load_records",
    "TermMatch",
    "QueryTextResult",
    "OntologyBucket",
    "RecordResult",
    "OntologyVectorStore",
    "OntologyRag",
    "LOW_SCORE_THRESHOLD",
]

ID_COLUMN = "id"
NAME_COLUMN = "name"
LOW_SCORE_THRESHOLD = 0.7
DEFAULT_VECTOR_DB_DIR = (
    Path(__file__).resolve().parents[1] / "dataset/ontology/vector_db"
)


@dataclass(slots=True)
class TermMatch:
    """A single candidate ontology term returned for a query."""

    term_id: str
    term_name: str
    label: str
    score: float
    rank: int


@dataclass(slots=True)
class QueryTextResult:
    """Top k ontology term matches for a single query_text."""

    text: str
    matches: list[TermMatch] = field(default_factory=list)


@dataclass(slots=True)
class OntologyBucket:
    """Retrieval results for one ontology mapping (bucket), echoing its Plan
    mapping's src_fields/query_texts for provenance (see
    src/ontology_selection/engine.md, section 6.3)."""

    ontology: str
    src_fields: list[SrcField] = field(default_factory=list)
    query_texts: list[QueryTextResult] = field(default_factory=list)


@dataclass(slots=True)
class RecordResult:
    """Retrieval results for every ontology mapping (bucket) on one record."""

    record_id: str
    buckets: list[OntologyBucket] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


class OntologyVectorStore:
    """Loads and caches per-ontology FAISS indexes built from parquet embedding tables.

    Each parquet file holds one row per ontology term: an `id_column` holding the
    term ID, a `name_column` holding the term's canonical name, and one column per
    embedding dimension.
    """

    def __init__(
        self,
        vector_db_dir: str | Path,
        id_column: str = ID_COLUMN,
        name_column: str = NAME_COLUMN,
        metric: str | FaissMetric = FaissMetric.COSINE,
        device: str | FaissDevice = FaissDevice.CPU,
    ):
        self.vector_db_dir = Path(vector_db_dir)
        self.id_column = id_column
        self.name_column = name_column
        self.faiss = Faiss(metric=metric, device=device)
        self._indices: dict[str, faiss.Index] = {}
        self._ids: dict[str, np.ndarray] = {}
        self._names: dict[str, np.ndarray] = {}

    def load(self, ontology: str) -> tuple[faiss.Index, np.ndarray, np.ndarray]:
        """Load (and cache) the FAISS index, term IDs, and term names for an ontology."""
        if ontology in self._indices:
            return self._indices[ontology], self._ids[ontology], self._names[ontology]

        path = self.vector_db_dir / f"{ontology.lower()}.parquet"
        if not path.exists():
            raise FileNotFoundError(
                f"No vector database found for ontology '{ontology}' at {path}."
            )

        df = pl.read_parquet(path)
        for column in (self.id_column, self.name_column):
            if column not in df.columns:
                raise ValueError(
                    f"Vector database at {path} is missing the '{column}' column."
                )

        ids = df[self.id_column].to_numpy()
        names = df[self.name_column].to_numpy()
        vectors = np.ascontiguousarray(
            df.drop(self.id_column, self.name_column).to_numpy(), dtype=np.float32
        )
        index = self.faiss.build(vectors)

        self._indices[ontology] = index
        self._ids[ontology] = ids
        self._names[ontology] = names

        return index, ids, names


class OntologyRag:
    """Retrieves the top k ontology terms most similar to free-text query_texts."""

    def __init__(
        self,
        vector_db_dir: str | Path,
        metric: str | FaissMetric = FaissMetric.COSINE,
        device: str | FaissDevice = FaissDevice.CPU,
        id_column: str = ID_COLUMN,
        name_column: str = NAME_COLUMN,
    ):
        self.store = OntologyVectorStore(
            vector_db_dir,
            id_column=id_column,
            name_column=name_column,
            metric=metric,
            device=device,
        )

    def query(
        self,
        records: list[MetadataRecord],
        embeddings: dict[tuple[str, str, str], npt.NDArray[np.float32]],
        k: int = 10,
        low_score_threshold: float = LOW_SCORE_THRESHOLD,
    ) -> list[RecordResult]:
        """Run a batch of ontology-linking queries and return the top k matches per
        query_text, grouped into one bucket per ontology mapping per record.

        Queries across all records/mappings are grouped by ontology so each
        ontology's index is loaded once and searched in a single batched FAISS
        call, regardless of how many records/mappings/query_texts reference it.

        Arguments:
            records (list[MetadataRecord]):
                Metadata records to link, each carrying one ontology mapping
                (bucket) per ontology to search, with one or more query_texts
                per mapping.
            embeddings (dict[tuple[str, str, str], npt.NDArray[np.float32]]):
                Precomputed query embeddings keyed by (record_id, ontology,
                query_text), as produced by scripts/embed_query_records.py and
                loaded with ontology_rag.query_embeddings.read_query_embeddings.
            k (int):
                Number of top ontology terms to retrieve per query_text.
            low_score_threshold (float):
                Top-match scores below this value raise a flag on the record.

        Returns:
            (list[RecordResult]): One result per input record, holding one
                bucket per ontology mapping (with its src_fields provenance and
                per-query_text top k matches) plus any flags raised.
        """
        results = {
            record.record_id: RecordResult(record_id=record.record_id, flags=list(record.flags))
            for record in records
        }

        by_ontology: dict[str, list[tuple[str, QueryTextResult, str]]] = {}
        for record in records:
            result = results[record.record_id]
            for mapping in record.mappings:
                bucket = OntologyBucket(
                    ontology=mapping.ontology, src_fields=list(mapping.src_fields)
                )
                result.buckets.append(bucket)
                for query_text in mapping.query_texts:
                    query_text_result = QueryTextResult(text=query_text)
                    bucket.query_texts.append(query_text_result)
                    by_ontology.setdefault(mapping.ontology, []).append(
                        (record.record_id, query_text_result, query_text)
                    )

        for ontology, items in by_ontology.items():
            index, ids, names = self.store.load(ontology)

            vectors = []
            for record_id, _, query_text in items:
                key = (record_id, ontology, query_text)
                if key not in embeddings:
                    raise ValueError(
                        f"No precomputed embedding found for record '{record_id}' "
                        f"ontology '{ontology}' query_text '{query_text}'. Run "
                        "scripts/embed_query_records.py on the input records first."
                    )
                vectors.append(embeddings[key])
            query_vectors = np.stack(vectors).astype(np.float32)

            if query_vectors.shape[1] != index.d:
                raise ValueError(
                    f"Embedding dimension {query_vectors.shape[1]} does not match "
                    f"the {ontology} vector database dimension {index.d}."
                )

            similarities, indices = self.store.faiss.query(index, query_vectors, k=k)
            if k == 1:
                similarities = similarities.reshape(len(items), 1)
                indices = indices.reshape(len(items), 1)

            for (record_id, query_text_result, query_text), sim_row, idx_row in zip(
                items, similarities, indices
            ):
                matches = [
                    TermMatch(
                        term_id=str(ids[i]),
                        term_name=str(names[i]),
                        label=str(names[i]),
                        score=float(s),
                        rank=rank,
                    )
                    for rank, (s, i) in enumerate(zip(sim_row, idx_row))
                    if i != -1
                ]

                query_text_result.matches = matches
                results[record_id].flags.extend(
                    _flag_matches(ontology, query_text, matches, low_score_threshold)
                )

        return list(results.values())


def _flag_matches(
    ontology: str, query_text: str, matches: list[TermMatch], low_score_threshold: float
) -> list[str]:
    """Raise a warning flag when a query_text returned nothing or only weak matches."""
    if not matches:
        return [f"{ontology}: no matches found for query '{query_text}'"]

    top_score = matches[0].score
    if top_score < low_score_threshold:
        return [
            f"{ontology}: top match score {top_score:.3f} below threshold "
            f"{low_score_threshold} for query '{query_text}'"
        ]

    return []


def main() -> None:
    parser = ArgumentParser()
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        required=True,
        help="Path to input Plan-output JSONL file of metadata records to link.",
    )
    parser.add_argument(
        "-e",
        "--embeddings",
        type=Path,
        required=True,
        help="Path to precomputed query embeddings parquet, as produced by "
        "scripts/embed_query_records.py for the same input records.",
    )
    parser.add_argument(
        "-v",
        "--vector-db-dir",
        type=Path,
        required=True,
        help="Directory containing one <ontology>.parquet vector database per ontology.",
    )
    parser.add_argument(
        "-k",
        "--top-k",
        type=int,
        default=10,
        help="Number of top ontology terms to retrieve per query_text.",
    )
    parser.add_argument(
        "--metric",
        choices=[metric.value for metric in FaissMetric],
        default=FaissMetric.COSINE.value,
        help="FAISS similarity metric.",
    )
    parser.add_argument(
        "--device",
        choices=[device.value for device in FaissDevice],
        default=FaissDevice.CPU.value,
        help="Compute device for the FAISS index.",
    )
    parser.add_argument(
        "--low-score-threshold",
        type=float,
        default=LOW_SCORE_THRESHOLD,
        help="Top-match scores below this value raise a flag on the record.",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        type=Path,
        default="rag_results.jsonl",
        help="Path to write the top k matches per record as JSONL (one record per line).",
    )
    args = parser.parse_args()

    records = load_records(args.input)
    embeddings = read_query_embeddings(args.embeddings)
    rag = OntologyRag(
        vector_db_dir=args.vector_db_dir,
        metric=args.metric,
        device=args.device,
    )
    results = rag.query(
        records, embeddings, k=args.top_k, low_score_threshold=args.low_score_threshold
    )

    with open(args.outfile, "w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(asdict(result)) + "\n")


if __name__ == "__main__":
    main()
