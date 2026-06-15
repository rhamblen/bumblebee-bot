# Changelog

All notable changes to the Bumblebee Bot project are recorded here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

Convention: work is committed locally during a session and recorded here under
**[Unreleased]**. When a session is confirmed finished, the entry is versioned,
dated, and pushed to GitHub along with any docs/Wiki updates — so the repository
always reflects the project's true current status and the choices made.

## [Unreleased]
### Added
- **Admin Console — Devices tab + gateway device registry.** The console gained a **Devices** tab
  listing every ESP32 client the [gateway](docs/Service-Xiaozhi-Gateway.md#device-registry) has seen —
  online/offline (live), last-seen, IP, last **heard** (transcript) and last **said** (reply text, when the
  brain returns it), with an **editable friendly name** per device. This realises the previously-roadmapped
  "client / wake-word panel".
  - **Gateway (`docker/xiaozhi-gateway/`)** now keeps a small **Redis-backed registry** (`bumblebee:devices`
    hash, `REDIS_URL` default `redis://redis:6379/0`) so names + last heard/said **survive a restart**;
    online state is derived live from an in-memory `CONNECTED` set (never stale). Records are upserted on the
    OTA boot POST, on WS connect/disconnect, and per utterance. Anonymous WS connections (no `Device-Id`) are
    ignored. Redis is **best-effort** — its failure never blocks the voice path. Two new routes on the OTA
    server (:5011): `GET /clients` and `POST /clients/{mac}/name`. `get_wav_url` → `get_reply` now returns the
    full brain response so the reply text can be captured when present. Adds the `redis` dependency.
  - **Console (`docker/admin-console/`)** proxies the gateway via `GET /api/clients` + `POST /api/clients/rename`
    (new `GATEWAY_URL`, reusing the health-probe target), with an inline rename editor mirroring the Voices
    locked-then-edit pattern.

## [0.10.0] - 2026-06-15
### Added
- **Admin Console — Voices tab: editable Parler descriptions + per-voice audition.** The character
  table (`docker/admin-console/`) gained three operator features:
  - **Editable `voice_description`** — Parler-only rows now show their style prompt in an extra column,
    **locked read-only by default** (no open text field that a stray click could change). A per-row
    **✏ Edit** unlocks just that cell (textarea + ✓ Save / ✗ Cancel). Saving posts to a new orchestrator
    `POST /admin/voice-description {name, voice_description}` that updates `character_descriptor.json`
    atomically (shared `_save_descriptor()` helper, also refactored into `/admin/scan-references`).
  - **▶ Play preview (last column)** — generates a fresh random in-character line via Ollama
    (`OLLAMA_MODEL`, default `mistral:latest`), synthesizes it with the character's engine (F5+clip or
    Parler+description), and plays it inline. The orchestrator's new `SpeakRequest.keep_raw` returns the
    pre-filter WAV alongside the filtered one, so you can A/B **▶ Filtered** vs **▶ Raw** from a single
    render; **↻ New line** rolls another. Audio streams **same-origin** through the console
    (`GET /api/voice-preview-audio`, reading the shared `/media/generated` mount) — mirroring Clip
    Capture, since the browser can't reach the orchestrator's `:5005` directly.
  - **🔥 Warm up F5** — fires one throwaway F5 render to pay F5's one-time ~20–25s ASR-init up front
    (F5 keeps no per-voice cache; this is once per container, not per voice).
  - Roster now **sorted by model then name** (F5-clip voices first, then Parler-only, each A–Z), re-applied on refresh.

## [0.9.1] - 2026-06-15
### Documentation
- **Install Guide for Claude Code** ([Install-Guide-for-Claude-Code.md](docs/Install-Guide-for-Claude-Code.md)):
  an ordered, end-to-end install runbook for a fresh cloner — written so a new user's Claude Code can stand
  the whole solution up. Unlike the component reference pages, it sequences the install in phases (host check →
  code + shared network → `.env`/compose → build → seed voice table + first clip → wire n8n + end-to-end test →
  optional ESP32), each ending in a **verification gate**. Inlines the real traps (substitute the author's LAN
  IPs, single-GPU fallback, Dockerfile-needs-a-real-build, drive the stack with one tool, the n8n macvlan →
  public-webhook fix) and flags ▶ YOUR TURN steps. Linked from Home + sidebar under "Build & deploy".
