# Where the code stands

**Temporary working note — not a contract.** The source of truth for structure is
`src/engine.md`; for style it is `andrew-docs/code-standars.md`. This file is a
snapshot to orient someone (including future me) and should be deleted once it stops being true.

Snapshot taken: 2026-09-17 · branch `ontology-selection` · commit `eb3b3df` · working tree clean,
pushed to origin · **351 tests passing offline**.

---

## The one-sentence version

Step **A (Plan)** of the three-step engine is built, tested and running on real data; steps **B
(Retrieve)** and **C (Resolve)** do not exist yet.

```
records.jsonl  →  A. Plan  →  [ B. Retrieve ]  →  [ C. Resolve ]
                  ^^^^^^^      not started         not started
```

Plan decides *what to search for* and *which ontologies to search* (UBERON / ENVO / MONDO). It
deliberately produces **no CURIEs** — that invariant is enforced by a test over the whole corpus.

---

## What runs today

Two entry points, both offline-capable:

```bash
PY=~/miniforge3/envs/metadata-curation/bin/python

# nested multi-source records  ->  flat engine rows
PYTHONPATH=src $PY scripts/adapt_nested_records.py --in test.json --stats --unknown-keys

# flat rows  ->  plans
PYTHONPATH=src $PY -m ontology_selection.select_ontology --no-llm \
    --in tests/fixtures/isolation_sources.jsonl --out plans.jsonl --stats
```

Plus `scripts/fetch_biosample_records.py`, which turns the accession lists in `dataset/` into real
rows via NCBI E-utilities. That is where `data/raw/records.jsonl` came from.

The full offline gate, which must stay green:

```bash
env -u ANTHROPIC_AUTH_TOKEN -u ANTHROPIC_API_KEY $PY -m pytest tests/
```

The `env -u` is not decoration: it is what guarantees the suite never reaches a gateway regardless of
what the developer's shell has set.

---

## Module map

| File | Lines | Job |
|---|---|---|
| `models.py` | 271 | The JSON contracts — `Row`, `SourceValue`, `PlanResult`, `Flag`, JSONL I/O |
| `normalize.py` | 262 | Text hygiene: casefolding, junk sentinels, clause splitting, query-text derivation |
| `lexicon.py` | 245 | Loads the rule table; matches, scores and resolves ontologies; decisiveness |
| `adapt.py` | 563 | Nested multi-source record → flat `Row`. Allowlist by key name |
| `plan.py` | 310 | Step A itself. Rules first, LLM only when the rules fall short |
| `llm.py` | 689 | Argo gateway client, prompt rendering, answer cache, the null planner |
| `checks.py` | 83 | Soft checkers — flags, never exceptions; the hard invented-CURIE guard |
| `pipeline.py` | 171 | Chunking and batching, so a 131k-row run streams |
| `select_ontology.py` | 229 | The CLI. argparse and plumbing only; every decision lives elsewhere |

Roughly 2,800 lines of source against 2,300 lines of test.

### Two data files, both curator-editable, no code in either

- `data/lexicon/isolation_source_rules.json` — the pattern rules that answer the head of the
  distribution without an LLM.
- `data/lexicon/source_key_roles.json` — which nested keys are harvested and as what role.

---

## The design decisions worth knowing before you touch it

**Rules first, LLM second.** The lexicon answers the common cases deterministically. The model is
consulted only when the rules are absent or leave most of the value unexplained, and its answer is
merged rather than obeyed. `plan_row` is pure and takes an already-resolved suggestion, so the merge
policy is testable with no client at all.

**No token, no network.** `build_planner` returns a `NullPlanLlm()` when no token is present, so the
default posture is offline. The token lives in the environment, never in code or config.

**The adapter is an allowlist by key name.** Each JSONL line is one sample and each top-level key is
a source; a source appears only when relevant, so the count varies per line (3 in `test.json`, 6+
expected). Every source is walked recursively and a leaf is kept only when its *key* matches the role
table. **A new source therefore costs zero code.** Unknown keys are ignored — and counted, which is
the part that makes the silence safe (`--unknown-keys`).

**Sources are equal peers.** Presence is the relevance signal. The table's `source_priority` orders
output deterministically so `query_texts[0]` is predictable; it is *not* a credibility ranking. Where
sources disagree, every distinct value becomes a query text and every contributing path is recorded
in `columns_used` — the disagreement is reported, not resolved in Plan.

**Roles, not sources.** A key means the same thing wherever it appears: `value` is searched, `prose`
is clause-split then searched, `host` fills `Row.host` and is *never* searched, `context` fills
`comments` for the model and is never searched.

**The golden files are reviewed, not generated.** `expected_plans.jsonl` (44 flat rows) and
`expected_nested_plans.jsonl` (6 nested) were both read line by line before freezing. A diff there
means a deliberate decision changed — read it, decide whether the new output is better, regenerate on
purpose. The flat file doubles as proof that the whole nested slice changed nothing for flat input:
its digest `558eb3bc…` has been identical since before the adapter existed.

---

## Open items

### Two things I flagged and left alone, pending a call

1. **Disagreeing sources can still be judged decisive.** Nested fixture row 4 has three sources
   saying `blood` / `nasal swab` / `nares`, and the row skips the LLM. All three do become query
   texts and all three paths land in `columns_used`, and the ontology answer is UBERON either way —
   so the "all contribute" decision is honoured. The open question is whether source disagreement
   should *by itself* force a second opinion. My reasoning for leaving it: *which* site is right is a
   Retrieve/Resolve question, not a Plan one.
2. **A lexicon rule I fixed that deserves review.** The `natural_water` rule was matching the word
   "stream" inside "blood **stream** infection" and, because it carries `env_dominates: true`, was
   pulling ENVO onto a human bloodstream sample. I added a `not_pattern` veto for
   blood/urine/mid/down/up-stream. Real fix, but it is an edit to curated data.

### Known lexicon defects, deliberately deferred

Found in a histogram over real data, none fixed:

- `env_dominates` overreach: `'Hospital patients'` → `['ENVO']`; `'blood from patients of rural
  regional hospital'` → `['UBERON','ENVO']`; `'collected on the ward'` → ENVO. All the same root
  cause, and exactly the engine.md §2 assumption already flagged as "worth a second look".
- Plain gaps: `Right-Forearm`, `Penile Swab`, `catheteruria` should reach UBERON; `vital sign monitor
  knob` should reach ENVO.

### Next, per the commit message on `eb3b3df`

- Wire up the Argo gateway properly and run the corpus with the LLM layer on, not just `--no-llm`.
- Adjust the output format.
- Then step B (Retrieve): embeddings or OLS behind a swappable backend, top-k per
  `(query_text × ontology)`.
