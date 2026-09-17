# Implementation Status - Day 1 Complete

## ✅ What's Been Built

### Core Architecture (100% Complete)

All files have been created and the architecture is fully implemented:

```
src/ontology_selection/
├── config.yaml              ✅ Main configuration
├── .env.example             ✅ Credentials template
├── environment.yaml         ✅ Conda environment definition
├── .gitignore               ✅ Git configuration
│
├── models.py                ✅ All Pydantic schemas (Plan, RAG, Resolve, etc.)
├── utils.py                 ✅ Argo client, logging, field extraction
├── plan_agent.py            ✅ LLM-based Plan agent
├── rag_client.py            ✅ RAG CLI wrapper (subprocess interface)
├── resolve_agent.py         ✅ LLM-based Resolve agent
├── checks.py                ✅ Soft validation (flags)
├── pipeline.py              ✅ Full orchestration
├── ingest.py                ✅ BV-BRC JSON normalization
│
├── scripts/
│   ├── 00_mock_rag.py       ✅ Mock RAG for testing
│   └── 02_run_pipeline.py   ✅ Main CLI entry point
│
└── README.md                ✅ Complete documentation
```

### Features Implemented

1. **Plan → RAG → Resolve Pipeline** 
   - Sequential execution with error handling
   - Soft validation checks (flags + continue)
   - Multi-ontology support (UBERON, MONDO, ENVO)
   - Fallback logic when LLM fails

2. **Pydantic Models**
   - `RecordInput`: Normalized BV-BRC metadata
   - `PlanOutput`: Ontology mappings + query texts
   - `RAGOutput`: Candidate terms from retrieval
   - `ResolveOutput`: Final selected terms with confidence

3. **RAG Integration**
   - Subprocess wrapper for external `rag.py`
   - Supports batch ontology queries (1-3 per record)
   - Temp file I/O for CLI communication
   - Mock RAG system for testing

4. **CLI Interface**
   - `--test` mode with handcrafted records
   - `--input` for real JSONL files
   - `--limit N` for subset processing
   - `--workers N` for parallelization (config ready)
   - `--no-save` dry run mode

5. **Error Handling & Logging**
   - Structured logging to console + file
   - Exception capture with fallback strategies
   - Flag accumulation across all stages
   - Progress bars (tqdm)

---

## ⚠️ Current Blocker: Argo `max_tokens` Issue

### Problem

The Argo API requires **explicit `max_tokens`** for Anthropic models (claudesonnet5), and it must be ≤21000 for non-streaming calls. However, LangChain's `ChatOpenAI` wrapper doesn't properly pass this parameter through, causing all LLM calls to fail with:

```
Error code: 500 - {'error': {'message': 'Streaming is required for operations that may take longer than 10 minutes...'}}
```

### Root Cause

From the ANL-Argo-Quickstart README (section 6):

> **All Anthropic models**, on `/v1/chat/completions`: **You must send `max_tokens` explicitly, and keep it at 21,000 or below for non-streaming calls.** Argo reads only `max_tokens` here — `max_completion_tokens` is silently ignored.

LangChain's abstraction layer is not preserving the `max_tokens` parameter when creating the API payload.

### Attempted Fix

In `utils.py:get_argo_llm()`:

```python
# Add max_tokens if specified (required for Claude models)
if 'max_tokens' in config['llm']:
    llm_config['max_tokens'] = min(config['llm']['max_tokens'], 21000)
```

**Status:** Not working - parameter still not reaching Argo API.

### Solutions to Try Tomorrow (Day 2)

1. **Option A: Use Anthropic SDK instead of LangChain**
   - Import `anthropic` package
   - Use `/v1/messages` endpoint (native Anthropic format)
   - Guaranteed to work but requires rewriting agents

2. **Option B: Use different LLM model**
   - Try `claudehaiku45` or `gpt56sol`
   - Check if they have same `max_tokens` requirement
   - Quickest fix if other models work

3. **Option C: Use LangChain's `model_kwargs`**
   ```python
   ChatOpenAI(
       model="claudesonnet5",
       model_kwargs={"max_tokens": 4096},
       ...
   )
   ```

