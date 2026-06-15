import asyncio
import io
import json
import logging
import os
import re
import tempfile
import time
import uuid
import wave

import httpx
import numpy as np
import opuslib
import redis.asyncio as aioredis
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


# --------------------------------------------------------------------------- device registry
# A persisted, named list of the ESP32 clients that have talked to us. The gateway already
# treats the firmware's `Device-Id` (MAC) as the conversation key; here we also remember the
# device across restarts so the Admin Console can show it (online state, last-seen, last
# heard/said) and let the operator give it a friendly name. Online/offline is NOT persisted —
# it's derived from CONNECTED (the set of MACs with a live WS right now); Redis only holds the
# durable facts. Redis is best-effort: if it's unreachable the gateway still runs, the registry
# just goes quiet (a device with no name simply shows as its MAC).
REDIS_URL = os.environ.get("REDIS_URL", "redis://redis:6379/0")
DEVICES_KEY = "bumblebee:devices"

CONNECTED: set[str] = set()  # MACs with a live WebSocket connection right now
_redis: "aioredis.Redis | None" = None


async def _get_redis():
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def register_device(mac: str | None, *, ip: str | None = None, **fields) -> None:
    """Upsert a device record, preserving its friendly name and first_seen. Pass extra
    durable facts as kwargs (last_heard, last_said, last_activity). Best-effort: any Redis
    failure is logged and swallowed so the voice path is never blocked by the registry."""
    if not mac or mac == "?":
        return  # no real Device-Id header (anonymous WS) — don't pollute the registry
    try:
        r = await _get_redis()
        raw = await r.hget(DEVICES_KEY, mac)
        rec = json.loads(raw) if raw else {}
        now = int(time.time())
        rec.setdefault("mac", mac)
        rec.setdefault("name", "")
        rec.setdefault("first_seen", now)
        rec["last_seen"] = now
        if ip:
            rec["last_ip"] = ip
        rec.update({k: v for k, v in fields.items() if v is not None})
        await r.hset(DEVICES_KEY, mac, json.dumps(rec))
    except Exception as e:  # noqa: BLE001 - registry is best-effort, never fatal
        log.warning("[devices] register failed for %s: %s", mac, e)


# --------------------------------------------------------------------------- playback targets
# Per-device output routing: each device's reply can play on its own ESP32 speaker (default)
# or on any Home Assistant media_player (e.g. the Sonos Roam). The Admin Console needs a list
# of valid targets to populate its dropdown; we fetch that from HA here (the gateway is the
# single HA-credential holder) and cache it in Redis so the dropdown loads without re-hitting
# HA every time — the console's "↻ Refresh playback devices" button forces a live re-pull.
# HA is on UR2 (a different host than this bridge), so it's reachable by LAN IP — no tunnel.
HA_URL = os.environ.get("HA_URL", "")        # e.g. http://192.168.1.64:8123
HA_TOKEN = os.environ.get("HA_TOKEN", "")    # long-lived access token (HA → Profile → Security)
PLAYBACK_KEY = "bumblebee:playback_devices"  # cached HA media_player list

# "Speakers only" filter: drop TVs / receivers / hubs by entity_id substring. media_player has
# no reliable speaker-vs-tv device_class, so this is a pragmatic denylist, refined as needed.
_NON_SPEAKER_RE = re.compile(r"(tv|appletv|apple_tv|fire_tv|firetv|firestick|_hub|str_dn|television|receiver|audi_)", re.I)


def ha_configured() -> bool:
    return bool(HA_URL and HA_TOKEN)


async def fetch_playback_devices() -> list[dict]:
    """Pull media_player entities from HA, filter to likely speakers, dedupe HA's `_N` registry
    duplicates (keeping the best per base name), sort by friendly name, and cache in Redis."""
    if not ha_configured():
        raise RuntimeError("HA_URL/HA_TOKEN not configured")
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            f"{HA_URL.rstrip('/')}/api/states",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
        )
        resp.raise_for_status()
        states = resp.json()

    by_base: dict[str, dict] = {}
    for s in states:
        eid = s.get("entity_id", "")
        if not eid.startswith("media_player.") or _NON_SPEAKER_RE.search(eid):
            continue
        state = s.get("state", "")
        if state == "unavailable":
            continue
        name = s.get("attributes", {}).get("friendly_name") or eid
        base = re.sub(r"_\d+$", "", eid)  # collapse "..._2"/"..._3" duplicates onto one base
        # keep the first available candidate per base (states already exclude 'unavailable')
        by_base.setdefault(base, {"entity_id": eid, "name": name, "state": state})

    devices = sorted(by_base.values(), key=lambda d: d["name"].lower())
    try:
        r = await _get_redis()
        await r.set(PLAYBACK_KEY, json.dumps(devices))
    except Exception as e:  # noqa: BLE001
        log.warning("[playback] cache write failed: %s", e)
    return devices


async def cached_playback_devices() -> list[dict]:
    try:
        r = await _get_redis()
        raw = await r.get(PLAYBACK_KEY)
        return json.loads(raw) if raw else []
    except Exception:  # noqa: BLE001
        return []


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


async def get_reply(text: str, session_id: str) -> dict:
    """Return the brain's response as the raw JSON dict (at least {"url": ...}). The reply
    *text* is surfaced for the device registry when the source provides it: n8n may echo the
    generated line back (under text/reply/response), otherwise only the audio url is known."""
    async with httpx.AsyncClient(timeout=60) as client:
        if N8N_WEBHOOK_URL:
            # Production: n8n handles C1+C2+C3, returns {"url": "..."}
            # Webhook reads body.text (falls back to body.phrase); send `text`, not `message`.
            resp = await client.post(
                N8N_WEBHOOK_URL,
                json={"text": text, "session_id": session_id},
            )
            resp.raise_for_status()
            return resp.json()
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
            return resp.json()


