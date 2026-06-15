# 🐝 Bumblebee Bot — Project Wiki

> An alien who lost his voice box. He can only answer you by replaying snippets he's intercepted from Earth's old radio and TV broadcasts — cloned into the right voice and run through a vintage filter so it sounds like a recording pulled out of the static.

You type or speak a phrase. Bumblebee reads your mood, "tunes" to the closest character in his memory of 1950s–70s broadcasting, and plays back a short, period-authentic clip *as if he found exactly the right channel*. When he misreads you, he plays the wrong channel — and that's part of the joke.

This wiki is the full write-up of how the system is built: a local, GPU-accelerated voice pipeline running on Unraid + Docker, orchestrated with n8n and Ollama, with ESP32 (Xiaozhi) devices for hands-free voice in and out.

---

## Start here

- **[Concept & Lore](Concept-and-Lore)** — what Bumblebee is, the canon behind it, and the design rules that follow
- **[Module Reference](Module-Reference)** — the decoder for the build's short codes (C1, C2/C3, A2, J3, K4 …): a letter is a work-stream, a number is a step within it
- **[Architecture & Workflow](Architecture-and-Workflow)** — the diagrams: system layout, request lifecycle, and multi-device topology. The n8n workflow is broken out stage by stage: [mood](Workflow-Mood-Classification) → [casting](Workflow-Character-Selection) → [composition](Workflow-Composition) → [orchestration & playback](Workflow-Orchestration-and-Playback)

## Build & deploy

- **[Install Guide (for Claude Code)](Install-Guide-for-Claude-Code)** — the ordered end-to-end runbook for a fresh cloner: an agent-oriented install path with verification gates, links the reference pages together
- **[Docker Containers](Docker-Containers)** — the 9 services, building, publishing, and custom Unraid icons. Per-service deep dives: [Orchestrator](Service-Orchestrator), [Gateway](Service-Xiaozhi-Gateway), [Whisper STT](Service-Whisper-STT), [Audio Converter](Service-Audio-Converter)
- **[Unraid Template](Unraid-Template)** — install on Unraid, the shared Docker network, env vars, and volumes

## Operate

- **[Admin Console](Admin-Console)** — the operator web UI; one page per tab: [Service Health](Admin-Console-Service-Health), [Config](Admin-Console-Config), [Voices](Admin-Console-Voices), [Workflow I/O](Admin-Console-Workflow-IO)

## Voice in / out

- **[STT Options](STT-Options)** — speech-to-text choices (faster-whisper) and how to swap them
- **[TTS Options](TTS-Options)** — the four TTS engines compared, and when to use each
- **[Voice Input: Alexa → ESP32 / Xiaozhi](Voice-Input-Alexa-vs-ESP32)** — what we tried with Alexa, why it didn't fit, and the move to ESP32

## How the brain decides

- **[Input Metadata Schema](Input-Metadata-Schema)** — the mood fields the LLM extracts from your phrase and what each one drives
- **[Character & Response Table](Character-Response-Table)** — how a voice (or 1–3 voices) is chosen by mood

---

## At a glance

| | |
|---|---|
| **Inference** | All local — Ollama (LLM) + 4 TTS engines on 2× NVIDIA GPUs |
| **Orchestration** | n8n workflow → Python/FastAPI orchestrator → FFmpeg vintage filter |
| **Voice in** | ESP32-S3 + Xiaozhi → gateway → Whisper (or just POST text to the webhook) |
| **Audio out** | Sonos via Home Assistant, and/or the ESP32 speaker |
| **Host** | Unraid + Docker |

## Status

The core pipeline works end-to-end: text/voice in → mood read → 1–3 character voices → vintage filter → played on Sonos. The main frontier is sourcing more reference clips (more clips = richer voice cloning) and finishing the multi-device ESP32 voice path.

> This wiki is kept in sync with the repository — it mirrors the `docs/` folder, and is updated alongside functional changes so it always reflects the project's true current state. See the repo's `CHANGELOG.md` for version history.
