"""
Data ingestion: Convert raw BV-BRC/NCBI JSON to normalized RecordInput format.

Two input shapes are accepted, detected per line:

- Nested BV-BRC/NCBI records, flattened by normalize_bvbrc_record.
- Flat engine rows (engine.md section 6.1), already carrying record_id and the
  searchable fields, handled by normalize_engine_row.

Handles the complex nested structure and applies field selection rules.
"""

from typing import Dict, Any, List
import logging
import json

from .models import RecordInput
from .utils import (
    extract_nested_field,
    flatten_biosample_attributes,
    extract_relevant_fields
)

logger = logging.getLogger(__name__)


def normalize_bvbrc_record(raw_data: Dict[str, Any], config: Dict[str, Any]) -> RecordInput:
    """
    Convert raw BV-BRC JSON to normalized RecordInput.
    
    The raw BV-BRC structure is complex and nested:
    {
      "genomes": {
        "assemblyInfo": {
          "biosample": {
            "accession": "...",
            "host": "...",
            "attributes": [{name, value}, ...]
          },
          "bioprojectAccession": "..."
        }
      }
    }
    
    We flatten this to RecordInput with standardized field names.
    
    Args:
        raw_data: Raw BV-BRC JSON record
        config: Configuration dictionary
        
    Returns:
        RecordInput object
    """
    # Extract biosample data (primary source)
    biosample = extract_nested_field(raw_data, "genomes.assemblyInfo.biosample") or {}
    
    # Get record ID (prefer biosample accession)
    record_id = biosample.get('accession')
    if not record_id:
        # Fallback to other identifiers
        record_id = extract_nested_field(raw_data, "genomes.assemblyInfo.bioprojectAccession")
    if not record_id:
        record_id = f"unknown_{hash(str(raw_data)) % 1000000}"
    
    # Flatten biosample attributes array
    attrs = flatten_biosample_attributes(biosample.get('attributes', []))
    
    # Extract standard fields
    record = RecordInput(
        record_id=record_id,
        
        # From biosample attributes
        isolation_source=attrs.get('isolation_source') or attrs.get('isolation source'),
        body_sample_site=attrs.get('body_sample_site') or attrs.get('body sample site'),
        note=attrs.get('note'),
        strain=attrs.get('strain'),
        disease=attrs.get('disease'),
        tissue=attrs.get('tissue'),
        environment=attrs.get('env_medium') or attrs.get('environment'),
        collection_date=attrs.get('collection_date') or biosample.get('collectionDate'),
        
        # From biosample direct fields
        host=biosample.get('host'),
        geo_loc_name=biosample.get('geoLocName') or attrs.get('geo_loc_name'),
        
        # From description/title
        biosample_description=extract_nested_field(raw_data, "genomes.assemblyInfo.biosample.description.title"),
        bioproject_title=extract_nested_field(raw_data, "genomes.assemblyInfo.bioprojectLineage.0.bioprojects.0.title"),
        
        # Store all attributes for reference
        extras={
            'biosample_attrs': attrs,
            'raw_biosample': biosample,
            'bioproject_accession': extract_nested_field(raw_data, "genomes.assemblyInfo.bioprojectAccession")
        }
    )
    
    return record


def normalize_engine_row(raw_data: Dict[str, Any], config: Dict[str, Any]) -> RecordInput:
    """
    Convert a flat engine row (engine.md section 6.1) to RecordInput.

    These rows are already normalized - record_id and the searchable fields sit at
    the top level - but the richer metadata stays in extras.attributes. Fields the
    Plan agent can use are lifted out of there when the top level leaves them unset.

    Args:
        raw_data: Flat engine row
        config: Configuration dictionary

    Returns:
        RecordInput object
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


def normalize_record(raw_data: Dict[str, Any], config: Dict[str, Any]) -> RecordInput:
    """
    Normalize one input record, dispatching on its shape.

    Args:
        raw_data: Raw record, either a flat engine row or a nested BV-BRC record
        config: Configuration dictionary

    Returns:
        RecordInput object
    """
    if 'record_id' in raw_data:
        return normalize_engine_row(raw_data, config)
    return normalize_bvbrc_record(raw_data, config)


def ingest_jsonl(
    filepath: str,
    config: Dict[str, Any],
    limit: int = None
) -> List[RecordInput]:
    """
    Load and normalize records from JSONL file.
    
    Args:
        filepath: Path to input JSONL file
        config: Configuration dictionary
        limit: Maximum number of records to load (for testing)
        
    Returns:
        List of normalized RecordInput objects
    """
    records = []
    
    with open(filepath) as f:
        for i, line in enumerate(f):
            if limit and i >= limit:
                break
            
            line = line.strip()
            if not line:
                continue
            
            try:
                raw_data = json.loads(line)
                record = normalize_record(raw_data, config)
                records.append(record)
                
            except Exception as e:
                logger.error(f"Failed to parse line {i+1}: {e}")
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
