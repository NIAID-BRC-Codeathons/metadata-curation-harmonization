#!/usr/bin/env python3
"""
Mock RAG System

Simulates the external RAG retrieval system for testing.
Accepts the same CLI arguments as the real rag.py.

Usage:
    python scripts/00_mock_rag.py --input in.json --top-k 10 --outfile out.json
"""

import argparse
import json
import sys
from pathlib import Path


# Mock ontology term database
MOCK_TERMS = {
    "UBERON": [
        {"curie": "UBERON:0000178", "label": "blood", "definition": "A fluid connective tissue composed of formed elements suspended in a liquid medium."},
        {"curie": "UBERON:0002097", "label": "skin of body", "definition": "The organ covering the body that consists of dermis and epidermis."},
        {"curie": "UBERON:0000062", "label": "organ", "definition": "Anatomical structure that performs a specific function or group of functions."},
        {"curie": "UBERON:0002048", "label": "lung", "definition": "Respiration organ that develops as an outpocketing of the esophagus."},
        {"curie": "UBERON:0001004", "label": "respiratory system", "definition": "Anatomical system that has as its parts the organs concerned with the exchange of gases."},
        {"curie": "UBERON:0001977", "label": "blood serum", "definition": "The clear fluid obtained by removing the clot from clotted blood."},
        {"curie": "UBERON:0000344", "label": "mucosa", "definition": "A membrane that lines various cavities in the body and surrounds internal organs."},
        {"curie": "UBERON:0001831", "label": "parotid gland", "definition": "A salivary gland situated in front of and below the ear."},
        {"curie": "UBERON:0002107", "label": "liver", "definition": "An exocrine gland which secretes bile and functions in metabolism."},
        {"curie": "UBERON:0002113", "label": "kidney", "definition": "A paired organ whose primary function is the production of urine."},
    ],
    "MONDO": [
        {"curie": "MONDO:0005737", "label": "obsolete infectious disease", "definition": "A disease due to the presence of pathogenic microbial agents."},
        {"curie": "MONDO:0005015", "label": "sepsis", "definition": "A systemic inflammatory response to infection."},
        {"curie": "MONDO:0004485", "label": "wound infection", "definition": "An infection that involves a wound."},
        {"curie": "MONDO:0005249", "label": "pneumonia", "definition": "An acute, acute and chronic, or chronic inflammation of the lung parenchyma."},
        {"curie": "MONDO:0005083", "label": "psoriasis", "definition": "A common genetically determined, chronic skin condition."},
        {"curie": "MONDO:0005550", "label": "infectious disease", "definition": "A disease due to the presence of pathogenic microbial agents."},
        {"curie": "MONDO:0002050", "label": "bacterial infectious disease", "definition": "An infection caused by bacteria."},
        {"curie": "MONDO:0004369", "label": "bacteremia", "definition": "The presence of bacteria in the blood."},
        {"curie": "MONDO:0021178", "label": "urinary tract infection", "definition": "An infection of any part of the urinary system."},
        {"curie": "MONDO:0005108", "label": "skin disease", "definition": "A disease involving the integumentary system."},
    ],
    "ENVO": [
        {"curie": "ENVO:00002042", "label": "surface water", "definition": "Water that has collected on the surface of the ground."},
        {"curie": "ENVO:00002001", "label": "wastewater", "definition": "Water that has been adversely affected in quality by anthropogenic influence."},
        {"curie": "ENVO:00000428", "label": "biome", "definition": "An ecosystem to which resident ecological communities have evolved adaptations."},
        {"curie": "ENVO:00002007", "label": "sediment", "definition": "Solid material that has settled down from a state of suspension in a liquid."},
        {"curie": "ENVO:00000067", "label": "hospital", "definition": "A building in which health care is provided for people."},
        {"curie": "ENVO:00002985", "label": "hospital environment", "definition": "An environment which is located in or around a hospital."},
        {"curie": "ENVO:00002006", "label": "liquid water", "definition": "Water in its liquid phase."},
        {"curie": "ENVO:00001998", "label": "soil", "definition": "The unconsolidated mineral or organic matter on the surface of the Earth."},
        {"curie": "ENVO:00002011", "label": "freshwater", "definition": "Water that contains less than one percent dissolved salts."},
        {"curie": "ENVO:00002030", "label": "aquatic environment", "definition": "An environment whose dynamics are strongly influenced by water."},
    ]
}


