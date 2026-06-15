# Workflow · 3. Composition (C2/C3)

> Stage 3 of 4 in the n8n workflow. Previous: [Character Selection](Workflow-Character-Selection.md) · Next: [Orchestration & Playback](Workflow-Orchestration-and-Playback.md).

Now that the cast is chosen, this stage writes the **actual in-character lines** each voice will say — the second Ollama call — and packages them as render-ready segments.

## Nodes involved

```
(Parse Ollama Response built the compose prompt)
  → Ask Ollama Compose     (C2/C3 — write the in-character text)
  → Parse Segments         (attach reference_clip / voice_description / tts_engine by name)
  → Write Session          (Redis SET, TTL 300s)
```

## Input

- The chosen cast + compose prompt from [stage 2](Workflow-Character-Selection.md).
- The mood fields from [stage 1](Workflow-Mood-Classification.md) (energy/intensity/humour gate tone and whether a lighter segment is allowed).

## Processing

*Ask Ollama Compose* generates one line per cast member, in that character's voice, obeying the multi-character rules (gravity first, humour earned, ordering). This uses the **creative-prose** model (`mistral`) — distinct from the JSON-strong model used for C1/selection. *Parse Segments* then attaches each character's voice spec **by name** so the orchestrator knows exactly how to render it.

## Output

A `segments[]` array, each entry render-ready:

```json
{ "text": "We shall never surrender…",
  "tts_engine": "f5",            // or "parler"
  "reference_clip": "/media/references/churchill/clip.wav",
  "voice_description": null }
```

This is exactly the shape the orchestrator's [`/speak-multi`](Service-Orchestrator.md#api) expects.

## Session write

The turn (input + mood) is written back to Redis with a **300 s TTL** so the next phrase within the session has context. Short TTL by design — a conversation is a burst, not a permanent log.

## Models

- **C1 / selection** → `llama3.1` (structured JSON)
- **C3 composition** → `mistral` (creative prose)

See [Input Metadata Schema § Models](Input-Metadata-Schema.md#models). The per-engine `[f5]`/`[parler]` tag on each segment is what later surfaces clip-coverage gaps in the [Workflow I/O log](Admin-Console-Workflow-IO.md).

→ **Next:** [4. Orchestration & Playback](Workflow-Orchestration-and-Playback.md)
