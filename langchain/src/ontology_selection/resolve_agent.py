"""
Resolve Agent: Picks final ontology terms from RAG candidates.

Uses LLM to make contextual decisions about which candidate terms
best represent the sample metadata.
"""

from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from typing import Dict, Any
import logging
import json

from .models import RAGOutput, ResolveOutput, ResolvedTerm, RAGBucket
from .utils import get_argo_llm, format_metadata_for_prompt

logger = logging.getLogger(__name__)


RESOLVE_SYSTEM_PROMPT = """You are an expert curator selecting the best ontology terms for bacterial/viral sample metadata.

You are given:
1. Original sample metadata (isolation_source, notes, host, etc.)
2. Candidate ontology terms from RAG retrieval (top-10 per ontology, ranked by similarity)

Your task:
- Select the BEST term(s) from the candidates that accurately describe the sample
- You MAY select multiple terms across ontologies (e.g., UBERON + MONDO for "wound infection")
- Assign roles: "primary" (best match), "secondary" (additional context), "alternate" (plausible alternative)
- Assign confidence scores (0.0-1.0) based on how well the term fits
- You MAY abstain (return empty terms list) if no candidates fit well

CRITICAL RULES:
1. **ONLY select CURIEs that appear in the candidate lists** - NEVER invent new CURIEs
2. For ambiguous cases, prefer multiple terms with appropriate confidence over forcing one choice
3. "wound infection" context → likely BOTH UBERON:wound + MONDO:infection
4. "blood" without disease context → UBERON only
5. If all candidates are poor matches (low scores, irrelevant labels) → abstain with clear reason

CONFIDENCE GUIDELINES:
- 0.9-1.0: Exact match, high certainty
- 0.7-0.9: Strong match, some minor ambiguity
- 0.5-0.7: Reasonable match, moderate uncertainty
- 0.3-0.5: Weak match, consider as alternate only
- <0.3: Poor match, avoid unless no better options

ROLE GUIDELINES:
- "primary": The single best term for this ontology
- "secondary": Additional term providing complementary information
- "alternate": Plausible alternative interpretation

EXAMPLES:

Input metadata: {{"isolation_source": "blood", "note": null}}
Candidates: UBERON top-3: [("UBERON:0000178", "blood", 0.95), ("UBERON:0001977", "blood serum", 0.82), ...]
Output: {{
  "outcome": "proposed",
  "terms": [{{"ontology": "UBERON", "term_id": "UBERON:0000178", "label": "blood", "role": "primary", "confidence": 0.95}}],
  "abstain_reason": null
}}

Input metadata: {{"isolation_source": "wound infection", "note": "patient with sepsis"}}
Candidates: 
  UBERON: [("UBERON:0002097", "skin of body", 0.88), ("UBERON:0000062", "organ", 0.45), ...]
  MONDO: [("MONDO:0004485", "wound infection", 0.93), ("MONDO:0005015", "sepsis", 0.91), ...]
Output: {{
  "outcome": "proposed",
  "terms": [
    {{"ontology": "UBERON", "term_id": "UBERON:0002097", "label": "skin of body", "role": "primary", "confidence": 0.85}},
    {{"ontology": "MONDO", "term_id": "MONDO:0004485", "label": "wound infection", "role": "primary", "confidence": 0.92}},
    {{"ontology": "MONDO", "term_id": "MONDO:0005015", "label": "sepsis", "role": "secondary", "confidence": 0.88}}
  ],
  "abstain_reason": null
}}

Input metadata: {{"isolation_source": "unknown", "note": null}}
Candidates: UBERON: [("UBERON:0000062", "organ", 0.35), ...] (all low scores)
Output: {{
  "outcome": "insufficient_evidence",
  "terms": [],
  "abstain_reason": "Isolation source is vague ('unknown') and no candidates have strong matches"
}}

IMPORTANT: You MUST respond with valid JSON matching this exact structure:
{{
  "outcome": "proposed" | "insufficient_evidence" | "proposed_with_flags",
  "terms": [
    {{"ontology": "UBERON"|"MONDO"|"ENVO", "term_id": "<CURIE from candidates>", "label": "<label>", "role": "primary"|"secondary"|"alternate", "confidence": <0.0-1.0>}}
  ],
  "abstain_reason": null | "<reason>"
}}

DO NOT include any explanatory text, only the JSON object."""


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