def score_term(query: str, term: dict) -> float:
    """
    Simple string matching score (mock similarity).
    
    Args:
        query: Search query string
        term: Ontology term dictionary
        
    Returns:
        Mock similarity score (0.0-1.0)
    """
    query_lower = query.lower()
    label_lower = term['label'].lower()
    
    # Exact match
    if query_lower == label_lower:
        return 0.95
    
    # Contains query
    if query_lower in label_lower:
        return 0.85
    
    # Query contains label
    if label_lower in query_lower:
        return 0.80
    
    # Word overlap
    query_words = set(query_lower.split())
    label_words = set(label_lower.split())
    if query_words & label_words:
        overlap = len(query_words & label_words) / len(query_words | label_words)
        return 0.60 + (overlap * 0.2)
    
    # Random low score (mock non-match)
    return 0.40


def retrieve_mock(ontology: str, query_texts: list, top_k: int, threshold: float) -> list:
    """
    Mock RAG retrieval.
    
    Args:
        ontology: Ontology name (UBERON, MONDO, ENVO)
        query_texts: List of query strings
        top_k: Number of results to return
        threshold: Minimum score threshold
        
    Returns:
        List of candidate dictionaries
    """
    if ontology not in MOCK_TERMS:
        return []
    
    # Score all terms against all queries
    scored_terms = []
    for term in MOCK_TERMS[ontology]:
        # Max score across all queries
        max_score = max(score_term(q, term) for q in query_texts)
        
        if max_score >= threshold:
            scored_terms.append({
                "curie": term["curie"],
                "label": term["label"],
                "definition": term.get("definition"),
                "ontology": ontology,
                "score": round(max_score, 2)
            })
    
    # Sort by score descending
    scored_terms.sort(key=lambda x: x['score'], reverse=True)
    
    # Add rank
    for i, term in enumerate(scored_terms[:top_k]):
        term['rank'] = i
    
    return scored_terms[:top_k]


def main():
    parser = argparse.ArgumentParser(description="Mock RAG retrieval system")
    parser.add_argument("--input", required=True, help="Input JSON file")
    parser.add_argument("--outfile", required=True, help="Output JSON file")
    parser.add_argument("--top-k", type=int, default=10, help="Number of results per ontology")
    parser.add_argument("--model", default="biomedbert", help="Embedding model (ignored in mock)")
    parser.add_argument("--metric", default="cosine", help="Distance metric (ignored in mock)")
    parser.add_argument("--device", default="cpu", help="Device (ignored in mock)")
    parser.add_argument("--low-score-threshold", type=float, default=0.70, help="Minimum score threshold")
    
    args = parser.parse_args()
    
    # Load input
    with open(args.input) as f:
        request = json.load(f)
    
    record_id = request.get('record_id', 'unknown')
    ontologies_data = request.get('ontologies', [])
    
    # Process each ontology
    buckets = []
    for onto_data in ontologies_data:
        ontology = onto_data['ontology']
        query_texts = onto_data.get('query_texts', [])
        fields = onto_data.get('fields', [])
        
        # Retrieve candidates
        candidates = retrieve_mock(
            ontology=ontology,
            query_texts=query_texts,
            top_k=args.top_k,
            threshold=args.low_score_threshold
        )
        
        buckets.append({
            "ontology": ontology,
            "fields": fields,
            "query_texts": query_texts,
            "candidates": candidates
        })
    
    # Build response (matching RAGOutput structure)
    response = {
        "record_id": record_id,
        "buckets": buckets
    }
    
    # Write output
    with open(args.outfile, 'w') as f:
        json.dump(response, f, indent=2)
    
    print(f"Mock RAG: Generated {sum(len(b['candidates']) for b in buckets)} candidates for {len(buckets)} ontologies", file=sys.stderr)


if __name__ == "__main__":
    main()
