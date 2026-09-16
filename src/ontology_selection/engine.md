# Engine structure — plan · retrieve · resolve

**Scope (v1):** *Staphylococcus aureus* · `isolation_source`  
**I/O:** JSON / JSONL inside the engine  
**This doc is the source of truth for structure.** Implementation details (rules, stubs, OLS, Argo) come later.

---

## 1. Layers

```
DATA                    ENGINE                         VALIDATION / HITL
────                    ──────                         ─────────────────
records.jsonl      →    A. Plan (what to search)   →   soft checkers (flags)
gold (later)       →    B. Retrieve (RAG top-k)    →   human review
row context        →    C. Resolve (final terms)   →   score vs gold
```

- **Data** loads and normalizes rows.  
- **Engine** proposes ontology terms.  
- **Validation** checks quality and lets humans decide — it does **not** replace the engine.

Raw collaborator files (CSV, etc.) are adapted **once** into JSONL. The engine only sees the internal row.

---

## 2. Multi-ontology is normal (not an edge case)

CEDAR gold uses **UBERON / ENVO / MONDO**. Many real `isolation_source` strings are **not** “one ontology only.”

| Example free text | Likely ontologies | Why |
|---|---|---|
| `blood` | UBERON | Pure anatomy |
| `wastewater` | ENVO | Pure environment |
| `wound infection` | **UBERON + MONDO** | Site + disease context |
| `abscess drainage` | **UBERON + MONDO** | Anatomy + clinical condition |
| `hospital wastewater` | ENVO (+ maybe UBERON if “hospital” is weak) | Environment-first |

So **UBERON + MONDO at the same time is expected**, not a bug.

### What we do when more than one ontology applies

1. **Plan** may name **1–N ontologies** (typically 1–3). Do not force a single winner early.  
2. **Retrieve** runs **in parallel per ontology** → separate top-k lists.  
3. **Resolve** emits a **term set**, not only one CURIE:
   - `terms: [{ontology, term_id, label, role}, …]`
   - `role` ∈ `primary` | `secondary` | `alternate`
4. **Gold / eval** can match:
   - exact single-term gold, or  
   - partial credit if gold has one term and we proposed that term among several, or  
   - set overlap when gold itself has multiple mappings.

**Do not** collapse “wound infection” into only UBERON *or* only MONDO in the engine output if both are supported. Prefer listing both; let the **human review** drop one if CEDAR policy says “one primary ontology per row.”

**Policy for review UI (later):** show all proposed terms; curator can accept a subset. That matches “likely both.”

---

## 3. Engine steps (structure only)

### A — Plan

**Job:** From one row (+ optional context), decide *search intent*.

**Inputs:** `isolation_source`, optional `body_sample_site`, comments, host (for “misfiled host” detection only).

**Outputs:**
- `query_texts[]` — strings to embed / search  
- `ontologies[]` — which stores to query (**may be multiple**)  
- `columns_used[]`  
- optional `notes` / soft flags (e.g. `looks_like_host`)

Plan does **not** invent CURIEs. It only chooses *where and what to search*.

### B — Retrieve (RAG)

**Job:** For each `(query_text × ontology)`, return **top-k** candidates with real CURIEs.

```
row
 ├─ UBERON  → [c1, c2, … ck]
 ├─ MONDO   → [m1, m2, … mk]
 └─ ENVO    → […]   # only if planned
```

Invariant: every CURIE is **retrieved**, never typed by an LLM.

### C — Resolve

**Job:** Turn candidate lists into a **final answer object** for that row.

- May pick **zero** terms (abstain)  
- May pick **one** term  
- May pick **several** terms across ontologies (e.g. UBERON primary + MONDO secondary)

Resolve only chooses among retrieved CURIEs (plus abstain).

---

## 4. Soft checkers (per step) — flag, don’t hard-stop

Yes: a checker after each step makes sense.  
No: a soft failure should **not** kill the pipeline by default.

