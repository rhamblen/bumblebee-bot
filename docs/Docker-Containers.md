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

Key env vars (see compose): `F5_TTS_URL`, `PARLER_TTS_URL`, `COQUI_TTS_URL`, `CHATTERBOX_URL`, `AUDIO_CONVERTER_URL`, `MEDIA_DIR=/media/generated`, `PUBLIC_BASE_URL`, `DESCRIPTOR_PATH=/media/character_descriptor.json`, `REFERENCES_DIR=/media/references`, `TTS_RETRIES=1` (extra attempts on a transient TTS failure), `TTS_RETRY_BACKOFF=1.5` (seconds between attempts).

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
