- Scripts
- generate_embeddings.py
  ```bash
  python scripts/generate_embeddings.py \
  -i dataset/ontology/uberon_descriptions.parquet \
  -l document -m biomedbert --id-column id --text-column description \
  -o test.parquet
  ```
