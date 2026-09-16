"""
Main pipeline: Orchestrates Plan → RAG → Resolve.

Handles sequential execution with optional parallelization.
"""

from typing import List, Dict, Any
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

from .models import RecordInput, PlanOutput, RAGOutput, ResolveOutput
from .plan_agent import PlanAgent
from .rag_client import RAGClient
from .resolve_agent import ResolveAgent
from .checks import run_checks
from .utils import save_jsonl

logger = logging.getLogger(__name__)


class OntologyPipeline:
    """
    Main pipeline orchestrator for Plan → RAG → Resolve.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize pipeline with all components.
        
        Args:
            config: Configuration dictionary
        """
        self.config = config
        
        # Initialize agents
        self.plan_agent = PlanAgent(config)
        self.rag_client = RAGClient(config)
        self.resolve_agent = ResolveAgent(config)
        
        # Parallelization settings
        self.max_workers = config.get('parallelism', {}).get('max_workers', 1)
        self.save_intermediate = config.get('output', {}).get('save_intermediate', True)
        
        logger.info(f"Pipeline initialized: max_workers={self.max_workers}")
    
    def process_record(self, record: RecordInput) -> ResolveOutput:
        """
        Process a single record through the full pipeline.
        
        Args:
            record: Input record
            
        Returns:
            ResolveOutput with final selected terms
        """
        logger.info(f"Processing {record.record_id}")
        
        # Step 1: Plan
        plan_output = self.plan_agent.plan(record)
        plan_output = run_checks(plan_output, stage="plan")
        logger.debug(f"  Plan: {len(plan_output.mappings)} mappings, flags={plan_output.flags}")
        
        # Step 2: Retrieve (RAG)
        rag_output = self.rag_client.retrieve(plan_output)
        rag_output = run_checks(rag_output, stage="retrieve")
        logger.debug(f"  RAG: {len(rag_output.buckets)} buckets, "
                    f"{sum(len(b.candidates) for b in rag_output.buckets)} candidates, "
                    f"flags={rag_output.flags}")
        
        # Step 3: Resolve
        original_metadata = record.model_dump(exclude={'extras'}, exclude_none=True)
        resolve_output = self.resolve_agent.resolve(rag_output, original_metadata)
        resolve_output = run_checks(resolve_output, stage="resolve")
        
        # Consolidate flags from all stages
        all_flags = list(set(
            plan_output.flags + rag_output.flags + resolve_output.flags
        ))
        resolve_output.flags = all_flags
        
        logger.info(f"Completed {record.record_id}: {len(resolve_output.terms)} terms, "
                   f"outcome={resolve_output.outcome}, flags={len(all_flags)}")
        
        return resolve_output
    
    def process_batch(
        self,
        records: List[RecordInput],
        save_outputs: bool = True,
        show_progress: bool = True
    ) -> List[ResolveOutput]:
        """
        Process multiple records with optional parallelization.
        
        Args:
            records: List of input records
            save_outputs: Whether to save intermediate/final outputs to disk
            show_progress: Whether to show progress bar
            
        Returns:
            List of ResolveOutput objects
        """
        logger.info(f"Processing batch of {len(records)} records (max_workers={self.max_workers})")
        
        results = []
        
        if self.max_workers == 1:
            # Sequential processing
            iterator = tqdm(records, desc="Processing records") if show_progress else records
            for record in iterator:
                result = self.process_record(record)
                results.append(result)
        else:
            # Parallel processing
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                # Submit all tasks
                future_to_record = {
                    executor.submit(self.process_record, record): record
                    for record in records
                }
                
                # Collect results with progress bar
                iterator = as_completed(future_to_record)
                if show_progress:
                    iterator = tqdm(iterator, total=len(records), desc="Processing records")
                
                for future in iterator:
                    try:
                        result = future.result()
                        results.append(result)
                    except Exception as e:
                        record = future_to_record[future]
                        logger.error(f"Failed to process {record.record_id}: {e}", exc_info=True)
                        # Add error result
                        results.append(ResolveOutput(
                            record_id=record.record_id,
                            outcome="insufficient_evidence",
                            terms=[],
                            candidate_curies=[],
                            flags=[f"pipeline_error: {str(e)}"],
                            abstain_reason=f"Pipeline error: {str(e)}"
                        ))
        
        # Save outputs if requested
        if save_outputs:
            self._save_results(results)
        
        # Print summary statistics
        self._print_summary(results)
        
        return results
    
    def _save_results(self, results: List[ResolveOutput]):
        """
        Save final results to disk.
        
        Args:
            results: List of resolve outputs
        """
        output_path = self.config.get('output', {}).get('final_proposals', 'data/out/proposals.jsonl')
        save_jsonl(results, output_path)
        logger.info(f"Saved {len(results)} results to {output_path}")
    
    def _print_summary(self, results: List[ResolveOutput]):
        """
        Print summary statistics of pipeline results.
        
        Args:
            results: List of resolve outputs
        """
        total = len(results)
        
        # Count outcomes
        outcomes = {}
        for r in results:
            outcomes[r.outcome] = outcomes.get(r.outcome, 0) + 1
        
        # Count flagged records
        flagged = sum(1 for r in results if r.flags)
        
        # Count abstentions
        abstained = sum(1 for r in results if not r.terms)
        
        # Count terms per ontology
        ontology_counts = {"UBERON": 0, "MONDO": 0, "ENVO": 0}
        for r in results:
            for term in r.terms:
                ontology_counts[term.ontology] = ontology_counts.get(term.ontology, 0) + 1
        
        # Print summary
        print("\n" + "="*60)
        print("PIPELINE SUMMARY")
        print("="*60)
        print(f"Total records processed: {total}")
        print(f"\nOutcomes:")
        for outcome, count in sorted(outcomes.items()):
            print(f"  {outcome}: {count} ({count/total*100:.1f}%)")
        print(f"\nRecords with flags: {flagged} ({flagged/total*100:.1f}%)")
        print(f"Records abstained: {abstained} ({abstained/total*100:.1f}%)")
        print(f"\nTerms by ontology:")
        for onto, count in sorted(ontology_counts.items()):
            print(f"  {onto}: {count}")
        print("="*60 + "\n")


class PipelineStageRunner:
    """
    Helper class to run individual pipeline stages for debugging/analysis.
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.plan_agent = PlanAgent(config)
        self.rag_client = RAGClient(config)
        self.resolve_agent = ResolveAgent(config)
    
    def run_plan_only(self, records: List[RecordInput]) -> List[PlanOutput]:
        """Run only the Plan stage."""
        return [self.plan_agent.plan(r) for r in records]
    
    def run_rag_only(self, plans: List[PlanOutput]) -> List[RAGOutput]:
        """Run only the RAG stage (requires Plan outputs)."""
        return [self.rag_client.retrieve(p) for p in plans]
    
    def run_resolve_only(
        self,
        rag_outputs: List[RAGOutput],
        metadata_list: List[Dict[str, Any]]
    ) -> List[ResolveOutput]:
        """Run only the Resolve stage (requires RAG outputs)."""
        return [
            self.resolve_agent.resolve(rag, meta)
            for rag, meta in zip(rag_outputs, metadata_list)
        ]
