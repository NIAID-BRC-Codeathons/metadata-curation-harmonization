"""
Pydantic models for the Plan→Retrieve→Resolve pipeline.
Mirrors the JSON schemas from engine.md sections 5 & 6.
"""

from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field


# ============================================================================
# INPUT MODELS (from BV-BRC/NCBI)
# ============================================================================

class RecordInput(BaseModel):
    """
    Normalized input record for pipeline consumption.
    
    This is the flattened/simplified version of complex BV-BRC JSON.
    The ingest module converts raw BV-BRC data to this format.
    """
    record_id: str  # Primary identifier (biosample accession or genome_id)
    
    # Key metadata fields (extracted from nested BV-BRC structure)
    isolation_source: Optional[str] = None
    body_sample_site: Optional[str] = None
    host: Optional[str] = None
    note: Optional[str] = None
    geo_loc_name: Optional[str] = None
    collection_date: Optional[str] = None
    
    # Additional flattened attributes from biosample.attributes[]
    strain: Optional[str] = None
    disease: Optional[str] = None
    tissue: Optional[str] = None
    environment: Optional[str] = None
    
    # Contextual information
    bioproject_title: Optional[str] = None
    biosample_description: Optional[str] = None
    
    # Comments and extra data
    comments: List[str] = []
    extras: Dict[str, Any] = {}  # Any additional metadata for reference
    
    class Config:
        # Allow extra fields for forward compatibility
        extra = "allow"


# ============================================================================
# PLAN AGENT OUTPUT (engine.md section 6.2)
# ============================================================================

class SourceField(BaseModel):
    """
    Source field with path and value.
    
    Tracks which input fields were used and their values.
    """
    path: str  # JSON path, e.g., "bvbrc.isolation_source" or "biosample.attributes.note"
    name: str  # Field name, e.g., "isolation_source", "note"
    value: Optional[str] = None  # Original field value


class PlanMapping(BaseModel):
    """
    One mapping = one ontology + the fields/queries targeting it.
    
    Represents the Plan agent's decision to query a specific ontology
    based on specific input fields.
    """
    ontology: Literal["UBERON", "MONDO", "ENVO"]
    src_fields: List[SourceField]  # Source fields with paths and values
    query_texts: List[str]  # 1-3 search strings, e.g., ["wound infection", "wound", "infection"]


class PlanOutput(BaseModel):
    """
    Plan agent output: which ontologies to query and with what search terms.
    
    Example:
    {
      "record_id": "SAMN123",
      "mappings": [
        {"ontology": "UBERON", "fields": ["isolation_source"], "query_texts": ["blood"]},
        {"ontology": "MONDO", "fields": ["note"], "query_texts": ["sepsis", "bloodstream infection"]}
      ],
      "flags": []
    }
    """
    record_id: str
    mappings: List[PlanMapping]
    flags: List[str] = []  # e.g., ["looks_like_host", "empty_isolation_source"]


# ============================================================================
# RAG RETRIEVE OUTPUT (engine.md section 6.3)
# ============================================================================

class Candidate(BaseModel):
    """
    One ontology term candidate from RAG retrieval.
    
    All CURIEs come from the RAG system, never invented by LLM.
    """
    curie: str  # e.g., "UBERON:0002097"
    label: str  # e.g., "skin of body"
    definition: Optional[str] = None  # Can be null if RAG doesn't provide
    ontology: Literal["UBERON", "MONDO", "ENVO"]
    score: float  # RAG similarity score (0.0-1.0)
    rank: int  # 0-indexed rank in results (0 = best match)


class RAGBucket(BaseModel):
    """
    RAG results for one ontology.
    
    Aligned with PlanMapping by ontology.
    One bucket per ontology queried.
    """
    ontology: Literal["UBERON", "MONDO", "ENVO"]
    src_fields: List[SourceField]  # Echo from PlanMapping (with paths and values)
    query_texts: List[str]  # Echo from PlanMapping
    candidates: List[Candidate]  # Top-k from RAG (could be empty)


