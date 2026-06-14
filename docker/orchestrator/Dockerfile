FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    fastapi==0.111.0 \
    uvicorn==0.30.1 \
    httpx==0.27.0 \
    pydantic==2.7.4 \
    aiofiles==23.2.1

WORKDIR /app
COPY server.py .

EXPOSE 5005

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "5005", "--timeout-keep-alive", "600"]
