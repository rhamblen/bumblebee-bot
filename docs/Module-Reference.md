# Module Reference (C1, A2, J3 …)

Throughout this wiki, the commit history, and the working notes you'll see short
codes like **C1**, **C2/C3**, **A2**, **D2a**, **J3**, **K4**. They're not random —
they're the project's **work-stream map**. This page is the decoder.

## How the codes work

Every code is a **letter + number** (sometimes letter + letter + number):

- **The letter is a work-stream** — one subsystem or area of the build (e.g. `C` is
  "the brain", `J` is "ESP32 voice I/O", `B` is "the reference-clip library").
- **The number is a step within that work-stream** — a discrete task or component
  (e.g. `C1` is mood classification, `C2` is voice/character selection).
- **A trailing letter** marks a sub-stream or a follow-up split out later
  (e.g. `D2a` is a refinement of `D2`; `JA`/`JB`/`JC` are the three sub-streams of `J`).

So **C1** reads as *"the brain work-stream, step 1 — mood classification."*

> **Numbers are roadmap IDs, not a fixed count.** They're assigned as work is scoped,
> so a stream may stop at `C4` or `D3` — there's no `C5` or `D5`. If you're hunting a
> code that isn't on this page, it simply hasn't been scoped yet. The streams that are
> *also* live pipeline stages — **C1, C2, C3** — have their own deep-dive pages
> (linked below); the rest are roadmap/task labels.

## Work-streams at a glance

| Code | Work-stream | What it covers | Status |
|---|---|---|---|
| **A** | Raw vs filtered audio | Orchestrator A/B test of the FFmpeg vintage filter per character | Deferred |
| **B** | Reference clip library | Sourcing, trimming & converting voice clips (incl. `B7` batch tool) | Ongoing — the bottleneck |
| **C** | The brain (Ollama pipeline) | Mood → character → message. The core LLM reasoning | Live |
| **D** | n8n workflow | Webhook → Ollama → orchestrator → Sonos wiring | Complete |
| **E** | Alexa custom skill | Original *fallback* voice-input path (Echo → Lambda → webhook) | Parked |
| **F** | Coqui / XTTS housekeeping | Model-persistence and compose tidy-ups for the XTTS container | Open |
| **G** | Ollama keep-alive | Keep the model resident in VRAM (`OLLAMA_KEEP_ALIVE=12h`) | Done |
| **H** | Music integration | Optional Spotify music segment in a response | Open |
| **I** | Audio converter service | The port-5007 service that converts any format → WAV for TTS | Live |
| **J** | ESP32 voice I/O | **Primary** voice path: Xiaozhi gateway, Whisper, Opus, multi-device | Live (single device) |
| **K** | Admin character pipeline | Clip → character table → the descriptor injected into C2/C3 | Live |
| **L** | GitHub write-up | This repo's README + Wiki documentation effort | Live |
| **M** | Admin Console | The operator web UI and its tabs (health, config, voices, clip capture) | Live |
| **N** | Voices description editing | Inline edit of Parler `voice_description` from the Voices tab | Built |
| **P** | QT custom display faces | Bumblebee-face GIFs + emotion-driven display → superseded by Q | → see Q |
| **Q** | ESP32 device personality | Python asset compiler + assets-partition OTA: wake word, display faces, screen theme — all pushed wirelessly, no firmware reflash | Open |

## The brain — C (the codes you'll see most)

`C` is the LLM pipeline, split into independently-testable steps. **C1–C3 are also the
live workflow stages**, so each links to its full page:

| Code | Step | Role | Live page |
|---|---|---|---|
| **C0** | Session Manager | Redis conversation history (last 5 turns, 300s TTL) so C1 has context | — |
| **C1** | Mood Interpreter | Classifies the input phrase into a structured mood JSON. No reply is written yet | [Mood Classification](Workflow-Mood-Classification.md) · [Schema](Input-Metadata-Schema.md) |
| **C2** | Voice / Character Selector | Picks 1–3 characters by `response_type` + `response_register` | [Character Selection](Workflow-Character-Selection.md) |
| **C3** | Message Generator | Writes the in-character line(s). Merged with C2 as the **Response Composer** | [Composition (C2/C3)](Workflow-Composition.md) |
| **C4** | Integration | Chains C1 → C2 → C3 end-to-end in n8n | [Architecture](Architecture-and-Workflow.md) |

