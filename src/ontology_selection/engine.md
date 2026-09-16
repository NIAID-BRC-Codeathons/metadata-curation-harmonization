# Engine structure — plan · retrieve · resolve

**Scope (v1):** *Staphylococcus aureus* · primarily `isolation_source`  
**I/O:** JSON / JSONL inside the engine  
**This doc is the source of truth for structure.** Implementation comes later.

---

## 1. Layers

```
DATA                    ENGINE                         VALIDATION / HITL
────                    ──────                         ─────────────────
records.jsonl      →    A. Plan (mappings)         →   soft checkers (flags)
gold (later)       →    B. Retrieve (RAG top-k)    →   human review
row context        →    C. Resolve (term set)      →   score vs gold
```

- **Data** loads and normalizes rows.  
- **Engine** proposes ontology terms.  
- **Validation** checks quality and lets humans decide — it does **not** replace the engine.

Raw collaborator files (CSV, etc.) are adapted **once** into JSONL. The engine only sees the internal row.

---

## 2. Core idea: ontology ↔ field is N:M

Plan must emit a **clean mapping**, not two disconnected lists (`ontologies[]` + `fields[]`).

| Relationship | Allowed? | Example |
|---|---|---|
| 1 field → 1 ontology | Yes | `isolation_source` → ENVO only (`wastewater`) |
| 1 field → many ontologies | Yes (common) | `isolation_source` → UBERON **and** MONDO (`wound infection`) |
| many fields → 1 ontology | Yes | `isolation_source` + `body_sample_site` → UBERON |
| many fields → many ontologies | Yes | site text → UBERON; disease-ish phrase → MONDO |

**Not 1:1.** Do not assume one ontology per field or one field per ontology.

Unit of work after Plan = one **mapping entry**:

```text
(ontology, fields[], query_texts[])
```

Retrieve and Resolve both consume that same unit so the pipeline stays consistent.

---

## 3. Multi-ontology examples

| Free text | Mappings |
|---|---|
| `blood` | UBERON ← `isolation_source` |
| `wastewater` | ENVO ← `isolation_source` |
| `wound infection` | UBERON ← `isolation_source`; MONDO ← `isolation_source` |
| `nasal` + body site `swab` | UBERON ← `isolation_source`, `body_sample_site` |

Resolve may emit **several terms** (one or more per mapping). Human review can accept a subset.

---

## 4. Engine steps

### A — Plan

**Job:** Build the ontology↔field mapping for this row.

**Does:** choose which fields feed which ontologies, and which query strings to search.  
**Does not:** invent CURIEs.

### B — Retrieve

**Job:** For **each mapping entry**, run RAG → top-k candidates for that ontology (queries derived from the listed fields).
  * hypothetical command line: ```rag.py --input in.json --top-k 10 --model [biomedbert] --metric [cosine] --device gpu|cpu --low-score-threshold 0.70 --outfile [rag_results.json]```

Each candidate must carry **`curie` + `label` + `definition`** (from the ontology service / index).  
Label alone is not enough for Resolve, human review, or later checks — definition is the disambiguating text.


### C — Resolve

**Job:** From all candidate buckets, emit a **term set** (0..N terms). Each term must name its `field` + `ontology` + retrieved `term_id`, and **copy through `label` + `definition`** from the chosen candidate (do not regenerate definitions).

---

## 5. Soft checkers — flag, don’t hard-stop

| Step | Soft (continue + flag) | Hard (rare) |
|---|---|---|
| Plan | host-like text; empty query for one mapping | no usable fields at all → skip retrieve, abstain |
| Retrieve | empty top-k for one mapping; `missing_definition` on a hit (still keep candidate) | — |
| Resolve | weak confidence; missing definition on chosen term (prefer defined candidates) | **invented CURIE** → drop that term |

Flags accumulate on the object and travel to review/metrics.

---

## 6. JSON contracts (consistent end-to-end)

Shared enums:

