import asyncio
import io
import json
import logging
import os
import tempfile
import uuid
import wave

import httpx
import numpy as np
import opuslib
import soundfile as sf
import websockets
from scipy.signal import resample_poly
from math import gcd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

WHISPER_URL = os.environ.get("WHISPER_URL", "http://whisper-stt:5009")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://bumblebee-orchestrator:5005")
N8N_WEBHOOK_URL = os.environ.get("N8N_WEBHOOK_URL", "")  # when set, uses n8n instead of orchestrator directly

# Matches Xiaozhi firmware defaults
MIC_SAMPLE_RATE = 16000
MIC_CHANNELS = 1
FRAME_DURATION_MS = 60
FRAME_SAMPLES = int(MIC_SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # 960 samples

# Test-mode voice used when calling orchestrator directly (no n8n/C2+C3 yet)
TEST_VOICE_DESCRIPTION = (
    "A warm, clear male voice with a slight vintage radio quality, speaking at a measured pace."
)


def resample_audio(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return audio
    divisor = gcd(src_rate, dst_rate)
    up = dst_rate // divisor
    down = src_rate // divisor
    return resample_poly(audio, up, down).astype(np.int16)


async def transcribe(audio_bytes: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        with wave.open(tmp_path, "wb") as wf:
            wf.setnchannels(MIC_CHANNELS)
            wf.setsampwidth(2)  # 16-bit PCM
            wf.setframerate(MIC_SAMPLE_RATE)
            wf.writeframes(audio_bytes)

        async with httpx.AsyncClient(timeout=30) as client:
            with open(tmp_path, "rb") as f:
                resp = await client.post(
                    f"{WHISPER_URL}/transcribe",
                    files={"file": ("audio.wav", f, "audio/wav")},
                )
            resp.raise_for_status()
            return resp.json().get("text", "").strip()
    finally:
        os.unlink(tmp_path)


async def get_wav_url(text: str, session_id: str) -> str | None:
    async with httpx.AsyncClient(timeout=60) as client:
        if N8N_WEBHOOK_URL:
            # Production: n8n handles C1+C2+C3, returns {"url": "..."}
            resp = await client.post(
                N8N_WEBHOOK_URL,
                json={"message": text, "session_id": session_id},
            )
            resp.raise_for_status()
            return resp.json().get("url")
        else:
            # Test mode: call orchestrator directly with Parler
            resp = await client.post(
                f"{ORCHESTRATOR_URL}/speak",
                json={
                    "text": text,
                    "tts_engine": "parler",
                    "voice_description": TEST_VOICE_DESCRIPTION,
                },
            )
            resp.raise_for_status()
            return resp.json().get("url")


async def stream_wav_as_opus(websocket, wav_url: str):
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(wav_url)
        resp.raise_for_status()
        wav_bytes = resp.content

    audio, src_rate = sf.read(io.BytesIO(wav_bytes), dtype="int16")

    # Mono
    if audio.ndim > 1:
        audio = audio.mean(axis=1).astype(np.int16)

    # Resample to 16kHz for Opus encoding
    audio = resample_audio(audio, src_rate, MIC_SAMPLE_RATE)

    encoder = opuslib.Encoder(MIC_SAMPLE_RATE, MIC_CHANNELS, opuslib.APPLICATION_AUDIO)

    await websocket.send(json.dumps({"type": "tts", "state": "start"}))

    for i in range(0, len(audio), FRAME_SAMPLES):
        chunk = audio[i : i + FRAME_SAMPLES]
        if len(chunk) < FRAME_SAMPLES:
            chunk = np.pad(chunk, (0, FRAME_SAMPLES - len(chunk)))
        opus_frame = encoder.encode(chunk.tobytes(), FRAME_SAMPLES)
        await websocket.send(opus_frame)
        # Small yield to avoid blocking the event loop
        await asyncio.sleep(0)

    await websocket.send(json.dumps({"type": "tts", "state": "stop"}))


async def handle_connection(websocket):
    session_id = str(uuid.uuid4())
    opus_frames: list[bytes] = []
    decoder = opuslib.Decoder(MIC_SAMPLE_RATE, MIC_CHANNELS)
    listening = False

    log.info("[%s] Connected from %s", session_id, websocket.remote_address)

    try:
        async for message in websocket:
            if isinstance(message, str):
                try:
                    data = json.loads(message)
                except json.JSONDecodeError:
                    continue

                msg_type = data.get("type")

                if msg_type == "hello":
                    log.info("[%s] Hello received", session_id)
                    await websocket.send(json.dumps({
                        "type": "hello",
                        "transport": "websocket",
                        "session_id": session_id,
                        "audio_params": {
                            "format": "opus",
                            "sample_rate": MIC_SAMPLE_RATE,
                            "channels": MIC_CHANNELS,
                            "frame_duration": FRAME_DURATION_MS,
                        },
                    }))

                elif msg_type == "listen":
                    state = data.get("state")
                    if state in ("start", "detect"):
                        opus_frames = []
                        listening = True
                        log.info("[%s] Listening started", session_id)

                    elif state == "stop" and listening:
                        listening = False
                        log.info("[%s] Listening stopped — %d frames", session_id, len(opus_frames))

                        if not opus_frames:
                            continue

                        # Decode Opus frames → PCM
                        pcm_data = b""
                        for frame in opus_frames:
                            try:
                                pcm_data += decoder.decode(frame, FRAME_SAMPLES)
                            except opuslib.OpusError as e:
                                log.warning("[%s] Opus decode error: %s", session_id, e)

                        if not pcm_data:
                            continue

                        # STT
                        try:
                            text = await transcribe(pcm_data)
                        except Exception as e:
                            log.error("[%s] Transcribe failed: %s", session_id, e)
                            continue

                        log.info("[%s] STT result: '%s'", session_id, text)
                        if not text:
                            continue

                        await websocket.send(json.dumps({"type": "stt", "text": text}))

                        # TTS
                        try:
                            wav_url = await get_wav_url(text, session_id)
                        except Exception as e:
                            log.error("[%s] TTS request failed: %s", session_id, e)
                            continue

                        if not wav_url:
                            log.warning("[%s] No WAV URL returned", session_id)
                            continue

                        log.info("[%s] Streaming audio from %s", session_id, wav_url)
                        try:
                            await stream_wav_as_opus(websocket, wav_url)
                        except Exception as e:
                            log.error("[%s] Audio stream failed: %s", session_id, e)

                elif msg_type == "abort":
                    log.info("[%s] Abort received", session_id)
                    listening = False
                    opus_frames = []

            elif isinstance(message, bytes) and listening:
                opus_frames.append(message)

    except websockets.exceptions.ConnectionClosed:
        log.info("[%s] Connection closed", session_id)
    except Exception as e:
        log.error("[%s] Unhandled error: %s", session_id, e)


async def main():
    port = int(os.environ.get("PORT", 5010))
    mode = "n8n" if N8N_WEBHOOK_URL else "orchestrator-direct (test mode)"
    log.info("Xiaozhi gateway starting on port %d — mode: %s", port, mode)
    async with websockets.serve(handle_connection, "0.0.0.0", port):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
