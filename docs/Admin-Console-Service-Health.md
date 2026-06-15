# Admin Console · Service Health tab

> One of the four tabs of the [Admin Console](Admin-Console.md). Live up/down state for every container in the pipeline.

## What it shows

A table of every service in the stack, **ordered the way a request flows through them** — so a problem shows up at the stage it actually occurs rather than alphabetically:

```
admin-console → xiaozhi-gateway → whisper-stt → n8n → redis → ollama
              → orchestrator → audio-converter → f5-tts → parler-tts → coqui-tts → chatterbox
```

Three states per row — `● up`, `● down`, `○ n/a` (not configured) — plus a **detail** column (HTTP code, `+PONG`, or the error) so a red row tells you *why*, not just *that*.

## Probes — each service the right way

Not every service answers a plain `GET /health`, so each is checked appropriately:

| Service | Probe | Healthy when |
|---|---|---|
| orchestrator, f5/parler/coqui/chatterbox, audio-converter, whisper-stt | HTTP `GET /health` | `2xx` |
| **xiaozhi-gateway** | HTTP `GET /xiaozhi/ota/` | `2xx` — the [gateway](Service-Xiaozhi-Gateway.md) has no `/health`, so its OTA endpoint stands in |
| **admin-console** | HTTP `GET /health` (self) | `2xx` |
| **ollama** | HTTP `GET /` | `2xx` ("Ollama is running") |
| **redis** | TCP `PING` | reply contains `PONG` — a real protocol check, not just an open port |
| **n8n** | HTTP `GET /healthz` | `2xx` — via the tunnel; see below |

## The n8n row caveat

n8n sits on Unraid's macvlan, unreachable from the console's bridge by LAN IP. With `N8N_API_URL` pointed at the **Cloudflare Tunnel base**, `GET /healthz` lights the row green; with it blank, the row shows `○ n/a` rather than a false `down`. Full explanation: [Admin Console § Reaching n8n](Admin-Console.md#reaching-n8n-the-macvlan-caveat).

## Probe targets

The health-probe target URLs (`OLLAMA_URL`, `REDIS_HOST`/`REDIS_PORT`, `GATEWAY_URL`, the `*_TTS_URL` set, `SELF_URL`) are console-specific code defaults — listed under [Admin Console § Configuration](Admin-Console.md#configuration).

> Like every tab, this one **lazy-loads** on first open and has its own **↻ refresh** button and "last updated" timestamp.
