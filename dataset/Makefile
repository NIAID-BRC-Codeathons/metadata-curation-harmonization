SHELL := /bin/bash

FTP_URL   := https://ftp.ncbi.nlm.nih.gov/.baby/datafiles/.argonne/combined.jsonl.gz
FILTER    := ../scripts/filter_jsonl_by_assembly_accession.py
GOLD_JSON := curated_metadata_expanded.json

.PHONY: all clean

all: curated_combined.jsonl

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

clean:
	rm -f combined.jsonl.gz combined.jsonl curated_combined.jsonl
