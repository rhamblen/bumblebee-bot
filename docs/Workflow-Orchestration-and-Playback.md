# Workflow · 4. Orchestration & Playback

> Stage 4 of 4 in the n8n workflow. Previous: [Composition](Workflow-Composition.md). Back to [Architecture & Workflow](Architecture-and-Workflow.md).

The final stage hands the render-ready segments to the [orchestrator](Service-Orchestrator.md), gets back a single audio URL, responds to the caller, and plays the audio on the target device.

## Nodes involved

```
Call Orchestrator   (POST /speak-multi)
  → Respond          (returns {status, count, characters, mood, url})
  → Play on Sonos    (Home Assistant play_media)
```

## Input

- The `segments[]` array from [stage 3](Workflow-Composition.md).

## Processing — handed to the orchestrator

*Call Orchestrator* POSTs the segments to `/speak-multi`. The orchestrator then, per segment: selects the engine (F5 if a clip exists, else Parler), synthesises, applies the **vintage filter**, and concatenates the finals into one WAV with 0.6 s gaps. A segment that fails after its retry is dropped rather than aborting the reply. Full detail: [Service: Orchestrator](Service-Orchestrator.md#how-a-multi-segment-request-is-rendered).

## Output

- A single **WAV URL** plus metadata: `{status, count, characters, mood, url}` and the per-segment **timings** that feed the [Workflow I/O log](Admin-Console-Workflow-IO.md).
- The audio played on **Sonos** (via Home Assistant `play_media`) and/or streamed back to the **ESP32** by the gateway.

## The Respond-before-Play ordering (deliberate)

```
… → Call Orchestrator → Respond → Play on Sonos
```

`Respond` is placed **before** `Play on Sonos` on purpose: Home Assistant's `play_media` returns an empty array, which would otherwise stop the workflow before the webhook ever responded. `RespondToWebhook` passes its input through, so Sonos still receives the `url` and the HTTP caller still gets its response.

## Engine routing & fallback (recap)

Each segment is tagged `f5` (a reference clip exists) or `parler` (no clip → described voice). If an `f5` clip is missing at render time, the orchestrator **auto-falls back to Parler** instead of erroring — so casting never hard-fails on a missing file. See the [clip gate](Character-Response-Table.md#the-clip-gate-important).

← Back to the [pipeline overview](Architecture-and-Workflow.md)
