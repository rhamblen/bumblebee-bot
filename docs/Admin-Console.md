# Admin Console

A small **operator web UI** for the Bumblebee stack — `docker/admin-console/` (FastAPI + server-rendered HTML, port **5012**, on the `bumblebee_default` network). It's the pane you open when you want at-a-glance state without driving the stack from a shell.

> **Design principle: it never holds its own state.** The console reads the *same* sources the live containers read — the orchestrator's `GET /voices` table, the mounted `docker-compose.yml`, the mounted `.env` — and reports on them. There's no separate database to drift out of sync.
>
> **Writing config into running containers is deliberately out of scope.** The console *explains* what's wrong (e.g. a missing env var); you fix it the normal build way. This keeps the git-tracked compose/`.env` as the single source of truth and avoids config drift.

## The tabs

The UI is a thin tabbed shell driven by a single declarative array, so tabs are trivial to reorder or extend. Each tab **lazy-loads** its data on first open and has its own **↻ refresh** button plus a "last updated" timestamp.

| Tab | What it shows |
|---|---|
| **Service health** | Live up/down/na for every container in the pipeline (see below) |
| **Config** | Parses the mounted compose + `.env`, flags `${VAR}` referenced-but-undefined |
| **Voices** | Live character table from the orchestrator `/voices` — F5-clip vs Parler split, response_type/register |
| **Workflow I/O** | Last *N* n8n executions via the REST API |

### Service health — probed in workflow order

The health tab lists services **in the order a request flows through them** (admin-console first, then input → STT → brain → synthesis → engines), so a problem shows up at the stage it occurs:

```
admin-console → xiaozhi-gateway → whisper-stt → n8n → redis → ollama
              → orchestrator → audio-converter → f5-tts → parler-tts → coqui-tts → chatterbox
```

Not every service answers a plain `GET /health`, so each is probed the right way:

| Service | Probe | Healthy when |
|---|---|---|
| orchestrator, f5/parler/coqui/chatterbox, audio-converter, whisper-stt | HTTP `GET /health` | `2xx` |
| **xiaozhi-gateway** | HTTP `GET /xiaozhi/ota/` | `2xx` — the gateway has no `/health`, so its OTA endpoint stands in |
| **admin-console** | HTTP `GET /health` (self) | `2xx` |
| **ollama** | HTTP `GET /` | `2xx` ("Ollama is running") |
| **redis** | TCP `PING` | reply contains `PONG` — a real protocol check, not just an open port |
| **n8n** | HTTP `GET /healthz` | `2xx` — see the reachability note below |

The table shows three states — `● up`, `● down`, `○ n/a` (not configured) — plus a **detail** column (HTTP code, `+PONG`, or the error) so a red row tells you *why*.

### Config validator

Reads the compose file and `.env` (both mounted read-only at `/srv/`) and reports:
- `${VAR}` **referenced in compose but absent from `.env`** — a hard finding for a bare `${VAR}`, a soft warning for `${VAR:-default}` (it has an inline fallback).
- a missing `.env` entirely.

It's read-only by design — it surfaces config errors before a build; it never edits anything.

## Reaching n8n (the macvlan caveat)

n8n runs on Unraid's macvlan (`br0`), while the admin-console sits on the `bumblebee_default` bridge. Unraid **blocks macvlan↔bridge traffic on the same host**, so the n8n LAN IP (`192.168.1.47:5678`) is unreachable from the console — exactly the same constraint the [gateway hit](Docker-Containers.md#the-9-services).

The fix is the same trick the gateway uses for its webhook: point `N8N_API_URL` at the **public Cloudflare Tunnel base** (which dials outbound), not the LAN IP:

```dotenv
# docker/.env  (gitignored — keeps the domain out of the public repo)
N8N_API_URL=https://<your-tunnel-domain>
# N8N_API_KEY=<key>   # set to enable the Workflow I/O panel; /healthz needs no key
```

The tunnel forwards `GET /healthz` (→ `200`, lights the health probe) and `GET /api/v1/executions` (→ `401` without a key, populates the Workflow I/O tab once `N8N_API_KEY` is set). With `N8N_API_URL` blank, the n8n health row simply shows `○ n/a` rather than a false `down`.

## Configuration

Set on the `admin-console` service in `docker-compose.yml` (all have sane `bumblebee_default` defaults):

| Env var | Default | Purpose |
|---|---|---|
| `ORCHESTRATOR_URL` | `http://bumblebee-orchestrator:5005` | source of the live `/voices` table |
| `N8N_API_URL` | *(blank)* | n8n base for health + workflow panels — the **tunnel** URL, not the LAN IP |
| `N8N_API_KEY` | *(blank)* | enables the Workflow I/O panel |
| `OLLAMA_URL`, `REDIS_HOST`/`REDIS_PORT`, `GATEWAY_URL` | `ollama:11434`, `redis`/`6379`, `xiaozhi-gateway:5011` | health-probe targets |
| `COMPOSE_PATH`, `ENV_PATH` | `/srv/docker-compose.yml`, `/srv/.env` | read-only mounts for the validator |

The compose service mounts the **same** `docker-compose.yml` and `.env` it validates, read-only, and carries the standard `net.unraid.docker.icon` + `net.unraid.docker.webui` labels (so it gets an icon and a clickable WebUI entry in Unraid). See [Docker Containers](Docker-Containers.md) for the icon mechanics.

## Extending it

Adding a tab is the deliberate growth path:
1. Add a server endpoint (e.g. `GET /api/thing`) alongside the existing ones.
2. Write an `async function loadThing()` that fetches it and returns HTML.
3. Add one line to the `TABS` array.

The shell wires the button, panel, lazy-load, refresh, and timestamp automatically — and reordering tabs is just moving lines in that array.

## Roadmap

Planned, not yet built:
- **Client / wake-word panel** — read the per-device registry from the gateway.
- **Workflow I/O by pipeline stage** — lay out a run's input→output in the same workflow order as the health tab, flagging the first stage that errored, so you can see *where* it broke.
- **Config *generation*** — write a `.env`/compose to disk for you to build. Never a hot-push into running containers.