class RAGOutput(BaseModel):
    """
    RAG retrieval output: candidate ontology terms.
    
    Example (matching engine.md section 6.3):
    {
      "record_id": "SAMN123",
      "buckets": [
        {
          "ontology": "UBERON",
          "fields": ["isolation_source"],
          "query_texts": ["wound infection", "wound"],
          "candidates": [
            {"curie": "UBERON:0002097", "label": "skin of body", "score": 0.9, "rank": 0}
          ]
        }
      ],
      "flags": []
    }
    """
    record_id: str
    buckets: List[RAGBucket]
    flags: List[str] = []  # e.g., ["empty_rag_UBERON"]


# ============================================================================
# RESOLVE AGENT OUTPUT (engine.md section 6.4)
# ============================================================================

class ResolvedTerm(BaseModel):
    """
    One selected ontology term with metadata.
    
    The Resolve agent picks these from RAG candidates.
    """
    ontology: Literal["UBERON", "MONDO", "ENVO"]
    term_id: str  # CURIE, e.g., "UBERON:0002097"
    label: str
    role: Literal["primary", "secondary", "alternate"]
    confidence: float = Field(ge=0.0, le=1.0)


class ResolveOutput(BaseModel):
    """
    Final output: selected ontology terms for one record.
    
    This is the main pipeline output that goes to human review.
    
    Example (matching engine.md section 6.4):
    {
      "record_id": "SAMN123",
      "outcome": "proposed",
      "terms": [
        {
          "ontology": "UBERON",
          "term_id": "UBERON:0002097",
          "label": "skin of body",
          "role": "primary",
          "confidence": 0.85
        },
        {
          "ontology": "MONDO",
          "term_id": "MONDO:0004485",
          "label": "wound infection",
          "role": "secondary",
          "confidence": 0.78
        }
      ],
      "candidate_curies": ["UBERON:0002097", "UBERON:0000178", "MONDO:0004485"],
      "flags": [],
      "abstain_reason": null
    }
    """
    record_id: str
    outcome: Literal["proposed", "insufficient_evidence", "proposed_with_flags"]
    terms: List[ResolvedTerm]  # Can be empty if abstained
    candidate_curies: List[str]  # All CURIEs considered (for validation)
    flags: List[str] = []
    abstain_reason: Optional[str] = None
    
    # Optional: preserve original metadata for review UI
    original_metadata: Optional[Dict[str, Any]] = None


# ============================================================================
# GOLD STANDARD (for evaluation)
# ============================================================================

class GoldTerm(BaseModel):
    """One human-curated ontology term."""
    field: str  # "disease", "isolation_tissue", "isolation_environment"
    curie: str  # e.g., "MONDO:0005015"
    ontology: Optional[str] = None  # Derived from CURIE prefix


class GoldRecord(BaseModel):
    """
    Human-curated gold standard for evaluation.
    
    Expected format from CEDAR data.
    """
    record_id: str
    curated: List[GoldTerm]


# ============================================================================
# HUMAN REVIEW / DECISIONS
# ============================================================================

class ReviewDecision(BaseModel):
    """
    Human curator's decision on a proposed term.
    
    Used by the Web UI to capture accept/reject/edit actions.
    """
    term_id: str  # CURIE being reviewed
    action: Literal["accept", "reject", "edit"]
    edited_curie: Optional[str] = None  # If action == "edit"
    edited_label: Optional[str] = None
    notes: Optional[str] = None  # Curator's comments


class ReviewRecord(BaseModel):
    """
    Complete review record for one sample.
    
    Output format for human review UI.
    """
    record_id: str
    reviewer: Optional[str] = None
    review_date: Optional[str] = None
    decisions: List[ReviewDecision]
    final_terms: List[ResolvedTerm]  # After applying decisions
    review_status: Literal["pending", "in_progress", "completed", "flagged"]
