FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3.11 python3.11-dev python3-pip \
    ffmpeg libsndfile1 libgomp1 \
    git build-essential cmake \
    && rm -rf /var/lib/apt/lists/*

# Let chatterbox-tts pull its own torch==2.6.0 — don't pre-install
RUN pip3 install --no-cache-dir \
    chatterbox-tts==0.1.7 \
    fastapi \
    uvicorn \
    pydantic

WORKDIR /app
COPY server.py .

ENV HF_HOME=/tts-models

EXPOSE 5006

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5006"]
