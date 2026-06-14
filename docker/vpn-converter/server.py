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
TARGET_SAMPLE_RATE = 22050


class ConvertRequest(BaseModel):
    input_path: str
    output_dir: str


class ConvertResponse(BaseModel):
    output_path: str


@app.post("/convert", response_model=ConvertResponse)
def convert(req: ConvertRequest):
    if not os.path.exists(req.input_path):
        raise HTTPException(status_code=400, detail=f"Input file not found: {req.input_path}")

    ext = os.path.splitext(req.input_path)[1].lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {ext}")

    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, f"{uuid.uuid4()}.wav")

    cmd = [
        "ffmpeg", "-y",
        "-i", req.input_path,
        "-ac", "1",
        "-ar", str(TARGET_SAMPLE_RATE),
        "-sample_fmt", "s16",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("ffmpeg stderr: %s", result.stderr)
        raise HTTPException(status_code=500, detail=f"ffmpeg failed: {result.stderr[-500:]}")

    log.info("Converted %s -> %s", req.input_path, output_path)
    return ConvertResponse(output_path=output_path)


class DownloadRequest(BaseModel):
    url: str
    output_dir: str
    filename: str
    start_time: str | None = None
    end_time: str | None = None


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

    # Use browser cookies if present — required for YouTube bot detection bypass
    cookies_file = "/cookies/cookies.txt"
    if os.path.exists(cookies_file):
        cmd += ["--cookies", cookies_file]
        log.info("Using cookies file: %s", cookies_file)

    if req.start_time and req.end_time:
        cmd += ["--download-sections", f"*{req.start_time}-{req.end_time}"]
        log.info("Trimming %s to %s-%s", req.url, req.start_time, req.end_time)
    elif req.start_time or req.end_time:
        raise HTTPException(status_code=400, detail="Both start_time and end_time required for trimming")

    # No --proxy arg: traffic routes through the OpenVPN tunnel at the OS level
    cmd += ["-o", output_path, req.url]

    log.info("Downloading %s -> %s", req.url, output_path)
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("yt-dlp stderr: %s", result.stderr)
        raise HTTPException(status_code=500, detail=f"yt-dlp failed: {result.stderr[-500:]}")

    if not os.path.exists(output_path):
        raise HTTPException(status_code=500, detail="Download succeeded but output file not found")

    log.info("Downloaded %s -> %s", req.url, output_path)
    return ConvertResponse(output_path=output_path)


@app.get("/health")
def health():
    # Report VPN status alongside service health
    tun_up = subprocess.run(["ip", "link", "show", "tun0"], capture_output=True).returncode == 0
    return {"status": "ok", "vpn": "up" if tun_up else "down"}
