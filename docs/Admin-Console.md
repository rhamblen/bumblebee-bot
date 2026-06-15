# Admin Console

A small **operator web UI** for the Bumblebee stack — `docker/admin-console/` (FastAPI + server-rendered HTML, port **5012**, on the `bumblebee_default` network). It's the pane you open when you want at-a-glance state without driving the stack from a shell.

> **Design principle: it never holds its own state.** The console reads the *same* sources the live containers read — the orchestrator's `GET /voices` table, the mounted `docker-compose.yml`, the mounted `.env` — and reports on them. There's no separate database to drift out of sync.
>
> **Writing config into running containers is deliberately out of scope.** The console *explains* what's wrong (e.g. a missing env var) and can edit the git-tracked `.env`; you still rebuild to apply. This keeps the git-tracked compose/`.env` as the single source of truth and avoids config drift.

## The tabs

The UI is a thin tabbed shell driven by a single declarative array, so tabs are trivial to reorder or extend. Each tab **lazy-loads** its data on first open and has its own **↻ refresh** button plus a "last updated" timestamp. Each tab has its own page:

| Tab | What it shows | Page |
|---|---|---|
| **Service health** | Live up/down/na for every container, in workflow order | [Service Health](Admin-Console-Service-Health.md) |
| **Devices** | The ESP32 clients the gateway has seen — online/offline, last-seen, IP, last heard/said, editable friendly name (persisted) | [Service: Xiaozhi Gateway](Service-Xiaozhi-Gateway.md#device-registry) |
| **Config** | Per-container env view + validator + editable `.env` + drift check | [Config](Admin-Console-Config.md) |
| **Voices** | Live character table (sorted by model then name) — F5-clip vs Parler split, type/register, editable Parler descriptions, per-voice ▶ Play preview + 🔥 F5 warm-up | [Voices](Admin-Console-Voices.md) |
| **Clip Capture** | Source F5 reference clips from YouTube — per Parler-only voice: download a snippet, preview, accept → flips the voice to F5 | [Clip Capture](Admin-Console-Clip-Capture.md) |
| **Workflow I/O** | A running log of per-run pipeline traces, tailed live from n8n | [Workflow I/O](Admin-Console-Workflow-IO.md) |

## Reaching n8n (the macvlan caveat)

n8n runs on Unraid's macvlan (`br0`), while the admin-console sits on the `bumblebee_default` bridge. Unraid **blocks macvlan↔bridge traffic on the same host**, so the n8n LAN IP (`192.168.1.47:5678`) is unreachable from the console — exactly the same constraint the [gateway hit](Service-Xiaozhi-Gateway.md).

The fix is the same trick the gateway uses for its webhook: point `N8N_API_URL` at the **public Cloudflare Tunnel base** (which dials outbound), not the LAN IP:

```dotenv
# docker/.env  (gitignored — keeps the domain out of the public repo)
N8N_API_URL=https://<your-tunnel-domain>
# N8N_API_KEY=<key>   # set to enable the Workflow I/O panel; /healthz needs no key
```

The tunnel forwards `GET /healthz` (→ `200`, lights the health probe) and `GET /api/v1/executions` (→ `401` without a key, populates the [Workflow I/O tab](Admin-Console-Workflow-IO.md) once `N8N_API_KEY` is set). With `N8N_API_URL` blank, the n8n health row simply shows `○ n/a` rather than a false `down`.

## Configuration

The console's **own** settings on the `admin-console` service in `docker-compose.yml` (all have sane `bumblebee_default` defaults). The [Clip Capture tab](Admin-Console-Clip-Capture.md) adds a download toolchain to the image (`ffmpeg`, `yt-dlp[default]`, **deno**) and mounts the media share **read-write** (`/mnt/user/media/bumblebee:/media`, with `REFERENCES_DIR=/media/references`) so accepted clips land where the orchestrator reads them. The vars it shares with the rest of the stack (`ORCHESTRATOR_URL`, the `N8N_*` set, `COMPOSE_PATH`/`ENV_PATH`) are in the canonical [Environment variable reference](Docker-Containers.md#environment-variable-reference); the ones below are **specific to the console's probes** and aren't declared in compose (code defaults — override on the service if needed):

| Env var | Default | Purpose |
|---|---|---|
| `OLLAMA_URL` | `http://ollama:11434` | ollama health-probe target |
| `REDIS_HOST` / `REDIS_PORT` | `redis` / `6379` | redis TCP `PING` target |
| `GATEWAY_URL` | `http://xiaozhi-gateway:5011` | gateway OTA health-probe target |
| `WHISPER_URL`, `AUDIO_CONVERTER_URL`, `*_TTS_URL` | service hostnames | health-probe targets |
| `SELF_URL` | `http://localhost:5012` | the console's own health row |
| `DOCKER_SOCK` | `/var/run/docker.sock` | read-only docker socket for the Config-tab drift check |

The compose service mounts the **same** `docker-compose.yml` (read-only) and `.env` (read-write, so the Config tab can save) it reads, and carries the standard `net.unraid.docker.icon` + `net.unraid.docker.webui` labels (so it gets an icon and a clickable WebUI entry in Unraid). See [Docker Containers](Docker-Containers.md) for the icon mechanics.

## Extending it

Adding a tab is the deliberate growth path:
1. Add a server endpoint (e.g. `GET /api/thing`) alongside the existing ones.
2. Write an `async function loadThing()` that fetches it and returns HTML.
3. Add one line to the `TABS` array.

The shell wires the button, panel, lazy-load, refresh, and timestamp automatically — and reordering tabs is just moving lines in that array.

## Roadmap

Tab-specific plans live on each tab page ([Config](Admin-Console-Config.md#roadmap-for-this-tab), [Voices](Admin-Console-Voices.md#roadmap-for-this-tab)). Cross-cutting, not yet built:

- **Per-device routing & wake-word config** — the [Devices](#the-tabs) tab now lists and names clients (the per-device registry); the next step is making the friendly name/MAC drive output routing and per-device wake-word settings.
