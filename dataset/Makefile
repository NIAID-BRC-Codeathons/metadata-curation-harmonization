SHELL := /bin/bash

FTP_URL    := https://ftp.ncbi.nlm.nih.gov/.baby/datafiles/.argonne/combined.jsonl.gz
FILTER     := ../scripts/filter_jsonl_by_assembly_accession.py
SELECT     := ../scripts/select_unique_field_records.py
GOLD_JSON  := curated_metadata_expanded.json

.PHONY: all clean

all: curated_combined.jsonl \
     curated_combined.head10.jsonl \
     curated_combined.unique_isolation_host.jsonl

# alt target: download all remote datasets
#
download: combined.jsonl.gz

# 1. Download combined.jsonl.gz from NCBI FTP
combined.jsonl.gz:
	curl -o $@ "$(FTP_URL)"

# 2. Decompress to combined.jsonl
combined.jsonl: combined.jsonl.gz
	gunzip -k $<

# 3. Filter to the curated subset
curated_combined.jsonl: combined.jsonl $(GOLD_JSON) $(FILTER)
	python3 $(FILTER) -a $(GOLD_JSON) -i $< -o $@

# 4a. First 10 records
curated_combined.head10.jsonl: curated_combined.jsonl
	head -10 $< > $@

# 4b. One record per unique (isolation_source, host) pair
curated_combined.unique_isolation_host.jsonl: curated_combined.jsonl $(SELECT)
	python3 $(SELECT) -i $< -o $@ -f isolation_source host

clean:
	rm -f combined.jsonl.gz combined.jsonl curated_combined.jsonl \
	      curated_combined.head10.jsonl \
	      curated_combined.unique_isolation_host.jsonl
