# Service: Whisper STT

> Part of [Docker Containers](Docker-Containers.md). Speech-to-text for the voice-in path.

| | |
|---|---|
| **Container** | `whisper-stt` |
| **Port** | 5009 |
| **GPU** | RTX 3090 (CUDA, `float16`) |
| **Network** | `bumblebee_default` |
| **Source** | `docker/whisper-stt/server.py` (FastAPI + faster-whisper) |

## Role

A thin HTTP wrapper around **faster-whisper**. It takes an audio file and returns the transcribed text. The only caller today is the [xiaozhi-gateway](Service-Xiaozhi-Gateway.md), which posts the buffered utterance after its server-side VAD decides the user has stopped speaking. Text-only requests (curl/Sonos-less testing) skip this service entirely and POST straight to the n8n webhook.

See [STT Options](STT-Options.md) for why faster-whisper, and how to swap the model.

## Inputs

- **`POST /transcribe`** — multipart file upload (`file`), a 16 kHz mono WAV from the gateway.

## Outputs

- `{"text": "...", "language": "en"}` — the recognised text and detected/pinned language.

## API

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/transcribe` | Transcribe an uploaded audio file |
| `GET` | `/health` | Liveness + the loaded model name (`{"status":"ok","model":"base"}`) |

## Decoding settings that matter

The model is loaded once at startup onto CUDA. Each transcription runs with:

- **`vad_filter=True`** — drops non-speech before decoding, killing the silence/echo hallucinations that otherwise appear on near-silent audio.
- **`condition_on_previous_text=False`** — stops runaway repetition loops.
- **`language` pinned** (default `en`) — stops the model defaulting to another language and emitting CJK on noisy/quiet input. Blank = auto-detect.
- **`beam_size=5`.**

This is the first of the two noise guards; the gateway's `looks_like_noise()` is the second backstop. See [the gateway's guards](Service-Xiaozhi-Gateway.md#noise--hallucination-guards).

## Model trade-off

`WHISPER_MODEL` selects the faster-whisper size — `tiny` / `base` / `small` / `medium` / `large-v3` (plus `.en` English-only variants like `base.en`). Bigger = more accurate, slower, more VRAM; swapping reloads weights from `WHISPER_MODEL_DIR` (a persistent volume) on next start. Defaults and formats are in the [Environment variable reference](Docker-Containers.md#whisper-stt).
