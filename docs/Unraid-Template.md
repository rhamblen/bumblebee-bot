# Unraid Template

This project was built to run on **Unraid + Docker** with NVIDIA GPUs. You can run it two ways: the **docker-compose** stack (recommended, everything at once) or **per-container Community Apps templates** (one XML per service) if you prefer the Unraid GUI.

## Prerequisites

- Unraid with the **Nvidia-Driver** plugin and `--runtime=nvidia` working
- At least one CUDA GPU (two recommended — see the [GPU split](Docker-Containers.md#gpu-split))
- **Ollama**, **Redis**, and **n8n** running and reachable
- A shared media path, e.g. `/mnt/user/media/bumblebee`, containing `references/`, `generated/`, and `character_descriptor.json`

## Recommended: compose stack

```bash
mkdir -p /mnt/user/appdata/bumblebee-docker
# copy the docker/ folder here, then:
cd /mnt/user/appdata/bumblebee-docker
docker compose up -d --build
```

> Keep the project copy of `docker-compose.yml` and the deployed `appdata` copy **in sync** — the running stack builds from the appdata copy.

## Shared Docker network

Create the network once so the bumblebee services, Ollama, Redis, and n8n can reach each other by **hostname** (never hardcode LAN IPs internally):

```bash
docker network create bumblebee_default
```

| Container | Hostname | Port |
|---|---|---|
| n8n | `n8n` | 5678 |
| redis | `redis` | 6379 |
| ollama | `ollama` | 11434 |
| bumblebee-orchestrator | `bumblebee-orchestrator` | 5005 |
| f5-tts / parler-tts / coqui-tts / chatterbox | (same names) | 5003 / 5004 / 5002 / 5006 |

## Environment variables

| Variable | Service | Example / default | Notes |
|---|---|---|---|
| `NVIDIA_VISIBLE_DEVICES` | all GPU services | `0` or `1` | Which GPU index |
| `HF_HOME` / `COQUI_TTS_HOME` / `WHISPER_MODEL_DIR` | TTS / Whisper | `/tts-models` etc. | Model cache → mounted volume so it survives rebuilds |
| `WHISPER_MODEL` | whisper-stt | `base` | faster-whisper model size |
| `F5_TTS_URL` … `CHATTERBOX_URL` | orchestrator | `http://f5-tts:5003` | Internal hostnames |
| `AUDIO_CONVERTER_URL` | orchestrator | `http://audio-converter:5007` | |
| `MEDIA_DIR` | orchestrator | `/media/generated` | Where rendered WAVs are served from |
| `PUBLIC_BASE_URL` | orchestrator | `http://192.168.1.33:5005/files` | Must be reachable by Sonos |
| `DESCRIPTOR_PATH` | orchestrator | `/media/character_descriptor.json` | Live voice table |
| `REFERENCES_DIR` | orchestrator | `/media/references` | Reference clips root |
| `WHISPER_URL` / `ORCHESTRATOR_URL` | xiaozhi-gateway | internal hostnames | |
| `N8N_WEBHOOK_URL` | xiaozhi-gateway | *(blank = test mode)* | Set to the public webhook for production |

## Volumes

| Host | Container | Purpose |
|---|---|---|
| `/mnt/user/media/bumblebee` | `/media` | References, generated audio, descriptor (all services) |
| `/mnt/user/appdata/<svc>/models` | `/tts-models` (or `/whisper-models`) | Model weights, persisted across rebuilds |

## Publishing a Community Apps template (optional)

To let others one-click install, export each container's XML from **Unraid → Docker → (container) → Edit → "Apply" generates a template** under `/boot/config/plugins/dockerMan/templates-user/`, then publish them to a Community Apps template repository. Document the env vars above in each `<Config>` block, and set `<Icon>` to the hosted icon URL (mirrors the `net.unraid.docker.icon` label).