- `ontology` ∈ `UBERON` | `ENVO` | `MONDO` (optional RAG helper: `OBI`)
- `field` ∈ row keys used for search, mainly `isolation_source` (also `body_sample_site` when present)
- `role` ∈ `primary` | `secondary` | `alternate`
- `outcome` ∈ `proposed` | `insufficient_evidence` | `proposed_with_flags`

### 6.1 Row in

```json
{
  "record_id": "1280.123",
  "isolation_source": "wound infection",
  "body_sample_site": null,
  "host": "Homo sapiens",
  "comments": [],
  "extras": {}
}
```

### 6.2 After Plan — mapping list (N:M)

Each element ties **one ontology** to **one or more fields** and the **queries** built from those fields.

```json
{
  "record_id": "1280.123",
  "mappings": [
    {
      "ontology": "UBERON",
      "fields": ["isolation_source"],
      "query_texts": ["wound infection", "wound"]
    },
    {
      "ontology": "MONDO",
      "fields": ["isolation_source"],
      "query_texts": ["wound infection", "infection"]
    }
  ],
  "flags": []
}
```

**Rules for a valid Plan object:**

1. `mappings` may be length 0 (abstain path) or ≥ 1.  
2. Each mapping has exactly one `ontology`.  
3. `fields` is a non-empty list of row field names that contributed.  
4. `query_texts` is a non-empty list (unless the whole plan is empty / skipped).  
5. Same field may appear in **multiple** mappings (N:M).  
6. Same ontology should appear **at most once** in `mappings` (merge fields/queries if needed) — keeps Retrieve keys clean.  
7. No CURIEs in Plan.

**Many fields → one ontology example:**

```json
{
  "record_id": "1280.456",
  "mappings": [
    {
      "ontology": "UBERON",
      "fields": ["isolation_source", "body_sample_site"],
      "query_texts": ["nasal", "swab", "nasal swab"]
    }
  ],
  "flags": []
}
```

### 6.3 After Retrieve — one bucket per mapping

Same cardinality and keys as Plan `mappings` (aligned by `ontology`).

```json
{
  "record_id": "1280.123",
  "buckets": [
    {
      "ontology": "UBERON",
      "fields": ["isolation_source"],
      "query_texts": ["wound infection", "wound"],
      "candidates": [
        {
          "curie": "UBERON:0002097",
          "label": "skin of body",
          "definition": "The organ covering the body that consists of dermis and epidermis.",
          "ontology": "UBERON",
          "score": 0.9,
          "rank": 0
        }
      ]
    },
    {
      "ontology": "MONDO",
      "fields": ["isolation_source"],
      "query_texts": ["wound infection", "infection"],
      "candidates": [
        {
          "curie": "MONDO:0004485",
          "label": "wound infection",
          "definition": "An infection that involves a wound.",
          "ontology": "MONDO",
          "score": 0.85,
          "rank": 0
        }
      ]
    }
  ],
  "flags": []
}
```

**Rules:**

1. Every bucket `ontology` / `fields` / `query_texts` echoes its Plan mapping.  
2. Every `candidates[].curie` is retrieved (never model-typed).  
3. Every candidate should include `definition` when the ontology API provides one; use `null` + soft flag `missing_definition` if absent — do not invent definitions.  
4. Empty `candidates` is allowed → soft flag `empty_rag_<ONTOLOGY>`; do not stop other buckets.

**Why definition here:** Resolve (LLM or rules), human review, and later consistency checks need “what this term *means*,” not only the short label (labels collide; definitions usually do not).

### 6.4 After Resolve — term set (still field-aware)

