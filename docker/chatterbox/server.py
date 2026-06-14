import os
import uuid
import logging
from contextlib import asynccontextmanager

import torch
import torchaudio
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

model = None
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model
    from chatterbox.tts import ChatterboxTTS
    log.info(f"Loading Chatterbox TTS on {DEVICE} ...")
    model = ChatterboxTTS.from_pretrained(device=DEVICE)
    log.info("Chatterbox TTS ready")
    yield


app = FastAPI(lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str
    speaker_wav: str            # path to reference WAV inside container, e.g. /media/references/john_wayne.wav
    exaggeration: float = 0.4   # 0.0 = flat/calm, 1.0 = highly expressive/emotional
    cfg_weight: float = 0.5     # classifier-free guidance — higher = more faithful to description
    output_dir: str = "/media/generated"


class TTSResponse(BaseModel):
    output_path: str


@app.post("/tts", response_model=TTSResponse)
async def synthesize(req: TTSRequest):
    if not os.path.exists(req.speaker_wav):
        raise HTTPException(status_code=400, detail=f"Reference WAV not found: {req.speaker_wav}")

    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, f"{uuid.uuid4()}_raw.wav")

    try:
        wav = model.generate(
            req.text,
            audio_prompt_path=req.speaker_wav,
            exaggeration=req.exaggeration,
            cfg_weight=req.cfg_weight,
        )
        torchaudio.save(output_path, wav, model.sr)
        log.info(f"Saved: {output_path} (sr={model.sr})")
    except Exception as e:
        log.exception("Chatterbox inference failed")
        raise HTTPException(status_code=500, detail=str(e))

    return TTSResponse(output_path=output_path)


@app.get("/health")
async def health():
    gpu_name = torch.cuda.get_device_name(0) if DEVICE == "cuda" else None
    return {"status": "ok", "device": DEVICE, "gpu": gpu_name}
