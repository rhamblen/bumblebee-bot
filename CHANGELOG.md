# Changelog

All notable changes to the Bumblebee Bot project are recorded here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

Convention: work is committed locally during a session and recorded here under
**[Unreleased]**. When a session is confirmed finished, the entry is versioned,
dated, and pushed to GitHub along with any docs/Wiki updates — so the repository
always reflects the project's true current status and the choices made.

## [Unreleased]

## [0.7.0] - 2026-06-15
### Added
- **Admin console — Workflow I/O is now a live, appended pipeline-trace log.** The tab was
  a bare id/started/status table; it now renders one entry per n8n run showing what actually
  flowed through the pipeline, in flow order: 🎤 **heard** (transcript + device) → 🧠 **mood**
  (`mood · response_type · response_register`) → 🎭 **voices** (each with an `[f5]`/`[parler]`
  engine pill) → 💬 **said** (per-character text) → 🔊 **out** (WAV file · segment count ·
  skipped). Every field is pulled straight from the execution's `runData` by node name — no
  n8n workflow changes needed (`GET /api/workflow-trace`).
- **Live tail + controls.** n8n is itself an append-only ledger, so the panel fetches the last
  N traces then **polls every 8s**, prepending new runs (newest on top) with a `● live`
  indicator, a **⏸ pause** toggle, and a **🗑 clear** button; scrollback is capped so it can
  run all day. The console still holds no state — the log is just a rendered tail of n8n.
- **Failure flagging + latency.** Failed runs are bordered red and headed `✕ failed at <stage>`
  (n8n's `lastNodeExecuted` mapped to the pipeline stage, with the error message inline). Each
  entry shows end-to-end wall time and the LLM portion (⏱ · LLM), making it obvious when
  synthesis — not the brain — is the slow part. Engine pills surface F5 clip-coverage gaps at
  a glance.
### Changed
- Admin-console n8n endpoint `GET /api/workflow-runs` replaced by `GET /api/workflow-trace`
  (returns flattened per-stage traces instead of raw executions).

## [0.6.0] - 2026-06-14
### Added
- **Admin console — Config tab drift check ("needs recreate")**. Env is interpolated at
  container *create* time, so editing `.env` doesn't reach a running process until it's
  recreated. The tab now compares each service's expected value (compose, `${VAR}` resolved
  from `.env`) against the value the container is **actually running** (its Docker
  `Config.Env`, read over a **read-only `/var/run/docker.sock`** mount), and flags drift:
  a red banner listing the affected containers, plus an inline "⚠ running: … — recreate to
  apply" badge on each stale field. Covers the whole stack (`GET /api/drift`). Secrets masked.
  Degrades gracefully to "drift check unavailable" if the socket isn't mounted.
- **Admin console — Config tab is now an editable `.env` surface** (Phase 1). It walks
  every service's compose `environment:` block and shows each variable grouped by
  container, with its **resolved value** and **source** (`.env` / `default` / `compose`):
  - Variables compose references as `${VAR}` / `${VAR:-default}` are **editable** and
    saved back to `.env` (`POST /api/env`); bare compose literals are shown **read-only**
    (convert to `${VAR}` to make them editable, one section at a time).
  - **Secrets** (`N8N_API_KEY`, `OTA_WS_TOKEN`, …) are masked in transit and a write
    carrying the mask back is treated as "unchanged"; the writer preserves all other
    lines/comments in `.env`. The validator findings remain on the tab.
  - Directly unblocks setting **`N8N_API_KEY`** (and `N8N_API_URL` / `N8N_WORKFLOW_ID` /
    `N8N_WEBHOOK_URL`) from the UI for the Workflow I/O panel.
### Fixed
- Config validator no longer scans `#` comment lines — a `${VAR}` mentioned in a compose
  comment was wrongly flagged as referenced-but-undefined (compose never interpolates comments).
