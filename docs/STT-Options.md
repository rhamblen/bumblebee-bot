# STT Options (Speech-to-Text)

Bumblebee needs STT only on the **voice-in** path (ESP32 → gateway). Typed input skips it entirely. This page covers what we chose, why, and how to swap it.

## What we use: faster-whisper (CUDA)

The `whisper-stt` container runs **[faster-whisper](https://github.com/SYSTRAN/faster-whisper)** — a CTranslate2 reimplementation of OpenAI Whisper — on the RTX 3090.

- **Model:** `base` (set via `WHISPER_MODEL`). Good accuracy for short commands, low latency, small VRAM.
- **Why faster-whisper over vanilla Whisper:** 2–4× faster and lower memory for the same model, which matters when it shares a GPU with XTTS and Ollama.
- **Why local at all:** the whole project keeps inference on-box — no audio leaves the network.

It exposes a small HTTP service on `:5009`; the `xiaozhi-gateway` decodes Opus from the device to 16 kHz mono PCM and posts it for transcription.

## Choosing a model size

| Model | VRAM | Speed | Accuracy | Use when |
|---|---|---|---|---|
| `tiny` / `base` | ~1 GB | fastest | good for short commands | shared GPU, wake-word phrases |
| `small` / `medium` | 2–5 GB | moderate | better on accents/noise | dedicated GPU headroom |
| `large-v3` | ~10 GB | slowest | best | accuracy-critical, spare GPU |

Change it by setting `WHISPER_MODEL` and restarting the container — weights download to the mounted `/whisper-models` volume.

## Swapping in a different STT engine

The gateway only needs "audio in → text out," so any of these can replace `whisper-stt` behind the same interface:

- **whisper.cpp** — CPU-friendly, no CUDA required; good for low-power hosts.
- **Vosk** — lightweight, fully offline, streaming-capable; lower accuracy than Whisper.
- **NVIDIA Parakeet / Canary (NeMo)** — very fast and accurate on NVIDIA hardware; heavier setup.
- **A cloud API** (Deepgram, OpenAI, etc.) — easiest, but breaks the "all local" principle and adds per-use cost.

To swap: point `WHISPER_URL` at the new service (or reimplement the transcription call in the gateway) and keep the `{text, device_id}` output shape the n8n webhook expects.

## Where on-device STT could go instead

The ESP32 + Xiaozhi firmware streams audio to the gateway for STT (server-side). On-device wake-word detection (`esp-sr`/WakeNet) handles the trigger word locally; full transcription stays on the server because the ESP32 can't run Whisper-class models. See [Voice Input: Alexa → ESP32/Xiaozhi](Voice-Input-Alexa-vs-ESP32.md).
