# Data provenance

## main dataset - NCBI genome

```
datasets download genome taxon 1280 --include none
```

The assembly, biosample and bioproject accessions were extracted from the metadata file

|| Count || Dataset || Source ||
| 158,339 | Genomes | Above `datasets` command |
| ? | BV-BRC Records | Based on accession list from above command | 
| 131,185 | Biosample accessions  | Listed in genome record |
| 6,209 | Bioprojects listed in Genome | Listed in genome record |s
| 177,278 | SRA Experiments | Based on bioproject in SRA record |

## build subset of full dataset for just the curated records

Extract asembly IDs from curated dataset:
```
 jq -r '["index", "assembly_accession"],
         (.["Assembly Accessions"] | to_entries[] | [.key, .value])
         | @tsv' \
    curated_metadata_expanded.json \
    > curated_assembly_accessions.tsv
```

## sample.input.jsonl

Early prototype data to work out formats: 2 records, hand crafted

## combined.jsonl.gz

All of NCBI Genome, with bv-brc and SRA and BioProject/Sample records included, when linked

```
./pull_combined_from_ftp.sh
```