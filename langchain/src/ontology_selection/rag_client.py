"""
RAG Client: Wrapper for external RAG command-line program.

Converts PlanOutput → RAG CLI input → RAGOutput.
Handles subprocess management and JSON serialization.
"""

import subprocess
import json
import logging
import tempfile
from pathlib import Path
from typing import Dict, Any

from .models import PlanOutput, RAGOutput, RAGBucket, Candidate

logger = logging.getLogger(__name__)


class RAGClient:
    """
    Client for external RAG retrieval system.
    
    Wraps the rag.py command-line interface.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize RAG client.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        rag_config = config.get('rag', {})
        
        self.command = rag_config.get('command', 'python rag.py')
        self.top_k = rag_config.get('top_k', 10)
        self.model = rag_config.get('model', 'biomedbert')
        self.metric = rag_config.get('metric', 'cosine')
        self.device = rag_config.get('device', 'cpu')
        self.threshold = rag_config.get('low_score_threshold', 0.70)
        self.timeout = rag_config.get('timeout', 300)
    
    def retrieve(self, plan: PlanOutput) -> RAGOutput:
        """
        Call external RAG system for all ontologies in the plan.
        
        The RAG system supports multiple ontologies in one call (batch mode).
        
        Args:
            plan: Plan output with ontology mappings
            
        Returns:
            RAGOutput with candidate terms for each ontology
        """
        if not plan.mappings:
            logger.warning(f"No mappings in plan for {plan.record_id}")
            return RAGOutput(
                record_id=plan.record_id,
                buckets=[],
                flags=["no_mappings_in_plan"]
            )
        
        try:
            # Build RAG request
            rag_request = self._build_rag_request(plan)
            
            # Call RAG CLI
            rag_response = self._call_rag_cli(rag_request)
            
            # Convert to RAGOutput
            rag_output = self._parse_rag_response(rag_response, plan)
            
            logger.info(f"RAG for {plan.record_id}: {len(rag_output.buckets)} buckets, "
                       f"{sum(len(b.candidates) for b in rag_output.buckets)} total candidates")
            
            return rag_output
            
        except Exception as e:
            logger.error(f"RAG retrieval failed for {plan.record_id}: {e}", exc_info=True)
            # Return empty buckets with error flag
            return RAGOutput(
                record_id=plan.record_id,
                buckets=[],
                flags=[f"rag_error: {str(e)}"]
            )
    
    def _build_rag_request(self, plan: PlanOutput) -> Dict[str, Any]:
        """
        Build RAG request JSON from PlanOutput.
        
        Format:
        {
          "record_id": "SAMN123",
          "ontologies": [
            {"ontology": "UBERON", "fields": ["isolation_source"], "query_texts": ["wound", "infection"]},
            {"ontology": "MONDO", "fields": ["note"], "query_texts": ["sepsis"]}
          ]
        }
        
        Args:
            plan: Plan output
            
        Returns:
            Dictionary in RAG request format
        """
        return {
            "record_id": plan.record_id,
            "ontologies": [
                {
                    "ontology": mapping.ontology,
                    "src_fields": [sf.model_dump() for sf in mapping.src_fields],
                    "query_texts": mapping.query_texts
                }
                for mapping in plan.mappings
            ]
        }
    
    def _call_rag_cli(self, rag_request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Call external RAG CLI subprocess.
        
        Args:
            rag_request: Request dictionary
            
        Returns:
            RAG response dictionary
        """
        # Create temporary files for I/O
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as input_file:
            json.dump(rag_request, input_file, indent=2)
            input_path = input_file.name
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as output_file:
            output_path = output_file.name
        
        try:
            # Build command
            cmd = [
                *self.command.split(),
                "--input", input_path,
                "--outfile", output_path,
                "--top-k", str(self.top_k),
                "--model", self.model,
                "--metric", self.metric,
                "--device", self.device,
                "--low-score-threshold", str(self.threshold)
            ]
            
            logger.debug(f"RAG command: {' '.join(cmd)}")
            
            # Execute
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=True
            )
            
            # Log stderr (might contain progress messages)
            if result.stderr:
                logger.debug(f"RAG stderr: {result.stderr}")
            
            # Load response
            with open(output_path) as f:
                response = json.load(f)
            
            return response
            
        finally:
            # Clean up temp files
            Path(input_path).unlink(missing_ok=True)
            Path(output_path).unlink(missing_ok=True)
    
    def _parse_rag_response(self, rag_response: Dict[str, Any], plan: PlanOutput) -> RAGOutput:
        """
        Parse RAG CLI response into RAGOutput.
        
        Args:
            rag_response: Raw response from RAG CLI
            plan: Original plan (for validation and src_fields)
            
        Returns:
            RAGOutput with parsed buckets and candidates
        """
        record_id = rag_response.get('record_id', plan.record_id)
        buckets_data = rag_response.get('buckets', [])
        
        # Create mapping lookup for src_fields from plan
        plan_mapping_by_ontology = {m.ontology: m for m in plan.mappings}
        
        buckets = []
        flags = []
        
        for bucket_data in buckets_data:
            try:
                ontology = bucket_data['ontology']
                
                # Parse candidates
                candidates = [
                    Candidate(**candidate_data)
                    for candidate_data in bucket_data.get('candidates', [])
                ]
                
                # Check for empty candidates
                if not candidates:
                    flags.append(f"empty_rag_{ontology}")
                
                # Get src_fields from plan mapping (RAG response should echo them, but fallback to plan)
                src_fields_data = bucket_data.get('src_fields', [])
                from .models import SourceField
                
                if src_fields_data:
                    # Use src_fields from RAG response
                    src_fields = [SourceField(**sf) if isinstance(sf, dict) else sf for sf in src_fields_data]
                elif ontology in plan_mapping_by_ontology:
                    # Fallback: use src_fields from plan
                    src_fields = plan_mapping_by_ontology[ontology].src_fields
                else:
                    src_fields = []
                
                bucket = RAGBucket(
                    ontology=ontology,
                    src_fields=src_fields,
                    query_texts=bucket_data.get('query_texts', []),
                    candidates=candidates
                )
                
                buckets.append(bucket)
                
            except Exception as e:
                logger.error(f"Failed to parse bucket: {e}")
                flags.append(f"bucket_parse_error")
        
        return RAGOutput(
            record_id=record_id,
            buckets=buckets,
            flags=flags
        )
