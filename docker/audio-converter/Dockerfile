FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    ffmpeg \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install deno (JS runtime required by modern yt-dlp for YouTube)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    uvicorn==0.30.1 \
    pydantic==2.7.4 \
    yt-dlp

WORKDIR /app
COPY server.py .

EXPOSE 5007

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5007"]
