# Docker Containers

The stack is **9 containers** built from `docker/docker-compose.yml`, plus shared services: **Ollama** and **Redis** on the same `bumblebee_default` Docker network, and **n8n** on a separate Unraid macvlan (`br0`) interface (see the networking note below). All inference runs locally; nothing is sent to a cloud API.

## The 9 services

| Service | Container name | Port | GPU | Role |
|---|---|---|---|---|
| **orchestrator** | `bumblebee-orchestrator` | 5005 | — (CPU) | Routes to the right TTS, runs FFmpeg, stitches segments, serves WAVs at `/files/` |
| **f5-tts** | `f5-tts` | 5003 | RTX 3060 | Voice **cloning** from a reference clip — best quality |
| **parler-tts** | `parler-tts` | 5004 | RTX 3060 | **Described** voice synthesis — no reference clip needed |
| **coqui-tts** (XTTS v2) | `coqui-tts` | 5002 | RTX 3090 | Voice cloning — alternative to F5 |
| **chatterbox** | `chatterbox` | 5006 | RTX 3060 | Voice cloning with emotion/exaggeration control |
| **audio-converter** | `audio-converter` | 5007 | — (CPU) | Converts MP3/MP4/OGG reference clips → WAV (with caching) |
| **whisper-stt** | `whisper-stt` | 5009 | RTX 3090 | faster-whisper speech-to-text for ESP32 voice input |
| **xiaozhi-gateway** | `xiaozhi-gateway` | 5010 (WS) + 5011 (OTA) | — (CPU) | ESP32 WebSocket server: Opus in/out, server-side VAD, calls Whisper + n8n; serves `/ota` device discovery on 5011 |
| **admin-console** | `bumblebee-admin-console` | 5012 | — (CPU) | Operator web UI: stack health (workflow order), config validation, live voice table, n8n workflow I/O — see [Admin Console](Admin-Console.md) |

**Shared services:** Ollama `:11434` and Redis `:6379` on `bumblebee_default`; **n8n `:5678` on macvlan `br0`** (LAN IP `192.168.1.47`).

> **n8n networking gotcha.** n8n is on Unraid's macvlan (`br0`), not `bumblebee_default`. A bridge container (like `xiaozhi-gateway`) **cannot reach n8n on the same host** — neither by hostname (different network) nor by LAN IP `192.168.1.47` (Unraid blocks macvlan↔bridge same-host traffic; you get `All connection attempts failed`). So the gateway's `N8N_WEBHOOK_URL` is set to the **public Cloudflare Tunnel webhook** `https://<your-tunnel-domain>/webhook/bumblebee`, which dials outbound and works regardless. Leave `N8N_WEBHOOK_URL` blank to run the gateway in orchestrator-direct **test mode** (Parler, no n8n).

### GPU split

Two NVIDIA cards, assigned via `NVIDIA_VISIBLE_DEVICES`:
- **RTX 3060 (index 0):** F5, Parler, Chatterbox (they take turns — idle when not rendering).
- **RTX 3090 (index 1):** XTTS, Whisper, and Ollama (the heavy LLM).

> The 3060 is shared by three TTS engines, so heavy concurrent use can cause contention. If you have one GPU, set every `NVIDIA_VISIBLE_DEVICES` to `0` and expect serialized rendering.

## Environment variable reference

