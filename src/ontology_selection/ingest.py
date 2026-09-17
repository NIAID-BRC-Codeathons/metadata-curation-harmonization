"""
Data ingestion: Convert raw BV-BRC/NCBI JSON to normalized RecordInput format.

Three input shapes are accepted, detected per line:

- **Combined** (production dataset from ``combined.jsonl``): top-level sibling
  keys ``genome`` (dict), ``biosample`` (list), ``bioproject`` (list), and
  optionally ``bv_brc`` (list).  This is the default / primary format.
- **Flat engine rows** (engine.md section 6.1): already carrying ``record_id``
  and the searchable fields at the top level.
- **Legacy** (``sample.input.jsonl``): single top-level ``genomes`` dict whose
  ``assemblyInfo.biosample`` contains the biosample data.
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

def _is_combined_format(raw: Dict[str, Any]) -> bool:
    """True when the record uses the combined.jsonl layout."""
    return 'genome' in raw and 'biosample' in raw and isinstance(raw['biosample'], list)


def _is_engine_row(raw: Dict[str, Any]) -> bool:
    """True when the record is a pre-flattened engine row."""
    return 'record_id' in raw


def _is_legacy_format(raw: Dict[str, Any]) -> bool:
    """True when the record uses the sample.input.jsonl layout."""
    return 'genomes' in raw


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

def normalize_combined_record(raw: Dict[str, Any], config: Dict[str, Any]) -> RecordInput:
    """Normalize a record in the ``combined.jsonl`` layout.

    Top-level keys: ``genome`` (dict), ``biosample`` (list),
    ``bioproject`` (list), ``bv_brc`` (optional list).
    """
    bs = _first_or_none(raw.get('biosample')) or {}
    bp = _first_or_none(raw.get('bioproject')) or {}
    bvb = _first_or_none(raw.get('bv_brc')) or {}

    # Flatten biosample attributes array -> dict
    attrs = flatten_biosample_attributes(bs.get('attributes', []))

    # Record ID: prefer bv_brc genome_id, then biosample accession,
    # then genome accession.
    record_id = _coalesce(
        bvb.get('genome_id'),
        bs.get('accession'),
        extract_nested_field(raw, 'genome.accession'),
    ) or f"unknown_{hash(json.dumps(raw, sort_keys=True, default=str)) % 1_000_000}"

    return RecordInput(
        record_id=record_id,

        # Isolation / anatomy
        isolation_source=_coalesce(
            bvb.get('isolation_source'),
            attrs.get('isolation_source'),
            attrs.get('isolation source'),
        ),
        body_sample_site=_coalesce(
            bvb.get('body_sample_site'),
            attrs.get('body_sample_site'),
            attrs.get('body sample site'),
        ),
        tissue=_coalesce(attrs.get('tissue')),

        # Host
        host=_coalesce(
            bs.get('host'),
            bvb.get('host_name'),
            attrs.get('host'),
        ),

        # Disease / clinical
        disease=_coalesce(
            bvb.get('disease'),
            attrs.get('disease'),
        ),
        note=_coalesce(attrs.get('note')),

        # Environment
        environment=_coalesce(
            attrs.get('env_medium'),
            attrs.get('environment'),
        ),

        # Geography / date
        geo_loc_name=_coalesce(
            bs.get('geoLocName'),
            bvb.get('geographic_location'),
            attrs.get('geo_loc_name'),
        ),
        collection_date=_coalesce(
            bs.get('collectionDate'),
            bvb.get('collection_date'),
            attrs.get('collection_date'),
        ),

        # Strain
        strain=_coalesce(
            bs.get('strain'),
            bvb.get('strain'),
            attrs.get('strain'),
        ),

        # Descriptions / titles
        biosample_description=_coalesce(
            extract_nested_field(bs, 'description.title'),
        ),
        bioproject_title=_coalesce(bp.get('title')),

        # Extras — lightweight references only
        extras={
            'biosample_accession': bs.get('accession'),
            'bioproject_accession': extract_nested_field(
                raw, 'genome.assemblyInfo.bioprojectAccession'),
            'genome_accession': extract_nested_field(raw, 'genome.accession'),
            'bvbrc_genome_id': bvb.get('genome_id'),
            'biosample_attrs': attrs,
        },
    )


def normalize_engine_row(raw_data: Dict[str, Any], config: Dict[str, Any]) -> RecordInput:
    """
    Convert a flat engine row (engine.md section 6.1) to RecordInput.

    These rows are already normalized - record_id and the searchable fields sit at
    the top level - but the richer metadata stays in extras.attributes. Fields the
    Plan agent can use are lifted out of there when the top level leaves them unset.
    """
    extras = raw_data.get('extras') or {}
    attrs = extras.get('attributes') or {}

    def pick(*keys):
        """First non-empty attribute value among keys."""
        for key in keys:
            value = attrs.get(key)
            if value:
                return value
        return None

    return RecordInput(
        record_id=raw_data['record_id'],
        isolation_source=raw_data.get('isolation_source') or pick('isolation_source'),
        body_sample_site=raw_data.get('body_sample_site') or pick('body_sample_site'),
        host=raw_data.get('host') or pick('host'),
        note=raw_data.get('note') or pick('note'),
        disease=raw_data.get('disease') or pick('host_disease', 'disease'),
        tissue=raw_data.get('tissue') or pick('tissue'),
        environment=raw_data.get('environment') or pick('env_medium', 'environment'),
        strain=raw_data.get('strain') or pick('strain'),
        geo_loc_name=raw_data.get('geo_loc_name') or pick('geo_loc_name'),
        collection_date=raw_data.get('collection_date') or pick('collection_date'),
        biosample_description=extras.get('title'),
        comments=list(raw_data.get('comments') or []),
        extras=extras,
    )


def normalize_bvbrc_record(raw_data: Dict[str, Any], config: Dict[str, Any]) -> RecordInput:
    """Normalize a record in the legacy ``sample.input.jsonl`` layout.

    Single top-level key ``genomes`` containing
    ``assemblyInfo.biosample``.
    """
    biosample = extract_nested_field(raw_data, "genomes.assemblyInfo.biosample") or {}

    record_id = _coalesce(
        biosample.get('accession'),
        extract_nested_field(raw_data, "genomes.assemblyInfo.bioprojectAccession"),
    ) or f"unknown_{hash(json.dumps(raw_data, sort_keys=True, default=str)) % 1_000_000}"

    attrs = flatten_biosample_attributes(biosample.get('attributes', []))

    return RecordInput(
        record_id=record_id,
        isolation_source=_coalesce(
            attrs.get('isolation_source'), attrs.get('isolation source')),
        body_sample_site=_coalesce(
            attrs.get('body_sample_site'), attrs.get('body sample site')),
        note=_coalesce(attrs.get('note')),
        strain=_coalesce(attrs.get('strain')),
        disease=_coalesce(attrs.get('disease')),
        tissue=_coalesce(attrs.get('tissue')),
        environment=_coalesce(
            attrs.get('env_medium'), attrs.get('environment')),
        collection_date=_coalesce(
            attrs.get('collection_date'), biosample.get('collectionDate')),
        host=_coalesce(biosample.get('host')),
        geo_loc_name=_coalesce(
            biosample.get('geoLocName'), attrs.get('geo_loc_name')),
        biosample_description=_coalesce(
            extract_nested_field(raw_data, "genomes.assemblyInfo.biosample.description.title")),
        bioproject_title=_coalesce(
            extract_nested_field(raw_data, "genomes.assemblyInfo.bioprojectLineage.0.bioprojects.0.title")),
        extras={
            'biosample_attrs': attrs,
            'bioproject_accession': extract_nested_field(
                raw_data, "genomes.assemblyInfo.bioprojectAccession"),
        },
    )


def normalize_record(raw_data: Dict[str, Any], config: Dict[str, Any]) -> RecordInput:
    """Auto-detect format and normalize a single raw JSON record.

    Detection order (first match wins):

    1. **Combined** — ``genome`` + ``biosample`` (list) at top level.
       This is the production format and the default path.
    2. **Engine row** — ``record_id`` at top level (pre-flattened).
    3. **Legacy** — ``genomes`` at top level (old sample.input.jsonl).

    Raises ``ValueError`` if the layout is unrecognised.
    """
    if _is_combined_format(raw_data):
        return normalize_combined_record(raw_data, config)
    if _is_engine_row(raw_data):
        return normalize_engine_row(raw_data, config)
    if _is_legacy_format(raw_data):
        return normalize_bvbrc_record(raw_data, config)
    raise ValueError(
        f"Unrecognised record layout. Top-level keys: {list(raw_data.keys())}"
    )


# ---------------------------------------------------------------------------
# File ingestion
# ---------------------------------------------------------------------------

def ingest_jsonl(
    filepath: str,
    config: Dict[str, Any],
    limit: int = None,
    ids: Optional[Set[str]] = None,
) -> List[RecordInput]:
    """Load and normalize records from a JSONL (or ``.jsonl.gz``) file.

    Args:
        filepath: Path to input JSONL / JSONL.GZ file.
        config:   Configuration dictionary.
        limit:    Maximum number of *emitted* records (after ID filtering).
        ids:      Optional set of record IDs to keep.  If given, records
                  whose ``record_id`` is not in this set are skipped.

    Returns:
        List of normalised RecordInput objects.
    """
    records: List[RecordInput] = []

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
                record = normalize_record(raw_data, config)

                if ids is not None and record.record_id not in ids:
                    continue

                records.append(record)

            except Exception as e:
                logger.error(f"Failed to parse line {lineno}: {e}")
                continue

    logger.info(f"Ingested {len(records)} records from {filepath}")
    return records


def create_test_records() -> List[RecordInput]:
    """
    Create a few handcrafted test records for development.
    
    Returns:
        List of test RecordInput objects
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
            isolation_source="Homo sapiens",  # This should trigger "looks_like_host" flag
            host="Homo sapiens"
        ),
    ]
