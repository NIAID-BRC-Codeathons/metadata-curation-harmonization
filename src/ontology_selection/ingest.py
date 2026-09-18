"""
Data ingestion: Convert raw combined.v2.jsonl records to normalised RecordInput.

Only the **V2 combined** format is supported.  V2 is detected by the presence
of ``genome.genome_id`` in the first record.  Other formats raise ``ValueError``.

V2 top-level keys::

    genome      (dict)   — assembly metadata; ``currentAccession`` is the record ID.
    biosamples  (list)   — biosample records; attributes in ``attribute_recs``.
    bioprojects (list)   — bioproject records.
    bvbrc       (dict)   — BV-BRC enriched fields (isolation_source, host_name, …).
    sra_experiments, sra_runs, matched_by — ignored.
"""

from typing import Any, Dict, List, Optional, Set
import gzip
import logging
import json

from .models import RecordInput
from .utils import (
    extract_nested_field,
    flatten_biosample_attributes,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_or_none(lst: Any) -> Optional[Dict[str, Any]]:
    """Return first element of a list, or None."""
    if isinstance(lst, list) and lst:
        return lst[0]
    return None


def _coalesce(*values: Any) -> Optional[str]:
    """Return the first non-empty string value, or None.

    Lists are joined with ``'; '`` so that multi-valued BV-BRC fields
    (e.g. ``disease: ['Bacterial Interference']``) become a single string.
    """
    for v in values:
        if v is None:
            continue
        if isinstance(v, list):
            joined = "; ".join(str(x) for x in v if x)
            if joined:
                return joined
            continue
        s = str(v).strip()
        if s:
            return s
    return None


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

FORMAT_COMBINED_V2 = "combined_v2"


def detect_format(raw: Dict[str, Any]) -> Optional[str]:
    """Return ``'combined_v2'`` if the record has ``genome.genome_id``, else None."""
    genome = raw.get('genome')
    if isinstance(genome, dict) and 'genome_id' in genome:
        return FORMAT_COMBINED_V2
    return None


# ---------------------------------------------------------------------------
# V2 normaliser
# ---------------------------------------------------------------------------

def normalize_record(raw: Dict[str, Any], config: Dict[str, Any]) -> RecordInput:
    """Normalise a single V2 combined.jsonl record to :class:`RecordInput`.

    Raises ``ValueError`` if the record is not V2 format.
    """
    if detect_format(raw) != FORMAT_COMBINED_V2:
        raise ValueError(
            f"Expected combined_v2 format (genome.genome_id present). "
            f"Top-level keys: {list(raw.keys())}"
        )

    bs = _first_or_none(raw.get('biosamples')) or {}
    bp = _first_or_none(raw.get('bioprojects')) or {}
    bvb = raw.get('bvbrc') or {}
    if not isinstance(bvb, dict):
        bvb = {}

    # Flatten attribute_recs (keyed by harmonized_name via utils)
    attrs = flatten_biosample_attributes(bs.get('attribute_recs', []))

    # Record ID: genome.currentAccession (stable assembly accession)
    record_id = _coalesce(
        extract_nested_field(raw, 'genome.currentAccession'),
        extract_nested_field(raw, 'genome.accession'),
        bs.get('accession'),
        bvb.get('genome_id'),
    ) or f"unknown_{hash(json.dumps(raw, sort_keys=True, default=str)) % 1_000_000}"

    # Build comments from bvbrc.comments (can be a list of strings)
    comments_raw = bvb.get('comments') or []
    if isinstance(comments_raw, str):
        comments_raw = [comments_raw]
    comments = [str(c) for c in comments_raw if c]

    return RecordInput(
        record_id=record_id,

        # Isolation / anatomy — check bvbrc, then biosample attrs, then
        # biosample direct fields (V2 sometimes has isolationSource).
        isolation_source=_coalesce(
            bvb.get('isolation_source'),
            attrs.get('isolation_source'),
            bs.get('isolationSource'),
        ),
        body_sample_site=_coalesce(
            bvb.get('body_sample_site'),
            attrs.get('body_sample_site'),
        ),
        tissue=_coalesce(
            attrs.get('tissue'),
            attrs.get('tissue_type'),
        ),

        # Host
        host=_coalesce(
            attrs.get('host'),
            bvb.get('host_name'),
            bs.get('host'),
        ),

        # Disease / clinical
        disease=_coalesce(
            attrs.get('host_disease'),
            bvb.get('disease'),
            attrs.get('disease'),
        ),
        note=_coalesce(
            attrs.get('note'),
            attrs.get('description'),
            attrs.get('sample_type'),
        ),

        # Environment
        environment=_coalesce(
            attrs.get('env_medium'),
            attrs.get('env_broad_scale'),
            attrs.get('env_local_scale'),
            attrs.get('environment'),
        ),

        # Geography / date
        geo_loc_name=_coalesce(
            attrs.get('geo_loc_name'),
            bvb.get('geographic_location'),
            bs.get('geoLocName'),
        ),
        collection_date=_coalesce(
            attrs.get('collection_date'),
            bvb.get('collection_date'),
            bs.get('collectionDate'),
        ),

        # Strain
        strain=_coalesce(
            attrs.get('strain'),
            bvb.get('strain'),
            bs.get('strain'),
        ),

        # Descriptions / titles
        biosample_description=_coalesce(
            bs.get('title'),
            extract_nested_field(bs, 'description.title'),
        ),
        bioproject_title=_coalesce(bp.get('title')),

        # Comments (from bvbrc)
        comments=comments,

        # Extras — the full flattened attribute dict so the Plan agent
        # can see *every* biosample attribute, including ones we didn't
        # map to a named RecordInput field.
        extras={
            'biosample_accession': bs.get('accession'),
            'bioproject_accession': bp.get('accession'),
            'genome_accession': extract_nested_field(raw, 'genome.accession'),
            'bvbrc_genome_id': bvb.get('genome_id'),
            'biosample_attrs': attrs,
        },
    )


# Keep old name as alias.
normalize_bvbrc_record = normalize_record
normalize_combined_record = normalize_record


# ---------------------------------------------------------------------------
# File ingestion
# ---------------------------------------------------------------------------

def ingest_jsonl(
    filepath: str,
    config: Dict[str, Any],
    limit: int = None,
    ids: Optional[Set[str]] = None,
) -> List[RecordInput]:
    """Load and normalise records from a JSONL (or ``.jsonl.gz``) file.

    Only V2 combined format is accepted.

    Args:
        filepath: Path to input JSONL / JSONL.GZ file.
        config:   Configuration dictionary.
        limit:    Maximum number of *emitted* records (after ID filtering).
        ids:      Optional set of record IDs to keep.

    Returns:
        List of normalised RecordInput objects.
    """
    records: List[RecordInput] = []
    format_logged = False

    opener = gzip.open if filepath.endswith('.gz') else open
    with opener(filepath, 'rt', encoding='utf-8') as f:  # type: ignore[call-overload]
        for lineno, line in enumerate(f, start=1):
            if limit is not None and limit > 0 and len(records) >= limit:
                break

            line = line.strip()
            if not line:
                continue

            try:
                raw_data = json.loads(line)

                # Log the detected format once, from the first record.
                if not format_logged:
                    fmt = detect_format(raw_data)
                    if fmt != FORMAT_COMBINED_V2:
                        raise ValueError(
                            f"Expected combined_v2 format but detected "
                            f"'{fmt or 'unknown'}' in {filepath}. "
                            f"First record top-level keys: {list(raw_data.keys())}"
                        )
                    logger.info(
                        "Input format detected: %s (from %s)",
                        FORMAT_COMBINED_V2, filepath,
                    )
                    format_logged = True

                record = normalize_record(raw_data, config)

                if ids is not None and record.record_id not in ids:
                    continue

                records.append(record)

            except Exception as e:
                logger.error(f"Failed to parse line {lineno}: {e}")
                if not format_logged:
                    # Format error on line 1 is fatal — don't silently skip.
                    raise
                continue

    logger.info(f"Ingested {len(records)} records from {filepath}")
    return records


def create_test_records() -> List[RecordInput]:
    """
    Create a few handcrafted test records for development.
    """
    return [
        RecordInput(
            record_id="TEST001",
            isolation_source="blood",
            host="Homo sapiens",
            note="patient with bloodstream infection",
            geo_loc_name="Brazil: Rio de Janeiro",
            collection_date="2020-05-15"
        ),
        RecordInput(
            record_id="TEST002",
            isolation_source="wound infection",
            note="patient with sepsis",
            host="Homo sapiens"
        ),
        RecordInput(
            record_id="TEST003",
            isolation_source="hospital wastewater",
            geo_loc_name="United States: Chicago",
            environment="wastewater"
        ),
        RecordInput(
            record_id="TEST004",
            isolation_source="nasal swab",
            body_sample_site="nasal cavity",
            host="Homo sapiens",
            note="asymptomatic carrier"
        ),
        RecordInput(
            record_id="TEST005",
            isolation_source="Homo sapiens",
            host="Homo sapiens"
        ),
    ]
