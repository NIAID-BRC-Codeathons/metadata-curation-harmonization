"""
Data ingestion: Convert raw BV-BRC/NCBI JSON to normalized RecordInput format.

Four input shapes are accepted, detected per line:

- **Combined V2** (``combined.v2.jsonl``): detected by ``genome.genome_id``.
  Top-level keys: ``genome``, ``biosamples`` (plural, list), ``bioprojects``
  (plural, list), ``bvbrc`` (dict, no underscore).  Biosample attributes live
  in ``attribute_recs`` with key ``attribute_name``.
- **Combined V1** (``combined.jsonl``): detected by ``genome`` + ``biosample``
  (singular, list).  Top-level keys: ``genome``, ``biosample`` (list),
  ``bioproject`` (list), ``bv_brc`` (optional list).
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

# String labels returned by detect_format() and logged at ingest time.
FORMAT_COMBINED_V2 = "combined_v2"
FORMAT_COMBINED_V1 = "combined_v1"
FORMAT_ENGINE_ROW = "engine_row"
FORMAT_LEGACY = "legacy"


def detect_format(raw: Dict[str, Any]) -> Optional[str]:
    """Return a format label for the record, or None if unrecognised.

    Detection order (first match wins):

    1. **Combined V2** — ``genome.genome_id`` exists (V2 added this field).
    2. **Combined V1** — ``genome`` + ``biosample`` (singular, list).
    3. **Engine row** — ``record_id`` at top level.
    4. **Legacy** — ``genomes`` at top level.
    """
    genome = raw.get('genome')
    if isinstance(genome, dict) and 'genome_id' in genome:
        return FORMAT_COMBINED_V2
    if isinstance(genome, dict) and (
        isinstance(raw.get('biosample'), list)
        or isinstance(raw.get('biosamples'), list)
    ):
        return FORMAT_COMBINED_V1
    if 'record_id' in raw:
        return FORMAT_ENGINE_ROW
    if 'genomes' in raw:
        return FORMAT_LEGACY
    return None


# ---------------------------------------------------------------------------
# Normalizers
# ---------------------------------------------------------------------------

def _get_bvbrc(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the BV-BRC record, handling both V1 (list) and V2 (dict)."""
    # V2: bvbrc is a dict
    bvb = raw.get('bvbrc')
    if isinstance(bvb, dict):
        return bvb
    # V1: bv_brc is a list
    return _first_or_none(raw.get('bv_brc')) or {}


def _get_biosample(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the first biosample, handling both V1 and V2 key names."""
    # V2: biosamples (plural)
    bs = _first_or_none(raw.get('biosamples'))
    if bs is not None:
        return bs
    # V1: biosample (singular)
    return _first_or_none(raw.get('biosample')) or {}


def _get_bioproject(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the first bioproject, handling both V1 and V2 key names."""
    bp = _first_or_none(raw.get('bioprojects'))
    if bp is not None:
        return bp
    return _first_or_none(raw.get('bioproject')) or {}


def _get_biosample_attrs(bs: Dict[str, Any]) -> Dict[str, str]:
    """Flatten biosample attributes from either V1 or V2 format.

    V1 stores ``[{name, value}]`` in ``attributes``.
    V2 stores ``[{attribute_name, value, ...}]`` in ``attribute_recs``
    (``attributes`` is just a list of name strings).
    """
    # V2: attribute_recs has the actual name/value pairs
    attr_recs = bs.get('attribute_recs')
    if isinstance(attr_recs, list) and attr_recs:
        return flatten_biosample_attributes(attr_recs)
    # V1: attributes is [{name, value}]
    return flatten_biosample_attributes(bs.get('attributes', []))


def normalize_combined_record(raw: Dict[str, Any], config: Dict[str, Any]) -> RecordInput:
    """Normalize a record in combined V1 or V2 layout.

    V1 keys: ``genome``, ``biosample`` (list), ``bioproject`` (list),
    ``bv_brc`` (optional list).

    V2 keys: ``genome``, ``biosamples`` (list), ``bioprojects`` (list),
    ``bvbrc`` (dict).  Biosample attributes in ``attribute_recs``.
    """
    bs = _get_biosample(raw)
    bp = _get_bioproject(raw)
    bvb = _get_bvbrc(raw)
    attrs = _get_biosample_attrs(bs)

    # Record ID: prefer genome.currentAccession (the stable assembly
    # accession), then genome.accession, then biosample accession.
    record_id = _coalesce(
        extract_nested_field(raw, 'genome.currentAccession'),
        extract_nested_field(raw, 'genome.accession'),
        bs.get('accession'),
        bvb.get('genome_id'),
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
            attrs.get('host_disease'),
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
            bs.get('title'),
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

    1. **Combined V2** — ``genome.genome_id`` exists.
    2. **Combined V1** — ``genome`` + ``biosample``/``biosamples`` (list).
    3. **Engine row** — ``record_id`` at top level (pre-flattened).
    4. **Legacy** — ``genomes`` at top level (old sample.input.jsonl).

    Raises ``ValueError`` if the layout is unrecognised.
    """
    fmt = detect_format(raw_data)
    if fmt in (FORMAT_COMBINED_V2, FORMAT_COMBINED_V1):
        return normalize_combined_record(raw_data, config)
    if fmt == FORMAT_ENGINE_ROW:
        return normalize_engine_row(raw_data, config)
    if fmt == FORMAT_LEGACY:
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
    detected_format: Optional[str] = None

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
                if detected_format is None:
                    detected_format = detect_format(raw_data) or "unknown"
                    logger.info(
                        "Input format detected: %s (from %s)",
                        detected_format, filepath,
                    )

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
