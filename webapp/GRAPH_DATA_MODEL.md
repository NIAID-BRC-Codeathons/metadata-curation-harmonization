# Relationship graph: MVP scope and evidence

The Three-day MVP in `data/out/project_description.md` calls for studies, samples, sequences, publications, organisms, diseases, repositories, and NIAID programs. The repository and existing NCBI import support a useful partial implementation today. The graph is a per-dataset projection of original SQLite records. It never silently merges separate datasets or changes the imported JSON.

## Sources inspected

- The existing NCBI v2 dataset has 167,642 imported records. In a check of its first 25 records, extraction found 19 study identifiers, 23 sample identifiers, 70 sequence/assembly identifiers, 3 publications, 1 organism, 10 reported disease labels, and 8 repositories (134 entities and 327 relationships). These are counts for that selection, not a census. Multiple identifiers can refer to one biological entity, for example paired assemblies or BioSample/SRA aliases.
- The flat BV-BRC dataset also supports explicit accession and publication relationships. Literal `genome.*` keys are handled independently from nested NCBI objects.
- `data/raw/records.jsonl` contains 200 source records. `data/out/proposals.jsonl` contains 200 proposals with matching IDs; 53 records contain MONDO proposed terms. Across those records there are 73 sample-to-proposed-term links (some records have multiple terms). The convenience importer preserves input and proposal objects together so organisms and original disease fields remain available. These files chiefly support samples, organisms, diseases, and repository references.
- No explicit NIAID program assignments were found in the inspected NCBI subset or those two repository files. Program completeness cannot be claimed for the full NCBI import. The graph displays program nodes only when the examined metadata explicitly supplies them.
- `dataset/sample.input.jsonl` is handcrafted example data, not evidence for additional actual relationships. It is not automatically imported. The separate curated columnar JSON is also not silently joined to the NCBI data: that would require a defined identity mapping and review policy.

## Extraction rules

| Entity/relationship | Accepted evidence |
| --- | --- |
| Studies | Explicit BioProject or SRA study accessions. Assembly/sample project fields and SRA `study_accession` establish membership. |
| Samples | BioSample/SRA sample accessions. Assembly biosample fields and SRA `sample_accession` establish sequence-to-sample links. Explicit SRA aliases establish alternate identifiers. |
| Sequences | Assembly accessions, SRA experiment/run accessions, and recognized nucleotide accessions in sequence fields. Explicit paired assemblies and run-to-experiment references are linked. Raw sequence bases are not required. |
| Publications | Numeric PubMed IDs or recognized DOI values in publication fields, including BioProject publication objects. Titles, dates, and prose are not treated as IDs. |
| Organisms | Explicit organism taxonomy IDs, with organism names as labels; a name-only fallback is kept separate. Host taxonomy is not substituted for pathogen taxonomy. |
| Reported diseases | Explicit `disease`, `host_disease`, `hostDisease`, and corresponding BioSample attribute values. Missing-value sentinels are ignored. No disease is inferred from comments, titles, descriptions, or broad health-status text. |
| Proposed diseases | Same-record engine MONDO terms that occur in the proposal’s retrieved `candidate_curies`. These are unreviewed; original reported disease text remains a separate node. A retrieved candidate by itself is not a proposed or accepted annotation. |
| Repositories | Identifier namespace mappings, marked as **derived**, plus explicit BV-BRC source containers. This is not a claim of repository ownership or exclusivity. |
| NIAID programs | Explicit `niaid_program` / `niaid_programs` values on a supported identified entity. Affiliation, disease, or grant co-occurrence is insufficient. |

Each link records its source record ID, original source line number, field path, observed value, and evidence basis. Field paths identify the relevant field or containing object in the preserved source JSON. Source-record links open the complete evidence in the existing detail view. Proposed versus reported status is included in JSON exports as well as the visible line style.

## Bounded projection

The default view examines 25 rows selected by the existing table filters/search/order. Pagination traverses matching rows, not graph neighborhoods. A focused view examines one record. Entity identities are deduplicated within a view; relationships can have several supporting records. The UI shows node/edge counts, sampling scope, and truncation/oversize notices. Absent entities mean no supported evidence in the current selection, not proven absence from the full dataset.

There is no persistent graph index: extraction happens on demand with 400-node, 800-edge, 8-MiB-per-view and nested-collection limits. Existing filtering can still scan many stored values; graph bounds control payload processing and rendering, not all SQLite query costs. For a complete cross-dataset knowledge graph or multi-hop queries over all records, the next step would be persistent normalized entity/edge/evidence tables with incremental indexing and explicit identity resolution. That is outside this browsing MVP.

NIAID assignments can be added once a trustworthy entity-to-program mapping is available. Combining manually reviewed disease annotations with proposals likewise needs a defined review-status field; the current graph deliberately does not imply that engine suggestions have been approved.
