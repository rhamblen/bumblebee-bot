import asyncio
import io
import json
import logging
import os
import tempfile
import time
import uuid
import wave

import httpx
import numpy as np
import opuslib
import soundfile as sf
import webrtcvad
import websockets
from aiohttp import web
from scipy.signal import resample_poly
from math import gcd

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

WHISPER_URL = os.environ.get("WHISPER_URL", "http://whisper-stt:5009")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://bumblebee-orchestrator:5005")
N8N_WEBHOOK_URL = os.environ.get("N8N_WEBHOOK_URL", "")  # when set, uses n8n instead of orchestrator directly

# OTA/activation endpoint — Xiaozhi firmware POSTs here on boot to discover its WS server.
# OTA_WS_URL is the WebSocket URL we hand back to the device; it must be reachable from the
# device's LAN (the published host port of this gateway), NOT the docker-internal hostname.
OTA_PORT = int(os.environ.get("OTA_PORT", 5011))
OTA_WS_URL = os.environ.get("OTA_WS_URL", "ws://192.168.1.33:5010/xiaozhi/v1/")
OTA_WS_TOKEN = os.environ.get("OTA_WS_TOKEN", "bumblebee-test")

# Matches Xiaozhi firmware defaults
MIC_SAMPLE_RATE = 16000
MIC_CHANNELS = 1
FRAME_DURATION_MS = 60
FRAME_SAMPLES = int(MIC_SAMPLE_RATE * FRAME_DURATION_MS / 1000)  # 960 samples

# Server-side VAD. In "auto" mode the device streams mic audio continuously and never
# sends a listen-stop on end-of-speech, so the gateway must detect the utterance boundary
# itself: once we've heard real speech followed by SILENCE_END_MS of quiet, we cut and
# transcribe. webrtcvad needs 10/20/30ms 16-bit mono frames; a 60ms mic frame = 2x30ms.
VAD_AGGRESSIVENESS = int(os.environ.get("VAD_AGGRESSIVENESS", 2))  # 0..3, higher = more aggressive filtering
VAD_SUBFRAME_MS = 30
VAD_SUBFRAME_SAMPLES = int(MIC_SAMPLE_RATE * VAD_SUBFRAME_MS / 1000)  # 480
SILENCE_END_MS = int(os.environ.get("SILENCE_END_MS", 800))     # trailing silence that ends an utterance
MIN_SPEECH_MS = int(os.environ.get("MIN_SPEECH_MS", 300))       # ignore blips shorter than this
MAX_UTTERANCE_MS = int(os.environ.get("MAX_UTTERANCE_MS", 15000))  # hard cap so a noisy room can't run away

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


def looks_like_noise(text: str) -> bool:
    """Reject Whisper hallucinations from non-speech audio (silence, echo, room noise).

    Catches the two common failure shapes: a single character repeated many times
    (e.g. '小小小…' or 'you you you'), and output that is mostly non-ASCII when this is
    an English pipeline (Whisper defaulting to another language on noise)."""
    compact = "".join(text.split())
    if len(compact) < 2:
        return True
    if len(set(compact)) <= 2 and len(compact) > 6:
        return True
    ascii_ratio = sum(1 for c in compact if ord(c) < 128) / len(compact)
    return ascii_ratio < 0.5


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
            # Webhook reads body.text (falls back to body.phrase); send `text`, not `message`.
            resp = await client.post(
                N8N_WEBHOOK_URL,
                json={"text": text, "session_id": session_id},
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

    # Pace frames to real time. If we blast all frames at once, `tts:stop` arrives long
    # before the device finishes playing, so it resumes listening while its own speaker is
    # still going — the mic then captures the playback (echo) and Whisper hallucinates.
    # Sending at the frame cadence keeps `tts:stop` aligned with the true end of audio.
    frame_period = FRAME_DURATION_MS / 1000.0
    loop = asyncio.get_event_loop()
    next_send = loop.time()
    for i in range(0, len(audio), FRAME_SAMPLES):
        chunk = audio[i : i + FRAME_SAMPLES]
        if len(chunk) < FRAME_SAMPLES:
            chunk = np.pad(chunk, (0, FRAME_SAMPLES - len(chunk)))
        opus_frame = encoder.encode(chunk.tobytes(), FRAME_SAMPLES)
        await websocket.send(opus_frame)
        next_send += frame_period
        delay = next_send - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)

    await websocket.send(json.dumps({"type": "tts", "state": "stop"}))


def extract_device_id(websocket) -> str | None:
    """Pull the device MAC from the WS handshake `Device-Id` header (sent by Xiaozhi
    firmware). Robust across websockets versions. Used as the conversation key so Redis
    history persists per physical device across reconnects (and keys future room routing)."""
    req = getattr(websocket, "request", None)
    headers = getattr(req, "headers", None) if req is not None else None
    if headers is None:
        headers = getattr(websocket, "request_headers", None)  # legacy websockets
    if headers is None:
        return None
    try:
        return headers.get("Device-Id")
    except Exception:
        return None


