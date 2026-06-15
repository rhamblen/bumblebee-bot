# Install Guide — for Claude Code

> **Who this page is for.** This is a runbook addressed to **Claude Code** (or any coding agent)
> helping a new user stand up Bumblebee Bot end-to-end after cloning this repository. It is also
> readable by a human. It does **not** duplicate the reference pages — it sequences them, adds the
> decision points, and tells the agent where the real traps are. Follow it top to bottom; each
> phase ends in a **verification gate** you must pass before moving on.

---

## How to use this (agent instructions)

1. **Survey before you act.** Read [Architecture & Workflow](Architecture-and-Workflow) and
   [Docker Containers](Docker-Containers) first so you understand the 9 services and the data flow.
2. **Confirm the host reality with the user.** This stack was built for **Unraid + Docker with 2× NVIDIA
   GPUs**. Do not assume — ask what hardware/OS they actually have (see Phase 0). The single-GPU and
   no-Unraid paths work but change several steps.
3. **Never hardcode this project's LAN IPs.** Addresses like `192.168.1.33` / `.47` / `.64` are *this
   author's* network. Replace every one with the user's actual host. Internal service-to-service calls
   must use **Docker hostnames** on `bumblebee_default`, not IPs.
4. **Stop at each verification gate.** If a gate fails, debug it before continuing — a broken lower layer
   makes every later phase fail confusingly.
5. **Call out user-only actions explicitly.** Docker builds, file copies to the server, container
   recreates, and physical/hardware steps are the **user's** to run. Put them in a clear
   "▶ YOUR TURN" block; don't bury them in prose.

---

## Phase 0 — Prerequisites & host check

Confirm with the user, and only proceed once each is true:

- [ ] **Host:** Unraid (recommended) or any Docker host. With Unraid, the **Nvidia-Driver** plugin and
      `--runtime=nvidia` must be working.
