SHELL := /bin/bash

FTP_URL_V1 := https://ftp.ncbi.nlm.nih.gov/.baby/datafiles/.argonne/combined.jsonl.gz
FTP_URL_V2 := https://ftp.ncbi.nlm.nih.gov/pub/datasets/.argonne/combined.v2.jsonl.gz
FILTER     := ../scripts/filter_jsonl_by_assembly_accession.py
SELECT     := ../scripts/select_unique_field_records.py
GOLD_JSON  := curated_metadata_expanded.json

VERSIONS := v1 v2

.PHONY: all v1 v2 download clean

all: curated_assembly_accessions.tsv \
     curated_assembly_accessions.uniq.tsv \
     v1 v2

v1: v1/curated_combined.jsonl \
    v1/curated_combined.head10.jsonl \
    v1/curated_combined.unique_isolation_host.jsonl

v2: v2/curated_combined.jsonl \
    v2/curated_combined.head10.jsonl \
    v2/curated_combined.unique_isolation_host.jsonl

download: v1/combined.jsonl.gz v2/combined.jsonl.gz

# -- shared accession lists (version-independent) ---------------------------

# Extract assembly accessions from curated JSON
curated_assembly_accessions.tsv: $(GOLD_JSON)
	jq -r '["index", "assembly_accession"], (.["Assembly Accessions"] | to_entries[] | [.key, .value]) | @tsv' $< > $@

# Deduplicated assembly accessions (sorted, unique)
curated_assembly_accessions.uniq.tsv: curated_assembly_accessions.tsv
	tail -n+2 $< | cut -f2 | sort -u > $@

# -- v1: original combined.jsonl -------------------------------------------

v1/combined.jsonl.gz:
	mkdir -p v1
	curl -o $@ "$(FTP_URL_V1)"

v1/combined.jsonl: v1/combined.jsonl.gz
	gunzip -k $<

v1/curated_combined.jsonl: v1/combined.jsonl $(GOLD_JSON) $(FILTER)
	python3 $(FILTER) -a $(GOLD_JSON) -i $< -o $@

v1/curated_combined.head10.jsonl: v1/curated_combined.jsonl
	head -10 $< > $@

v1/curated_combined.unique_isolation_host.jsonl: v1/curated_combined.jsonl $(SELECT)
	python3 $(SELECT) -i $< -o $@ -f isolation_source host

# -- v2: combined.v2.jsonl -------------------------------------------------

v2/combined.jsonl.gz:
	mkdir -p v2
	curl -o $@ "$(FTP_URL_V2)"

v2/combined.jsonl: v2/combined.jsonl.gz
	gunzip -k $<

v2/curated_combined.jsonl: v2/combined.jsonl $(GOLD_JSON) $(FILTER)
	python3 $(FILTER) -a $(GOLD_JSON) -i $< -o $@

v2/curated_combined.head10.jsonl: v2/curated_combined.jsonl
	head -10 $< > $@

v2/curated_combined.unique_isolation_host.jsonl: v2/curated_combined.jsonl $(SELECT)
	python3 $(SELECT) -i $< -o $@ -f isolation_source host

# -- clean -------------------------------------------------------------------

clean:
	rm -rf v1 v2 \
	       curated_assembly_accessions.tsv curated_assembly_accessions.uniq.tsv
