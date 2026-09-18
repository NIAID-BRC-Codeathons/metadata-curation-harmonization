- Scripts

# Generate a vector database for a new ontology

To add a new ontology (e.g. `chebi`), place its OBO file at
`dataset/ontology/chebi.obo.gz`, then register its ID prefix (and, for
`build_vector_db.py`, its OBO filename) in the `ID_PREFIXES` dict in
`scripts/generate_term_descriptions.py` and the `OBO_FILES`/`ID_PREFIXES`
dicts in `scripts/build_vector_db.py`. OBO files can import terms from other
ontologies, so each script uses the ID prefix to pick out only the ontology's
own terms.

1. Generate term descriptions from the OBO file with `generate_term_descriptions.py`.
   Each term's name (and, with `--use-def`, its definition) is concatenated into a
   single lowercase sentence suitable for embedding. I found that using only the name
   performs better.

   ```bash
   python scripts/generate_term_descriptions.py \
   --ontology chebi \
   -o dataset/ontology/chebi_term_descriptions.parquet
   ```

2. Generate document-level embeddings for those descriptions with `generate_embeddings.py`.
   Output must land in `dataset/ontology/document_level_embeddings/<ontology>.parquet`
   so `build_vector_db.py` can find it.

   ```bash
   python scripts/generate_embeddings.py \
   -i dataset/ontology/chebi_term_descriptions.parquet \
   -l document -m biomedbert --id-column id --text-column description \
   -o dataset/ontology/document_level_embeddings/chebi.parquet
   ```

3. Build the vector database with `build_vector_db.py`. This attaches each term's
   canonical name from the OBO file to its embedding and writes an `id`/`name` +
   embedding-dimension parquet into `dataset/ontology/vector_db/`, matching the
   schema `ontology_rag.rag.OntologyVectorStore` expects.

   ```bash
   python scripts/build_vector_db.py --ontology chebi
   ```

The resulting `dataset/ontology/vector_db/chebi.parquet` is the vector database
consumed by `ontology_rag.rag.OntologyVectorStore`.
