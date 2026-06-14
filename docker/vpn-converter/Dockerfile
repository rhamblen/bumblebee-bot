FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    ffmpeg \
    openvpn \
    iproute2 \
    curl \
    unzip \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    uvicorn==0.30.1 \
    pydantic==2.7.4 \
    yt-dlp

WORKDIR /app
COPY server.py .
COPY start.sh .
RUN chmod +x start.sh

EXPOSE 5008

CMD ["/app/start.sh"]
