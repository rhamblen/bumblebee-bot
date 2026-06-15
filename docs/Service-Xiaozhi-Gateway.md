# Service: Xiaozhi Gateway

> Part of [Docker Containers](Docker-Containers.md). The bridge between ESP32 voice devices and the pipeline.

| | |
|---|---|
| **Container** | `xiaozhi-gateway` |
| **Ports** | 5010 (WebSocket) + 5011 (OTA/discovery) |
| **GPU** | — (CPU) |
| **Network** | `bumblebee_default` |
| **Source** | `docker/xiaozhi-gateway/server.py` (websockets + aiohttp) |

## Role

The gateway is the **voice front door**. An ESP32-S3 running [Xiaozhi firmware](Voice-Input-Alexa-vs-ESP32.md) (QT) streams microphone audio to it as Opus over a WebSocket; the gateway detects when the user stops talking, transcribes the utterance, runs it through the brain, and streams the spoken reply back to the device as Opus. It also answers the **OTA/discovery** request the firmware makes on boot, so a device learns where to connect without being re-flashed.

It's the device-facing counterpart to the [orchestrator](Service-Orchestrator.md): the orchestrator renders audio; the gateway gets audio *to and from the device*.

## Inputs

- **Opus audio frames** (60 ms, 16 kHz mono) over the WebSocket on **5010**, plus JSON control messages (`hello`, `listen`, `abort`).
- **OTA boot POST** from the firmware on **5011** (`/xiaozhi/ota/`), carrying `Device-Id` / `Client-Id` headers.

## Outputs

- **To Whisper:** the buffered utterance as a WAV (`POST /transcribe`).
- **To the brain:** the transcript — `POST` to `N8N_WEBHOOK_URL` (`{text, session_id}`) in production, or directly to the orchestrator's `/speak` in **test mode**.
- **To the device:** the reply streamed back as **Opus frames** (`tts:start` → frames → `tts:stop`), plus `stt` (the recognised text) and `hello` handshake messages.
- The OTA response: a `websocket` block (`OTA_WS_URL` + `OTA_WS_TOKEN`) telling the device where to stream.

## Server-side VAD (the key trick)

In Xiaozhi "auto" mode the device streams mic audio continuously and **never sends an end-of-speech signal**, so the gateway must find the utterance boundary itself. It runs **webrtcvad** on each 30 ms sub-frame:

- Once it has heard real speech (`MIN_SPEECH_MS`) followed by `SILENCE_END_MS` of quiet → **cut and transcribe**.
- A `MAX_UTTERANCE_MS` hard cap stops a noisy room running away.
- Push-to-talk devices that *do* send `listen:stop` use that instead (the `device-stop` path).

`VAD_AGGRESSIVENESS` (0–3) tunes how much non-speech is filtered. These tuning knobs are in-code defaults — see [Additional tuning variables](Docker-Containers.md#additional-tuning-variables-in-code-not-in-compose).

## Noise / hallucination guards

Two layers stop near-silent or echoey audio turning into garbage replies:

1. **Whisper-side:** `vad_filter` + `condition_on_previous_text=False` (set in [whisper-stt](Service-Whisper-STT.md)).
2. **Gateway-side `looks_like_noise()`:** rejects a transcript that's too short, one character repeated (`小小小…`, `you you you`), or mostly non-ASCII in this English pipeline.

It also **ignores mic frames while it's speaking** (the `speaking` flag) and **paces TTS frames to real time** so `tts:stop` lands at the true end of audio — otherwise the device resumes listening while its own speaker is still going and captures the playback as echo.

## Production vs test mode

| | `N8N_WEBHOOK_URL` set | blank |
|---|---|---|
| Brain | full n8n pipeline (C1 + C2/C3) | orchestrator `/speak` direct |
| Voice | mood-selected character(s) | one fixed Parler description |
| Use | normal operation | wiring/bring-up without n8n |

> **Why the webhook URL, not the LAN IP.** n8n is on Unraid's macvlan (`br0`) and the gateway is on the `bumblebee_default` bridge; Unraid blocks macvlan↔bridge on the same host, so `192.168.1.47:5678` is unreachable. `N8N_WEBHOOK_URL` is therefore set to the **public Cloudflare Tunnel** webhook, which dials outbound and works regardless. Same constraint the [Admin Console](Admin-Console.md#reaching-n8n-the-macvlan-caveat) hits for the n8n API.

## OTA / device onboarding

On boot the firmware POSTs to `/xiaozhi/ota/` (port 5011). The gateway replies with a `websocket` block (`url` = `OTA_WS_URL`, which must be **LAN-reachable by the device** — the host IP, not the docker hostname — and `token` = `OTA_WS_TOKEN`) and an empty `firmware` block (so the device attempts no firmware download). This is the basis of the **per-device, no-reflash onboarding** in the [multi-device topology](Architecture-and-Workflow.md#3-multi-device-topology-esp32-voice-io). The `Device-Id` (MAC) from the WS handshake is used as the conversation key, so Redis history persists per physical device.

> The gateway has no `GET /health`; the [Admin Console](Admin-Console-Service-Health.md) probes its OTA endpoint (`GET /xiaozhi/ota/`) as a liveness stand-in.

## Configuration

`N8N_WEBHOOK_URL`, `OTA_WS_URL`, `OTA_WS_TOKEN`, `WHISPER_URL`, `ORCHESTRATOR_URL`, and the VAD/silence tuning vars are all in the canonical [Environment variable reference](Docker-Containers.md#environment-variable-reference).