4. **Option D: Monkey-patch LangChain's payload builder**
   - Override `_create_message_dicts()` or `_create_chat_completion()`
   - Force `max_tokens` into the request dict

---

## 🎯 Day 2 Plan (Tomorrow)

### Morning (Priority 1)
1. ✅ Fix Argo LLM issue (try options A-D above)
2. ✅ Test full pipeline with real LLM calls
3. ✅ Integrate real RAG system (replace mock)
4. ✅ Process 10-100 real BV-BRC records

### Afternoon (Priority 2)
5. ✅ Build Web UI for human review (Streamlit)
   - Display record metadata + proposed terms
   - Accept/reject/edit actions
   - Save decisions to JSONL
6. ✅ Test UI with pipeline outputs

### Evening (if time)
7. ⚠️ Evaluation framework (vs gold standard)
8. ⚠️ Prompt tuning based on results

---

## 📋 Testing Notes

### What Works
- ✅ Project structure
- ✅ Mock RAG system (generates realistic candidates)
- ✅ Pipeline orchestration (Plan → RAG → Resolve flow)
- ✅ Fallback logic (heuristic-based when LLM fails)
- ✅ Flag accumulation and validation
- ✅ CLI argument parsing
- ✅ JSONL I/O

### What Needs Testing (After LLM Fix)
- ⏳ Plan agent LLM calls
- ⏳ Resolve agent LLM calls
- ⏳ JSON parsing from LLM responses
- ⏳ Multi-ontology term selection
- ⏳ Confidence scoring
- ⏳ Real BV-BRC data ingestion

---

## 🔧 Quick Start (For Collaborators)

### Setup
```bash
cd metadata-curation-harmonization/src/ontology_selection/

# Install dependencies
mamba env create -f environment.yaml   # or: conda env create -f environment.yaml
conda activate metadata-curation-langchain

# Set Argo credentials
cp .env.example .env
echo "ARGO_USER=ac.yourname" >> .env
```

### Test (When LLM is Fixed)
```bash
# Run on 5 test records
python scripts/02_run_pipeline.py --test

# Check outputs
cat data/out/proposals.jsonl
```

---

## 📊 Pipeline Validation (From Test Run)

Current test run results (with LLM failures, using fallbacks):

```
Total records processed: 5
Outcomes:
  insufficient_evidence: 5 (100.0%)
Records with flags: 5 (100.0%)
Records abstained: 5 (100.0%)
```

**Expected after LLM fix:**
```
Outcomes:
  proposed: 3-4 (60-80%)
  proposed_with_flags: 0-1 (0-20%)
  insufficient_evidence: 0-1 (0-20%)
```

---

## 💡 Known Issues & Workarounds

### 1. Mock RAG subprocess failures
**Issue:** `python scripts/00_mock_rag.py` returns exit code 1  
**Cause:** Using `python` instead of `python3`  
**Fix:** Update `config.yaml`:
```yaml
rag:
  command: "python3 scripts/00_mock_rag.py"
```

### 2. Pydantic warnings about `schema_extra`
**Issue:** `Valid config keys have changed in V2: 'schema_extra' has been renamed to 'json_schema_extra'`  
**Impact:** Harmless warning, doesn't affect functionality  
**Fix:** Update `models.py` line ~319:
```python
class Config:
    json_schema_extra = {...}  # Instead of schema_extra
```

### 3. ARGO_USER not set
**Issue:** Defaults to `ac.yourname` if env var missing  
**Fix:** Export in shell or add to `.env`:
```bash
export ARGO_USER=ac.curtish
```

---

## 📝 Next Files to Create (Day 2)

1. **`scripts/04_review_ui.py`** - Streamlit web UI
2. **`scripts/03_evaluate.py`** - Gold standard evaluation
3. **`tests/test_pipeline.py`** - Unit tests
4. **`notebooks/exploration.ipynb`** - Data exploration

---

## ✨ Summary

**Status:** Day 1 complete - all core infrastructure built  
**Blocker:** Argo `max_tokens` parameter not reaching API  
**Next Step:** Fix LLM integration (try Option B: different model first)  
**Timeline:** On track for 3-day hackathon delivery

---

*Last updated: 2026-09-16 17:05*
