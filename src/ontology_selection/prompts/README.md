# LLM Prompts

This directory contains system prompts for the Plan and Resolve agents, stored as markdown files for easy editing and version control.

## Files

### `plan_system.md`
System prompt for the Plan Agent.

**Purpose:** Analyzes sample metadata and decides:
- Which ontologies to query (UBERON, MONDO, ENVO)
- Which input fields are relevant
- What query texts to generate for RAG

**Used by:** `plan_agent.py`

### `resolve_system.md`
System prompt for the Resolve Agent.

**Purpose:** Selects final ontology terms from RAG candidates:
- Picks best matching terms
- Assigns roles (primary, secondary, alternate)
- Assigns confidence scores
- Can select multiple terms across ontologies

**Used by:** `resolve_agent.py`

## Editing Prompts

### To modify a prompt:

1. Edit the `.md` file directly (no code changes needed)
2. Test the changes:
   ```bash
   python3 scripts/02_run_pipeline.py --test
   ```
3. Commit the updated prompt:
   ```bash
   git add src/ontology_selection/prompts/
   git commit -m "Update Plan agent prompt: better host detection"
   ```

### Prompt Structure

Each prompt includes:
- **Role description** - What the agent does
- **Task description** - What it needs to decide
- **Guidelines** - Domain-specific rules (UBERON vs MONDO vs ENVO)
- **Output format** - JSON schema
- **Examples** - 3-4 examples showing expected behavior

### Best Practices

✅ **DO:**
- Include diverse examples (simple cases, edge cases, multi-ontology)
- Use clear, specific language
- Document expected JSON output format
- Add examples for edge cases (abstentions, flags)

❌ **DON'T:**
- Use curly braces `{` `}` in the markdown (they're auto-escaped for LangChain)
- Make prompts too long (>5000 chars becomes hard to maintain)
- Change output schema without updating `models.py`

## Technical Details

### How prompts are loaded:

```python
# In plan_agent.py or resolve_agent.py
def load_prompt(prompt_name: str) -> str:
    prompt_path = Path(__file__).parent / "prompts" / f"{prompt_name}.md"
    with open(prompt_path) as f:
        prompt_text = f.read()
    
    # Escape curly braces for LangChain's f-string template
    prompt_text = prompt_text.replace("{", "{{").replace("}", "}}")
    return prompt_text

PLAN_SYSTEM_PROMPT = load_prompt("plan_system")
```

### Why escape curly braces?

LangChain uses f-string templating for prompts. Any `{variable}` in the prompt text is treated as a template variable. Since our prompts contain JSON examples with curly braces, we need to escape them by doubling: `{` → `{{`

This happens automatically in the `load_prompt()` function.

## Prompt Versioning

When making significant prompt changes:

1. **Document the change** in git commit message
2. **Test on evaluation set** (if available)
3. **Compare before/after** metrics
4. **Consider A/B testing** for production

Example commit:
```bash
git commit -m "Improve Plan agent: add bacteria-specific ontology hints

- Add explicit UBERON examples for bacterial isolation sites
- Clarify ENVO usage for environmental samples
- Add edge case: handle 'culture' vs 'clinical sample'

Tested on 100 records: +5% mapping accuracy"
```

## See Also

- [engine.md](../../engine.md) - Pipeline architecture specification
- [models.py](../models.py) - JSON schema definitions
- [SUCCESS.md](../SUCCESS.md) - Current pipeline performance
