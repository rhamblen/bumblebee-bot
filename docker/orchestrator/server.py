import asyncio
import glob
import json
import os
import uuid
import logging

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI()

F5_TTS_URL = os.environ.get("F5_TTS_URL", "http://f5-tts:5003")
PARLER_TTS_URL = os.environ.get("PARLER_TTS_URL", "http://parler-tts:5004")
COQUI_TTS_URL = os.environ.get("COQUI_TTS_URL", "http://coqui-tts:5002")
CHATTERBOX_URL = os.environ.get("CHATTERBOX_URL", "http://chatterbox:5006")
AUDIO_CONVERTER_URL = os.environ.get("AUDIO_CONVERTER_URL", "http://audio-converter:5007")
MEDIA_DIR = os.environ.get("MEDIA_DIR", "/media/generated")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://192.168.1.33:5005/files")

# Per-segment failure guard (JB2): retry a transient TTS hiccup once before
# giving up, so a single engine 500 never silences a whole response.
TTS_RETRIES = int(os.environ.get("TTS_RETRIES", "1"))          # extra attempts after the first
TTS_RETRY_BACKOFF = float(os.environ.get("TTS_RETRY_BACKOFF", "1.5"))  # seconds between attempts

# Voice mapping table (single source of truth) + reference-clip library.
DESCRIPTOR_PATH = os.environ.get("DESCRIPTOR_PATH", "/media/character_descriptor.json")
REFERENCES_DIR = os.environ.get("REFERENCES_DIR", "/media/references")
AUDIO_EXTS = (".wav", ".mp3", ".mp4", ".m4a", ".ogg", ".opus", ".flac", ".aac")
# Parler voice used to speak the reference-scan confirmation. Easily changed.
ADMIN_VOICE_DESCRIPTION = "A clear, warm broadcast announcer. Calm, measured, mid-century radio."

# Serve generated audio files over HTTP so Sonos can reach them on the local network
os.makedirs(MEDIA_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=MEDIA_DIR), name="files")


