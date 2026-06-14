# Voice Input: Alexa → ESP32 / Xiaozhi

How do you talk to Bumblebee hands-free? We started down the **Amazon Alexa** road, hit a wall that's fundamental to Amazon's platform, and pivoted to **ESP32-S3 running Xiaozhi firmware**. Both paths are documented here so you can choose.

> You don't need either for a working bot — the pipeline is driven by a simple HTTP webhook (`POST /webhook/bumblebee {"text": "..."}`), so typing works out of the box. Voice input is the hands-free layer on top.

---

## Option A — Amazon Alexa (the original plan, **not completed**)

The idea: an Echo Dot captures your phrase via a custom Alexa skill → AWS Lambda → posts to the n8n webhook → Bumblebee plays the response.

**What works:** an Echo Dot *can* act as a voice-input front end via a custom skill + Lambda. That part is viable.

**What doesn't — and why we stopped:** the bigger goal was for Bumblebee to **play arbitrary audio** (the rendered WAV) back through the Echo. **Amazon blocks arbitrary-URL playback on Echo devices.** `play_media` with an external URL is silently rejected; Echo devices only play approved streaming services, ASK sounds, or platform TTS. So even with the skill built, you couldn't make the Echo *be Bumblebee's voice* — you'd still need a separate speaker for output.

That broke the appeal (one device that hears and speaks), so the Alexa skill was left as a **low-priority fallback input path** and never finished:
- ❌ Skill + Lambda not built
- ❌ Echo cannot be the audio output (Amazon restriction — not fixable our side)
- ✅ Sonos (via Home Assistant) remains the reliable output speaker

If you only want *input* and you're happy with Sonos for *output*, the Alexa route is still a legitimate option to build.

---

## Option B — ESP32-S3 + Xiaozhi firmware (the path we chose)

A cheap **ESP32-S3** flashed with the open **[Xiaozhi](https://github.com/78/xiaozhi-esp32)** firmware is both the **mic and the speaker** — exactly what Alexa couldn't be. It streams audio to our `xiaozhi-gateway`, which runs STT, calls the pipeline, and streams the spoken response back to the same device.

### Protocol (confirmed against current firmware)

- **One bidirectional WebSocket per device** — mic-up and TTS-down share the socket. Headers: `Authorization` (Bearer token), `Device-Id` (MAC), `Client-Id` (persistent UUID), `Protocol-Version`.
- A **`hello`** handshake negotiates Opus: 16 kHz mono up (mic), 24 kHz down (TTS), ~60 ms frames. Server replies `transport:"websocket"` + optional `session_id`.
- Device→server: `listen` (start/stop), `stt`, MCP commands. Server→device: `tts` (start/stop), `stt`, `llm` (emotion), system messages.
- On boot the device learns its server URL from an **OTA/activation endpoint** — this is the hook for zero-reflash multi-device onboarding.

### Components

| Piece | Role |
|---|---|
| `xiaozhi-gateway` (:5010) | Python WebSocket server; Opus decode/encode; calls Whisper + orchestrator/n8n |
| `whisper-stt` (:5009) | faster-whisper transcription (see [STT Options](STT-Options.md)) |
| Opus codec | `opuslib` / `pyogg` for the device audio frames |
| `/ota` endpoint *(planned)* | Device self-registration → multi-device |

### Why this won

- **One device hears *and* speaks** — the thing Amazon's platform forbids.
- **Fully local & open** — no cloud account, no per-device approval, no walled garden.
- **Scales cleanly** — the gateway can serve many devices, each with its own wake-word round trip and per-device output routing (`self` | `sonos:<entity>` | `both`).

### Status

Foundation done: Whisper container, gateway WebSocket server + `hello` handshake + Opus, and an ESP32-S3 flashed with current firmware. Remaining: split the inbound (STT) and outbound (TTS) streams into independently-validated pipelines, then add `/ota` + a device registry for multi-device. See the roadmap in the repo.

---

## Pick your path

| You want… | Use |
|---|---|
| Just to try it, no hardware | The **HTTP webhook** (type a phrase) |
| Hands-free input, Sonos output, already in Alexa's ecosystem | **Alexa skill** (input only — build it yourself) |
| One device that both listens and speaks, fully local | **ESP32-S3 + Xiaozhi** (recommended) |
