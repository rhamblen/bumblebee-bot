FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3.10 python3.10-dev python3-pip \
    ffmpeg libsndfile1 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# PyTorch with CUDA first — TTS will inherit it rather than pulling CPU torch
RUN pip3 install --no-cache-dir \
    torch==2.1.0 torchaudio==2.1.0 --index-url https://download.pytorch.org/whl/cu121

RUN pip3 install --no-cache-dir \
    TTS==0.22.0 \
    fastapi==0.111.0 \
    uvicorn==0.30.1 \
    pydantic==2.7.4

# Pin transformers AFTER TTS to prevent it being overridden — XTTS v2 needs 4.35.x
RUN pip3 install --no-cache-dir "transformers==4.35.2"

WORKDIR /app
COPY server.py .

# Model cache — TTS_HOME is the correct env var for Coqui TTS 0.22.0
ENV TTS_HOME=/tts-models

EXPOSE 5002

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5002"]