### Changed
- `admin-console` mounts `.env` **read-write** (`docker-compose.yml`) so the Config tab
  can save; `docker-compose.yml` stays read-only (validator only). Changes still take
  effect the normal build way — recreate the affected container(s).

## [0.5.0] - 2026-06-14
### Added
- **Admin console** (`docker/admin-console/`, port 5012) — a read-mostly operator web UI
  on `bumblebee_default` (FastAPI + server-rendered HTML). It never holds its own state:
  it reads the same sources the live containers read (orchestrator `/voices`, the mounted
  `docker-compose.yml` and `.env`). Tabbed shell driven by a declarative array, with
  per-tab lazy-load, **↻ refresh**, and a "last updated" timestamp:
  - **Service health** — the *whole stack* in **workflow order** (admin-console → gateway →
    whisper → n8n → redis → ollama → orchestrator → audio-converter → F5/Parler/XTTS/Chatterbox),
    each probed the right way: HTTP `/health` for the FastAPI services, the gateway's
    `/xiaozhi/ota/` (it has no `/health`), Ollama on `/`, **Redis via a TCP `PING`→`+PONG`**,
    and n8n on `/healthz`. Three states (`up`/`down`/`n/a`) + a detail column.
  - **Config validator** (read-only) — flags `${VAR}` referenced in compose but missing from
    `.env` (hard finding for bare `${VAR}`, soft warning for `${VAR:-default}`).
  - **Voices** — live character table from the orchestrator (F5-clip vs Parler split).
  - **Workflow I/O** — last *N* n8n executions via the REST API.
  - Writing config into running containers is intentionally out of scope.
  - Added to `docker-compose.yml` with `net.unraid.docker.icon` + `net.unraid.docker.webui`
    labels and read-only mounts of `docker-compose.yml` + `.env`.
- **[Admin Console](docs/Admin-Console.md) wiki page** — full write-up; linked from the README,
  Home, and the sidebar (new "Operate" section). Docker-Containers updated to 9 services.
