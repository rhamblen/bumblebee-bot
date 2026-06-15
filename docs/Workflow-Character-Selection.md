# Workflow · 2. Character Selection

> Stage 2 of 4 in the n8n workflow. Previous: [Mood Classification](Workflow-Mood-Classification.md) · Next: [Composition](Workflow-Composition.md).

This stage decides **who speaks** — it casts 1–3 characters from the roster based on the mood reading. Crucially, the random pick happens in **JavaScript inside n8n, not in the LLM**, so the variety is genuine rather than the model favouring the same "best" answer every run.

## Node involved

```
Parse Ollama Response
   filter by response_type
   → tighten by response_register
   → Fisher–Yates shuffle
   → weighted roll K = 1..3
   → build the compose prompt for the chosen voices
```

This single node consumes the C1 output and emits the cast + the prompt that the next stage runs.

## Input

- The C1 mood reading from [stage 1](Workflow-Mood-Classification.md) — primarily `response_type` and `response_register`.
- The **live voice table** (`character_descriptor.json`) read from the orchestrator's [`GET /voices`](Service-Orchestrator.md#api), so casting always reflects what's actually on disk.

## Processing — the cast algorithm

1. **Filter** the roster to characters whose `response_type[]` contains the C1 `response_type`.
2. **Tighten** by `response_register`.
3. **Shuffle** (Fisher–Yates) for real variety.
4. **Weighted roll** `K = 1–3` — `P(1)=27% / P(2)=40% / P(3)=33%`, renormalised when fewer voices match.
5. Attach each chosen character's `reference_clip` / `voice_description` / `tts_engine` **by name** and build the compose prompt.

## Output

The chosen cast (1–3 characters, each with its voice spec) plus the composition prompt, handed to [stage 3](Workflow-Composition.md).

## The rules that shape casting

A single well-cast voice is the default; extra segments must be **earned**:

- **Gravity first** — loss / genuine distress / high intensity → single voice, no levity, ever.
- **`response_register` is the gate** — `serious` & `dramatic` → single voice; `deadpan` → subtle wit only; `light` → multi-voice allowed.
- **Humour must be earned**, heavier segment first / lighter last, no repeated characters, 3 only for genuinely mixed moods.
- **Clip gate** — a character with no reference clip on disk is never offered to the cloner; it renders via Parler instead.

> **Full coverage matrix, multi-character rules, personality no-go zones, and clone-vs-described split:** [Character & Response Table](Character-Response-Table.md). This page is the workflow view; that page is the casting reference.

→ **Next:** [3. Composition (C2/C3)](Workflow-Composition.md)
