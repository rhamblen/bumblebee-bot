# Admin Console · Clip Capture tab

> One of the five tabs of the [Admin Console](Admin-Console.md). Source F5 reference clips from YouTube — preview, accept, done.

## Why this tab exists

The single biggest limiter on voice quality is **clip coverage**: a character with no reference clip on disk can't be cloned, so it falls back to a described Parler voice (see [Voices tab](Admin-Console-Voices.md)). For a long time only a handful of the ~36 characters had clips. This tab makes adding one a one-minute, point-and-click job — no SSH, no scripts, no leaving the browser.

It is deliberately an **accept/reject vetting station**, not a fire-and-forget downloader: you hear the clip before it ever enters the library.

## What it shows

A table of the **Parler-only voices** — every character from the orchestrator's [`GET /voices`](Service-Orchestrator.md#api) where `clip_on_disk` is false, i.e. exactly the voices still missing a clip. (As clips are added, characters drop off this table and appear as F5 on the [Voices tab](Admin-Console-Voices.md).) Each row is a work item with editable inputs:

| Field | Notes |
|---|---|
| **YouTube URL** | the source video |
| **start** | clip start, `mm:ss` (or plain seconds) |
| **secs** | clip **duration**, defaults to **30s** |

## The flow

1. **▶ Execute** — downloads just `start → start+duration` (yt-dlp `--download-sections`) and normalises it to the F5 reference format (**22050 Hz, mono, s16**) into a **staging area**. Nothing touches the clip library yet.
2. **Preview** — the row shows a native **`<audio controls>`** player. Play and replay the candidate as many times as you like before committing.
3. **✓ Accept** — moves the staged clip into `references/<character>/<character>_clip_NN.wav` (next free number) and calls the orchestrator's [`/admin/scan-references`](Service-Orchestrator.md#reference-scan-adminscan-references). That re-scan flips the character **Parler → F5 immediately** — the cell confirms "→ now F5".
4. **✗ Reject** — discards the staged file and leaves the row's inputs editable, so you can tweak the URL/start/duration and re-Execute.

The table **only reloads on the manual ↻ refresh** — an accepted row stays visible (so you can see what you just did) until you refresh, at which point it's gone from the list because it's now an F5 voice.

## How it runs (no VPN)

The console downloads clips **itself**, server-side in the `admin-console` container — there's no separate downloader service. The image carries `ffmpeg`, `yt-dlp[default]`, and a **deno** JS runtime, and the media share is mounted **read-write** so accepted clips land where the orchestrator reads them.

> **Why `yt-dlp[default]` and deno?** Modern YouTube requires solving an "n challenge" in JavaScript. yt-dlp needs **both** a JS runtime (**deno**) **and** the **`yt-dlp-ejs`** solver scripts — the `[default]` extra bundles the latter. Plain `yt-dlp` fails with `n challenge solving failed … This video is not available`.
>
> **No VPN needed.** UR1 shares the home's public IP, so a server-side download has the same geo/IP as running it on a desktop. (The old `vpn-converter` container is retired.)

If a *specific* video still reports "not available" after the toolchain is in place, that video is genuinely restricted (private / age- or region-gated / premium) — not a pipeline fault. For the occasional gated video, mount a `cookies.txt` at `/cookies/cookies.txt` and yt-dlp will use it automatically.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/clip-ready` | readiness — are `yt-dlp`/`ffmpeg` present and the references dir writable? Drives the capture-unavailable banner. |
| `POST /api/clip-capture` | download + normalise `{character, url, start, duration}` into staging |
| `GET /api/clip-preview` | stream a staged WAV for the player |
| `POST /api/clip-accept` | move staged clip into `references/<folder>/` + trigger the orchestrator re-scan |
| `POST /api/clip-reject` | discard a staged clip |

Inputs are guarded — folder/character names must match `^[A-Za-z0-9_]+$` and staged ids are 32-hex — so a request can't escape the references/staging directories.

## Roadmap for this tab

- **cookies.txt mount** for gated videos (the only thing the no-VPN path can't reach) — optional, wire on demand.

> Like every tab, this one **lazy-loads** on first open with its own **↻ refresh** and "last updated" timestamp.