- **`docker/_admin.sh` icon-refresh script** — writes each container icon straight into
  *both* Unraid icon locations (the download cache and the GUI-served copy under
  `emhttp/state/...`), bypassing Unraid's refusal to overwrite an existing cached icon.
  Run host-side via a one-time User Scripts entry (`bumblebee_admin`). See
  [Docker-Containers](docs/Docker-Containers.md#custom-unraid-icons).

### Changed
- Refreshed container icons (chatterbox; new admin-console icon).

### Notes
- **n8n reachability:** the admin-console reaches n8n via the **public Cloudflare Tunnel base**
  (`N8N_API_URL`), not the LAN IP — same macvlan↔bridge block the gateway hit. Set in the
  gitignored `docker/.env`; `/healthz` lights the health probe with no key, `N8N_API_KEY`
  additionally enables the Workflow I/O panel. Confirmed live.

## [0.4.1] - 2026-06-14
### Fixed
- **Per-segment TTS failure guard (JB2)** in `docker/orchestrator/server.py` — a single
  engine hiccup no longer silences a whole reply:
  - All engine calls now route through `_post_tts_with_retry()`, which retries once on a
    transient `5xx`/connection error before giving up (tunable via `TTS_RETRIES` and
    `TTS_RETRY_BACKOFF`). Covers both `/speak` and `/speak-multi`.
  - `/speak-multi` synthesizes each segment independently and **skips** one that still
    fails, dropping it from the concat instead of aborting. It only returns `502` if
    *every* segment fails, and the response now reports a `skipped` count.
  - Verified live: a deliberately broken segment (good Parler + `xtts` with no
    `reference_clip`) returned `{count:1, skipped:1}` with HTTP 200; the orchestrator log
    confirmed `speak-multi: segment 2/2 failed, skipping … 200 OK`.

## [0.4.0] - 2026-06-14
### Added
- **ESP32 voice I/O — full hands-free round trip** in `docker/xiaozhi-gateway/server.py`:
  WebSocket `hello` handshake, Opus decode/encode, **server-side `webrtcvad`** (detects
  end-of-speech in auto mode, where the device never sends a stop), and real-time TTS
  pacing with `ping_interval=None` so long replies don't trip the WS keepalive.
- **`/ota` discovery endpoint** (port 5011, aiohttp) — the device fetches its WS URL +
  token on boot; WS stays on 5010. Zero-reflash onboarding via the device captive portal.
- **n8n mode for the gateway** — `N8N_WEBHOOK_URL` routes transcripts through the full
  C1+C2+C3 character pipeline; the device **MAC is used as the Redis `session_id`** so each
  device keeps its own conversation history.
- Whisper hardening in `docker/whisper-stt/server.py` — `language=en`, `vad_filter=True`,
  `condition_on_previous_text=False`, plus a gateway-side noise/hallucination guard.

### Changed
- `docker/docker-compose.yml` — `xiaozhi-gateway` `N8N_WEBHOOK_URL` set to the **public
  Cloudflare webhook** (`https://<your-tunnel-domain>/webhook/bumblebee`), **not** the LAN IP.
  n8n runs on Unraid macvlan (`br0`) and the gateway on the `bumblebee_default` bridge;
  Unraid blocks macvlan↔bridge same-host traffic, so the LAN IP fails with
  `All connection attempts failed`. The Cloudflare tunnel dials outbound and works.
- Docs corrected for the above: Architecture & Workflow (gateway→n8n now via Cloudflare +
  networking note), Docker Containers (n8n is on macvlan, not `bumblebee_default`), and
  Voice Input (ESP32 round trip is live, not foundation-only).

### Known issues
- The ESP32 test device's amp/speaker is blown — on-device playback unverifiable; output is
  validated on Sonos meanwhile.
- One failed TTS segment still aborts a multi-segment reply (per-segment guard is planned).
  *(Resolved in 0.4.1.)*

## [0.3.0] - 2026-06-14
### Added
- Wiki landing page (`docs/Home.md`) and navigation sidebar (`docs/_Sidebar.md`) — `docs/`
  is now a complete mirror of the GitHub Wiki.
- `scripts/mirror_wiki.py` — one-command sync of `docs/` → the GitHub Wiki (rewrites
  internal `*.md` links to wiki form).
- `LICENSE` — MIT. README license section notes that bundled third-party TTS/STT models
  keep their own terms (e.g. Coqui XTTS is non-commercial CPML).

### Changed
- Repository made **public** on GitHub; published the Wiki (11 pages).
- Refreshed the orchestrator container icon (`docker/bumblebee-orchestrator-icon.png`).

## [0.2.0] - 2026-06-14
### Added
- Public-facing `README.md` (project intro, Mermaid architecture diagram, stack, status).
- `docs/` write-up (mirrors the GitHub Wiki): Concept & Lore, Architecture & Workflow
  (system / sequence / multi-device diagrams), Docker Containers, Unraid Template,
  STT Options, TTS Options, Voice Input (Alexa → ESP32/Xiaozhi), Input Metadata Schema,
  Character & Response Table.
- `CHANGELOG.md` and versioning convention.

### Changed
- **Reorganised the repository into folders by work area** for readability:
  `data/` (character tables, presets, CSV/XLSX), `scripts/n8n/`, `scripts/clips/`,
  `scripts/character/`, `tests/`, `notes/`. `docker/` and `docs/` unchanged.
- Updated path references inside the moved scripts so `.env` resolves to the repo root
  and data files resolve to `data/` (verified: scripts compile, paths resolve).

## [0.1.0] - 2026-06-14
### Added
- Initial commit: working end-to-end pipeline source — Docker stack (8 services:
  orchestrator, F5/Parler/XTTS/Chatterbox TTS, audio-converter, whisper-stt,
  xiaozhi-gateway), n8n update scripts, character data, and admin/test tooling.
- `.gitignore` scrubbing all secrets (`.env`, `.venv/`, `.claude/settings.local.json`,
  `cookies.txt`, `*.ovpn`, generated audio).