- [ ] **GPU:** at least one CUDA GPU. Two is the design target (see the
      [GPU split](Docker-Containers#gpu-split)). **One GPU?** Set every `NVIDIA_VISIBLE_DEVICES` to `0`
      and expect serialized rendering.
- [ ] **Shared media path** exists, e.g. `/mnt/user/media/bumblebee`, and will hold `references/`,
      `generated/`, and `character_descriptor.json`.
- [ ] **External services the stack depends on but does not ship:**
  - **Ollama** (the LLM brain) — reachable on the shared network as `ollama:11434`.
  - **Redis** (session memory) — reachable as `redis:6379`.
  - **n8n** (the workflow engine) — see the macvlan note in Phase 4.
  - **Home Assistant** — only if the user wants **Sonos** output (audio out via HA `media_player`).
  - **Cloudflare Tunnel** (or equivalent) — only if the user wants a **public HTTPS webhook** (needed for
    ESP32 voice-in, and the cleanest way around the n8n macvlan trap).
- [ ] **Decide the user's target scope** and tell them which later phases they can skip:
  - **Text-only** (POST a phrase, hear it on Sonos): Phases 1–5. Skip ESP32 (Phase 6).
  - **Full hands-free voice** (ESP32 wake → speak → reply): all phases.

**Gate 0:** GPU is visible to Docker (`nvidia-smi` in a test container), the media path is writable, and
Ollama + Redis answer on the shared network.

---

## Phase 1 — Get the code & the shared network

▶ **YOUR TURN (user):**
```bash
# on the Unraid host
mkdir -p /mnt/user/appdata/bumblebee-docker
# copy this repo's docker/ folder into that directory
docker network create bumblebee_default      # once; lets services find each other by hostname
```

Put Ollama, Redis, and the bumblebee containers all on `bumblebee_default`. See the hostname/port table
in [Unraid Template](Unraid-Template#shared-docker-network).

**Gate 1:** `docker network inspect bumblebee_default` shows the network; Ollama and Redis are attached.

---

## Phase 2 — Configure `.env` and review compose

The deployed `appdata` copy of `docker-compose.yml` is what actually builds — keep it in sync with the
repo copy. Create `docker/.env` (gitignored) for the user-specific values. The full variable reference is
on [Docker Containers → Environment variable reference](Docker-Containers#environment-variable-reference);
the ones a fresh install almost always edits:

| Variable | Service | Set to |
|---|---|---|
| `PUBLIC_BASE_URL` | orchestrator | `http://<USER-HOST-IP>:5005/files` — **must be reachable by the playback device (Sonos)** |
| `NVIDIA_VISIBLE_DEVICES` | each GPU service | `0`/`1` per the GPU split, or all `0` on a single-GPU host |
| `N8N_WEBHOOK_URL` | xiaozhi-gateway | **leave blank for now** (orchestrator-direct test mode); set in Phase 6 |
| `N8N_API_URL` / `N8N_API_KEY` / `N8N_WORKFLOW_ID` | admin-console | optional — enables the Workflow I/O panel |
| `OTA_WS_URL` | xiaozhi-gateway | `ws://<USER-HOST-IP>:5010/xiaozhi/v1/` — only matters for ESP32 |

Also replace the hardcoded `192.168.1.33` in the `net.unraid.docker.icon` labels (cosmetic — icons only).

**Gate 2:** every `192.168.1.x` in the deployed compose/`.env` is either the user's real host or
intentionally blank.

---

## Phase 3 — Build & start the stack

▶ **YOUR TURN (user):**
```bash
cd /mnt/user/appdata/bumblebee-docker
docker compose up -d --build        # builds all 9 images; first run pulls model weights — slow
```

> **Build/deploy traps (learned the hard way — see the deploy lessons in the task log):**
> - A **Dockerfile** change needs a real `docker compose build`. Unraid's "force update" only re-pulls a
>   tag; it does **not** rebuild from a local Dockerfile.
> - Drive the whole stack with **one tool** (the Unraid Compose Manager plugin, project `bumblebee`).
>   Mixing the CLI under a different project name puts containers on an isolated network where they can't
>   reach each other.
> - Model weights persist to per-service `appdata` volumes, so later rebuilds are fast.

**Gate 3:** all containers are `Up`; `GET http://<host>:5005/health` (orchestrator) returns OK; the four
TTS engines and whisper-stt are healthy. Use the **Admin Console** (`:5012`) →
[Service Health](Admin-Console-Service-Health) to confirm at a glance.

---

## Phase 4 — Seed the voice table and the first clip

The pipeline needs a voice table on the media share and at least one usable voice.

- [ ] Place `character_descriptor.json` at the media root (`/media/character_descriptor.json`). Build it
      from the character data with the script in `scripts/character/` if it isn't present.
- [ ] Confirm `GET http://<host>:5005/voices` serves the table.
- [ ] **Get at least one voice working.** Two routes:
  - **Parler (no clip needed):** any character with a `voice_description` works immediately — fastest way
    to first sound.
  - **F5 (clone):** drop a reference WAV into `references/<Character>/`, then
    `POST /admin/scan-references` (or use the Admin Console **Clip Capture** tab) to flip that character to
    F5. See [Admin Console → Clip Capture](Admin-Console-Clip-Capture).
- [ ] Tell the user the honest state: **clip sourcing is the project's standing bottleneck** — more F5
      clips = richer playback, but Parler covers everything in the meantime.

**Gate 4:** a direct `POST /speak` to the orchestrator with a Parler voice returns a WAV URL that plays.

---

## Phase 5 — Wire the n8n "brain" and verify end-to-end

This is where mood-reading and character selection (C1 → C2/C3) come in. Always test **through the n8n
webhook**, never by calling the orchestrator directly — direct calls skip the brain.

- [ ] Import / recreate the n8n workflow (node order is in the task log; the workflow drives
      Webhook → Read Session → Ollama mood → compose 1–3 segments → `/speak-multi` → Respond → Sonos).
- [ ] **Sonos output (optional):** the "Play on Sonos" step calls Home Assistant `media_player`. Point it
      at the user's HA host **by IP** (Docker often can't resolve HA's `.local` mDNS name) and pass the WAV
      `url`.
- [ ] **The n8n macvlan trap** (read [Docker Containers → n8n networking gotcha](Docker-Containers)): on
      Unraid, n8n typically runs on macvlan `br0`, while the bumblebee services are on the
      `bumblebee_default` bridge. A bridge container **cannot reach n8n** by hostname *or* by LAN IP on the
      same host (`All connection attempts failed`). The robust fix is to call n8n through its **public
      Cloudflare Tunnel webhook**, which dials outbound and sidesteps the isolation.

**Gate 5 (the real end-to-end test):**
```
POST https://<tunnel-or-host>/webhook/bumblebee   {"text": "the kitchen is a warzone after the kids' party"}
```
→ n8n reads a mood → picks a character → orchestrator renders → audio plays on Sonos. This is the
"it works" milestone for a text-driven install.

---

## Phase 6 — ESP32 hands-free voice (optional)

Only if the user wants wake-word voice in/out. Background:
[Voice Input: Alexa → ESP32/Xiaozhi](Voice-Input-Alexa-vs-ESP32) and
[Service: Xiaozhi Gateway](Service-Xiaozhi-Gateway).

- [ ] Flash an ESP32-S3 with the Xiaozhi firmware; point its OTA URL (via the device captive portal) at
      `http://<host>:5011/xiaozhi/ota/`.
- [ ] Set the gateway's `N8N_WEBHOOK_URL` to the **public** webhook (the macvlan trap from Phase 5 applies
      to the gateway too — do **not** use n8n's LAN IP). Leaving it blank keeps the gateway in
      orchestrator-direct **test mode** (Parler, no brain).
- [ ] Recreate the gateway (env-only change, no `--build` needed).

**Gate 6:** press-to-talk on the device → Whisper STT → n8n → a character reply streamed back / heard on
Sonos. (On-device speaker output depends on working amp hardware.)

---

## Definition of done

- **Text install:** Gate 5 passes — a webhook POST produces a mood-matched character reply on Sonos.
- **Full voice install:** Gate 6 also passes.

When finished, follow the repo conventions: bump `VERSION` + `CHANGELOG.md`, keep `docs/` and the Wiki in
sync, and only push once the session is confirmed working.

---

## Quick reference — where the detail lives

| Need | Page |
|---|---|
| The 9 services, ports, full env table, build/deploy traps | [Docker Containers](Docker-Containers) |
| Unraid install, shared network, volumes | [Unraid Template](Unraid-Template) |
| Diagrams + request lifecycle | [Architecture & Workflow](Architecture-and-Workflow) |
| Orchestrator API (`/speak`, `/speak-multi`, `/voices`, `/admin/scan-references`) | [Service: Orchestrator](Service-Orchestrator) |
| Operator web UI (health, config, voices, clip capture) | [Admin Console](Admin-Console) |
| Sourcing reference clips (the bottleneck) | [Admin Console → Clip Capture](Admin-Console-Clip-Capture) |
| What each short code (C1, C2/C3, J3 …) means | [Module Reference](Module-Reference) |
