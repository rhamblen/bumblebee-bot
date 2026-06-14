import os
import uuid
import logging
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from TTS.api import TTS

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

tts_instance: TTS = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tts_instance
    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info(f"Loading XTTS v2 on {device} ...")
    # Downloads ~2GB to COQUI_TTS_HOME on first run, cached after that
    tts_instance = TTS("tts_models/multilingual/multi-dataset/xtts_v2").to(device)
    log.info("XTTS v2 ready")
    yield


app = FastAPI(lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str
    speaker_wav: str   # absolute path inside container, e.g. /media/references/optimus_prime.wav
    language: str = "en"
    output_dir: str = "/media/generated"


class TTSResponse(BaseModel):
    output_path: str


@app.post("/tts", response_model=TTSResponse)
async def synthesize(req: TTSRequest):
    if not os.path.exists(req.speaker_wav):
        raise HTTPException(status_code=400, detail=f"Reference WAV not found: {req.speaker_wav}")

    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, f"{uuid.uuid4()}.wav")

    try:
        tts_instance.tts_to_file(
            text=req.text,
            speaker_wav=req.speaker_wav,
            language=req.language,
            file_path=output_path,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    return TTSResponse(output_path=output_path)


@app.get("/health")
async def health():
    device = "cuda" if torch.cuda.is_available() else "cpu"
    gpu_name = torch.cuda.get_device_name(0) if device == "cuda" else None
    return {"status": "ok", "device": device, "gpu": gpu_name}
