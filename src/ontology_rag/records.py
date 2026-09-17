"""
Metadata record / ontology-mapping data model for the ontology RAG pipeline.

Input is the JSONL "Plan" output produced upstream (see
src/ontology_selection/engine.md, section 6.2): one JSON object per line, each
holding a record_id plus a list of ontology mappings. Each mapping carries the
source fields that fed it (for provenance) and the query_texts to search
against that ontology's vector database.

Deliberately free of faiss and torch imports so it can be shared by both the
embedding-generation stage (scripts/embed_query_records.py, run in a
torch/transformers environment) and the FAISS retrieval stage
(ontology_rag.rag, run in a faiss environment) regardless of which heavy
dependency is installed in a given environment.

Author: Parker Hicks
Date: 2026-09-17
"""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class SrcField:
    """One source metadata field that contributed to an ontology mapping.

    Attributes:
        path (str):
            Dotted path of the field in the source record (e.g.
            "bvbrc.isolation_source").
        name (str):
            Short field name (e.g. "isolation_source").
        value (str):
            Free-text value of the field.
    """

    path: str
    name: str
    value: str


@dataclass(slots=True)
class OntologyMapping:
    """One ontology-linking request for one metadata record.

    Attributes:
        ontology (str):
            Name of the ontology to search (e.g. "UBERON", "MONDO"). Used to look
            up the associated vector database parquet file.
        src_fields (list[SrcField]):
            Source metadata fields that fed this mapping, kept for provenance.
        query_texts (list[str]):
            Free-text strings to embed and search against the ontology's vector
            database. Not necessarily one per src_field.
    """

    ontology: str
    src_fields: list[SrcField]
    query_texts: list[str]


@dataclass(slots=True)
class MetadataRecord:
    """A metadata record to link against one or more ontologies.

    Attributes:
        record_id (str):
            Identifier of the source metadata record (e.g. a BioSample accession).
        mappings (list[OntologyMapping]):
            One ontology-linking mapping per ontology to search on behalf of this
            record.
        flags (list[str]):
            Flags raised upstream (by the Plan step) for this record.
    """

    record_id: str
    mappings: list[OntologyMapping]
    flags: list[str] = field(default_factory=list)


def load_records(file: str | Path) -> list[MetadataRecord]:
    """Load metadata records to link from a Plan-output JSONL file.

    Expects one JSON object per line, each with a "record_id" string, a
    "mappings" array of
    {"ontology": ..., "src_fields": [{"path": ..., "name": ..., "value": ...}],
    "query_texts": [...]} objects, and an optional "flags" array, e.g.:

        {"record_id": "TEST001", "mappings": [{"ontology": "UBERON",
        "src_fields": [{"path": "bvbrc.isolation_source",
        "name": "isolation_source", "value": "blood"}],
        "query_texts": ["blood"]}], "flags": []}
    """
    records = []
    with open(file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            records.append(
                MetadataRecord(
                    record_id=entry["record_id"],
                    mappings=[
                        OntologyMapping(
                            ontology=m["ontology"],
                            src_fields=[
                                SrcField(
                                    path=sf["path"],
                                    name=sf["name"],
                                    value=sf["value"],
                                )
                                for sf in m["src_fields"]
                            ],
                            query_texts=list(m["query_texts"]),
                        )
                        for m in entry["mappings"]
                    ],
                    flags=list(entry.get("flags", [])),
                )
            )

    return records
