"""
Plan Agent: Decides which ontologies to query and what query texts to use.

Uses LangChain + Argo LLM (claudesonnet5) to analyze metadata and determine:
1. Which ontologies are relevant (UBERON, MONDO, ENVO)
2. Which input fields map to each ontology
3. What search queries to generate for RAG retrieval
"""

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from typing import Dict, Any
import logging
import json

from .models import RecordInput, PlanOutput, PlanMapping
from .utils import get_argo_llm, format_metadata_for_prompt

logger = logging.getLogger(__name__)


PLAN_SYSTEM_PROMPT = """You are an expert bioinformatics curator mapping bacterial/viral sample metadata to ontology terms.

Your task: Given metadata about a bacterial or viral isolate, decide:
1. Which ontologies to query (UBERON for anatomy/tissue, MONDO for disease, ENVO for environment)
2. Which input fields are relevant to each ontology
3. What query texts to generate for each ontology (1-3 search strings)

ONTOLOGY GUIDELINES:
- **UBERON**: Anatomical structures, tissues, body parts, organs
  - Examples: "blood", "skin", "wound", "nasal cavity", "lung"
  - Common fields: isolation_source, body_sample_site, tissue, organ
  
- **MONDO**: Diseases, infections, clinical conditions
  - Examples: "sepsis", "pneumonia", "wound infection", "bloodstream infection"
  - Common fields: note, disease, clinical context phrases
  
- **ENVO**: Environmental contexts, habitats, geographic locations
  - Examples: "wastewater", "soil", "hospital", "marine sediment"
  - Common fields: isolation_source, geo_loc_name, environment, habitat

MAPPING RULES:
1. **Multi-ontology is normal**: "wound infection" → BOTH UBERON (wound) AND MONDO (infection)
2. **Generate 1-3 query_texts** per ontology:
   - Include original phrase
   - Include decomposed/synonym terms
   - Keep queries concise (1-5 words each)
3. **Flag edge cases**:
   - "looks_like_host" if isolation_source appears to be a species name (e.g., "Homo sapiens", "Mus musculus")
   - "empty_record" if all key fields are null/empty
   - "ambiguous_source" if isolation_source is vague (e.g., "unknown", "not specified")

EXAMPLES:

Input: {{"isolation_source": "blood", "host": "Homo sapiens", "note": null}}
Output: {{
  "record_id": "...",
  "mappings": [
    {{"ontology": "UBERON", "fields": ["isolation_source"], "query_texts": ["blood"]}}
  ],
  "flags": []
}}

Input: {{"isolation_source": "wound infection", "note": "patient with sepsis"}}
Output: {{
  "record_id": "...",
  "mappings": [
    {{"ontology": "UBERON", "fields": ["isolation_source"], "query_texts": ["wound infection", "wound"]}},
    {{"ontology": "MONDO", "fields": ["isolation_source", "note"], "query_texts": ["wound infection", "infection", "sepsis"]}}
  ],
  "flags": []
}}

Input: {{"isolation_source": "hospital wastewater", "geo_loc_name": "Brazil: Rio de Janeiro"}}
Output: {{
  "record_id": "...",
  "mappings": [
    {{"ontology": "ENVO", "fields": ["isolation_source"], "query_texts": ["hospital wastewater", "wastewater"]}},
    {{"ontology": "UBERON", "fields": ["isolation_source"], "query_texts": ["hospital"]}}
  ],
  "flags": []
}}

Input: {{"isolation_source": "Homo sapiens", "host": "Homo sapiens"}}
Output: {{
  "record_id": "...",
  "mappings": [],
  "flags": ["looks_like_host"]
}}

IMPORTANT: You MUST respond with valid JSON matching this exact structure:
{{
  "record_id": "<same as input>",
  "mappings": [
    {{"ontology": "UBERON" | "MONDO" | "ENVO", "fields": ["field1", "field2"], "query_texts": ["query1", "query2"]}}
  ],
  "flags": ["flag1", "flag2"]  # can be empty list
}}

DO NOT include any explanatory text, only the JSON object."""


class PlanAgent:
    """
    LLM-based agent that creates a retrieval plan from metadata.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Plan agent.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.llm = get_argo_llm(config)
        
        # Use JsonOutputParser for structured output
        self.parser = JsonOutputParser()
        
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", PLAN_SYSTEM_PROMPT),
            ("human", "Record ID: {record_id}\n\nMetadata:\n{metadata}\n\nRespond with JSON only:")
        ])
        
        self.chain = self.prompt | self.llm | self.parser
    
    def plan(self, record: RecordInput) -> PlanOutput:
        """
        Generate retrieval plan for a single record.
        
        Args:
            record: Input record with metadata
            
        Returns:
            PlanOutput with ontology mappings and flags
        """
        # Format metadata for prompt
        metadata_dict = record.model_dump(exclude={'record_id', 'comments', 'extras'}, exclude_none=True)
        metadata_str = format_metadata_for_prompt(metadata_dict)
        
        try:
            # Invoke LLM chain
            result = self.chain.invoke({
                "record_id": record.record_id,
                "metadata": metadata_str
            })
            
            # Parse result into PlanOutput
            # Handle both direct dict and JSON string responses
            if isinstance(result, str):
                result = json.loads(result)
            
            # Convert mappings to PlanMapping objects
            mappings = [PlanMapping(**m) for m in result.get('mappings', [])]
            
            plan_output = PlanOutput(
                record_id=record.record_id,
                mappings=mappings,
                flags=result.get('flags', [])
            )
            
            logger.info(f"Plan for {record.record_id}: {len(plan_output.mappings)} mappings, {len(plan_output.flags)} flags")
            return plan_output
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error for {record.record_id}: {e}")
            return self._fallback_plan(record, error="json_parse_error")
            
        except Exception as e:
            logger.error(f"Plan agent failed for {record.record_id}: {e}", exc_info=True)
            return self._fallback_plan(record, error=str(e))
    
    def _fallback_plan(self, record: RecordInput, error: str) -> PlanOutput:
        """
        Create a safe fallback plan when LLM fails.
        
        Uses simple heuristics to avoid complete failure.
        
        Args:
            record: Input record
            error: Error description
            
        Returns:
            PlanOutput with basic mappings based on heuristics
        """
        mappings = []
        flags = [f"plan_agent_error: {error}"]
        
        # Simple heuristic: if isolation_source exists, map to UBERON
        if record.isolation_source and record.isolation_source.strip():
            iso_src = record.isolation_source.strip().lower()
            
            # Skip if it looks like a host species
            if any(species in iso_src for species in ['homo sapiens', 'mus musculus', 'human', 'mouse']):
                flags.append("looks_like_host")
            else:
                mappings.append(PlanMapping(
                    ontology="UBERON",
                    fields=["isolation_source"],
                    query_texts=[record.isolation_source]
                ))
        
        # If note exists and mentions clinical terms, map to MONDO
        if record.note and record.note.strip():
            note_lower = record.note.lower()
            if any(term in note_lower for term in ['infection', 'disease', 'patient', 'clinical', 'sepsis']):
                mappings.append(PlanMapping(
                    ontology="MONDO",
                    fields=["note"],
                    query_texts=[record.note]
                ))
        
        return PlanOutput(
            record_id=record.record_id,
            mappings=mappings,
            flags=flags
        )
    
    def plan_batch(self, records: list[RecordInput]) -> list[PlanOutput]:
        """
        Generate plans for multiple records.
        
        Args:
            records: List of input records
            
        Returns:
            List of PlanOutput objects
        """
        return [self.plan(record) for record in records]