These are the variables declared in each service's compose `environment:` block — i.e. exactly what the admin console's [**Config** tab](Admin-Console.md#config-tab-validator--env-editor) lists. All are read at container **start**, so a change applies on the next recreate.

**Editable** marks whether the Config tab can write the value: it can only do so when compose sources the variable from `.env` as `${VAR}` / `${VAR:-default}`. A bare literal (`FOO=http://x`) is shown read-only until you convert it to `${FOO:-http://x}` in compose. Secrets are masked in the UI.

### Shared connection URLs (orchestrator, gateway, admin-console)

| Variable | Meaning | Format | Default | Editable |
|---|---|---|---|---|
| `ORCHESTRATOR_URL` | Base URL of the orchestrator (gateway + admin-console call it) | `http://host:port` | `http://bumblebee-orchestrator:5005` | literal |
| `WHISPER_URL` | STT endpoint the gateway calls | `http://host:port` | `http://whisper-stt:5009` | literal |
| `F5_TTS_URL` / `PARLER_TTS_URL` / `COQUI_TTS_URL` / `CHATTERBOX_URL` | The four TTS engine endpoints the orchestrator routes to | `http://host:port` | `http://<svc>:500{3,4,2,6}` | literal |
| `AUDIO_CONVERTER_URL` | Reference-clip → WAV converter | `http://host:port` | `http://audio-converter:5007` | literal |

> These are Docker-network hostnames on `bumblebee_default`; only change them if you move a service off the network or rename it.

### xiaozhi-gateway

| Variable | Meaning | Format / values | Default | Editable |
|---|---|---|---|---|
| `N8N_WEBHOOK_URL` | Public HTTPS webhook the gateway POSTs transcribed text to (the n8n "brain"). **Blank = orchestrator-direct test mode** (Parler, no n8n). Must be the **tunnel** URL, not the LAN IP (macvlan blocks it). | `https://<tunnel>/webhook/bumblebee` or blank | *(blank)* | **yes** |
| `OTA_WS_URL` | WebSocket URL handed to the ESP32 (QT) at OTA boot so it knows where to stream audio. Must be **LAN-reachable by the device** — use the host IP, not the docker hostname. | `ws://<lan-host>:5010/xiaozhi/v1/` | `ws://192.168.1.33:5010/xiaozhi/v1/` | literal |
| `OTA_WS_TOKEN` | Shared token the device presents on the WS connection. | string (secret) | `bumblebee-test` | literal |

### whisper-stt

| Variable | Meaning | Format / values | Default | Editable |
|---|---|---|---|---|
| `WHISPER_MODEL` | faster-whisper model size. Bigger = more accurate, slower, more VRAM. Swapping reloads weights. | `tiny`/`base`/`small`/`medium`/`large-v3` (+ `.en` English-only, e.g. `base.en`) | `base` | literal |
| `WHISPER_LANGUAGE` | Pin transcription language. Blank = auto-detect. | ISO code, e.g. `en` | `en` | literal |
| `WHISPER_MODEL_DIR` | Model cache dir inside the container (persistent volume). | path | `/whisper-models` | literal |

### admin-console

| Variable | Meaning | Format / values | Default | Editable |
|---|---|---|---|---|
| `N8N_API_URL` | n8n REST API base for the Workflow I/O panel + n8n health probe. The **tunnel** base, not the LAN IP. Blank = panel/probe show `n/a`. | `https://<tunnel>` or blank | *(blank)* | **yes** |
| `N8N_API_KEY` | n8n REST API key (n8n → Settings → API). Enables the Workflow I/O panel. | string (secret) | *(blank)* | **yes** |
| `N8N_WORKFLOW_ID` | Scopes the executions shown to one workflow. Found in the n8n editor URL. | id string | `ykVWvfFBHQpaC2h3` | **yes** |
| `COMPOSE_PATH` / `ENV_PATH` | Internal — where the console reads compose / writes `.env`. Don't change. | path | `/srv/docker-compose.yml`, `/srv/.env` | literal |

### orchestrator

| Variable | Meaning | Format / values | Default | Editable |
|---|---|---|---|---|
| `PUBLIC_BASE_URL` | Externally-reachable base URL players (Sonos) use to **fetch rendered audio**. Must be LAN-reachable by the playback device. | `http://<lan-host>:5005/files` | `http://192.168.1.33:5005/files` | literal |
| `MEDIA_DIR` | Where rendered WAVs are written. | path | `/media/generated` | literal |
| `DESCRIPTOR_PATH` | The live character/voice table. | path | `/media/character_descriptor.json` | literal |
| `REFERENCES_DIR` | Reference clips dir. | path | `/media/references` | literal |

### TTS engines (f5-tts, parler-tts, coqui-tts, chatterbox)

| Variable | Meaning | Format / values | Default | Editable |
|---|---|---|---|---|
| `NVIDIA_VISIBLE_DEVICES` | GPU index(es) exposed to the container — the one value worth re-tuning. | `0` (3060) / `1` (3090) / `0,1` / `all` | per [GPU split](#gpu-split) | literal |
| `HF_HOME` (f5/parler/chatterbox) / `COQUI_TTS_HOME` (coqui) | Model cache dir (persistent volume). | path | `/tts-models` | literal |
| `COQUI_TOS_AGREED` (coqui) | Accept the XTTS license non-interactively — required for the model to load. | `1` | `1` | literal |

> **audio-converter** has no env vars.

### Additional tuning variables (in code, **not** in compose)

These are read by the services with sensible defaults but aren't declared in `docker-compose.yml`, so they **don't appear in the Config tab** yet. To surface/edit one, add it to that service's compose `environment:` as `${VAR:-default}`.

| Service | Variable | Default | Meaning |
|---|---|---|---|
| xiaozhi-gateway | `VAD_AGGRESSIVENESS` | `2` | webrtcvad level 0–3; higher = filters more non-speech |
| | `SILENCE_END_MS` | `800` | trailing silence that ends an utterance |
| | `MIN_SPEECH_MS` | `300` | ignore blips shorter than this |
| | `MAX_UTTERANCE_MS` | `15000` | hard cap so a noisy room can't run away |
| | `OTA_PORT` | `5011` | OTA/discovery HTTP port |
| orchestrator | `TTS_RETRIES` | `1` | extra attempts after a transient TTS failure |
| | `TTS_RETRY_BACKOFF` | `1.5` | seconds between attempts |

## The orchestrator API

FastAPI. Key routes:

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/speak` | Render one segment → filtered WAV, return URL |
| `POST` | `/speak-multi` | Render N segments, concat into one WAV (0.6s gaps). Each engine call retries once on a transient failure; a segment that still fails is skipped (dropped from the concat) so one hiccup never silences the reply — only `502`s if every segment fails |
| `GET` | `/voices` | Serve the live `character_descriptor.json` voice table |
| `POST` | `/admin/scan-references` | Rescan `references/`, update each character's clip status, speak a confirmation |
| `GET` | `/health` | Liveness |
| `GET` | `/files/<uuid>.wav` | Static serving of rendered audio (this is what Sonos fetches) |

Env vars are documented in the [Environment variable reference](#environment-variable-reference) above.

## Building locally

```bash
cd docker
docker compose build          # build all images
docker compose up -d           # start the stack
docker compose up -d --build orchestrator   # rebuild a single service
```

All services mount the shared media share at `/media` (host `/mnt/user/media/bumblebee`), which holds `references/`, `generated/`, and `character_descriptor.json`. Model weights persist to per-service `appdata` volumes so they survive rebuilds.

## Publishing images

> Images are kept **private** while the repo is private. Flip to public alongside the repo.

Tag and push to a registry — GHCR (ties to this repo) or Docker Hub:

```bash
# GHCR example
echo $GHCR_PAT | docker login ghcr.io -u rhamblen --password-stdin
docker tag f5-tts ghcr.io/rhamblen/bumblebee-f5-tts:latest
docker push ghcr.io/rhamblen/bumblebee-f5-tts:latest
# repeat per service, then reference the pushed images in a compose override
```

## Custom Unraid icons

Unraid does **not** auto-detect icons by filename. Each container needs the `net.unraid.docker.icon` **label** (already in the compose):

```yaml
labels:
  net.unraid.docker.icon: "http://192.168.1.33:5005/files/icons/bumblebee-<svc>-icon.png"
```

How it works:
1. Master PNGs live in `docker/bumblebee-<svc>-icon.png` (filename = image name).
2. They're hosted via the orchestrator's static server (copied to `media/bumblebee/generated/icons/`), reachable at the label URL.
3. On `docker compose up -d --build`, Unraid fetches each label URL and caches it.

**Gotcha:** Unraid won't overwrite an existing cached icon, and it keeps **two** copies — the download cache at `/var/lib/docker/unraid/images/<container>-icon.png` *and* the copy the GUI actually serves at `/usr/local/emhttp/state/plugins/dynamix.docker.manager/images/<container>-icon.png`. Because of this, simply replacing the source PNG (or even clearing the cache) often leaves a stale icon on screen: the served copy is never refreshed. The cache filename uses the **container** name, not the image name (e.g. `chatterbox-icon.png`, but `bumblebee-orchestrator-icon.png`).

**`docker/_admin.sh` — the icon-refresh script.** To make icon swaps reliable, this script `curl`s each icon from the orchestrator's static server and writes it **straight into both directories**, bypassing Unraid's won't-overwrite logic entirely — so you never have to hunt down and delete stale cache files. It runs host-side via a one-time Unraid **User Scripts** entry named `bumblebee_admin` whose body is just:

```bash
#!/bin/bash
bash /mnt/user/appdata/bumblebee-docker/_admin.sh
```

To change an icon: update `docker/bumblebee-<svc>-icon.png`, copy it to `media/bumblebee/generated/icons/` (what the label URL serves), run the `bumblebee_admin` script, then hard-refresh the Docker page (Ctrl+F5) to flush the browser image cache.

See [Unraid Template](Unraid-Template.md) for one-click install.
