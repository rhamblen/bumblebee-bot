import os
import uuid
import logging
from contextlib import asynccontextmanager

import torch
import soundfile as sf
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

model = None
tokenizer = None
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

MODEL_NAME = "parler-tts/parler-tts-mini-v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model, tokenizer
    from parler_tts import ParlerTTSForConditionalGeneration
    from transformers import AutoTokenizer

    log.info(f"Loading Parler TTS ({MODEL_NAME}) on {DEVICE} ...")
    model = ParlerTTSForConditionalGeneration.from_pretrained(MODEL_NAME).to(DEVICE)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    log.info("Parler TTS ready")
    yield


app = FastAPI(lifespan=lifespan)


class TTSRequest(BaseModel):
    text: str
    voice_description: str
    output_dir: str = "/media/generated"


class TTSResponse(BaseModel):
    output_path: str


@app.post("/tts", response_model=TTSResponse)
async def synthesize(req: TTSRequest):
    os.makedirs(req.output_dir, exist_ok=True)
    output_path = os.path.join(req.output_dir, f"{uuid.uuid4()}_raw.wav")

    try:
        # Tokenize description with attention mask to avoid garbage generation
        desc = tokenizer(req.voice_description, return_tensors="pt")
        input_ids = desc.input_ids.to(DEVICE)
        attention_mask = desc.attention_mask.to(DEVICE)

        # Tokenize speech text separately
        prompt = tokenizer(req.text, return_tensors="pt")
        prompt_input_ids = prompt.input_ids.to(DEVICE)
        prompt_attention_mask = prompt.attention_mask.to(DEVICE)

        with torch.no_grad():
            generation = model.generate(
                input_ids=input_ids,
                attention_mask=attention_mask,
                prompt_input_ids=prompt_input_ids,
                prompt_attention_mask=prompt_attention_mask,
            )

        # generation shape: (1, num_samples) — squeeze to 1D numpy array
        audio = generation.cpu().numpy().squeeze()
        log.info(f"Audio stats — shape: {audio.shape}, dtype: {audio.dtype}, "
                 f"min: {audio.min():.4f}, max: {audio.max():.4f}, "
                 f"rms: {(audio**2).mean()**0.5:.4f}, "
                 f"duration: {len(audio)/model.config.sampling_rate:.1f}s, "
                 f"sample_rate: {model.config.sampling_rate}")
        sf.write(output_path, audio, model.config.sampling_rate)
        log.info(f"Saved: {output_path}")

    except Exception as e:
        log.exception("Parler TTS inference failed")
        raise HTTPException(status_code=500, detail=str(e))

    return TTSResponse(output_path=output_path)


@app.get("/health")
async def health():
    gpu_name = torch.cuda.get_device_name(0) if DEVICE == "cuda" else None
    return {"status": "ok", "device": DEVICE, "gpu": gpu_name, "model": MODEL_NAME}
