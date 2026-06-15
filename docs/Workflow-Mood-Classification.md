# Workflow · 1. Mood Classification (C1)

> Stage 1 of 4 in the n8n workflow. See [Architecture & Workflow](Architecture-and-Workflow.md) for the whole pipeline. Next: [Character Selection](Workflow-Character-Selection.md).

This stage turns the raw phrase into a structured **mood reading** — it doesn't write a reply yet. Everything downstream keys off its output, so it's the contract the rest of the workflow depends on.

## Nodes involved

```
Webhook
  → Read Session (Redis GET)
  → Build Ollama Request   (assemble the C1 classify prompt)
  → Ask Ollama             (C1 call)
  → Parse Ollama Response  (… continues into Character Selection)
```

## Input

- **Webhook body** — `{text, session_id}`. Text comes either straight from a curl/HTTP client or from the [xiaozhi-gateway](Service-Xiaozhi-Gateway.md) after STT. The webhook reads `body.text` (falls back to `body.phrase`).
- **Redis session** — the last few turns for this `session_id`, so the mood read is context-aware (a mood *trajectory*, not a single-shot guess).

## Processing

*Build Ollama Request* assembles the C1 prompt (current input + history) and *Ask Ollama* runs it on the JSON-strong model (`llama3.1` — reasoning models are avoided here because they "think out loud" and pollute the JSON).

## Output

A JSON mood reading. The **two fields that drive everything downstream** are `response_type` and `response_register`; the rest (energy, intensity, sentiment, humour, confidence, trajectory…) tune later choices.

```json
{ "primary_mood": "frustrated", "response_type": "motivate",
  "response_register": "serious", "confidence": 0.85, ... }
```

> **Full field list, allowed values, and what each one controls:** [Input Metadata Schema](Input-Metadata-Schema.md). That page is the canonical contract; this page is just where it sits in the flow.

## Design notes

- **Low confidence is allowed.** C1 is told to infer, not to hedge into blandness — *misreading the mood is part of the character* (Bumblebee "played the wrong channel"). See [Concept & Lore](Concept-and-Lore.md).
- The live C1 prompt currently emits the three driving fields (`mood`, `response_type`, `response_register`); enriching it to the fuller schema above is a separate workflow change — the [Workflow I/O log](Admin-Console-Workflow-IO.md) shows exactly what it emits today.

→ **Next:** [2. Character Selection](Workflow-Character-Selection.md)