async def apply_vintage_filter(input_path: str, output_path: str):
    """Bandpass telephone band + echo — simplified for clarity."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", "-i", input_path,
        "-af", "highpass=f=300,lowpass=f=3000,aecho=0.5:0.5:60:0.12,volume=3.0",
        "-ar", "22050",
        output_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg failed: {stderr.decode()}")


async def concat_wavs(paths: list[str], output_path: str, gap_seconds: float = 0.6):
    """Concatenate WAVs into one file with a silence gap between each.

    All inputs are normalised to 22050 Hz mono s16 first so the concat filter
    never trips on mismatched stream parameters between TTS engines.
    """
    inputs = []
    for p in paths:
        inputs += ["-i", p]

    n = len(paths)
    parts, labels = [], []
    for i in range(n):
        pad = f",apad=pad_dur={gap_seconds}" if i < n - 1 else ""
        parts.append(
            f"[{i}:a]aresample=22050,aformat=sample_fmts=s16:channel_layouts=mono{pad}[a{i}]"
        )
        labels.append(f"[a{i}]")
    filt = ";".join(parts) + ";" + "".join(labels) + f"concat=n={n}:v=0:a=1[out]"

    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filt, "-map", "[out]", "-ar", "22050",
        output_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg concat failed: {stderr.decode()}")


class SpeakRequest(BaseModel):
    text: str
    tts_engine: str = "parler"
    reference_clip: str | None = None
    voice_description: str | None = None
    exaggeration: float = 0.4       # chatterbox only: 0.0 calm → 1.0 highly expressive
    output_dir: str = MEDIA_DIR


async def resolve_reference_clip(clip_path: str, client: httpx.AsyncClient) -> str:
    """Return a WAV path for the given clip, converting and caching if needed."""
    if clip_path.lower().endswith(".wav"):
        return clip_path

    # Cached WAV sits next to the original with the same stem
    wav_path = os.path.splitext(clip_path)[0] + ".wav"
    if os.path.exists(wav_path):
        log.info("Using cached WAV: %s", wav_path)
        return wav_path

    log.info("Converting %s → %s", clip_path, wav_path)
    try:
        resp = await client.post(
            f"{AUDIO_CONVERTER_URL}/convert",
            json={"input_path": clip_path, "output_dir": os.path.dirname(clip_path)},
        )
        resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.RequestError) as e:
        raise HTTPException(status_code=502, detail=f"Audio converter error: {e}")

    converted = resp.json()["output_path"]

    # Rename UUID-named output to the deterministic cached name
    os.rename(converted, wav_path)
    log.info("Cached converted WAV at %s", wav_path)
    return wav_path


async def _post_tts_with_retry(
    client: httpx.AsyncClient, url: str, payload: dict, engine: str
) -> str:
    """POST to a TTS engine, retrying once on a transient failure (JB2).

    Returns the engine's output_path. Raises HTTPException(502) only after all
    attempts are exhausted, so a single hiccup (e.g. a Parler 500 / GPU blip)
    self-heals instead of silencing the response.
    """
    attempts = TTS_RETRIES + 1
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()["output_path"]
        except (httpx.HTTPStatusError, httpx.RequestError) as e:
            last_exc = e
            log.warning("TTS %s attempt %d/%d failed: %s", engine, attempt, attempts, e)
            if attempt < attempts:
                await asyncio.sleep(TTS_RETRY_BACKOFF)
    raise HTTPException(
        status_code=502, detail=f"TTS {engine} failed after {attempts} attempts: {last_exc}"
    )


async def synthesize(req: SpeakRequest) -> dict:
    """Render req.text to a vintage-filtered WAV and return its public URL + path.

    Shared by the /speak endpoint and the admin reference-scan confirmation.
    Engine selection: explicit xtts/chatterbox honoured; otherwise F5 if the
    reference clip exists on disk, else Parler using voice_description.
    """
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    os.makedirs(req.output_dir, exist_ok=True)
    raw_path = os.path.join(req.output_dir, f"{uuid.uuid4()}_raw.wav")
    final_path = os.path.join(req.output_dir, f"{uuid.uuid4()}.wav")

    # Dynamic engine selection: if reference_clip exists on disk use F5, otherwise Parler.
    # tts_engine field is only honoured for explicit xtts/chatterbox requests.
    if req.tts_engine in ("xtts", "chatterbox"):
        effective_engine = req.tts_engine
    elif req.reference_clip and os.path.exists(req.reference_clip):
        effective_engine = "f5"
        log.info("F5 selected — clip found: %s", req.reference_clip)
    else:
        effective_engine = "parler"
        if req.reference_clip:
            log.info("Parler selected — no clip at: %s", req.reference_clip)

    async with httpx.AsyncClient(timeout=300.0) as client:
        if effective_engine == "f5":
            speaker_wav = await resolve_reference_clip(req.reference_clip, client)
            tts_url = f"{F5_TTS_URL}/tts"
            payload = {"text": req.text, "speaker_wav": speaker_wav, "output_dir": req.output_dir}
        elif effective_engine == "parler":
            tts_url = f"{PARLER_TTS_URL}/tts"
            payload = {
                "text": req.text,
                "voice_description": req.voice_description or "A warm clear voice",
                "output_dir": req.output_dir,
            }
        elif effective_engine == "xtts":
            if not req.reference_clip:
                raise HTTPException(status_code=400, detail="reference_clip required for xtts engine")
            speaker_wav = await resolve_reference_clip(req.reference_clip, client)
            tts_url = f"{COQUI_TTS_URL}/tts"
            payload = {"text": req.text, "speaker_wav": speaker_wav, "output_dir": req.output_dir}
        elif effective_engine == "chatterbox":
            if not req.reference_clip:
                raise HTTPException(status_code=400, detail="reference_clip required for chatterbox engine")
            speaker_wav = await resolve_reference_clip(req.reference_clip, client)
            tts_url = f"{CHATTERBOX_URL}/tts"
            payload = {
                "text": req.text,
                "speaker_wav": speaker_wav,
                "exaggeration": req.exaggeration,
                "output_dir": req.output_dir,
            }
        else:
            raise HTTPException(status_code=400, detail=f"Unknown tts_engine: {effective_engine}")

        raw_path = await _post_tts_with_retry(client, tts_url, payload, effective_engine)

    try:
        await apply_vintage_filter(raw_path, final_path)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if os.path.exists(raw_path) and raw_path != final_path:
            os.remove(raw_path)

    filename = os.path.basename(final_path)
    return {"url": f"{PUBLIC_BASE_URL}/{filename}", "path": final_path}


@app.post("/speak")
async def speak(req: SpeakRequest):
    return await synthesize(req)


class MultiSegment(BaseModel):
    text: str
    tts_engine: str = "parler"
    reference_clip: str | None = None
    voice_description: str | None = None
    exaggeration: float = 0.4


class SpeakMultiRequest(BaseModel):
    segments: list[MultiSegment]
    gap_seconds: float = 0.6
    output_dir: str = MEDIA_DIR


@app.post("/speak-multi")
async def speak_multi(req: SpeakMultiRequest):
    """Render N segments and play them back-to-back as one combined WAV.

    Each segment goes through the normal synthesize() path (per-segment engine
    selection + vintage filter), then the finals are concatenated so Sonos only
    has to play a single file. Bumblebee replaying 1-3 intercepted channels.
    """
    if not req.segments:
        raise HTTPException(status_code=400, detail="at least one segment is required")

    # JB2: synthesize each segment independently; a segment that still fails
    # after its retry is dropped from the concat rather than aborting the whole
    # response. Only error out if EVERY segment fails.
    seg_results, skipped = [], 0
    total = len(req.segments)
    for i, seg in enumerate(req.segments):
        try:
            seg_results.append(await synthesize(SpeakRequest(
                text=seg.text,
                tts_engine=seg.tts_engine,
                reference_clip=seg.reference_clip,
                voice_description=seg.voice_description,
                exaggeration=seg.exaggeration,
                output_dir=req.output_dir,
            )))
        except HTTPException as e:
            skipped += 1
            log.warning(
                "speak-multi: segment %d/%d failed, skipping (%s): %r",
                i + 1, total, e.detail, seg.text[:60],
            )

    if not seg_results:
        raise HTTPException(status_code=502, detail="all segments failed to synthesize")

    if len(seg_results) == 1:
        r = seg_results[0]
        return {"url": r["url"], "count": 1, "skipped": skipped}

    paths = [r["path"] for r in seg_results]
    combined = os.path.join(req.output_dir, f"{uuid.uuid4()}.wav")
    try:
        await concat_wavs(paths, combined, req.gap_seconds)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # The combined file is what gets played; drop the per-segment finals.
        for p in paths:
            if os.path.exists(p) and p != combined:
                os.remove(p)

    return {
        "url": f"{PUBLIC_BASE_URL}/{os.path.basename(combined)}",
        "count": len(seg_results),
        "skipped": skipped,
    }


def _load_descriptor() -> dict:
    with open(DESCRIPTOR_PATH, encoding="utf-8") as f:
        return json.load(f)


@app.get("/voices")
async def voices():
    """Serve the voice mapping table so n8n can read it live each run."""
    try:
        return _load_descriptor()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"descriptor not found at {DESCRIPTOR_PATH}")
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"descriptor is not valid JSON: {e}")


def _pick_clip(char: dict, folder_dir: str) -> str | None:
    """Return the chosen reference clip path for a character folder, or None.

    Prefer the descriptor's existing predicted filename if it now exists;
    otherwise the newest audio file by mtime.
    """
    if not os.path.isdir(folder_dir):
        return None
    audio = [
        os.path.join(folder_dir, f)
        for f in os.listdir(folder_dir)
        if f.lower().endswith(AUDIO_EXTS) and os.path.isfile(os.path.join(folder_dir, f))
    ]
    if not audio:
        return None
    predicted = os.path.basename(char.get("reference_clip", ""))
    for path in audio:
        if os.path.basename(path) == predicted:
            return path
    return max(audio, key=os.path.getmtime)


@app.post("/admin/scan-references")
async def scan_references():
    """Rescan REFERENCES_DIR, update each character's reference_clip/clip_on_disk
    to match what's actually on disk, persist the table, and speak a confirmation."""
    try:
        desc = _load_descriptor()
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"descriptor not found at {DESCRIPTOR_PATH}")

    characters = desc.get("characters", [])
    new_clips, updated, matched_folders = [], [], set()

    for char in characters:
        folder = char.get("reference_folder") or char["name"]
        folder_dir = os.path.join(REFERENCES_DIR, folder)
        clip = _pick_clip(char, folder_dir)
        was_on_disk = char.get("clip_on_disk", False)

        if clip:
            matched_folders.add(folder)
            # Store as a /media-rooted path so the orchestrator's os.path.exists works.
            new_path = clip
            if new_path != char.get("reference_clip") or not was_on_disk:
                updated.append(char["name"])
            char["reference_clip"] = new_path
            char["clip_on_disk"] = True
            if not was_on_disk:
                new_clips.append(char["name"])
        else:
            char["clip_on_disk"] = False

    # Folders on disk that have audio but match no character (reported, not added).
    unmatched_folders = []
    if os.path.isdir(REFERENCES_DIR):
        for entry in sorted(os.listdir(REFERENCES_DIR)):
            d = os.path.join(REFERENCES_DIR, entry)
            if not os.path.isdir(d) or entry in matched_folders:
                continue
            if any(f.lower().endswith(AUDIO_EXTS) for f in os.listdir(d)):
                unmatched_folders.append(entry)

    desc["clips_on_disk"] = sum(1 for c in characters if c.get("clip_on_disk"))
    desc["parler_only"] = sum(1 for c in characters if not c.get("clip_on_disk"))

    # Atomic write back to the table.
    tmp = DESCRIPTOR_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(desc, f, indent=2, ensure_ascii=False)
    os.replace(tmp, DESCRIPTOR_PATH)

    # Build + speak the confirmation.
    if new_clips:
        names = ", ".join(n.replace("_", " ") for n in new_clips)
        count = len(new_clips)
        summary = (f"Reference check complete. {count} new "
                   f"{'voice' if count == 1 else 'voices'} added: {names}.")
    else:
        summary = "Reference check complete. No new voices found."

    spoken = await synthesize(SpeakRequest(
        text=summary, tts_engine="parler", voice_description=ADMIN_VOICE_DESCRIPTION,
    ))

    log.info("scan-references: new=%s updated=%s unmatched=%s", new_clips, updated, unmatched_folders)
    return {
        "summary": summary,
        "new_clips": new_clips,
        "updated": updated,
        "unmatched_folders": unmatched_folders,
        "clips_on_disk": desc["clips_on_disk"],
        "url": spoken["url"],
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
