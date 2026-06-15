# Service: Audio Converter

> Part of [Docker Containers](Docker-Containers.md). Turns arbitrary reference clips into the WAV the cloners need.

| | |
|---|---|
| **Container** | `audio-converter` |
| **Port** | 5007 |
| **GPU** | — (CPU) |
| **Network** | `bumblebee_default` |
| **Source** | `docker/audio-converter/server.py` (FastAPI + FFmpeg + yt-dlp) |

## Role

The voice-cloning engines (F5, XTTS, Chatterbox) all want a **22050 Hz mono 16-bit WAV** reference clip. Real-world source clips arrive as MP3/MP4/OGG/etc., or as a YouTube URL. This service normalises both into that canonical WAV so the rest of the pipeline only ever deals with one format.

## Inputs

- **`POST /convert`** — `{input_path, output_dir}`. An existing file on the shared `/media` mount → WAV.
- **`POST /download`** — `{url, output_dir, filename, start_time?, end_time?, proxy?}`. A yt-dlp-supported URL → WAV, optionally trimmed to a timestamp range and optionally fetched through a proxy.

## Outputs

- `{"output_path": "/media/.../<name>.wav"}` — a 22050 Hz mono s16 WAV on disk. (The orchestrator renames the converter's UUID output to a deterministic, cached name next to the source clip so repeat renders skip re-conversion.)

## API

| Method | Route | Purpose |
|---|---|---|
| `POST` | `/convert` | Transcode a local file to the canonical WAV |
| `POST` | `/download` | yt-dlp a URL (optionally trimmed) to the canonical WAV |
| `GET` | `/health` | Liveness |

## Supported input formats

`.mp3 .mp4 .ogg .wav .m4a .aac .flac .webm`. Anything else returns `400`.

## How it's used

- **At render time** the [orchestrator](Service-Orchestrator.md#how-a-multi-segment-request-is-rendered) calls `/convert` lazily — only when a chosen character's reference clip isn't already a WAV — and **caches** the result (same stem, `.wav`) so it converts once, not every render.
- **At sourcing time** `/download` is used to pull a snippet from a broadcast on YouTube directly into a character's references folder, trimmed to just the line you want (`--download-sections`), with credentials kept out of the logs (only the proxy host is logged).

> This service has **no env vars** — it's pure transcode plumbing.
