# Fixes Needed - Argo max_tokens Issue

## Problem Summary

The Argo API **requires** `max_tokens` parameter for all Anthropic models (claudesonnet5, claudehaiku45, etc.), but LangChain's `ChatOpenAI` wrapper is not correctly passing this parameter to the API.

## Error Message

```
Error code: 500 - {'error': {'message': 'Streaming is required for operations that may take longer than 10 minutes...'}}
```

This is Argo's way of saying "`max_tokens` is missing or too high (>21000)".

## What We've Tried

1. ✅ **Fixed mock RAG command**: Changed `python` → `python3` in config.yaml
2. ✅ **Switched models**: Changed from `claudesonnet5` to `claudehaiku45` 
3. ❌ **Passed max_tokens directly**: `ChatOpenAI(max_tokens=4096)` → parameter gets dropped
4. ❌ **Passed via model_kwargs**: `ChatOpenAI(model_kwargs={"max_tokens": 4096})` → still dropped

## Current Workaround

The pipeline **DOES work** using fallback heuristics when LLMs fail:
- Plan agent fallback: Maps "blood" → UBERON based on simple rules
- RAG works: Mock RAG returns candidates successfully  
- Resolve agent fallback: Picks top candidate if score > 0.8

**Result**: We got 1 correct term out of 5 test records (20% success using fallbacks only).

## Recommended Solutions (in order of priority)

### Option 1: Use GPT models instead of Claude ✅ QUICKEST FIX

```yaml
# In config.yaml
llm:
  model: gpt56terra  # or gpt56sol, gpt56luna
  temperature: 1
  # max_tokens should work correctly with GPT models
```

GPT models handle `max_tokens` differently and may work with LangChain.

### Option 2: Switch to native Anthropic SDK 🔧 BEST LONG-TERM

Replace `ChatOpenAI` with `anthropic.Anthropic` client in plan_agent.py and resolve_agent.py:

```python
from anthropic import Anthropic

client = Anthropic(
    api_key=argo_user,
    base_url="https://apps.inside.anl.gov/argoapi"  # No /v1
)

response = client.messages.create(
    model="claudehaiku45",
    max_tokens=4096,  # Native Anthropic SDK will pass this correctly
    messages=[{"role": "user", "content": "..."}]
)
```

**Pros**: Guaranteed to work, native API
**Cons**: Need to rewrite agents (no LangChain abstraction)

### Option 3: Use OpenAI Python SDK directly 🔧 MEDIUM EFFORT

```python
from openai import OpenAI

client = OpenAI(
    api_key=argo_user,
    base_url="https://apps.inside.anl.gov/argoapi/v1"
)

response = client.chat.completions.create(
    model="claudehaiku45",
    messages=[...],
    max_tokens=4096
)
```

**Pros**: More control, likely to work
**Cons**: Lose LangChain's structured output parsing

### Option 4: Monkey-patch LangChain 🐒 HACKY

Override `_generate()` method in ChatOpenAI to force `max_tokens` into the payload.

**Pros**: Keeps LangChain
**Cons**: Fragile, hard to maintain

## Immediate Action Items

1. **TODAY**: Try Option 1 (GPT model) - change 1 line in config.yaml
2. **If Option 1 fails**: Implement Option 2 (Anthropic SDK) tomorrow morning
3. **Document** which approach works for the team

## Testing Command

```bash
cd langchain/
python3 scripts/02_run_pipeline.py --test --limit 1 --no-save
```

**Expected success**: "proposed" outcome with 2-3 terms from LLM agents (not just fallback).

## Status

- ✅ Pipeline architecture: WORKING
- ✅ Mock RAG: WORKING  
- ✅ Fallback logic: WORKING
- ❌ LLM agents: BLOCKED on max_tokens issue
- ⏳ Next step: Try GPT model

---

*Last updated: 2026-09-16 22:25*
