# Bumblebee Bot — Project Brief for Claude Code

## Concept

Inspired by the Transformers character Bumblebee, who communicates by playing clips from TV and radio. The user types a message and an AI pipeline interprets its meaning, finds matching audio clips (song extracts, old TV broadcasts, radio shows), and plays them in sequence on Amazon Echo Dot devices via Home Assistant.

---

## How It Works (End-to-End)

1. User types a message (e.g. "I'm lost" or "everything is going to be okay")
2. Local LLM interprets the emotional/semantic meaning and generates search queries
3. Vector database finds matching transcript segments (songs, TV, radio)
4. ffmpeg cuts the exact audio clips from source files
5. Clips are served as temporary audio URLs via a local HTTP server
6. Home Assistant plays the clips in sequence on target Echo Dot device(s)

---

## Component Breakdown

### 1. Local LLM (Message Interpretation)
- **Tool**: Ollama running a model like llama3 or mistral
- **GPU**: NVIDIA CUDA card — Ollama auto-detects and uses it
- **Role**: Takes user's typed message → outputs semantic meaning + 2-3 search queries to find matching clips
- **API**: Ollama exposes OpenAI-compatible REST API at localhost:11434

### 2. Content Sources & Transcription
- **Primary source**: Internet Archive (archive.org)
  - TV News Archive: 70+ years of broadcasts, many with existing timestamped captions
  - Old Time Radio collection: Jack Benny, Orson Welles, thousands of shows
  - Free API, direct media file URLs, supports byte-range requests (seek without full download)
- **Secondary source**: Musixmatch API — song lyrics with millisecond-level timestamps
- **Transcription**: OpenAI Whisper (faster-whisper library) running locally with CUDA
  - Input: audio file URL from Archive.org
  - Output: word-level timestamped transcript
  - Speed: ~10-50x faster on GPU vs CPU

### 3. Vector Database (Semantic Search)
- **Tool**: Qdrant (runs in Docker)
- **What's stored**: Each 10-30 second transcript segment as a vector embedding, with metadata: source_url, start_ms, end_ms, transcript_text, source_type (tv/radio/song)
- **Embedding model**: sentence-transformers running locally (e.g. all-MiniLM-L6-v2)
- **Query flow**: LLM-generated search queries → embeddings → Qdrant similarity search → top N matching segments returned

### 4. Python Sidecar Service (Flask API)
Handles the tasks n8n can't do natively:

- `POST /transcribe` — takes an Archive.org URL, runs Whisper with CUDA, chunks transcript into segments, stores embeddings in Qdrant
- `POST /clip` — takes {url, start_ms, end_ms}, runs ffmpeg to cut that exact segment, saves to temp folder, returns local HTTP URL
- `POST /search` — takes a text query, returns top matching segments from Qdrant
- Static file serving for cut audio clips

**Dependencies**: faster-whisper, sentence-transformers, qdrant-client, flask, ffmpeg-python

### 5. Orchestration (n8n)
n8n workflow triggered by webhook (user types message):

1. **Webhook node** — receives user message
2. **AI Agent node** → Ollama — interprets meaning, generates search queries
3. **HTTP Request node** → Python sidecar /search — returns matching clip metadata
4. **HTTP Request node** → Python sidecar /clip (×N clips) — cuts and serves audio files
5. **Home Assistant node** — calls media_player.play_media in sequence on named Echo Dot(s)

### 6. Playback (Home Assistant → Echo Dots)
- Echo Dots with Spotify linked also work as Spotify Connect devices
- Home Assistant Alexa Media Player integration allows `media_player.play_media` calls with arbitrary audio URLs
- No custom Alexa Skill required
- Clips play in sequence — n8n manages timing between clips

### 7. Spotify Integration (songs only)
- Spotify Web API (Premium required) for music clips
- Musixmatch provides timestamped lyrics → find which second a lyric appears
- Spotify `start_playback` API with `position_ms` to seek to exact lyric
- Alexa Echo Dots appear as Spotify Connect devices, can be targeted by device ID
- n8n manages timed pause after extract plays

---

## Infrastructure Summary

| Component | Tool | Runs On |
|---|---|---|
| LLM inference | Ollama + llama3 | Local, CUDA GPU |
| Transcription | faster-whisper | Local, CUDA GPU |
| Vector DB | Qdrant | Docker |
| Orchestration | n8n | Docker |
| Clip extraction | ffmpeg via Python Flask API | Local |
| Playback | Home Assistant | Existing HA instance |
| Echo Dot control | Alexa Media Player (HA integration) | Existing |
| Song lyrics timestamps | Musixmatch API | Cloud API |
| Content archive | Internet Archive API | Cloud (free) |

---

## Suggested Build Order (Stages)

**Stage 1** — Indexing pipeline
- Set up Qdrant + sentence-transformers
- Build /transcribe endpoint: Archive.org URL → Whisper → chunk → embed → store in Qdrant
- Curate a small starting collection (e.g. 10 Old Time Radio episodes)

**Stage 2** — Search + clip extraction
- Build /search endpoint: query → Qdrant → return segment metadata
- Build /clip endpoint: ffmpeg cuts segment → serve via local HTTP

**Stage 3** — Playback wiring
- Configure Home Assistant media_player.play_media for Echo Dots
- Test with a hardcoded clip URL

**Stage 4** — n8n orchestration
- Build the full n8n workflow: webhook → Ollama → search → clip → HA playback
- Add multi-clip sequencing logic

**Stage 5** — Spotify integration
- Add Musixmatch lyric timestamp lookup
- Add Spotify playback control (position_ms seek, timed pause)
- Fold songs into the same n8n pipeline alongside TV/radio clips

**Stage 6** — Tuning
- Improve LLM prompt for creative multi-clip narrative sequencing
- Expand indexed content collections
- Add clip caching to reduce latency

---

## Key Technical Constraints

- Spotify playback API requires **Spotify Premium**
- Archive.org content copyright varies — pre-1928 is public domain; for personal home use later content is generally fine
- Latency will be several seconds (LLM + vector search + ffmpeg) — acceptable for a home system
- Multi-clip stitching from Spotify is tricky (no native per-track start/stop in queue) — manage transitions via timed n8n API calls
- Echo Dots must have Spotify linked in the Alexa app to appear as Spotify Connect devices
