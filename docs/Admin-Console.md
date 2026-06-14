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
| **Config** | Per-container env view — resolved value + source; edit `${VAR}`-backed values and save to `.env`. Includes the validator findings |
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

### Config tab (validator + `.env` editor)

The tab is **data-driven**: it walks every service's compose `environment:` block and, for each variable, shows the **resolved value** and where it comes from — `.env` (an override is set), `default` (a `${VAR:-default}` fallback in compose), or `compose` (a bare literal). Fields are grouped by container.

**What's editable:** a variable can be edited *only when compose references it as `${VAR}` or `${VAR:-default}`* — i.e. it's sourced from `.env`. Editing it and pressing **💾 Save .env** writes the value to `.env` (`POST /api/env`). A bare literal (`FOO=http://x`) is shown **read-only**; to make it editable, convert it to `${FOO:-http://x}` in compose — done deliberately, one section at a time, so the change surface stays reviewable.

**Secrets** (e.g. `N8N_API_KEY`, `OTA_WS_TOKEN`) are masked (`••••••••`) in the browser; a save that sends the mask back unchanged is skipped, so secrets are never round-tripped or clobbered by accident. The writer updates only the managed keys and **preserves every other line and comment** in `.env`.

**Applying changes:** env vars are read at container start, so a save takes effect when you **recreate the affected container(s)** — this is still "generate config, you build", never a hot-push into a running container.

The tab also shows the **validator findings**: `${VAR}` referenced in compose but absent from `.env` (a hard finding for a bare `${VAR}`, a soft warning for `${VAR:-default}`), and a missing `.env` entirely.

**What each field means** — every variable, its format, valid values, and default is in the [Environment variable reference](Docker-Containers.md#environment-variable-reference).

#### Drift check — "needs recreate"

Because env vars are interpolated at container **create** time, editing `.env` (here or by hand) doesn't reach a *running* process until that container is recreated — a `restart` is not enough. The tab makes this visible: it compares each service's **expected** value (compose, with `${VAR}` resolved from `.env`) against the value the container is **actually running** (its Docker `Config.Env`), read over a **read-only `/var/run/docker.sock`** mount.

When they differ you get a red banner listing the affected containers, plus an inline **⚠ running: … — recreate to apply** badge on each stale field. Secrets are masked. If the socket isn't mounted the check shows "drift check unavailable" and the rest of the tab is unaffected.

> **Why a socket mount:** reading another container's running env needs the Docker API. The mount is **read-only**, but be aware docker-socket access is powerful (≈ root on the host) — a deliberate tradeoff for a homelab operator pane.

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

The console's **own** settings on the `admin-console` service in `docker-compose.yml` (all have sane `bumblebee_default` defaults). The vars it shares with the rest of the stack (`ORCHESTRATOR_URL`, the `N8N_*` set, `COMPOSE_PATH`/`ENV_PATH`) are in the canonical [Environment variable reference](Docker-Containers.md#environment-variable-reference); the ones below are **specific to the console's probes** and aren't declared in compose (code defaults — override on the service if needed):

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

Planned, not yet built:
- **Client / wake-word panel** — read the per-device registry from the gateway.
- **Workflow I/O by pipeline stage** — lay out a run's input→output in the same workflow order as the health tab, flagging the first stage that errored, so you can see *where* it broke.
- **Config editing — more fields** — Phase 1 edits the `${VAR}`-backed values (the n8n keys). Next: convert the high-value compose literals (GPU pins, service URLs, VAD/silence tuning) to `${VAR:-default}` so they become editable too.
- **Live config store** — move the *tunable* behavioural values (VAD/silence, TTS retries, whisper language) out of `.env` into a store services re-read on a `/reload`, so they apply without a container recreate. `.env` keeps only what genuinely needs a rebuild (connections, paths, ports, GPU).
- **Brain config on the Voices tab** — voice-count, weighting, persona, and the Ollama model→role map are *brain* settings, not infra; they belong with Voices, not Config.