- **Module Reference page** ([Module-Reference.md](docs/Module-Reference.md)): a decoder for
  the build's short codes (C1, C2/C3, A2, D2a, J3, K4 …) that appear across the wiki, commits,
  and working notes. Explains the convention (letter = work-stream, number = step), a glance
  table of all work-streams A–N, and deep-dives on the `C` brain pipeline (C0–C4) and the `J`
  ESP32 sub-streams (JA/JB/JC). Linked from Home + sidebar under "Start here".
- **TTS Options — speed notes** ([TTS-Options.md](docs/TTS-Options.md)): measured F5-vs-Parler
  head-to-head (same text via `/speak`). F5 ~8s vs Parler ~14.7s (~1.8× faster) despite F5 being on
  the slower 3060 — it's non-autoregressive. F5 has **no per-voice cold start or expiry** (a never-
  rendered voice's first call was ~5s); the only cold start is a one-time ~20–25s service init on the
  first request after a container restart. F5 (3060) + Parler (3090) also give true cross-GPU
  parallelism on mixed multi-voice runs.

## [0.9.0] - 2026-06-15
### Added
- **Admin Console — Clip Capture tab** (`docker/admin-console/`). Sources F5 reference clips from
  YouTube without leaving the browser, directly attacking the clip bottleneck (only 2/36 voices had
  clips on disk). It's an accept/reject vetting station, not a fire-and-forget downloader:
  - **Table = the Parler-only voices** (rows from `GET /voices` where `clip_on_disk:false` — exactly
    the voices that still need a clip). Per row: YouTube URL, start (mm:ss), and a duration that
    **defaults to 30s**.
  - **▶ Execute** downloads just that section (yt-dlp `--download-sections`) and normalises to the F5
    reference format (22050 Hz mono s16) into a **staging area** — nothing touches the library yet.
  - A native **`<audio controls>` player** lets you preview/replay the candidate before deciding.
  - **✓ Accept** moves the clip into `references/<character>/<character>_clip_NN.wav` (next free
    number) and calls the orchestrator's [`/admin/scan-references`](docs/Service-Orchestrator.md) —
    so the voice **flips Parler→F5 on the spot**. **✗ Reject** discards the staging file and leaves
    the row editable to tweak + re-run. The table only reloads on the manual **↻ refresh**.
  - New endpoints: `GET /api/clip-ready`, `POST /api/clip-capture`, `GET /api/clip-preview`,
    `POST /api/clip-accept`, `POST /api/clip-reject`. Path inputs are guarded (folder `^[A-Za-z0-9_]+$`,
    staged-id 32-hex) so nothing can escape the references/staging dirs.
- **admin-console image gained a download toolchain** — `ffmpeg` + `yt-dlp[default]` + a **deno** JS
  runtime, and the **media share is now mounted read-write** (`/mnt/user/media/bumblebee:/media`) so
  accepted clips land where the orchestrator reads them. `REFERENCES_DIR` env added to the service.

### Notes / gotchas (so we don't repeat the build saga)
- **Modern yt-dlp needs deno _and_ the EJS challenge solver.** A JS runtime alone isn't enough —
  YouTube's "n challenge" needs the `yt-dlp-ejs` solver scripts, which is why the image installs
  **`yt-dlp[default]`** (bundles them) rather than plain `yt-dlp`. Symptom of missing it:
  `n challenge solving failed … This video is not available`.
- **No VPN.** The earlier `vpn-converter` container (in-container OpenVPN) is **retired** — it never
  worked reliably. UR1 shares the home public IP, so a server-side yt-dlp on UR1 needs no tunnel.
