import logging
import os
import subprocess
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI()

SUPPORTED_EXTENSIONS = {".mp3", ".mp4", ".ogg", ".wav", ".m4a", ".aac", ".flac", ".webm"}

# TTS engines expect 22050 Hz mono WAV — good balance of quality vs file size
TARGET_SAMPLE_RATE = 22050


class ConvertRequest(BaseModel):
    input_path: str   # absolute path inside container, e.g. /media/references/raw/john_wayne.mp3
    output_dir: str   # directory to write the converted WAV into


class ConvertResponse(BaseModel):
    output_path: str


@app.post("/convert", response_model=ConvertResponse)
def convert(req: ConvertRequest):
    if not os.path.exists(req.input_path):
        raise HTTPException(status_code=400, detail=f"Input file not found: {req.input_path}")

    ext = os.path.splitext(req.input_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {ext}. Supported: {sorted(SUPPORTED_EXTENSIONS)}")

    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, f"{uuid.uuid4()}.wav")

    cmd = [
        "ffmpeg", "-y",
        "-i", req.input_path,
        "-ac", "1",                        # mono
        "-ar", str(TARGET_SAMPLE_RATE),    # 22050 Hz
        "-sample_fmt", "s16",              # 16-bit PCM
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("ffmpeg stderr: %s", result.stderr)
        raise HTTPException(status_code=500, detail=f"ffmpeg conversion failed: {result.stderr[-500:]}")

    log.info("Converted %s -> %s", req.input_path, output_path)
    return ConvertResponse(output_path=output_path)


class DownloadRequest(BaseModel):
    url: str          # YouTube (or any yt-dlp supported) URL
    output_dir: str   # absolute container path to write the WAV into
    filename: str     # desired output filename without extension, e.g. "winston_churchill_ref_01"
    start_time: str | None = None  # e.g. "02:11" — trim start (MM:SS or HH:MM:SS)
    end_time: str | None = None    # e.g. "02:32" — trim end
    proxy: str | None = None       # e.g. "socks5://user:pass@us123.nordvpn.com:1080"


@app.post("/download", response_model=ConvertResponse)
def download(req: DownloadRequest):
    if not req.url.strip():
        raise HTTPException(status_code=400, detail="url is required")
    if not req.filename.strip():
        raise HTTPException(status_code=400, detail="filename is required")

    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, f"{req.filename}.wav")

    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-x",
        "--audio-format", "wav",
        "--postprocessor-args", f"ffmpeg:-ac 1 -ar {TARGET_SAMPLE_RATE} -sample_fmt s16",
    ]

    # Trim to timestamp range if provided — avoids downloading the full video
    if req.start_time and req.end_time:
        cmd += ["--download-sections", f"*{req.start_time}-{req.end_time}"]
        log.info("Trimming %s to %s-%s", req.url, req.start_time, req.end_time)
    elif req.start_time or req.end_time:
        raise HTTPException(status_code=400, detail="Both start_time and end_time are required when trimming")

    if req.proxy:
        cmd += ["--proxy", req.proxy]
        log.info("Using proxy: %s", req.proxy.split("@")[-1])  # log host only, not credentials

    cmd += ["-o", output_path, req.url]

    log.info("Downloading %s -> %s", req.url, output_path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("yt-dlp stderr: %s", result.stderr)
        raise HTTPException(status_code=500, detail=f"yt-dlp failed: {result.stderr[-500:]}")

    if not os.path.exists(output_path):
        raise HTTPException(status_code=500, detail="Download succeeded but output file not found")

    log.info("Downloaded and converted %s -> %s", req.url, output_path)
    return ConvertResponse(output_path=output_path)


@app.get("/health")
def health():
    return {"status": "ok"}
