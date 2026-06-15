# Admin Console · Workflow I/O tab

> One of the five tabs of the [Admin Console](Admin-Console.md). A live, append-only log of what actually flowed through the pipeline.

## What it shows

A **running log** — one entry per n8n run — in the same flow order as the [Service Health tab](Admin-Console-Service-Health.md): heard → mood → voices → said → output.

```
#64 · ✓ · 21:13:02 · 📟 QT · ⏱ 33.1s · LLM 3.7s
 🎤 heard    "I just landed the promotion I chased for three years!"
 🧠 mood     excited · celebrate · light
 🎭 voices   Lucille Ball [parler] · Debbie Reynolds [parler]
 💬 said     Lucille Ball: "…"  ·  Debbie Reynolds: "…"
 ⏱ steps    session 3ms · mood 0.8s · compose 2.9s · parse 4ms · synthesis 31.3s · play 0.1s
 ↳ tts      seg1 parler gen 16.0s +flt 0.8s · seg2 parler gen 31.1s +flt 0.1s · concat 76ms · ∥ parallel
 🔊 out      …d1097f8fe.wav · 2 segs · 0 skipped
```

## It's a tail, not a snapshot

n8n is itself an append-only ledger (executions have monotonic ids) and the console holds **no state** — so the panel fetches the last N traces, **polls every 8 s**, and prepends any new run (newest on top, with a `● live` indicator and a **⏸ pause** toggle). Scrollback is capped so it can run all day.

Each field is pulled straight from the execution's `runData` by node name, via `GET /api/workflow-trace`:

| Field | Source node | Workflow stage |
|---|---|---|
| 🎤 heard | `Webhook` | [Mood Classification](Workflow-Mood-Classification.md) |
| 🧠 mood | `Parse Ollama Response` | [Mood Classification](Workflow-Mood-Classification.md) |
| 🎭 voices + 💬 said | `Parse Segments` | [Composition](Workflow-Composition.md) |
| 🔊 out | `Call Orchestrator` | [Orchestration & Playback](Workflow-Orchestration-and-Playback.md) |

## Per-step latency

Two rows break down where a run's time actually goes:

- **`⏱ steps`** — wall time of each pipeline node, from n8n's per-node `executionTime` (recorded *as each node runs*, so it costs nothing extra): `session · mood · compose · parse · synthesis · play`. In practice ~90 % of a run is the **synthesis** (`Call Orchestrator`) step; the brain (mood + compose) is only a second or two.
- **`↳ tts`** — the orchestrator opens that synthesis step up *per segment*: each voice's `generate` (TTS) vs `+flt` (vintage filter) time, plus `concat`. Generation dominates; the filter and concat are sub-second. A trailing **`∥ parallel`** flags that segments were synthesised concurrently (see below).

This is the whole point of the tab: when a run takes a minute, these rows tell you it's TTS generation — not the LLM, not FFmpeg — so you know whether the lever is a faster/warmer model, more GPU, or real F5 clips.

### Concurrent synthesis

For a multi-voice run the orchestrator synthesises segments **concurrently** (`asyncio.gather`), not back-to-back. Measured win on a 2-voice run: serial-equivalent 47 s → **31 s actual (~34 % faster)**. The segments share one GPU, so it's a solid overlap rather than a full halving — the `↳ tts` row shows the individual `gen` times while the `⏱ steps` synthesis figure shows the (shorter) wall time. See [Orchestration & Playback](Workflow-Orchestration-and-Playback.md).

## What it surfaces at a glance

- **The `[f5]` / `[parler]` engine tag per voice** — clip-coverage gaps, the same signal as the [Voices tab](Admin-Console-Voices.md).
- **The latency split** — `⏱ steps` + `↳ tts` make it obvious when **synthesis**, not the brain, is the slow part, and exactly which segment/engine cost what.

## Failure flagging

A failed run is bordered red and its header reads `✕ failed at <stage>` — n8n's `lastNodeExecuted` mapped to the pipeline stage that broke, with the error message inline.

## Requirements

This tab needs `N8N_API_URL` (the tunnel base) **and** `N8N_API_KEY` set; `N8N_WORKFLOW_ID` scopes it to one workflow. See [Admin Console § Reaching n8n](Admin-Console.md#reaching-n8n-the-macvlan-caveat).

> The live C1 call currently emits three mood fields (`mood`, `response_type`, `response_register`); the log shows exactly those. Enriching C1 to the [fuller schema](Input-Metadata-Schema.md) is a separate workflow change.