| Step | Checker asks | Soft (continue + flag) | Hard (only rare) |
|---|---|---|---|
| **A Plan** | Empty query? Host-like value? Zero ontologies? | `flag: looks_like_host`, still allow retrieve if policy says try | Row truly empty → skip retrieve, emit abstain |
| **B Retrieve** | Empty top-k for an ontology? Duplicate CURIEs? | `flag: empty_rag_UBERON`; still resolve with other ontologies | — |
| **C Resolve** | Term not in candidates? Empty terms with no abstain reason? | Prefer rewrite to abstain + flag | **Invented CURIE** → reject that term (invariant) |

**Pattern:**

```
out = step(in)
out, flags = check_step(out)
carry flags forward
always pass out to the next step unless hard-skip
```

Final record keeps `flags[]` for metrics and for the review UI (“why was this weird?”).  
Human review and gold eval are the real stop/go — not the mid-pipeline checkers.

---

## 5. JSON shapes (structure)

### Row in

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

### After Plan

```json
{
  "record_id": "1280.123",
  "query_texts": ["wound infection", "wound", "infection"],
  "ontologies": ["UBERON", "MONDO"],
  "columns_used": ["isolation_source"],
  "flags": []
}
```

### After Retrieve

```json
{
  "record_id": "1280.123",
  "by_ontology": {
    "UBERON": [{"curie": "UBERON:…", "label": "…", "score": 0.9, "rank": 0}],
    "MONDO": [{"curie": "MONDO:…", "label": "…", "score": 0.8, "rank": 0}]
  },
  "flags": []
}
```

### After Resolve (multi-term capable)

```json
{
  "record_id": "1280.123",
  "outcome": "proposed",
  "terms": [
    {
      "ontology": "UBERON",
      "term_id": "UBERON:…",
      "label": "skin of body",
      "role": "primary",
      "confidence": 0.8
    },
    {
      "ontology": "MONDO",
      "term_id": "MONDO:…",
      "label": "wound infection",
      "role": "secondary",
      "confidence": 0.7
    }
  ],
  "candidate_curies": ["UBERON:…", "MONDO:…"],
  "flags": [],
  "abstain_reason": null
}
```

`outcome`: `proposed` | `insufficient_evidence` | `proposed_with_flags`

Every `term_id` must appear in `candidate_curies`.

---

## 6. Host vs isolation_source

| Field | Ontologies | In this engine? |
|---|---|---|
| `isolation_source` | UBERON, ENVO, MONDO (+ OBI for RAG help) | **Yes** |
| `host` | NCBITaxon | **No (v1)** |

If the free text is really a host (`human`, species name), Plan should **flag** `looks_like_host`. Soft checker records it; Resolve usually **abstains** rather than inventing a body-site term. That is not the same as “run UBERON + MONDO.”

---

## 7. Package layout (when we implement)

Logical modules — names can match this doc:

```
engine/
  plan.py        # A
  retrieve.py    # B (RAG)
  resolve.py     # C
  checks.py      # soft checkers per step + hard invented-ID guard
  pipeline.py    # wire A→B→C, accumulate flags
  models.py      # JSON contracts above
```

No dependency on fake lexicons in the **design**. Retrieval backends (OLS, embeddings, stubs) are swappable behind `retrieve`.

---

## 8. Data files

| Path | Format | Role |
|---|---|---|
| `data/raw/records.jsonl` | JSONL | Working rows |
| `data/out/proposals.jsonl` | JSONL | Engine output (multi-term OK) |
| `data/gold/` | JSON (CSV adapted if needed) | CEDAR truth |
| `data/out/decisions.jsonl` | JSONL | Human accept / reject / edit subset of terms |

---

## 9. Decisions locked by this doc

1. Engine steps are **Plan → Retrieve → Resolve** (not “single ontology forever”).  
2. **Multiple ontologies per row are first-class** (UBERON + MONDO is expected).  
3. Output is a **term set** with roles; review can accept a subset.  
4. **Per-step checkers are soft** (flags + continue); only invented CURIEs are hard rejects.  
5. **JSON/JSONL** is the engine language.  
6. Host / NCBITaxon stays outside v1.

---

## 10. Non-goals (v1)

- Writing back to BV-BRC  
- LLM-invented gold  
- Full multi-user website  
- Replacing human review with checkers
