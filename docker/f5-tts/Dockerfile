FROM nvidia/cuda:12.1.0-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    python3.10 python3.10-dev python3-pip \
    ffmpeg libsndfile1 libgomp1 git \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir \
    torch==2.1.0 torchaudio==2.1.0 --index-url https://download.pytorch.org/whl/cu121

RUN pip3 install --no-cache-dir \
    f5-tts \
    fastapi==0.111.0 \
    uvicorn==0.30.1 \
    pydantic==2.7.4

WORKDIR /app
COPY server.py .

# Model cache volume — survives container rebuilds
ENV HF_HOME=/tts-models

EXPOSE 5003

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5003"]
