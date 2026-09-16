# Data provenance

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