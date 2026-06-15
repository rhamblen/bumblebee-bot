# Admin Console · Config tab

> One of the five tabs of the [Admin Console](Admin-Console.md). A per-container env view, validator, and editable `.env` surface.

## What it shows

The tab is **data-driven**: it walks every service's compose `environment:` block and, for each variable, shows the **resolved value** and where it comes from:

- **`.env`** — an override is set
- **`default`** — a `${VAR:-default}` fallback in compose
- **`compose`** — a bare literal

Fields are grouped by container. **What each variable means, its format and valid values** is in the canonical [Environment variable reference](Docker-Containers.md#environment-variable-reference).

## What's editable

A variable is editable **only when compose references it as `${VAR}` or `${VAR:-default}`** — i.e. it's sourced from `.env`. Editing it and pressing **💾 Save .env** writes the value to `.env` (`POST /api/env`).

A bare literal (`FOO=http://x`) is shown **read-only**; to make it editable you convert it to `${FOO:-http://x}` in compose — done deliberately, one section at a time, so the change surface stays reviewable.

**Secrets** (e.g. `N8N_API_KEY`, `OTA_WS_TOKEN`) are masked (`••••••••`); a save that sends the mask back unchanged is skipped, so secrets are never round-tripped or clobbered. The writer updates only the managed keys and **preserves every other line and comment** in `.env`.

## Applying changes

Env vars are read at container **start**, so a save takes effect when you **recreate** the affected container(s) — this is still "generate config, you build", never a hot-push into a running process. That's consistent with the console's [design principle](Admin-Console.md): the git-tracked compose/`.env` stays the single source of truth.

## Validator findings

The tab flags configuration problems inline:

- `${VAR}` referenced in compose but **absent from `.env`** — a hard finding for a bare `${VAR}`, a soft warning for `${VAR:-default}`.
- A **missing `.env`** entirely.

## Drift check — "needs recreate"

Because env vars are interpolated at container **create** time, editing `.env` doesn't reach a *running* process until that container is recreated — a `restart` is **not** enough. The tab makes this visible: it compares each service's **expected** value (compose, with `${VAR}` resolved from `.env`) against what the container is **actually running** (its Docker `Config.Env`), read over a **read-only `/var/run/docker.sock`** mount.

When they differ you get a red banner listing the affected containers, plus an inline **⚠ running: … — recreate to apply** badge on each stale field. Secrets are masked. If the socket isn't mounted the check shows "drift check unavailable" and the rest of the tab is unaffected.

> **Why a socket mount:** reading another container's running env needs the Docker API. The mount is **read-only**, but docker-socket access is powerful (≈ root on the host) — a deliberate tradeoff for a homelab operator pane.

## Roadmap for this tab

- **More editable fields** — Phase 1 edits the `${VAR}`-backed values (the n8n keys). Next: convert high-value compose literals (GPU pins, service URLs, VAD/silence tuning) to `${VAR:-default}`.
- **Live config store** — move *tunable behavioural* values (VAD/silence, TTS retries, whisper language) into a store services re-read on a `/reload`, so they apply without a recreate; `.env` keeps only what genuinely needs a rebuild.
