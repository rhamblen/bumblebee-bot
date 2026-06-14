FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3.10 python3.10-dev python3-pip \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
    faster-whisper \
    fastapi==0.111.0 \
    uvicorn==0.30.1 \
    python-multipart

WORKDIR /app
COPY server.py .

# Model cache — survives container rebuilds
ENV WHISPER_MODEL_DIR=/whisper-models
ENV WHISPER_MODEL=base

EXPOSE 5009

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5009"]
