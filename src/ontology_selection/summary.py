"""
Run summaries printed at the end of a pipeline stage.

Author: Andrew LaPointe
Date: 2026-09-17
"""

from typing import List

from .models import ResolveOutput


def print_resolve_summary(results: List[ResolveOutput]):
    """
    Print summary statistics of the resolved proposals.

    Args:
        results: List of resolve outputs
    """
    total = len(results)
    if not total:
        print("\nNo records processed.\n")
        return

    outcomes = {}
    for r in results:
        outcomes[r.outcome] = outcomes.get(r.outcome, 0) + 1

    flagged = sum(1 for r in results if r.flags)
    abstained = sum(1 for r in results if not r.terms)

    ontology_counts = {"UBERON": 0, "MONDO": 0, "ENVO": 0}
    for r in results:
        for term in r.terms:
            ontology_counts[term.ontology] = ontology_counts.get(term.ontology, 0) + 1

    print("\n" + "=" * 60)
    print("PIPELINE SUMMARY")
    print("=" * 60)
    print(f"Total records processed: {total}")
    print(f"\nOutcomes:")
    for outcome, count in sorted(outcomes.items()):
        print(f"  {outcome}: {count} ({count/total*100:.1f}%)")
    print(f"\nRecords with flags: {flagged} ({flagged/total*100:.1f}%)")
    print(f"Records abstained: {abstained} ({abstained/total*100:.1f}%)")
    print(f"\nTerms by ontology:")
    for onto, count in sorted(ontology_counts.items()):
        print(f"  {onto}: {count}")
    print("=" * 60 + "\n")


def print_plan_summary(plans):
    """
    Print summary statistics of the generated plans.

    Args:
        plans: List of PlanOutput objects
    """
    total = len(plans)
    if not total:
        print("\nNo records processed.\n")
        return

    ontology_counts = {}
    for plan in plans:
        for mapping in plan.mappings:
            ontology_counts[mapping.ontology] = (
                ontology_counts.get(mapping.ontology, 0) + 1
            )

    empty = sum(1 for p in plans if not p.mappings)
    flagged = sum(1 for p in plans if p.flags)

    print("\n" + "=" * 60)
    print("PLAN SUMMARY")
    print("=" * 60)
    print(f"Total records planned: {total}")
    print(f"Records with no mappings: {empty} ({empty/total*100:.1f}%)")
    print(f"Records with flags: {flagged} ({flagged/total*100:.1f}%)")
    print(f"\nMappings by ontology:")
    for onto, count in sorted(ontology_counts.items()):
        print(f"  {onto}: {count}")
    print("=" * 60 + "\n")
