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

# bf16 roughly halves both generation time and VRAM vs fp32. bf16 (not fp16) avoids
# the NaN/overflow issues fp16 can hit, and the 3090 (where Parler now runs) supports it.
DTYPE = torch.bfloat16 if (DEVICE == "cuda" and torch.cuda.is_bf16_supported()) else torch.float32
# torch.compile can speed generation further but is finicky with variable-length
# autoregressive decode (recompiles), so it's opt-in via env until proven on this model.
COMPILE = os.environ.get("PARLER_COMPILE", "0") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model, tokenizer
    from parler_tts import ParlerTTSForConditionalGeneration
    from transformers import AutoTokenizer

    log.info(f"Loading Parler TTS ({MODEL_NAME}) on {DEVICE} as {DTYPE} ...")
    model = ParlerTTSForConditionalGeneration.from_pretrained(
        MODEL_NAME, torch_dtype=DTYPE).to(DEVICE)
    # The DAC audio decoder (vocoder) uses weight_norm, whose CUDA kernel is NOT
    # implemented for bfloat16 — it raises at decode time. Keep that (fast) stage in
    # fp32; the slow autoregressive transformer still runs in bf16, which is where the
    # speed/VRAM win lives. Audio codes are integer indices, so there's no dtype clash.
    if DTYPE != torch.float32:
        model.audio_encoder.to(torch.float32)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

    if COMPILE and DEVICE == "cuda":
        try:
            model.forward = torch.compile(model.forward)
            log.info("torch.compile enabled on model.forward")
        except Exception as e:  # noqa: BLE001 - never let compile setup break startup
            log.warning("torch.compile setup failed, continuing uncompiled: %s", e)

    # Warm up: the first generate() pays lazy CUDA init (and any compile) cost — do it
    # now at startup so it never lands on a user's request.
    try:
        _d = tokenizer("A calm clear broadcast voice.", return_tensors="pt")
        _p = tokenizer("Warming up the channel.", return_tensors="pt")
        with torch.no_grad():
            model.generate(
                input_ids=_d.input_ids.to(DEVICE),
                attention_mask=_d.attention_mask.to(DEVICE),
                prompt_input_ids=_p.input_ids.to(DEVICE),
                prompt_attention_mask=_p.attention_mask.to(DEVICE),
            )
        log.info("Parler TTS warmed up")
    except Exception as e:  # noqa: BLE001 - warmup is best-effort
        log.warning("Parler warmup failed (non-fatal): %s", e)

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

        # generation shape: (1, num_samples) — cast to float32 first (numpy has no
        # bfloat16, so a bf16 model's output must be upcast before .numpy()), squeeze to 1D.
        audio = generation.to(torch.float32).cpu().numpy().squeeze()
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
    return {"status": "ok", "device": DEVICE, "gpu": gpu_name, "model": MODEL_NAME,
            "dtype": str(DTYPE), "compiled": COMPILE}
