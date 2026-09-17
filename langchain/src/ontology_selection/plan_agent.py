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
from pathlib import Path
import logging
import json

from .models import RecordInput, PlanOutput, PlanMapping, SourceField
from .utils import get_argo_llm, format_metadata_for_prompt

logger = logging.getLogger(__name__)


def load_prompt(prompt_name: str) -> str:
    """
    Load prompt from markdown file.
    
    LangChain uses f-string templating, so we need to escape curly braces
    in the prompt text by doubling them: { -> {{, } -> }}
    
    Args:
        prompt_name: Name of prompt file (without .md extension)
        
    Returns:
        Prompt text with escaped braces for LangChain
    """
    prompt_path = Path(__file__).parent / "prompts" / f"{prompt_name}.md"
    with open(prompt_path) as f:
        prompt_text = f.read()
    
    # Escape curly braces for LangChain's f-string template
    # { -> {{, } -> }}
    prompt_text = prompt_text.replace("{", "{{").replace("}", "}}")
    
    return prompt_text


# Load system prompt from markdown file
PLAN_SYSTEM_PROMPT = load_prompt("plan_system")


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
            # The LLM should provide src_fields with path, name, value
            mappings = []
            for m in result.get('mappings', []):
                # Convert src_fields dicts to SourceField objects
                src_fields = [SourceField(**sf) for sf in m.get('src_fields', [])]
                mappings.append(PlanMapping(
                    ontology=m['ontology'],
                    src_fields=src_fields,
                    query_texts=m.get('query_texts', [])
                ))
            
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
                    src_fields=[SourceField(
                        path="bvbrc.isolation_source",
                        name="isolation_source",
                        value=record.isolation_source
                    )],
                    query_texts=[record.isolation_source]
                ))
        
        # If note exists and mentions clinical terms, map to MONDO
        if record.note and record.note.strip():
            note_lower = record.note.lower()
            if any(term in note_lower for term in ['infection', 'disease', 'patient', 'clinical', 'sepsis']):
                mappings.append(PlanMapping(
                    ontology="MONDO",
                    src_fields=[SourceField(
                        path="biosample.attributes.note",
                        name="note",
                        value=record.note
                    )],
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
