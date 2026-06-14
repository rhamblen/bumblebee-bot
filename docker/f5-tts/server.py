import os
import uuid
import logging
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Imported at startup to avoid slow cold path on first request
f5tts = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global f5tts
    from f5_tts.api import F5TTS
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info(f"Loading F5-TTS on {device} ...")
    f5tts = F5TTS(device=device)
    log.info("F5-TTS ready")
    yield


app = FastAPI(lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str
    speaker_wav: str        # absolute path inside container, e.g. /media/references/john_wayne.wav
    output_dir: str = "/media/generated"
    speed: float = 1.0


class TTSResponse(BaseModel):
    output_path: str


@app.post("/tts", response_model=TTSResponse)
async def synthesize(req: TTSRequest):
    if not os.path.exists(req.speaker_wav):
        raise HTTPException(status_code=400, detail=f"Reference WAV not found: {req.speaker_wav}")

    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, f"{uuid.uuid4()}.wav")

    try:
        wav, sr, _ = f5tts.infer(
            ref_file=req.speaker_wav,
            ref_text="",          # F5-TTS transcribes the reference automatically
            gen_text=req.text,
            speed=req.speed,
        )
        import soundfile as sf
        sf.write(output_path, wav, sr)
    except Exception as e:
        log.exception("F5-TTS inference failed")
        raise HTTPException(status_code=500, detail=str(e))

    return TTSResponse(output_path=output_path)


@app.get("/health")
async def health():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    gpu_name = torch.cuda.get_device_name(0) if device == "cuda" else None
    return {"status": "ok", "device": device, "gpu": gpu_name}
