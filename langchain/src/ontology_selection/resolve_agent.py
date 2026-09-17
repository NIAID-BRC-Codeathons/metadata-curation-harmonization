"""
Resolve Agent: Picks final ontology terms from RAG candidates.

Uses LLM to make contextual decisions about which candidate terms
best represent the sample metadata.
"""

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from typing import Dict, Any
from pathlib import Path
import logging
import json

from .models import RAGOutput, ResolveOutput, ResolvedTerm, RAGBucket
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
RESOLVE_SYSTEM_PROMPT = load_prompt("resolve_system")


class ResolveAgent:
    """
    LLM-based agent that selects final terms from RAG candidates.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Resolve agent.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        self.llm = get_argo_llm(config)
        
        # Use JsonOutputParser for structured output
        self.parser = JsonOutputParser()
        
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", RESOLVE_SYSTEM_PROMPT),
            ("human", "Record ID: {record_id}\n\nOriginal Metadata:\n{metadata}\n\nRAG Candidates:\n{candidates}\n\nRespond with JSON only:")
        ])
        
        self.chain = self.prompt | self.llm | self.parser
    
    def resolve(self, rag_output: RAGOutput, original_metadata: Dict[str, Any]) -> ResolveOutput:
        """
        Select final terms from RAG candidates.
        
        Args:
            rag_output: RAG retrieval results
            original_metadata: Original sample metadata (for context)
            
        Returns:
            ResolveOutput with selected terms
        """
        # Format metadata and candidates for prompt
        metadata_str = format_metadata_for_prompt(original_metadata)
        candidates_str = self._format_candidates(rag_output.buckets)
        
        # Collect all candidate CURIEs for validation
        all_candidate_curies = {
            c.curie for bucket in rag_output.buckets for c in bucket.candidates
        }
        
        try:
            # Invoke LLM chain
            result = self.chain.invoke({
                "record_id": rag_output.record_id,
                "metadata": metadata_str,
                "candidates": candidates_str
            })
            
            # Parse result
            if isinstance(result, str):
                result = json.loads(result)
            
            # Convert terms to ResolvedTerm objects
            terms = []
            flags = []
            
            for term_data in result.get('terms', []):
                term_id = term_data.get('term_id')
                
                # Validate that CURIE is in candidates
                if term_id not in all_candidate_curies:
                    logger.warning(f"LLM invented CURIE {term_id} not in candidates, skipping")
                    flags.append("invented_curie_removed")
                    continue
                
                terms.append(ResolvedTerm(**term_data))
            
            # Build ResolveOutput
            resolve_output = ResolveOutput(
                record_id=rag_output.record_id,
                outcome=result.get('outcome', 'proposed'),
                terms=terms,
                candidate_curies=list(all_candidate_curies),
                flags=flags,
                abstain_reason=result.get('abstain_reason'),
                original_metadata=original_metadata  # Preserve for review UI
            )
            
            # Validate outcome consistency
            if not resolve_output.terms and resolve_output.outcome == "proposed":
                resolve_output.outcome = "insufficient_evidence"
                if not resolve_output.abstain_reason:
                    resolve_output.abstain_reason = "No terms selected"
            
            logger.info(f"Resolve for {rag_output.record_id}: {len(resolve_output.terms)} terms, outcome={resolve_output.outcome}")
            return resolve_output
            
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error for {rag_output.record_id}: {e}")
            return self._fallback_resolve(rag_output, all_candidate_curies, error="json_parse_error")
            
        except Exception as e:
            logger.error(f"Resolve agent failed for {rag_output.record_id}: {e}", exc_info=True)
            return self._fallback_resolve(rag_output, all_candidate_curies, error=str(e))
    
    def _format_candidates(self, buckets: list[RAGBucket]) -> str:
        """
        Format RAG buckets as human-readable text for LLM.
        
        Args:
            buckets: List of RAG buckets
            
        Returns:
            Formatted string
        """
        if not buckets:
            return "(no candidates available)"
        
        lines = []
        for bucket in buckets:
            lines.append(f"\n{bucket.ontology} candidates (query: {', '.join(bucket.query_texts[:2])}):")
            
            if not bucket.candidates:
                lines.append("  (no results)")
                continue
            
            # Show top 5 for brevity
            for c in bucket.candidates[:5]:
                lines.append(f"  [{c.rank}] {c.curie} - {c.label} (score: {c.score:.2f})")
                if c.definition:
                    # Truncate long definitions
                    def_text = c.definition[:150] + "..." if len(c.definition) > 150 else c.definition
                    lines.append(f"      Definition: {def_text}")
            
            if len(bucket.candidates) > 5:
                lines.append(f"  ... ({len(bucket.candidates) - 5} more candidates)")
        
        return "\n".join(lines)
    
    def _fallback_resolve(self, rag_output: RAGOutput, candidate_curies: set, error: str) -> ResolveOutput:
        """
        Create a safe fallback resolution when LLM fails.
        
        Simple heuristic: pick top-ranked candidate from each ontology.
        
        Args:
            rag_output: RAG output
            candidate_curies: Set of all candidate CURIEs
            error: Error description
            
        Returns:
            ResolveOutput with heuristic selections
        """
        terms = []
        flags = [f"resolve_agent_error: {error}"]
        
        # Heuristic: pick rank-0 candidate from each bucket if score > 0.8
        for bucket in rag_output.buckets:
            if bucket.candidates and bucket.candidates[0].score > 0.8:
                top_candidate = bucket.candidates[0]
                terms.append(ResolvedTerm(
                    ontology=bucket.ontology,
                    term_id=top_candidate.curie,
                    label=top_candidate.label,
                    role="primary",
                    confidence=top_candidate.score
                ))
        
        outcome = "proposed_with_flags" if terms else "insufficient_evidence"
        abstain_reason = None if terms else f"Resolve agent failed: {error}"
        
        return ResolveOutput(
            record_id=rag_output.record_id,
            outcome=outcome,
            terms=terms,
            candidate_curies=list(candidate_curies),
            flags=flags,
            abstain_reason=abstain_reason
        )
