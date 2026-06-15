# Service: Orchestrator

> Part of [Docker Containers](Docker-Containers.md). The hub of the synthesis half of the stack.

| | |
|---|---|
| **Container** | `bumblebee-orchestrator` |
| **Port** | 5005 |
| **GPU** | — (CPU only; it *calls* the GPU engines) |
| **Network** | `bumblebee_default` |
| **Source** | `docker/orchestrator/server.py` (FastAPI) |

## Role

The orchestrator is the single front door to voice synthesis. n8n (or the gateway in test mode) hands it **text + a voice spec**; it decides which TTS engine to use, renders each segment, runs the **vintage filter**, stitches the segments into one file, and serves the result over HTTP so a player (Sonos) can fetch it. It also serves the live voice table and runs the reference-clip rescan.

It holds no model weights of its own — it's pure routing, FFmpeg, and file serving.

## Inputs

- **`POST /speak-multi`** (the production path, from n8n's *Call Orchestrator* node): a list of segments, each with `text` and a voice spec (`tts_engine`, `reference_clip`, `voice_description`, `exaggeration`).
- **`POST /speak`** (single segment; used by the gateway's test mode and internally).
- **`POST /admin/scan-references`** (from the [Admin Console](Admin-Console.md) / a manual trigger): rescan the references dir.
- Reads `character_descriptor.json` and the `references/` clips from the shared `/media` mount.

## Outputs

- A **vintage-filtered WAV** written to `MEDIA_DIR`, returned as a **public URL** (`PUBLIC_BASE_URL/<uuid>.wav`) that the playback device fetches.
- Per-run **timings** (`generate_ms` / `filter_ms` per segment, `synth_ms`, `concat_ms`, `parallel`, `skipped`) that flow back through n8n into the Admin Console's [Workflow I/O log](Admin-Console-Workflow-IO.md).
- The live voice table on `GET /voices`.

## API

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/speak` | Render one segment → filtered WAV, return URL + timing (`keep_raw:true` also keeps the pre-filter WAV and returns `url_raw`) |
| `POST` | `/speak-multi` | Render N segments concurrently, concat into one WAV (0.6s gaps) |
| `GET` | `/voices` | Serve the live `character_descriptor.json` voice table |
| `POST` | `/admin/scan-references` | Rescan `references/`, update each character's clip status, speak a confirmation |
| `POST` | `/admin/voice-description` | Update one character's Parler `voice_description` (`{name, voice_description}`) and persist the table — used by the [Admin Console Voices tab](Admin-Console-Voices.md#editing-a-parler-description) |
| `GET` | `/health` | Liveness (`{"status":"ok"}`) |
| `GET` | `/files/<uuid>.wav` | Static serving of rendered audio (what Sonos fetches) |

## Engine selection (per segment)

The orchestrator picks the engine itself, so callers don't have to know what's on disk:

1. An explicit `tts_engine` of `xtts` or `chatterbox` is honoured (both require a `reference_clip`).
2. Otherwise, if `reference_clip` **exists on disk** → **F5** (cloning).
3. Otherwise → **Parler** (described voice from `voice_description`).

This is the **auto-fallback**: a character whose clip is missing at render time silently renders via Parler instead of erroring. See [Character & Response Table](Character-Response-Table.md#the-clip-gate-important) for how clip status drives casting, and [TTS Options](TTS-Options.md) for the engine trade-offs.

## How a multi-segment request is rendered

```
/speak-multi {segments[]}
  → synthesize() each segment CONCURRENTLY (asyncio.gather)
      → choose engine → POST to that TTS service → raw WAV
      → FFmpeg vintage filter (highpass 300 / lowpass 3000 / echo / +volume)
      → if a non-WAV reference clip is needed, resolve via audio-converter (cached)
  → concat finals into one WAV (resample 22050 mono s16, 0.6s gap between)
  → return {url, count, skipped, timings}
```

**Per-segment failure guard (JB2).** Each engine call retries once on a transient failure (`TTS_RETRIES` / `TTS_RETRY_BACKOFF`). A segment that *still* fails is **dropped from the concat** rather than aborting the whole reply — so one engine hiccup never silences the response. It only returns `502` if **every** segment fails. Segments are synthesised concurrently because generation dominates wall time; `parallel`/`synth_ms` in the response reflect that.

## The vintage filter

Every rendered segment is run through FFmpeg to sound like an intercepted broadcast pulled out of the static:

```
highpass=f=300, lowpass=f=3000   # telephone/radio band
aecho=0.5:0.5:60:0.12            # short echo
volume=3.0                        # gain back the band-limited level
-ar 22050                         # mono 22050 Hz out
```

This is the audible signature of the [concept](Concept-and-Lore.md) — Bumblebee can only "replay" what he intercepted.

## Reference scan (`/admin/scan-references`)

Walks each character's folder under `REFERENCES_DIR`, picks a clip (the descriptor's predicted filename if present, else newest by mtime), updates `reference_clip` / `clip_on_disk`, recomputes the `clips_on_disk` / `parler_only` counts, writes the table back atomically, and **speaks a spoken confirmation** ("Reference check complete. 2 new voices added: …"). Folders with audio that match no character are reported as `unmatched_folders` (not auto-added).

## Configuration

All env vars (`F5_TTS_URL`, `PARLER_TTS_URL`, `COQUI_TTS_URL`, `CHATTERBOX_URL`, `AUDIO_CONVERTER_URL`, `MEDIA_DIR`, `PUBLIC_BASE_URL`, `DESCRIPTOR_PATH`, `REFERENCES_DIR`, plus the in-code `TTS_RETRIES` / `TTS_RETRY_BACKOFF`) are documented in the canonical [Environment variable reference](Docker-Containers.md#environment-variable-reference).

> `PUBLIC_BASE_URL` must be **LAN-reachable by the playback device** — it's the host IP, not the docker hostname, because Sonos is off the docker network.
