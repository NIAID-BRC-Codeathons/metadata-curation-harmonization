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

Extract assembly IDs from curated dataset:
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

## Makefile — derived datasets

Run `make` in this directory to download, decompress, and build all derived
datasets. The combined dataset has two versions, stored in `v1/` and `v2/`
subdirectories:

- **v1** — original combined.jsonl from `.baby/datafiles/.argonne/`
- **v2** — combined.v2.jsonl from `pub/datasets/.argonne/`

```bash
make          # build both v1 and v2
make v1       # build only v1
make v2       # build only v2
make download # download both without processing
```

### Shared files (version-independent)

| File | Records | Description |
|------|---------|-------------|
| `curated_assembly_accessions.tsv` | 3,078 | Assembly accessions extracted from `curated_metadata_expanded.json` via `jq` |
| `curated_assembly_accessions.uniq.tsv` | 2,634 | Deduplicated assembly accessions (sorted, unique) |

### Per-version files (in `v1/` and `v2/`)

| File | v1 records | Description |
|------|------------|-------------|
| `combined.jsonl.gz` | 158,339 | Downloaded from NCBI FTP |
| `combined.jsonl` | 158,339 | Uncompressed copy |
| `curated_combined.jsonl` | ~5,211 | Filtered to assembly accessions present in `curated_metadata_expanded.json` |
| `curated_combined.head10.jsonl` | 10 | First 10 records from the curated subset (quick smoke tests) |
| `curated_combined.unique_isolation_host.jsonl` | ~309 | One record per unique `(isolation_source, host)` pair; ties broken by alphabetically-first assembly accession |

### Scripts

Scripts used by the Makefile live in `../scripts/`:

- **`filter_jsonl_by_assembly_accession.py`** — filters JSONL to records
  matching a list of assembly accessions. Accepts either the JSON gold-standard
  file (`curated_metadata_expanded.json`) or a TSV. Matches on both
  `genome.accession` (GCF\_) and `genome.pairedAccession` (GCA\_).
- **`select_unique_field_records.py`** — selects one record per unique
  combination of named metadata fields. Supports: `isolation_source`, `host`,
  `disease`, `body_sample_site`, `geo_loc_name`, `strain`, `tissue`,
  `environment`, `note`, `collection_date`.