> **Why C2 and C3 are written together as "C2/C3":** character selection and message
> writing have to happen as one step — you can't write a line until you know who's
> speaking — so the two were merged into the single *Response Composer* node.

## ESP32 voice I/O — J (and its JA / JB / JC sub-streams)

`J` is large enough that it's split into three sub-streams, each built and tested on
its own. See [Voice Input: Alexa → ESP32](Voice-Input-Alexa-vs-ESP32.md) and
[Service: Xiaozhi Gateway](Service-Xiaozhi-Gateway.md).

| Code | Sub-stream | What it covers |
|---|---|---|
| **J1–J5** | Foundation | Whisper container, gateway WS server, Opus codec, firmware flash |
| **JA** | Inbound STT | Mic → Opus → VAD → Whisper → text → webhook (`session_id = device MAC`) |
| **JB** | Outbound TTS | WAV → Opus frames → streamed to the device speaker, with per-segment failure guard |
| **JC** | Join & multi-device | Device registry, `/ota` self-onboarding, per-device output routing |
| **J7** | Wake word | → superseded by **Q** (assets-partition OTA, no reflash) |

## ESP32 device personality — Q

`Q` covers everything that makes the physical ESP32 device feel like Bumblebee rather than a
generic voice assistant. Wake word, display faces, and screen theme are all packed into the same
`assets` partition binary and pushed to the device wirelessly — no USB, no firmware reflash.
See [ESP32 Assets OTA](ESP32-Assets-OTA.md) for the full binary format and push mechanism.

| Code | Step | What it covers |
|---|---|---|
| **Q1** | Model extraction | Copy pre-compiled ESP-SR model binaries out of the xiaozhi-assets-generator repo |
| **Q2** | Python asset compiler | `srmodels_bin.py` + `assets_bin.py` + `image_conv.py` + `build.py` + `config.yaml` |
| **Q3** | First assets.bin | Produce and hex-verify the binary before touching any device |
| **Q4** | Gateway: serve binary | `GET /assets/current.bin` on port 5011; `OTA_HOST` env var |
| **Q5** | Gateway: MCP send | `CONNECTED` dict + `send_mcp_command()` coroutine |
| **Q6** | Gateway: push endpoint | `POST /clients/{mac}/push-assets` → `set_download_url` + `reboot` MCP commands |
| **Q7** | Admin console button | "Push Assets" on each Devices-tab row; polls until device reconnects |
| **Q8** | Phase 1 test | Wake word live on device via one button click |
| **Q9** | Bumblebee face GIFs | Idle, listening, speaking, thinking, happy, sad — 240×240 ST7789 |
| **Q10** | Faces in compiler | Emoji set added to `config.yaml`; same binary push carries wake word + faces |
| **Q11** | Emotion forwarding | Gateway forwards `emotion` from brain reply to device display |
| **Q12** | Phase 2 test | Face transitions live end-to-end |
| **Q13** | Custom wake word | "Hey Bee" via MultiNet `mn7_en` + FST compilation (Phase 3, non-trivial) |

## Reading a code in the wild

- **C1** → brain, step 1 → mood classification.
- **A2** → audio A/B stream, step 2 → return both `url_raw` and `url_filtered` from `/speak`.
- **D2a** → n8n workflow, step 2, refinement *a* → the Respond node returns the WAV URL.
- **K3** → admin character pipeline, step 3 → point the C2/C3 prompt at the ready-clips descriptor.
- **JA2** → ESP32 inbound stream, step 2 → POST the transcript to n8n keyed by device MAC.

---

*The authoritative, fully-itemised task list (every numbered step and its status)
lives in the project's working notes; this page is the stable decoder for the codes
those notes, the commits, and the rest of this wiki use.*
