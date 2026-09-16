"""
Utility functions: Argo client, logging, file I/O, field extraction.
"""

import os
import logging
import json
from pathlib import Path
from typing import Dict, Any, List
from langchain_openai import ChatOpenAI
import yaml


def load_config(config_path: str = "config.yaml") -> dict:
    """Load YAML configuration file."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def get_argo_llm(config: dict) -> ChatOpenAI:
    """
    Initialize Argo LLM client from config.
    Reads ARGO_USER from environment.
    
    Args:
        config: Configuration dictionary
        
    Returns:
        ChatOpenAI instance configured for Argo
    """
    argo_user = os.getenv("ARGO_USER", "ac.yourname")
    
    if argo_user == "ac.yourname":
        logging.warning("ARGO_USER not set in environment, using default 'ac.yourname'")
    
    # For Anthropic models on Argo, max_tokens must be set and <= 21000 for non-streaming
    # See ANL-Argo-Quickstart README.md section 6
    llm_config = {
        "model": config['llm']['model'],
        "api_key": argo_user,
        "base_url": config['llm']['base_url'],
        "temperature": config['llm']['temperature'],
    }
    
    # Add max_tokens if specified (required for Claude models)
    if 'max_tokens' in config['llm']:
        llm_config['max_tokens'] = min(config['llm']['max_tokens'], 21000)
    
    return ChatOpenAI(**llm_config)


def setup_logging(config: dict):
    """
    Configure logging from config.
    
    Args:
        config: Configuration dictionary
    """
    log_config = config.get('logging', {})
    log_level = getattr(logging, log_config.get('level', 'INFO'))
    log_file = log_config.get('file')
    log_format = log_config.get('format', '%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    
    handlers = [logging.StreamHandler()]
    
    if log_file:
        # Ensure log directory exists
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=handlers
    )


def extract_nested_field(data: Dict[str, Any], path: str) -> Any:
    """
    Extract a field from nested dictionary using dot notation.
    
    Args:
        data: Nested dictionary
        path: Dot-separated path, e.g., "genomes.biosample.host"
        
    Returns:
        Field value or None if not found
        
    Example:
        >>> data = {"genomes": {"biosample": {"host": "Homo sapiens"}}}
        >>> extract_nested_field(data, "genomes.biosample.host")
        "Homo sapiens"
    """
    keys = path.split('.')
    value = data
    
    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        else:
            return None
        
        if value is None:
            return None
    
    return value


def flatten_biosample_attributes(attributes: List[Dict[str, str]]) -> Dict[str, str]:
    """
    Flatten biosample.attributes array to dictionary.
    
    Args:
        attributes: List of {name, value} dictionaries
        
    Returns:
        Dictionary mapping name to value
        
    Example:
        >>> attrs = [{"name": "strain", "value": "Bmb9393"}, {"name": "host", "value": "Homo sapiens"}]
        >>> flatten_biosample_attributes(attrs)
        {"strain": "Bmb9393", "host": "Homo sapiens"}
    """
    if not attributes:
        return {}
    
    return {attr['name']: attr['value'] for attr in attributes if 'name' in attr and 'value' in attr}


def should_ignore_field(field_path: str, config: dict) -> bool:
    """
    Check if a field path should be ignored based on stop list.
    
    Args:
        field_path: Dot-separated path, e.g., "genomes.assemblyInfo.assemblyStats"
        config: Configuration dictionary
        
    Returns:
        True if field should be ignored
    """
    ignore_paths = config.get('field_selection', {}).get('ignore_paths', [])
    
    for ignore_path in ignore_paths:
        # Check for exact match or prefix match
        if field_path == ignore_path or field_path.startswith(ignore_path + '.'):
            return True
    
    return False


def extract_relevant_fields(raw_data: Dict[str, Any], config: dict) -> Dict[str, Any]:
    """
    Extract relevant fields from raw BV-BRC data based on config.
    
    This applies the stop list and consider list to filter the input data.
    
    Args:
        raw_data: Raw BV-BRC JSON record
        config: Configuration dictionary
        
    Returns:
        Dictionary of relevant fields with simplified structure
    """
    relevant = {}
    consider_paths = config.get('field_selection', {}).get('consider_paths', [])
    
    for path in consider_paths:
        if should_ignore_field(path, config):
            continue
        
        value = extract_nested_field(raw_data, path)
        if value is not None:
            # Use just the last part of the path as key
            key = path.split('.')[-1]
            relevant[key] = value
    
    # Special handling for biosample.attributes (flatten to dict)
    if config.get('field_selection', {}).get('flatten_attributes', False):
        attrs_path = "genomes.biosample.attributes"
        attrs = extract_nested_field(raw_data, attrs_path)
        if attrs:
            flattened = flatten_biosample_attributes(attrs)
            relevant.update(flattened)
    
    return relevant


def save_jsonl(data: List[Any], filepath: str):
    """
    Save list of Pydantic models or dicts to JSONL file.
    
    Args:
        data: List of objects (Pydantic models or dicts)
        filepath: Output file path
    """
    Path(filepath).parent.mkdir(parents=True, exist_ok=True)
    
    with open(filepath, 'w') as f:
        for item in data:
            if hasattr(item, 'model_dump_json'):
                # Pydantic v2
                f.write(item.model_dump_json() + '\n')
            elif hasattr(item, 'json'):
                # Pydantic v1
                f.write(item.json() + '\n')
            else:
                # Plain dict
                f.write(json.dumps(item) + '\n')


def load_jsonl(filepath: str) -> List[Dict[str, Any]]:
    """
    Load JSONL file to list of dictionaries.
    
    Args:
        filepath: Input file path
        
    Returns:
        List of dictionaries
    """
    data = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                data.append(json.loads(line))
    return data


def format_metadata_for_prompt(metadata: Dict[str, Any], max_fields: int = 20) -> str:
    """
    Format metadata dictionary as human-readable text for LLM prompts.
    
    Args:
        metadata: Dictionary of metadata fields
        max_fields: Maximum number of fields to include
        
    Returns:
        Formatted string
    """
    lines = []
    count = 0
    
    for key, value in metadata.items():
        if count >= max_fields:
            lines.append(f"... ({len(metadata) - count} more fields)")
            break
        
        if value is not None and value != "":
            # Truncate very long values
            value_str = str(value)
            if len(value_str) > 200:
                value_str = value_str[:197] + "..."
            
            lines.append(f"- {key}: {value_str}")
            count += 1
    
    return "\n".join(lines) if lines else "(no metadata available)"


def get_curie_ontology(curie: str) -> str:
    """
    Extract ontology prefix from CURIE.
    
    Args:
        curie: Ontology term CURIE, e.g., "UBERON:0002097"
        
    Returns:
        Ontology prefix, e.g., "UBERON"
    """
    if ':' in curie:
        return curie.split(':')[0]
    return "UNKNOWN"
