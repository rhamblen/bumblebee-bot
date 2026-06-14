import os
import tempfile
from fastapi import FastAPI, UploadFile, File, HTTPException
from faster_whisper import WhisperModel

app = FastAPI()

MODEL_DIR = os.environ.get("WHISPER_MODEL_DIR", "/whisper-models")
MODEL_NAME = os.environ.get("WHISPER_MODEL", "base")
# Pin language (blank = auto-detect). Pinning to "en" stops the model defaulting to other
# languages — and hallucinating CJK — on near-silent / noisy audio.
LANGUAGE = os.environ.get("WHISPER_LANGUAGE", "en") or None

print(f"Loading Whisper model '{MODEL_NAME}' onto CUDA...")
model = WhisperModel(MODEL_NAME, device="cuda", compute_type="float16", download_root=MODEL_DIR)
print("Whisper model ready.")


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME}


@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    suffix = os.path.splitext(file.filename)[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    try:
        # vad_filter drops non-speech before decoding (kills silence/echo hallucinations);
        # condition_on_previous_text=False stops runaway repetition loops.
        segments, info = model.transcribe(
            tmp_path,
            beam_size=5,
            language=LANGUAGE,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return {"text": text, "language": info.language}
    finally:
        os.unlink(tmp_path)