async def handle_connection(websocket):
    session_id = str(uuid.uuid4())  # per-connection id, used for the hello handshake + logs
    device_id = extract_device_id(websocket) or session_id  # stable per-device conversation key
    decoder = opuslib.Decoder(MIC_SAMPLE_RATE, MIC_CHANNELS)
    vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
    listening = False
    speaking = False  # True while we stream TTS back — ignore mic frames during playback
    mode = "auto"

    # Incremental utterance state (reset per utterance)
    pcm_buf = bytearray()
    speech_ms = 0
    silence_ms = 0
    started = False  # have we heard real speech yet this utterance

    def reset_utterance():
        nonlocal pcm_buf, speech_ms, silence_ms, started
        pcm_buf = bytearray()
        speech_ms = 0
        silence_ms = 0
        started = False

    async def finalize(reason: str):
        """Transcribe the buffered utterance, then synthesize + stream the reply."""
        nonlocal speaking
        pcm_data = bytes(pcm_buf)
        reset_utterance()
        if len(pcm_data) < 2:
            return

        dur_ms = len(pcm_data) // 2 * 1000 // MIC_SAMPLE_RATE
        log.info("[%s] Utterance end (%s) — %d ms", session_id, reason, dur_ms)

        try:
            text = await transcribe(pcm_data)
        except Exception as e:
            log.error("[%s] Transcribe failed: %s", session_id, e)
            return

        log.info("[%s] STT result: '%s'", session_id, text)
        if not text or looks_like_noise(text):
            log.info("[%s] Dropping empty/noise transcript", session_id)
            return

        await websocket.send(json.dumps({"type": "stt", "text": text}))

        try:
            wav_url = await get_wav_url(text, device_id)
        except Exception as e:
            log.error("[%s] TTS request failed: %s", session_id, e)
            return

        if not wav_url:
            log.warning("[%s] No WAV URL returned", session_id)
            return

        log.info("[%s] Streaming audio from %s", session_id, wav_url)
        speaking = True
        try:
            await stream_wav_as_opus(websocket, wav_url)
        except Exception as e:
            log.error("[%s] Audio stream failed: %s", session_id, e)
        finally:
            speaking = False

    log.info("[%s] Connected from %s (device_id=%s)", session_id, websocket.remote_address, device_id)

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
                        mode = data.get("mode", mode)
                        reset_utterance()
                        listening = True
                        log.info("[%s] Listening started (mode=%s)", session_id, mode)
                    elif state == "stop" and listening:
                        # Manual/push-to-talk path: device signals end-of-speech itself.
                        log.info("[%s] Listening stopped by device", session_id)
                        listening = False
                        await finalize("device-stop")

                elif msg_type == "abort":
                    log.info("[%s] Abort received", session_id)
                    listening = False
                    reset_utterance()

            elif isinstance(message, bytes) and listening and not speaking:
                # Decode this 60ms Opus frame and run VAD incrementally so we can detect
                # end-of-speech ourselves in auto mode (device never sends a stop there).
                try:
                    pcm = decoder.decode(message, FRAME_SAMPLES)
                except opuslib.OpusError as e:
                    log.warning("[%s] Opus decode error: %s", session_id, e)
                    continue

                pcm_buf.extend(pcm)
                samples = np.frombuffer(pcm, dtype=np.int16)

                for off in range(0, len(samples) - VAD_SUBFRAME_SAMPLES + 1, VAD_SUBFRAME_SAMPLES):
                    sub = samples[off:off + VAD_SUBFRAME_SAMPLES]
                    try:
                        is_speech = vad.is_speech(sub.tobytes(), MIC_SAMPLE_RATE)
                    except Exception:
                        is_speech = True  # on VAD error, keep the audio rather than drop it

                    if is_speech:
                        speech_ms += VAD_SUBFRAME_MS
                        silence_ms = 0
                        started = True
                    elif started:
                        silence_ms += VAD_SUBFRAME_MS

                    total_ms = len(pcm_buf) // 2 * 1000 // MIC_SAMPLE_RATE
                    if started and speech_ms >= MIN_SPEECH_MS and silence_ms >= SILENCE_END_MS:
                        await finalize("vad-silence")
                        break
                    if total_ms >= MAX_UTTERANCE_MS:
                        await finalize("max-duration")
                        break

    except websockets.exceptions.ConnectionClosed:
        log.info("[%s] Connection closed", session_id)
    except Exception as e:
        log.error("[%s] Unhandled error: %s", session_id, e)


async def ota_handler(request: web.Request) -> web.Response:
    """Xiaozhi OTA/activation endpoint.

    The firmware POSTs device info here on boot and reads back where to connect.
    We return a `websocket` block (so the device connects to our WS server) and a
    benign `firmware` block (empty url → no OTA update attempt). We deliberately
    omit the `activation` block so the device skips activation and connects directly.
    """
    device_id = request.headers.get("Device-Id", "?")
    client_id = request.headers.get("Client-Id", "?")
    log.info("[OTA] %s from Device-Id=%s Client-Id=%s", request.method, device_id, client_id)

    return web.json_response({
        "server_time": {
            "timestamp": int(time.time() * 1000),
            "timezone_offset": 0,
        },
        "firmware": {
            "version": "1.0.0",
            "url": "",  # empty → device does not attempt a firmware download
        },
        "websocket": {
            "url": OTA_WS_URL,
            "token": OTA_WS_TOKEN,
        },
    })


async def start_ota_server():
    app = web.Application()
    # Accept both POST (firmware) and GET (manual browser check) on the conventional path.
    app.router.add_route("*", "/xiaozhi/ota/", ota_handler)
    app.router.add_route("*", "/xiaozhi/ota", ota_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", OTA_PORT)
    await site.start()
    log.info("OTA endpoint listening on port %d — handing back WS url: %s", OTA_PORT, OTA_WS_URL)


async def main():
    port = int(os.environ.get("PORT", 5010))
    mode = "n8n" if N8N_WEBHOOK_URL else "orchestrator-direct (test mode)"
    log.info("Xiaozhi gateway starting on port %d — mode: %s", port, mode)
    await start_ota_server()
    # ping_interval=None disables the library keepalive. The device can't reliably pong
    # while it's busy decoding/playing a long TTS reply (CPU on the Opus→I2S path), and
    # the Xiaozhi protocol manages its own session lifecycle — so library pings would
    # otherwise drop healthy connections mid-stream with a 1011 ping-timeout.
    async with websockets.serve(handle_connection, "0.0.0.0", port, ping_interval=None):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