def _reply_text(reply: dict) -> str | None:
    """Best-effort extraction of the spoken reply text from a brain response, across the
    field names n8n/orchestrator might use. None when only an audio url came back."""
    for k in ("reply", "response", "said", "text", "message"):
        v = reply.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


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
    device_mac = extract_device_id(websocket)        # real MAC, or None for an anonymous WS
    device_id = device_mac or session_id             # stable per-device conversation key
    peer = websocket.remote_address
    peer_ip = peer[0] if peer else None
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
        await register_device(device_mac, last_heard=text, last_activity=int(time.time()))

        try:
            reply = await get_reply(text, device_id)
        except Exception as e:
            log.error("[%s] TTS request failed: %s", session_id, e)
            return

        wav_url = reply.get("url")
        if not wav_url:
            log.warning("[%s] No WAV URL returned", session_id)
            return

        said = _reply_text(reply)
        if said:
            await register_device(device_mac, last_said=said, last_activity=int(time.time()))

        log.info("[%s] Streaming audio from %s", session_id, wav_url)
        speaking = True
        try:
            await stream_wav_as_opus(websocket, wav_url)
        except Exception as e:
            log.error("[%s] Audio stream failed: %s", session_id, e)
        finally:
            speaking = False

    log.info("[%s] Connected from %s (device_id=%s)", session_id, websocket.remote_address, device_id)
    if device_mac:
        CONNECTED.add(device_mac)
        await register_device(device_mac, ip=peer_ip)

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
    finally:
        if device_mac:
            CONNECTED.discard(device_mac)
            await register_device(device_mac)  # bump last_seen on disconnect


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
    # A boot/OTA POST is the earliest we see a device — register it so it appears in the
    # console even before it opens a WS. (GET is the console's own liveness probe; skip those.)
    if request.method == "POST":
        await register_device(device_id, ip=request.remote)

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


async def clients_handler(request: web.Request) -> web.Response:
    """List every known device for the Admin Console. Merges the durable Redis records with
    the live CONNECTED set so `online` reflects right-now state, not what was last persisted."""
    devices = []
    try:
        r = await _get_redis()
        raw = await r.hgetall(DEVICES_KEY)
        for mac, blob in raw.items():
            try:
                rec = json.loads(blob)
            except json.JSONDecodeError:
                continue
            rec["online"] = mac in CONNECTED
            devices.append(rec)
    except Exception as e:  # noqa: BLE001 - report empty rather than 500 the console
        return web.json_response({"devices": [], "online": 0, "error": str(e)})
    # Online first, then most-recently-seen.
    devices.sort(key=lambda d: (not d.get("online"), -(d.get("last_seen") or 0)))
    return web.json_response({"devices": devices, "online": len(CONNECTED)})


async def rename_handler(request: web.Request) -> web.Response:
    """Set a device's friendly name. The MAC stays the conversation key; the name is cosmetic."""
    mac = request.match_info["mac"]
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    name = (body.get("name") or "").strip()[:40]
    try:
        r = await _get_redis()
        raw = await r.hget(DEVICES_KEY, mac)
        if not raw:
            return web.json_response({"error": "unknown device"}, status=404)
        rec = json.loads(raw)
        rec["name"] = name
        await r.hset(DEVICES_KEY, mac, json.dumps(rec))
    except Exception as e:  # noqa: BLE001
        return web.json_response({"error": str(e)}, status=502)
    return web.json_response({"ok": True, "mac": mac, "name": name})


async def output_handler(request: web.Request) -> web.Response:
    """Set a device's playback target: `"device"` (its own speaker) or a HA media_player entity_id."""
    mac = request.match_info["mac"]
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    output = (body.get("output") or "device").strip()[:80]
    try:
        r = await _get_redis()
        raw = await r.hget(DEVICES_KEY, mac)
        if not raw:
            return web.json_response({"error": "unknown device"}, status=404)
        rec = json.loads(raw)
        rec["output"] = output
        await r.hset(DEVICES_KEY, mac, json.dumps(rec))
    except Exception as e:  # noqa: BLE001
        return web.json_response({"error": str(e)}, status=502)
    return web.json_response({"ok": True, "mac": mac, "output": output})


async def playback_devices_handler(request: web.Request) -> web.Response:
    """List valid playback targets for the Admin Console dropdown. `?refresh=1` forces a live
    HA pull (and re-caches); otherwise serve the Redis cache, falling back to a live pull when
    the cache is empty. Soft-fails to an empty list + reason so the tab still renders."""
    want_refresh = request.query.get("refresh") == "1"
    try:
        if want_refresh:
            devices = await fetch_playback_devices()
        else:
            devices = await cached_playback_devices()
            if not devices and ha_configured():
                devices = await fetch_playback_devices()
    except Exception as e:  # noqa: BLE001
        return web.json_response(
            {"devices": [], "error": str(e), "ha_configured": ha_configured()})
    return web.json_response({"devices": devices, "ha_configured": ha_configured()})


async def start_ota_server():
    app = web.Application()
    # Accept both POST (firmware) and GET (manual browser check) on the conventional path.
    app.router.add_route("*", "/xiaozhi/ota/", ota_handler)
    app.router.add_route("*", "/xiaozhi/ota", ota_handler)
    # Device registry for the Admin Console (same HTTP server the console already reaches).
    app.router.add_get("/clients", clients_handler)
    app.router.add_post("/clients/{mac}/name", rename_handler)
    app.router.add_post("/clients/{mac}/output", output_handler)
    app.router.add_get("/playback-devices", playback_devices_handler)
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
