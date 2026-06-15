FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ffmpeg: clip normalise (22050 mono s16, the F5 reference format).
# curl/unzip: fetch + unpack deno below.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# deno: the JS runtime modern yt-dlp needs for YouTube (it only enables deno by
# default). Matches the proven audio-converter setup. PATH so yt-dlp finds it.
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

# yt-dlp[default] pulls in yt-dlp-ejs — the YouTube n-challenge solver scripts
# that deno runs. Plain `yt-dlp` lacks them and fails with "n challenge solving
# failed" on modern YouTube.
RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    uvicorn==0.30.1 \
    httpx==0.27.0 \
    pyyaml==6.0.1 \
    "yt-dlp[default]"

WORKDIR /app
COPY server.py .

EXPOSE 5012

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5012"]
