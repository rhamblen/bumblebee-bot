FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

# ffmpeg: clip normalise (22050 mono s16, the F5 reference format).
# nodejs: modern yt-dlp needs a JS runtime to decipher YouTube signatures.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    uvicorn==0.30.1 \
    httpx==0.27.0 \
    pyyaml==6.0.1 \
    yt-dlp

WORKDIR /app
COPY server.py .

EXPOSE 5012

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5012"]
