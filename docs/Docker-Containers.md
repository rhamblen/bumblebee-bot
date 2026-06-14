# Docker Containers

The stack is **8 containers** built from `docker/docker-compose.yml`, plus three shared services (Ollama, Redis, n8n) that live on the same `bumblebee_default` Docker network. All inference runs locally; nothing is sent to a cloud API.

## The 8 services

| Service | Container name | Port | GPU | Role |
|---|---|---|---|---|
| **orchestrator** | `bumblebee-orchestrator` | 5005 | — (CPU) | Routes to the right TTS, runs FFmpeg, stitches segments, serves WAVs at `/files/` |
| **f5-tts** | `f5-tts` | 5003 | RTX 3060 | Voice **cloning** from a reference clip — best quality |
| **parler-tts** | `parler-tts` | 5004 | RTX 3060 | **Described** voice synthesis — no reference clip needed |
| **coqui-tts** (XTTS v2) | `coqui-tts` | 5002 | RTX 3090 | Voice cloning — alternative to F5 |
| **chatterbox** | `chatterbox` | 5006 | RTX 3060 | Voice cloning with emotion/exaggeration control |
| **audio-converter** | `audio-converter` | 5007 | — (CPU) | Converts MP3/MP4/OGG reference clips → WAV (with caching) |
| **whisper-stt** | `whisper-stt` | 5009 | RTX 3090 | faster-whisper speech-to-text for ESP32 voice input |
| **xiaozhi-gateway** | `xiaozhi-gateway` | 5010 | — (CPU) | ESP32 WebSocket server: Opus in/out, calls Whisper + n8n |

**Shared (not in this compose, same network):** Ollama `:11434`, Redis `:6379`, n8n `:5678`.

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
| `POST` | `/speak-multi` | Render N segments, concat into one WAV (0.6s gaps) |
| `GET` | `/voices` | Serve the live `character_descriptor.json` voice table |
| `POST` | `/admin/scan-references` | Rescan `references/`, update each character's clip status, speak a confirmation |
| `GET` | `/health` | Liveness |
| `GET` | `/files/<uuid>.wav` | Static serving of rendered audio (this is what Sonos fetches) |

Key env vars (see compose): `F5_TTS_URL`, `PARLER_TTS_URL`, `COQUI_TTS_URL`, `CHATTERBOX_URL`, `AUDIO_CONVERTER_URL`, `MEDIA_DIR=/media/generated`, `PUBLIC_BASE_URL`, `DESCRIPTOR_PATH=/media/character_descriptor.json`, `REFERENCES_DIR=/media/references`.

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

**Gotcha:** Unraid won't overwrite an existing cached icon. To change an icon later, delete the stale `/var/lib/docker/unraid/images/<container>-icon.png` (or force-update the container) so it re-downloads.

See [Unraid Template](Unraid-Template.md) for one-click install.