- **A Dockerfile change requires a real image rebuild** (`docker compose build`, not the Unraid
  Docker-tab "force update", which only re-pulls a tag). Manage admin-console through **one** tool
  (the Unraid Compose Manager plugin) — driving it from the CLI under a different compose project
  name lands it on an isolated network (`bumblebee-docker_default`) where it can't reach the
  orchestrator, and clashes on the fixed `container_name`.

## [0.8.1] - 2026-06-15
### Changed
- **Parler TTS — performance pass** (synthesis was ~90% of every run, all of it Parler generation):
  - **Moved Parler to the RTX 3090** (`NVIDIA_VISIBLE_DEVICES` 0→1). It's the faster card and is
    idle during synthesis (Ollama finishes first); the 3060 was VRAM-saturated (~95%). f5-tts /
    chatterbox stay on the 3060.
  - **bf16 inference** (`torch_dtype=bfloat16`) — roughly halves both generation time and VRAM vs
    fp32 (bf16 over fp16 to avoid NaNs). bf16 output is upcast to float32 before `numpy()`/write.
  - **Gotcha (so we don't repeat it):** a blanket bf16 cast crashes Parler's **DAC vocoder** —
    `RuntimeError: "weight_norm_fwd_first_dim_kernel" not implemented for 'BFloat16'` (PyTorch has
    no bf16 kernel for `weight_norm`). Fix: keep the **audio decoder in fp32**
    (`model.audio_encoder.to(torch.float32)`) while the transformer stays bf16. Symptom was
    misleading — `/health` passed (lifespan completed) but every `/tts` returned 500 → orchestrator
    502, because the startup warmup swallowed the error. Likely applies to any weight_norm vocoder
    (F5/Chatterbox/Coqui) if bf16 is tried there.
  - **Startup warmup** — a throwaway `generate()` in `lifespan` so lazy CUDA init never lands on a
    user request.
  - **Opt-in `torch.compile`** via `PARLER_COMPILE=1` (default off — finicky with variable-length
    decode), guarded so it can never break startup. `/health` now reports `dtype` + `compiled`.
  - **Measured result: modest, not the hoped ~2×.** Per-segment generate, 3060+fp32 → 3090+bf16:
    1-voice ~11–18s → ~17s (≈ flat); 3-voice max-seg 66.7s → 55.3s (~17%). Autoregressive decode is
    latency/overhead-bound, not compute-bound, and generation time tracks **output length** (same run
    showed 55s vs 16s segments). Net win is **VRAM** (Parler off the saturated 3060, bf16 ~halves its
    footprint) plus ~17% on long multi-voice. **Further perf work parked** — next levers are bounding
    output length (`max_new_tokens` cap + shorter composed text) and a *proper* `torch.compile` with a
    static KV cache. Benchmark + plan in the project memory for a future session.

## [0.8.0] - 2026-06-15
### Added
- **Workflow I/O log — per-step latency.** Each log entry now shows a `⏱ steps`
  breakdown (`session · mood · compose · parse · synthesis · play`) from n8n's per-node
  `executionTime` — recorded as each node runs, so it's free. Immediately reveals that
  ~90% of a run is the `synthesis` (Call Orchestrator) step; the brain (mood+compose) is
  only ~1–7s.
- **Orchestrator — per-segment synthesis timing.** `/speak-multi` (and `/speak`) now return
  a `timings` block: per-segment `generate_ms` / `filter_ms` + `engine`, plus `concat_ms`
  and `synth_ms`. The log surfaces it as a `↳ tts` row, breaking the opaque synthesis step
  into per-voice generate-vs-filter — confirming TTS generation is ~100% of synthesis (filter
  <1s, concat <0.1s).
### Changed
- **Orchestrator — segments now synthesize concurrently.** `/speak-multi` replaced its serial
  `for`-loop with `asyncio.gather`, so a multi-voice run overlaps its TTS calls instead of
  running them back-to-back (the `↳ tts` row flags `∥ parallel`). Per-segment failure-skip
  behaviour is preserved; unexpected errors still fail loud. **Validated in production** (2-voice
  run #64): serial-equivalent 47s → 31s actual, ~34% faster. Both voices share one Parler GPU,
  so it's a solid overlap rather than a full halving.

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
