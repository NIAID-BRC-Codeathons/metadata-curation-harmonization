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
        # Build metadata dict for the LLM.
        # Include comments (may contain useful free text like "from a throat swab").
        # Exclude extras (raw nested objects) but surface extras.biosample_attrs
        # so the LLM can see all biosample attributes, including ones we didn't
        # map to named RecordInput fields.
        metadata_dict = record.model_dump(exclude={'record_id', 'extras'}, exclude_none=True)

        # Surface the full set of biosample attributes under "biosample_attributes"
        # so the LLM sees field names we may not have normalised.
        biosample_attrs = (record.extras or {}).get('biosample_attrs') or {}
        if biosample_attrs:
            metadata_dict['biosample_attributes'] = biosample_attrs

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

        Scans all available fields — isolation_source, body_sample_site,
        tissue, disease, environment, note, comments — and builds mappings
        from any that carry useful text.
        """
        mappings = []
        flags = [f"plan_agent_error: {error}"]

        host_terms = {'homo sapiens', 'mus musculus', 'human', 'mouse'}
        clinical_terms = {'infection', 'disease', 'patient', 'clinical',
                          'sepsis', 'pneumonia', 'abscess', 'wound'}

        def _add(ontology: str, field_name: str, value: str):
            """Append a mapping if value is non-empty and not a host species."""
            v = value.strip()
            if not v:
                return
            if v.lower() in host_terms:
                flags.append("looks_like_host")
                return
            mappings.append(PlanMapping(
                ontology=ontology,
                src_fields=[SourceField(
                    path=f"biosample.{field_name}",
                    name=field_name,
                    value=v,
                )],
                query_texts=[v],
            ))

        # Anatomy / specimen source → UBERON
        for field in ('isolation_source', 'body_sample_site', 'tissue'):
            val = getattr(record, field, None)
            if val:
                _add("UBERON", field, val)

        # Disease / clinical → MONDO
        if record.disease:
            _add("MONDO", "disease", record.disease)

        # Environment → ENVO
        if record.environment:
            _add("ENVO", "environment", record.environment)

        # Free text fields — scan for clinical or anatomical hints
        for field in ('note', 'biosample_description', 'bioproject_title'):
            val = getattr(record, field, None)
            if not val:
                continue
            val_lower = val.lower()
            if any(t in val_lower for t in clinical_terms):
                _add("MONDO", field, val)
            # Also check for body-part language
            if any(t in val_lower for t in ('blood', 'wound', 'throat', 'nasal',
                                            'swab', 'skin', 'lung', 'tissue',
                                            'sputum', 'urine', 'stool')):
                _add("UBERON", field, val)

        # Comments (list of strings)
        for i, comment in enumerate(record.comments or []):
            if not comment:
                continue
            c_lower = comment.lower()
            if any(t in c_lower for t in clinical_terms):
                _add("MONDO", f"comments[{i}]", comment)
            if any(t in c_lower for t in ('blood', 'wound', 'throat', 'nasal',
                                          'swab', 'skin', 'lung', 'tissue')):
                _add("UBERON", f"comments[{i}]", comment)

        # Check biosample_attrs for fields we may have missed
        biosample_attrs = (record.extras or {}).get('biosample_attrs') or {}
        for attr_name, attr_val in biosample_attrs.items():
            if not attr_val or not isinstance(attr_val, str):
                continue
            # Already covered by named fields above
            if attr_name in ('isolation_source', 'host', 'strain',
                             'geo_loc_name', 'collection_date'):
                continue
            attr_lower = attr_val.lower()
            if any(t in attr_lower for t in ('blood', 'wound', 'throat', 'nasal',
                                             'swab', 'skin', 'lung', 'tissue',
                                             'sputum', 'urine')):
                _add("UBERON", f"biosample.{attr_name}", attr_val)
            if any(t in attr_lower for t in clinical_terms):
                _add("MONDO", f"biosample.{attr_name}", attr_val)

        if not mappings:
            flags.append("empty_record")

        return PlanOutput(
            record_id=record.record_id,
            mappings=mappings,
            flags=flags,
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