```json
{
  "record_id": "1280.123",
  "outcome": "proposed",
  "terms": [
    {
      "field": "isolation_source",
      "ontology": "UBERON",
      "term_id": "UBERON:0002097",
      "label": "skin of body",
      "definition": "The organ covering the body that consists of dermis and epidermis.",
      "role": "primary",
      "confidence": 0.8
    },
    {
      "field": "isolation_source",
      "ontology": "MONDO",
      "term_id": "MONDO:0004485",
      "label": "wound infection",
      "definition": "An infection that involves a wound.",
      "role": "secondary",
      "confidence": 0.7
    }
  ],
  "candidate_curies": ["UBERON:0002097", "MONDO:0004485"],
  "mappings": [
    {
      "ontology": "UBERON",
      "fields": ["isolation_source"],
      "query_texts": ["wound infection", "wound"]
    },
    {
      "ontology": "MONDO",
      "fields": ["isolation_source"],
      "query_texts": ["wound infection", "infection"]
    }
  ],
  "flags": [],
  "abstain_reason": null
}
```

**Rules:**

1. Each term names **`field` + `ontology` + `term_id` + `label` + `definition`**.  
2. `term_id` ∈ `candidate_curies` (hard check).  
3. `label` / `definition` are **copied from the Retrieve candidate**, not rewritten by the LLM.  
4. `field` must be one of the `fields` on the mapping for that `ontology`.  
5. `mappings` is copied through for provenance / review.  
6. `terms` may be empty only with `outcome: insufficient_evidence` and an `abstain_reason`.  
7. Multiple terms may share the same `field` (multi-ontology on one column).

---

## 7. Worked flow (one string → two ontologies)

```text
Row.isolation_source = "wound infection"

Plan.mappings =
  [ UBERON ← [isolation_source]
  , MONDO  ← [isolation_source] ]

Retrieve.buckets =
  [ UBERON top-k
  , MONDO  top-k ]

Resolve.terms =
  [ {field: isolation_source, ontology: UBERON, …}
  , {field: isolation_source, ontology: MONDO,  …} ]

Human review: accept both / one / neither
```

---

## 8. Host vs isolation_source

| Field | Ontologies | In this engine? |
|---|---|---|
| `isolation_source` | UBERON, ENVO, MONDO (+ OBI optional for RAG) | **Yes** |
| `body_sample_site` | usually UBERON / OBI as helper field in a mapping | optional input |
| `host` | NCBITaxon | **No (v1)** |

Host-like values in `isolation_source` → Plan `flags: ["looks_like_host"]`, usually empty `mappings` / abstain at Resolve. That is **not** a UBERON+MONDO mapping.

---

## 9. Package layout (when we implement)

```
engine/
  plan.py        # A → PlanResult { mappings[] }
  retrieve.py    # B → RetrieveResult { buckets[] }
  resolve.py     # C → Proposal { terms[] }
  checks.py      # soft flags + hard invented-ID guard
  pipeline.py    # A→B→C, carry mappings + flags
  models.py      # contracts in §6
```

---

## 10. Data files

| Path | Format | Role |
|---|---|---|
| `data/raw/records.jsonl` | JSONL | Working rows |
| `data/out/proposals.jsonl` | JSONL | Resolve output |
| `data/gold/` | JSON | CEDAR truth |
| `data/out/decisions.jsonl` | JSONL | Human accept / reject / edit term subset |

---

## 11. Decisions locked

1. Plan output is a **`mappings[]`** list — explicit ontology↔field links (N:M).  
2. Retrieve **`buckets[]`** align 1:1 with plan mappings (by ontology).  
3. Retrieve candidates and Resolve terms both carry **`label` + `definition`** (definition from ontology; never invented).  
4. Resolve **`terms[]`** always carry `field` + `ontology` + retrieved `term_id`.  
5. Multi-ontology on one field is normal.  
6. Soft checkers flag and continue; invented CURIEs are hard drops.  
7. JSON/JSONL only inside the engine.  
8. Host / NCBITaxon out of v1.

---

## 12. Non-goals (v1)

- Writing back to BV-BRC  
- LLM-invented gold  
- Full multi-user website  
- Replacing human review with checkers  
- Forcing 1:1 ontology↔field
