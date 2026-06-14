FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    libopus0 libopus-dev \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    websockets \
    opuslib \
    soundfile \
    numpy \
    httpx \
    scipy

WORKDIR /app
COPY server.py .

EXPOSE 5010

CMD ["python", "server.py"]
