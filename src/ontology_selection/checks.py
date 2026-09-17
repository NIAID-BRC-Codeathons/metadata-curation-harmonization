"""
Soft validation checks (flag + continue, not hard errors).

These checkers run after each pipeline stage to detect anomalies
and add flags without stopping the pipeline.
"""

from typing import Union
import logging

from .models import PlanOutput, RAGOutput, ResolveOutput

logger = logging.getLogger(__name__)


def run_checks(output: Union[PlanOutput, RAGOutput, ResolveOutput], stage: str):
    """
    Run appropriate checks based on pipeline stage.
    
    Mutates output.flags in place.
    
    Args:
        output: Pipeline stage output
        stage: "plan", "retrieve", or "resolve"
        
    Returns:
        Modified output with additional flags
    """
    if stage == "plan":
        return check_plan(output)
    elif stage == "retrieve":
        return check_retrieve(output)
    elif stage == "resolve":
        return check_resolve(output)
    else:
        logger.warning(f"Unknown stage for checks: {stage}")
        return output


def check_plan(plan: PlanOutput) -> PlanOutput:
    """
    Check Plan output for anomalies.
    
    Args:
        plan: Plan output
        
    Returns:
        Modified plan with additional flags
    """
    # No mappings generated
    if not plan.mappings:
        if "no_mappings" not in plan.flags:
            plan.flags.append("no_mappings")
    
    # Check for suspiciously long query_texts (likely errors)
    for mapping in plan.mappings:
        for qt in mapping.query_texts:
            if len(qt) > 200:
                if "long_query_text" not in plan.flags:
                    plan.flags.append("long_query_text")
                logger.warning(f"Long query text in {plan.record_id}: {qt[:50]}...")
    
    # Check for duplicate query texts within same ontology
    for mapping in plan.mappings:
        if len(mapping.query_texts) != len(set(mapping.query_texts)):
            if "duplicate_query_texts" not in plan.flags:
                plan.flags.append("duplicate_query_texts")
    
    # Check for same ontology mapped twice (should be combined)
    ontologies = [m.ontology for m in plan.mappings]
    if len(ontologies) != len(set(ontologies)):
        if "duplicate_ontology_mappings" not in plan.flags:
            plan.flags.append("duplicate_ontology_mappings")
    
    return plan


def check_retrieve(rag: RAGOutput) -> RAGOutput:
    """
    Check RAG output for anomalies.
    
    Args:
        rag: RAG output
        
    Returns:
        Modified RAG output with additional flags
    """
    # Check for empty buckets (already flagged by RAGClient, but double-check)
    for bucket in rag.buckets:
        if not bucket.candidates:
            flag = f"empty_rag_{bucket.ontology}"
            if flag not in rag.flags:
                rag.flags.append(flag)
    
    # Check for duplicate CURIEs within a bucket
    for bucket in rag.buckets:
        curies = [c.curie for c in bucket.candidates]
        if len(curies) != len(set(curies)):
            flag = f"duplicate_curies_{bucket.ontology}"
            if flag not in rag.flags:
                rag.flags.append(flag)
            logger.warning(f"Duplicate CURIEs in {rag.record_id} {bucket.ontology}")
    
    # Check for suspiciously low scores (all candidates < 0.5)
    for bucket in rag.buckets:
        if bucket.candidates:
            max_score = max(c.score for c in bucket.candidates)
            if max_score < 0.5:
                flag = f"low_scores_{bucket.ontology}"
                if flag not in rag.flags:
                    rag.flags.append(flag)
    
    # No missing_definition check: the vector database carries term ids and names
    # only, so it would fire on every record and make `outcome` constant. Restore
    # it once the vector database is rebuilt with definitions.
    
    return rag


def check_resolve(resolve: ResolveOutput) -> ResolveOutput:
    """
    Check Resolve output for anomalies.
    
    Args:
        resolve: Resolve output
        
    Returns:
        Modified resolve output with additional flags
    """
    # Validate that all selected terms are in candidate_curies
    selected_curies = {t.term_id for t in resolve.terms}
    if not selected_curies.issubset(set(resolve.candidate_curies)):
        if "term_not_in_candidates" not in resolve.flags:
            resolve.flags.append("term_not_in_candidates")
        logger.error(f"Resolve {resolve.record_id}: selected terms not in candidates!")
    
    # Empty terms without abstain reason
    if not resolve.terms and not resolve.abstain_reason:
        if "empty_terms_no_reason" not in resolve.flags:
            resolve.flags.append("empty_terms_no_reason")
        resolve.abstain_reason = "Unknown reason (flagged by checker)"
    
    # Multiple primary terms for same ontology (should be primary + secondary)
    ontology_roles = {}
    for term in resolve.terms:
        key = (term.ontology, term.role)
        ontology_roles[key] = ontology_roles.get(key, 0) + 1
    
    for (ontology, role), count in ontology_roles.items():
        if role == "primary" and count > 1:
            if "multiple_primary_same_ontology" not in resolve.flags:
                resolve.flags.append("multiple_primary_same_ontology")
            logger.warning(f"Resolve {resolve.record_id}: {count} primary terms for {ontology}")
    
    # Confidence out of valid range (should be caught by Pydantic, but double-check)
    for term in resolve.terms:
        if not (0.0 <= term.confidence <= 1.0):
            if "invalid_confidence" not in resolve.flags:
                resolve.flags.append("invalid_confidence")
    
    # Low confidence primary terms (< 0.5)
    for term in resolve.terms:
        if term.role == "primary" and term.confidence < 0.5:
            if "low_confidence_primary" not in resolve.flags:
                resolve.flags.append("low_confidence_primary")
    
    # Set outcome based on flags and terms
    if resolve.flags and resolve.terms:
        # Has terms but also has flags -> proposed_with_flags
        if resolve.outcome == "proposed":
            resolve.outcome = "proposed_with_flags"
    elif resolve.terms and not resolve.flags:
        # Has terms, no flags -> proposed
        resolve.outcome = "proposed"
    else:
        # No terms -> insufficient_evidence
        resolve.outcome = "insufficient_evidence"
    
    return resolve
