"""Bumblebee Admin Console.

A read-mostly operator pane for the Bumblebee stack. It never holds its own copy
of state: it reads the SAME sources the live containers read (the orchestrator's
voice descriptor, the compose file, the .env) and reports on them. It can edit
.env (the values compose sources via ${VAR}), but changes still take effect the
normal build way — by recreating the affected container; it never hot-pushes into
a running container.

Panels:
  1. Service health   — pings each bumblebee service /health on bumblebee_default.
  2. Config           — per-container env view (resolved value + source). Values
                        compose sources via ${VAR} are editable and saved to .env;
                        bare literals are read-only. Plus the validator findings
                        (${VARS} referenced but undefined, missing .env).
  3. Voice/character  — live table from the orchestrator GET /voices (clip vs Parler).
  4. Clip Capture     — per Parler-only voice: grab a YouTube snippet (yt-dlp +
                        ffmpeg, no VPN), preview it, then accept -> write into
                        references/<folder>/ + re-scan (flips the voice to F5).
  5. Workflow I/O     — a running LOG of per-run pipeline traces (heard -> mood ->
                        voices -> said -> output), flattened from n8n executions and
                        tailed forward by the browser.

Not built yet: client/wake-word panel, bumblebee-scoped container status, a live
config store for the tunable values (apply without a recreate), and brain config
(voice-count/weighting/persona/model->role) on the Voices tab.
"""

import os
import re
import json
import time
import random
import asyncio
import logging
from datetime import datetime

import httpx
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(title="Bumblebee Admin Console")

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://bumblebee-orchestrator:5005")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://xiaozhi-gateway:5011")  # device registry source
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://ollama:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "mistral:latest")  # C3 personality model for preview lines

# Health panel — listed in WORKFLOW ORDER (admin-console first, then a request as
# it flows: input edge → STT → brain → synthesis → engines). Each entry declares
# HOW to probe it, because the stack isn't all plain HTTP /health: Redis speaks RESP
# (TCP PING), Ollama answers on /, the gateway only exposes its OTA path, and n8n
# lives on a different (macvlan) network.
#   http: GET {url}{path}, healthy on 2xx
#   tcp:  open a socket to host:port; if `ping` set, expect `expect` in the reply
SERVICES = [
    # Operator entry point.
    {"name": "admin-console",   "kind": "http", "url": os.environ.get("SELF_URL", "http://localhost:5012")},
    # Input edge: gateway has no /health — probe its OTA endpoint (200 JSON on GET).
    {"name": "xiaozhi-gateway", "kind": "http", "url": os.environ.get("GATEWAY_URL", "http://xiaozhi-gateway:5011"), "path": "/xiaozhi/ota/"},
    {"name": "whisper-stt",     "kind": "http", "url": os.environ.get("WHISPER_URL", "http://whisper-stt:5009")},
    # Brain: n8n + its two dependencies in the order n8n hits them (redis read, then ollama).
    # n8n is on macvlan (br0) — unreachable from this bridge unless N8N_API_URL points at
    # a routable address. Probed only when configured, else reported as n/a (not down).
    {"name": "n8n",             "kind": "http", "url": os.environ.get("N8N_API_URL", ""), "path": "/healthz", "optional": True},
    # Redis isn't HTTP — TCP PING expecting +PONG.
    {"name": "redis",           "kind": "tcp",  "host": os.environ.get("REDIS_HOST", "redis"),
     "port": int(os.environ.get("REDIS_PORT", "6379")), "ping": "PING\r\n", "expect": "PONG"},
    # Ollama answers 200 "Ollama is running" on /.
    {"name": "ollama",          "kind": "http", "url": os.environ.get("OLLAMA_URL", "http://ollama:11434"), "path": "/"},
    # Synthesis: orchestrator + its reference-clip converter helper.
    {"name": "orchestrator",    "kind": "http", "url": ORCHESTRATOR_URL},
    {"name": "audio-converter", "kind": "http", "url": os.environ.get("AUDIO_CONVERTER_URL", "http://audio-converter:5007")},
    # TTS engines, ordered by the orchestrator's selection priority.
    {"name": "f5-tts",          "kind": "http", "url": os.environ.get("F5_TTS_URL", "http://f5-tts:5003")},
    {"name": "parler-tts",      "kind": "http", "url": os.environ.get("PARLER_TTS_URL", "http://parler-tts:5004")},
    {"name": "coqui-tts",       "kind": "http", "url": os.environ.get("COQUI_TTS_URL", "http://coqui-tts:5002")},
    {"name": "chatterbox",      "kind": "http", "url": os.environ.get("CHATTERBOX_URL", "http://chatterbox:5006")},
]

# Mounted read-only so the console reads the same source of truth the user builds from.
COMPOSE_PATH = os.environ.get("COMPOSE_PATH", "/srv/docker-compose.yml")
ENV_PATH = os.environ.get("ENV_PATH", "/srv/.env")

# n8n REST API. NOTE the macvlan caveat: the orchestrator/gateway can't reach n8n's
# LAN IP from the bumblebee_default bridge (Unraid blocks macvlan<->bridge same-host).
# Point this at a reachable address — a host with a route to n8n, or the tunnel.
N8N_API_URL = os.environ.get("N8N_API_URL", "")           # e.g. http://192.168.1.47:5678
N8N_API_KEY = os.environ.get("N8N_API_KEY", "")
N8N_WORKFLOW_ID = os.environ.get("N8N_WORKFLOW_ID", "ykVWvfFBHQpaC2h3")

# Clip Capture — the console downloads YouTube snippets itself (yt-dlp + ffmpeg on
# UR1, same home public IP as the local box that proved the workflow; no VPN). A
# capture lands in STAGING_DIR for preview; on accept it's moved into the matching
# references/<folder>/ and the orchestrator re-scans to flip that voice to F5.
REFERENCES_DIR = os.environ.get("REFERENCES_DIR", "/media/references")
# Where the orchestrator writes rendered WAVs (same shared /media mount). The voice
# preview serves these back SAME-ORIGIN through the console (like clip-preview) so the
# browser never has to reach the orchestrator's :5005 directly.
GENERATED_DIR = os.environ.get("GENERATED_DIR", "/media/generated")
STAGING_DIR = os.environ.get("CLIP_STAGING_DIR", "/tmp/clip-staging")
COOKIES_FILE = os.environ.get("YTDLP_COOKIES", "/cookies/cookies.txt")
CLIP_SAMPLE_RATE = 22050  # mono s16 — the F5 reference-clip format
AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".ogg", ".flac", ".aac", ".webm", ".mp4")


# --------------------------------------------------------------------------- data

async def _probe_http(client: httpx.AsyncClient, svc: dict) -> dict:
    url = svc.get("url")
    if not url:  # e.g. n8n with no N8N_API_URL — neutral, not down
        return {"ok": None, "detail": "not configured", "url": "(not configured)"}
    target = url.rstrip("/") + svc.get("path", "/health")
    try:
        r = await client.get(target)
        return {"ok": 200 <= r.status_code < 300, "detail": f"HTTP {r.status_code}", "url": target}
    except Exception as e:  # noqa: BLE001 - reachability probe, any failure = down
        return {"ok": False, "detail": str(e), "url": target}


async def _probe_tcp(svc: dict) -> dict:
    url = f"tcp://{svc['host']}:{svc['port']}"
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(svc["host"], svc["port"]), timeout=4.0)
        detail = "socket open"
        ok = True
        if svc.get("ping"):
            writer.write(svc["ping"].encode())
            await writer.drain()
            data = await asyncio.wait_for(reader.read(64), timeout=4.0)
            txt = data.decode(errors="replace").strip()
            ok = svc.get("expect", "") in txt
            detail = f"{svc['expect']} reply" if ok else f"unexpected: {txt!r}"
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:  # noqa: BLE001
            pass
        return {"ok": ok, "detail": detail, "url": url}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "detail": str(e), "url": url}


async def service_health() -> list[dict]:
    """Probe every service per its declared `kind`; report up/down/na without throwing."""
    out = []
    async with httpx.AsyncClient(timeout=4.0) as client:
        for svc in SERVICES:
            res = await (_probe_tcp(svc) if svc["kind"] == "tcp" else _probe_http(client, svc))
            out.append({"name": svc["name"], **res})
    return out


async def voices() -> dict:
    """Live voice table from the orchestrator (single source of truth)."""
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(f"{ORCHESTRATOR_URL}/voices")
        r.raise_for_status()
        return r.json()


async def clients() -> dict:
    """Device registry from the gateway (the only thing that sees the ESP32 connections).
    Soft-fails to an empty list with a reason so the tab renders even if the gateway is down."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{GATEWAY_URL.rstrip('/')}/clients")
            r.raise_for_status()
            return r.json()
    except Exception as e:  # noqa: BLE001
        return {"devices": [], "online": 0, "error": str(e)}


async def rename_client(mac: str, name: str) -> dict:
    """Proxy a friendly-name change to the gateway, which owns the registry."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.post(f"{GATEWAY_URL.rstrip('/')}/clients/{mac}/name", json={"name": name})
        if r.status_code >= 400:
            return {"ok": False, "reason": r.json().get("error", r.text)}
        return {"ok": True, **r.json()}


_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)(?::-[^}]*)?\}")


def _parse_env(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    if not os.path.exists(path):
        return env
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def validate_config() -> dict:
    """Flag ${VARS} referenced in compose but absent from .env (and a defaulted set).

    A var written as ${FOO:-default} has an inline default, so it's only a soft
    warning if missing; a bare ${FOO} with no .env entry is a hard finding.
    """
    findings: list[dict] = []
    if not os.path.exists(COMPOSE_PATH):
        return {"ok": False, "findings": [{"level": "error",
                "msg": f"compose file not mounted at {COMPOSE_PATH}"}]}

    raw = open(COMPOSE_PATH, encoding="utf-8").read()
    try:
        compose = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return {"ok": False, "findings": [{"level": "error", "msg": f"compose YAML invalid: {e}"}]}

    env = _parse_env(ENV_PATH)
    if not env and not os.path.exists(ENV_PATH):
        findings.append({"level": "warn", "msg": f".env not found at {ENV_PATH} (defaults will be used)"})

    # Scan only non-comment lines — compose never interpolates ${VAR} inside a
    # `#` comment, so neither should we (otherwise a var named in a comment is
    # flagged as undefined).
    scan = "\n".join(l for l in raw.splitlines() if not l.lstrip().startswith("#"))

    # Which referenced vars carry an inline default (${VAR:-...})?
    defaulted = set(re.findall(r"\$\{([A-Z0-9_]+):-", scan))
    referenced = set(_VAR_RE.findall(scan))

    for var in sorted(referenced):
        if var in env:
            continue
        level = "warn" if var in defaulted else "error"
        findings.append({"level": level, "var": var,
                         "msg": f"${{{var}}} referenced in compose but not set in .env"
                                + (" (has inline default)" if var in defaulted else "")})

    services = list((compose or {}).get("services", {}).keys())
    return {"ok": all(f["level"] != "error" for f in findings),
            "services": services, "env_keys": sorted(env.keys()), "findings": findings}


# ----------------------------------------------------------------- env editing
# The Config tab is data-driven: it walks the compose `environment:` blocks and,
# for each var, decides if it's EDITABLE. A var is editable only when compose
# references it as ${VAR} / ${VAR:-default} — i.e. it's sourced from .env. A bare
# literal (FOO=http://x) is shown read-only: to make it editable, convert it to
# ${FOO:-http://x} in compose (one section at a time, deliberately).
#
# Secrets are never sent to the browser in clear — they're masked, and a write
# carrying the mask back is treated as "unchanged" and skipped.

SECRET_KEYS = {"N8N_API_KEY", "OTA_WS_TOKEN", "VPN_AUTH"}
MASK = "•" * 8  # ••••••••  (also the JS sentinel — keep both in sync)

_ENVREF_RE = re.compile(r"^\$\{([A-Z0-9_]+)(?::-(.*))?\}$")


def _compose_services_env() -> dict[str, list[tuple[str, str]]]:
    """{service: [(KEY, raw), ...]} from each service's environment block.

    Handles both YAML forms — list (`- KEY=val`) and mapping (`KEY: val`).
    """
    if not os.path.exists(COMPOSE_PATH):
        return {}
    try:
        compose = yaml.safe_load(open(COMPOSE_PATH, encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    out: dict[str, list[tuple[str, str]]] = {}
    for name, svc in (compose or {}).get("services", {}).items():
        env = (svc or {}).get("environment", [])
        items: list[tuple[str, str]] = []
        if isinstance(env, dict):
            items = [(k, "" if v is None else str(v)) for k, v in env.items()]
        else:
            for line in env or []:
                k, _, v = str(line).partition("=")
                items.append((k.strip(), v.strip()))
        out[name] = items
    return out


def _editable_vars() -> set[str]:
    """The set of ${VAR} names that compose sources from .env — the only writable ones."""
    allowed: set[str] = set()
    for items in _compose_services_env().values():
        for _key, raw in items:
            m = _ENVREF_RE.match(raw)
            if m:
                allowed.add(m.group(1))
    return allowed


def env_config() -> dict:
    """Per-service env view: resolved value, source (.env / default / compose), editability."""
    env = _parse_env(ENV_PATH)
    groups = []
    for svc, items in _compose_services_env().items():
        fields = []
        for key, raw in items:
            m = _ENVREF_RE.match(raw)
            if m:
                var, default = m.group(1), (m.group(2) or "")
                editable, env_key = True, var
                value = env.get(var, default)
                source = "env" if var in env else "default"
            else:
                editable, env_key = False, key
                value, source = raw, "compose"
            secret = key in SECRET_KEYS or env_key in SECRET_KEYS
            fields.append({
                "key": key, "env_key": env_key, "editable": editable,
                "secret": secret, "source": source,
                "value": (MASK if (secret and value) else value),
                "has_value": bool(value),
            })
        groups.append({"service": svc, "fields": fields})
    env_dir = os.path.dirname(ENV_PATH) or "."
    writable = (os.access(ENV_PATH, os.W_OK) if os.path.exists(ENV_PATH)
                else os.access(env_dir, os.W_OK))
    return {"groups": groups, "env_path": ENV_PATH, "writable": writable}


def write_env(updates: dict[str, str]) -> dict:
    """Update only editable (${VAR}-backed) keys in .env, preserving all other lines."""
    allowed = _editable_vars()
    rejected = sorted(k for k in updates if k not in allowed)
    apply = {k: v for k, v in updates.items() if k in allowed and v != MASK}

    lines = open(ENV_PATH, encoding="utf-8").read().splitlines() if os.path.exists(ENV_PATH) else []
    seen: set[str] = set()
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k = s.split("=", 1)[0].strip()
        if k in apply:
            lines[i] = f"{k}={apply[k]}"
            seen.add(k)
    for k, v in apply.items():
        if k not in seen:
            lines.append(f"{k}={v}")
    try:
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
    except OSError as e:
        return {"ok": False, "reason": f"{e} (is .env mounted read-write?)"}
    return {"ok": True, "written": sorted(apply.keys()), "rejected": rejected}


# --------------------------------------------------------------- drift check
# "Does the running container still match .env/compose?" Env is interpolated at
# container CREATE time, so editing .env doesn't reach a running process until it
# is recreated. We compare each service's EXPECTED value (compose, with ${VAR}
# resolved from .env) against the value the container is ACTUALLY running with
# (its Docker `Config.Env`), read over the read-only docker socket.

DOCKER_SOCK = os.environ.get("DOCKER_SOCK", "/var/run/docker.sock")


def _compose_container_names() -> dict[str, str]:
    """{service: container_name} for the services that declare one."""
    if not os.path.exists(COMPOSE_PATH):
        return {}
    try:
        compose = yaml.safe_load(open(COMPOSE_PATH, encoding="utf-8"))
    except yaml.YAMLError:
        return {}
    return {name: (svc or {}).get("container_name")
            for name, svc in (compose or {}).get("services", {}).items()
            if (svc or {}).get("container_name")}


def _resolve(raw: str, env: dict[str, str]) -> str:
    """The value compose WOULD inject: ${VAR:-def} → env or default; literal → as-is."""
    m = _ENVREF_RE.match(raw)
    if m:
        return env.get(m.group(1), m.group(2) or "")
    return raw


async def drift_check() -> dict:
    """Flag env vars whose running container value differs from .env/compose."""
    if not os.path.exists(DOCKER_SOCK):
        return {"available": False, "reason": f"docker socket not mounted at {DOCKER_SOCK}"}
    env = _parse_env(ENV_PATH)
    services = _compose_services_env()
    cnames = _compose_container_names()
    findings: list[dict] = []
    try:
        transport = httpx.AsyncHTTPTransport(uds=DOCKER_SOCK)
        async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
            async def dget(path):
                r = await client.get("http://localhost" + path)
                r.raise_for_status()
                return r.json()

            name_to_id: dict[str, str] = {}
            for c in await dget("/containers/json?all=1"):
                for n in c.get("Names", []):
                    name_to_id[n.lstrip("/")] = c["Id"]

            for svc, items in services.items():
                cname = cnames.get(svc) or svc
                cid = name_to_id.get(cname)
                if not cid:
                    continue  # container not present — nothing to compare
                detail = await dget(f"/containers/{cid}/json")
                actual_env = {}
                for e in (detail.get("Config", {}).get("Env") or []):
                    k, _, v = e.partition("=")
                    actual_env[k] = v
                for key, raw in items:
                    expected = _resolve(raw, env)
                    actual = actual_env.get(key, "")
                    if expected != actual:
                        secret = key in SECRET_KEYS
                        findings.append({
                            "service": svc, "container": cname, "key": key,
                            "expected": MASK if (secret and expected) else expected,
                            "actual": MASK if (secret and actual) else actual,
                        })
    except Exception as e:  # noqa: BLE001 - socket/daemon issues → report, don't crash
        return {"available": False, "reason": str(e)}
    return {"available": True, "findings": findings,
            "affected": sorted({f["container"] for f in findings})}


# ----------------------------------------------------------- workflow trace
# The Workflow I/O tab is a running LOG of pipeline traces. n8n is itself an
# append-only ledger (executions have monotonic ids) and the console holds no
# state — so the "log" is just a rendered tail of /executions, polled forward by
# the browser. Each entry pulls per-stage I/O straight out of the execution's
# runData, keyed by node name (see Architecture-and-Workflow.md for node order).

# Node name -> human stage label, in flow order. Drives failure flagging: when a
# run errors we map n8n's lastNodeExecuted to the stage that broke.
TRACE_STAGES = {
    "Webhook": "input",
    "Read Session": "session-read",
    "Build Ollama Request": "mood-prompt",
    "Ask Ollama": "mood",
    "Parse Ollama Response": "voice-select",
    "Ask Ollama Compose": "compose",
    "Parse Segments": "segments",
    "Write Session": "session-write",
    "Call Orchestrator": "synthesis",
    "Respond": "respond",
    "Play on Sonos": "playback",
}

# QT — the user's primary ESP32 device, recognised by its MAC so the log labels it
# rather than showing a bare address. Other devices show their raw session_id.
KNOWN_DEVICES = {"d8:3b:da:9d:18:64": "QT"}

# Per-step latency: the meaningful nodes (short label), in flow order. Each n8n node
# carries its own executionTime in runData, recorded as it ran — so the breakdown is
# free. `Call Orchestrator` (synthesis) is the one to watch; the orchestrator returns
# a finer per-segment split inside it (see `timings` on that node's output).
STEP_LABELS = [
    ("Read Session", "session"),
    ("Ask Ollama", "mood"),
    ("Ask Ollama Compose", "compose"),
    ("Parse Segments", "parse"),
    ("Call Orchestrator", "synthesis"),
    ("Play on Sonos", "play"),
]


def _node_json(run: dict, node: str):
    """First output item's json for a node in runData, or None if absent/empty."""
    try:
        return run[node][0]["data"]["main"][0][0]["json"]
    except Exception:  # noqa: BLE001 - tolerate any shape we didn't expect
        return None


def _node_ms(run: dict, node: str):
    """A node's execution time in ms, or None."""
    try:
        return run[node][0].get("executionTime")
    except Exception:  # noqa: BLE001
        return None


def _iso_ms(a: str, b: str):
    """Wall-clock ms between two ISO timestamps, or None."""
    try:
        ta = datetime.fromisoformat(a.replace("Z", "+00:00"))
        tb = datetime.fromisoformat(b.replace("Z", "+00:00"))
        return int((tb - ta).total_seconds() * 1000)
    except Exception:  # noqa: BLE001
        return None


def _trace_one(e: dict) -> dict:
    """Flatten one n8n execution into a per-stage trace entry for the log."""
    rd = (e.get("data") or {}).get("resultData") or {}
    run = rd.get("runData") or {}
    status = e.get("status") or ("success" if e.get("finished") else "running")

    wh = _node_json(run, "Webhook") or {}
    body = wh.get("body", {}) if isinstance(wh, dict) else {}
    sid = body.get("session_id")
    por = _node_json(run, "Parse Ollama Response") or {}
    ps = _node_json(run, "Parse Segments") or {}
    orch = _node_json(run, "Call Orchestrator") or {}

    # Voices + what they said: segments carry character/text/engine together. If the
    # run failed before Parse Segments, fall back to the picked[] selection (no text).
    voices = []
    segs = ps.get("segments") if isinstance(ps, dict) else None
    if segs:
        for s in segs:
            voices.append({"character": s.get("character"),
                           "engine": s.get("tts_engine"), "text": s.get("text")})
    elif por.get("picked"):
        for p in por["picked"]:
            voices.append({"character": p.get("name"),
                           "engine": "f5" if p.get("clip_on_disk") else "parler",
                           "text": None})

    # Per-step latency (n8n node executionTime), friendly-labelled, in flow order.
    steps = [{"label": label, "ms": _node_ms(run, node)}
             for node, label in STEP_LABELS if _node_ms(run, node) is not None]
    # Finer breakdown inside synthesis, if the orchestrator was instrumented.
    synth = orch.get("timings") if isinstance(orch, dict) else None

    # Failure flagging: which stage broke (only meaningful when not a success).
    failed_node = rd.get("lastNodeExecuted") if status not in ("success", "running") else None
    err = rd.get("error") or {}
    err_msg = err.get("message") if isinstance(err, dict) else str(err)

    return {
        "id": e.get("id"),
        "status": status,
        "started": e.get("startedAt"),
        "wall_ms": _iso_ms(e.get("startedAt", ""), e.get("stoppedAt", "")),
        "llm_ms": (_node_ms(run, "Ask Ollama") or 0) + (_node_ms(run, "Ask Ollama Compose") or 0),
        "device": KNOWN_DEVICES.get(sid, sid),
        "device_raw": sid,
        "heard": body.get("text"),
        "mood": por.get("mood"),
        "response_type": por.get("response_type"),
        "response_register": por.get("response_register"),
        "voices": voices,
        "output": {"url": orch.get("url"), "count": orch.get("count"), "skipped": orch.get("skipped")},
        "steps": steps,
        "synth": synth,
        "failed_stage": TRACE_STAGES.get(failed_node, failed_node),
        "error": err_msg if failed_node else None,
    }


async def workflow_trace(limit: int = 30) -> dict:
    """Last N executions flattened to per-stage traces (newest first), for the log."""
    if not (N8N_API_URL and N8N_API_KEY):
        return {"ok": False, "reason": "N8N_API_URL / N8N_API_KEY not configured"}
    headers = {"X-N8N-API-KEY": N8N_API_KEY, "accept": "application/json"}
    params = {"limit": limit, "includeData": "true"}
    if N8N_WORKFLOW_ID:
        params["workflowId"] = N8N_WORKFLOW_ID
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{N8N_API_URL.rstrip('/')}/api/v1/executions",
                                  headers=headers, params=params)
            r.raise_for_status()
            return {"ok": True, "runs": [_trace_one(e) for e in r.json().get("data", [])]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{e} (macvlan? see N8N_API_URL note in server.py)"}


# ----------------------------------------------------------- clip capture
# Download a YouTube snippet straight into a staging dir, preview it, then on
# accept move it into references/<folder>/ and ask the orchestrator to re-scan
# (which flips that voice from Parler to F5). The download recipe mirrors the
# proven `process_archetype_csv.py --local` path: yt-dlp section-trim → ffmpeg
# normalise to 22050 Hz mono s16. No VPN — UR1 shares the home public IP.

import shutil
import uuid

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_HEX_RE = re.compile(r"^[0-9a-f]{32}$")
# A rendered WAV basename: <uuid4>.wav or <uuid4>_raw.wav (no path traversal).
_GEN_FILE_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(_raw)?\.wav$")


def _safe_folder(name: str) -> str:
    """Reject anything that isn't a bare [A-Za-z0-9_] token (no path traversal)."""
    name = (name or "").strip()
    if not _SAFE_NAME_RE.match(name):
        raise ValueError(f"unsafe folder/character name: {name!r}")
    return name


def _ts_to_seconds(ts: str) -> float:
    """'mm:ss' / 'h:mm:ss' / plain seconds → float seconds."""
    ts = str(ts).strip()
    if not ts:
        return 0.0
    parts = [float(p) for p in ts.split(":")]
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def clip_ready() -> dict:
    """Are the tools + mounts present to capture clips at all?"""
    refs_ok = os.path.isdir(REFERENCES_DIR) and os.access(REFERENCES_DIR, os.W_OK)
    return {
        "yt_dlp": bool(shutil.which("yt-dlp")),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "references_dir": REFERENCES_DIR,
        "references_writable": refs_ok,
        "cookies": os.path.exists(COOKIES_FILE),
    }


async def _run(cmd: list[str]) -> tuple[int, str]:
    """Run a subprocess, returning (returncode, last-500-chars-of-stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, stderr = await proc.communicate()
    return proc.returncode, (stderr or b"").decode(errors="replace")[-500:]


async def clip_capture(character: str, url: str, start: str, duration) -> dict:
    """Download [start, start+duration] of `url` to STAGING_DIR as a normalised WAV."""
    if not (url or "").strip():
        return {"ok": False, "reason": "url is required"}
    rdy = clip_ready()
    if not (rdy["yt_dlp"] and rdy["ffmpeg"]):
        return {"ok": False, "reason": "yt-dlp / ffmpeg not available in this container"}

    try:
        start_sec = _ts_to_seconds(start)
        dur_sec = float(duration) if duration not in (None, "") else 30.0
    except ValueError:
        return {"ok": False, "reason": "start/duration must be mm:ss or seconds"}
    if dur_sec <= 0:
        return {"ok": False, "reason": "duration must be positive"}
    end_sec = start_sec + dur_sec

    # strip the ?si=… share param that breaks yt-dlp
    url = url.split("?si=")[0].strip()

    os.makedirs(STAGING_DIR, exist_ok=True)
    staged_id = uuid.uuid4().hex
    raw_tmpl = os.path.join(STAGING_DIR, f"_{staged_id}_raw.%(ext)s")

    cmd = [
        "yt-dlp", "--no-playlist", "-x", "--audio-format", "wav", "--audio-quality", "0",
        "--download-sections", f"*{start_sec}-{end_sec}", "--force-keyframes-at-cuts",
    ]
    if os.path.exists(COOKIES_FILE):
        cmd += ["--cookies", COOKIES_FILE]
    cmd += ["-o", raw_tmpl, url]

    rc, err = await _run(cmd)
    raw = os.path.join(STAGING_DIR, f"_{staged_id}_raw.wav")
    if rc != 0 or not os.path.exists(raw):
        # yt-dlp may not always land on .wav — find whatever it wrote
        cand = [p for p in (os.path.join(STAGING_DIR, f) for f in os.listdir(STAGING_DIR))
                if os.path.basename(p).startswith(f"_{staged_id}_raw")]
        if not cand:
            return {"ok": False, "reason": f"yt-dlp failed: {err or 'no output file'}"}
        raw = cand[0]

    staged = os.path.join(STAGING_DIR, f"{staged_id}.wav")
    rc, err = await _run([
        "ffmpeg", "-y", "-i", raw,
        "-ar", str(CLIP_SAMPLE_RATE), "-ac", "1", "-sample_fmt", "s16", staged,
    ])
    try:
        os.remove(raw)
    except OSError:
        pass
    if rc != 0 or not os.path.exists(staged):
        return {"ok": False, "reason": f"ffmpeg normalise failed: {err}"}

    return {"ok": True, "staged_id": staged_id,
            "seconds": round(end_sec - start_sec, 1),
            "size": os.path.getsize(staged)}


def _next_clip_name(folder_dir: str, folder: str) -> str:
    """Next free '<folder_lower>_clip_NN.wav' in the destination folder."""
    base = folder.lower()
    pat = re.compile(rf"^{re.escape(base)}_clip_(\d+)\.wav$", re.IGNORECASE)
    nums = []
    if os.path.isdir(folder_dir):
        for f in os.listdir(folder_dir):
            m = pat.match(f)
            if m:
                nums.append(int(m.group(1)))
    return f"{base}_clip_{(max(nums) + 1 if nums else 1):02d}.wav"


async def clip_accept(character: str, staged_id: str, folder: str) -> dict:
    """Move a staged clip into references/<folder>/ then re-scan to flip it to F5."""
    if not _HEX_RE.match(staged_id or ""):
        return {"ok": False, "reason": "bad staged_id"}
    staged = os.path.join(STAGING_DIR, f"{staged_id}.wav")
    if not os.path.exists(staged):
        return {"ok": False, "reason": "staged clip not found (already accepted/expired?)"}
    try:
        folder = _safe_folder(folder or character)
    except ValueError as e:
        return {"ok": False, "reason": str(e)}

    dest_dir = os.path.join(REFERENCES_DIR, folder)
    try:
        os.makedirs(dest_dir, exist_ok=True)
        name = _next_clip_name(dest_dir, folder)
        dest = os.path.join(dest_dir, name)
        shutil.move(staged, dest)
    except OSError as e:
        return {"ok": False, "reason": f"could not write clip: {e} (references mounted rw?)"}

    # Ask the orchestrator to re-scan — this flips the voice Parler→F5 and persists.
    scan = {"ok": False}
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(f"{ORCHESTRATOR_URL}/admin/scan-references")
            r.raise_for_status()
            scan = {"ok": True, **r.json()}
    except Exception as e:  # noqa: BLE001
        scan = {"ok": False, "reason": str(e)}

    return {"ok": True, "saved": dest, "name": name, "scan": scan}


def clip_reject(staged_id: str) -> dict:
    """Discard a staged clip."""
    if not _HEX_RE.match(staged_id or ""):
        return {"ok": False, "reason": "bad staged_id"}
    staged = os.path.join(STAGING_DIR, f"{staged_id}.wav")
    try:
        os.remove(staged)
    except FileNotFoundError:
        pass
    except OSError as e:
        return {"ok": False, "reason": str(e)}
    return {"ok": True}


# ----------------------------------------------------------- voice preview / warmup
# Hear any character on demand: generate a fresh random in-character line (Ollama,
# the C3 personality model), synthesize it via the orchestrator, and return BOTH the
# vintage-filtered and the raw URLs so the operator can A/B them. F5-warmup fires one
# throwaway F5 render to pay F5's one-time ASR init so the first real Play is fast.

_PREVIEW_TOPICS = [
    "the weather today", "how your day is going", "a word of encouragement",
    "what's for dinner", "a fond memory", "the morning news", "a passing thought",
    "the view outside", "an old friend", "your plans for the weekend",
]


async def _ollama_line(name: str, voice_description: str, register: str) -> str:
    """Ask Ollama for ONE short in-character spoken line (for the voice preview)."""
    persona = name.replace("_", " ")
    topic = random.choice(_PREVIEW_TOPICS)
    system = (f"You are {persona}. Reply with ONE short spoken line (max 15 words) in your "
              f"distinctive voice and manner." + (f" Tone: {register}." if register else "") +
              " No quotation marks, no stage directions, no preamble — just the spoken line.")
    if voice_description:
        system += f" Voice: {voice_description}"
    body = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": f"Say something about {topic}."}],
        "stream": False,
        "options": {"temperature": 0.9, "seed": random.randint(1, 2_000_000_000)},
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{OLLAMA_URL}/api/chat", json=body)
        r.raise_for_status()
        content = ((r.json().get("message") or {}).get("content") or "").strip().strip('"').strip()
    return content or f"Hello, this is {persona}."


async def voice_preview(name: str) -> dict:
    """Generate a random in-character line and synthesize it (filtered + raw URLs)."""
    try:
        name = _safe_folder(name)
    except ValueError as e:
        return {"ok": False, "reason": str(e)}
    try:
        desc = await voices()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"could not load voices: {e}"}
    char = next((c for c in desc.get("characters", []) if c.get("name") == name), None)
    if char is None:
        return {"ok": False, "reason": f"unknown character: {name}"}

    try:
        text = await _ollama_line(name, char.get("voice_description", ""),
                                  ", ".join(char.get("response_register", []) or []))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"comment generation failed (ollama): {e}"}

    on_disk = bool(char.get("clip_on_disk"))
    payload = {
        "text": text,
        "tts_engine": "f5" if on_disk else "parler",
        "reference_clip": char.get("reference_clip") if on_disk else None,
        "voice_description": char.get("voice_description"),
        "keep_raw": True,
    }
    try:
        async with httpx.AsyncClient(timeout=300.0) as client:
            r = await client.post(f"{ORCHESTRATOR_URL}/speak", json=payload)
            if r.status_code >= 400:
                detail = (r.json().get("detail", r.text)
                          if r.headers.get("content-type", "").startswith("application/json") else r.text)
                return {"ok": False, "reason": f"synthesis failed: {detail}", "text": text}
            d = r.json()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"orchestrator unreachable: {e}", "text": text}
    # Return basenames; the browser plays them SAME-ORIGIN via /api/voice-preview-audio
    # (the orchestrator's :5005 isn't reliably reachable from the browser).
    return {"ok": True, "text": text, "engine": d.get("engine"),
            "file": (d.get("url") or "").rsplit("/", 1)[-1] or None,
            "file_raw": (d.get("url_raw") or "").rsplit("/", 1)[-1] or None}


async def f5_warmup() -> dict:
    """Render one throwaway line on an F5 voice to pay F5's one-time ASR init."""
    try:
        desc = await voices()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"could not load voices: {e}"}
    f5 = next((c for c in desc.get("characters", []) if c.get("clip_on_disk")), None)
    if f5 is None:
        return {"ok": False, "reason": "no F5 voice (clip on disk) to warm up"}
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{ORCHESTRATOR_URL}/speak",
                                  json={"text": "Warming up.", "tts_engine": "f5",
                                        "reference_clip": f5.get("reference_clip")})
            r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"warmup render failed: {e}"}
    return {"ok": True, "voice": f5.get("name"), "ms": int((time.perf_counter() - t0) * 1000)}


# --------------------------------------------------------------------------- API

@app.get("/api/health")
async def api_health():
    return await service_health()


@app.get("/api/voices")
async def api_voices():
    return await voices()


@app.get("/api/clients")
async def api_clients():
    return await clients()


@app.post("/api/clients/rename")
async def api_clients_rename(req: Request):
    b = await req.json()
    return await rename_client(b.get("mac", ""), b.get("name", ""))


@app.post("/api/voice-preview")
async def api_voice_preview(req: Request):
    b = await req.json()
    return await voice_preview(b.get("name", ""))


@app.get("/api/voice-preview-audio")
async def api_voice_preview_audio(file: str):
    """Stream a rendered preview WAV back SAME-ORIGIN (mirrors /api/clip-preview)."""
    if not _GEN_FILE_RE.match(file or ""):
        return JSONResponse({"error": "bad file"}, status_code=400)
    path = os.path.join(GENERATED_DIR, file)
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/f5-warmup")
async def api_f5_warmup():
    return await f5_warmup()


@app.post("/api/voice-description")
async def api_voice_description(req: Request):
    """Proxy a single Parler voice_description edit to the orchestrator."""
    b = await req.json()
    payload = {"name": b.get("name", ""), "voice_description": b.get("voice_description", "")}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(f"{ORCHESTRATOR_URL}/admin/voice-description", json=payload)
            if r.status_code >= 400:
                detail = r.json().get("detail", r.text) if r.headers.get("content-type", "").startswith("application/json") else r.text
                return JSONResponse({"ok": False, "reason": detail}, status_code=r.status_code)
            return {"ok": True, **r.json()}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "reason": str(e)}, status_code=502)


@app.get("/api/config")
async def api_config():
    return validate_config()


@app.get("/api/env")
async def api_env():
    return env_config()


@app.post("/api/env")
async def api_env_save(req: Request):
    body = await req.json()
    return write_env(body.get("updates", {}))


@app.get("/api/drift")
async def api_drift():
    return await drift_check()


@app.get("/api/workflow-trace")
async def api_workflow_trace(limit: int = 30):
    return await workflow_trace(limit)


@app.get("/api/clip-ready")
async def api_clip_ready():
    return clip_ready()


@app.post("/api/clip-capture")
async def api_clip_capture(req: Request):
    b = await req.json()
    return await clip_capture(b.get("character", ""), b.get("url", ""),
                              b.get("start", "0"), b.get("duration", 30))


@app.get("/api/clip-preview")
async def api_clip_preview(staged_id: str):
    if not _HEX_RE.match(staged_id or ""):
        return JSONResponse({"error": "bad staged_id"}, status_code=400)
    path = os.path.join(STAGING_DIR, f"{staged_id}.wav")
    if not os.path.exists(path):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(path, media_type="audio/wav")


@app.post("/api/clip-accept")
async def api_clip_accept(req: Request):
    b = await req.json()
    return await clip_accept(b.get("character", ""), b.get("staged_id", ""), b.get("folder", ""))


@app.post("/api/clip-reject")
async def api_clip_reject(req: Request):
    b = await req.json()
    return clip_reject(b.get("staged_id", ""))


@app.get("/health")
async def health():
    return {"status": "ok"}


# --------------------------------------------------------------------------- UI

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>Bumblebee Admin Console</title>
<style>
 body{font:14px system-ui,sans-serif;margin:0;background:#1a1a1a;color:#eee}
 header{background:#3a2f00;padding:12px 20px;border-bottom:2px solid #ffcc00}
 header h1{margin:0;font-size:18px;color:#ffcc00}
 nav{display:flex;gap:4px;padding:0 20px;background:#222;border-bottom:1px solid #333;flex-wrap:wrap}
 nav button{background:transparent;color:#bbb;border:0;border-bottom:3px solid transparent;
  border-radius:0;padding:12px 16px;cursor:pointer;font-weight:600;font-size:14px}
 nav button:hover{color:#fff}
 nav button.active{color:#ffcc00;border-bottom-color:#ffcc00}
 main{padding:20px;max-width:1100px}
 .panel{display:none}
 .panel.active{display:block}
 .panelhead{display:flex;align-items:center;gap:12px;margin-bottom:12px}
 .panelhead h2{margin:0;font-size:15px;color:#ffcc00}
 .panelhead .updated{margin-left:auto;font-size:12px;color:#888}
 table{width:100%;border-collapse:collapse;font-size:13px}
 th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #333}
 .ok{color:#4caf50}.bad{color:#f44336}.warn{color:#ffb300}
 .pill{padding:2px 8px;border-radius:10px;font-size:11px}
 .clip{background:#1b3a1b;color:#9f9}.parler{background:#3a2f1b;color:#fc9}
 .vdtext{color:#fc9;white-space:pre-wrap}
 .vdbtn{padding:2px 8px;font-size:11px}
 textarea.vdedit{width:100%;box-sizing:border-box;background:#111;color:#eee;border:1px solid #444;border-radius:4px;padding:5px 7px;font:12px/1.4 monospace;resize:vertical}
 pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px}
 button.refresh{background:#ffcc00;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;font-weight:600}
 /* Workflow I/O log */
 .wftools{display:flex;align-items:center;gap:10px;margin-bottom:10px;font-size:12px;color:#888}
 .livedot{width:8px;height:8px;border-radius:50%;background:#4caf50;display:inline-block;animation:pulse 1.6s infinite}
 .livedot.paused{background:#888;animation:none}
 @keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}
 .wfentry{border:1px solid #333;border-left:3px solid #4caf50;border-radius:6px;padding:8px 12px;margin-bottom:8px;background:#202020}
 .wfentry.fail{border-left-color:#f44336}
 .wfentry.new{animation:flash 1.4s ease-out}
 @keyframes flash{from{background:#2c3a2c}to{background:#202020}}
 .wfhead{display:flex;gap:10px;align-items:center;font-size:12px;color:#999;margin-bottom:6px;flex-wrap:wrap}
 .wfhead .id{color:#ffcc00;font-weight:600}
 .wfhead .lat{margin-left:auto;color:#888;font-family:monospace}
 .wfrow{display:flex;gap:8px;font-size:13px;padding:2px 0}
 .wfrow .k{flex:0 0 80px;color:#888}
 .wfrow .v{flex:1;min-width:0;word-break:break-word}
 .said b{color:#ffcc00;font-weight:600}.said div{padding:1px 0}
</style></head><body>
<header><h1>🐝 Bumblebee Admin Console <small style="color:#aa9">P1</small></h1></header>
<nav id=tabs></nav>
<main id=panels></main>
<script>
async function j(u){const r=await fetch(u);return r.json()}

// ---- loaders: each returns the inner HTML for its panel body -----------------
async function loadHealth(){
 const d=await j('/api/health');
 return '<table><tr><th>service</th><th>state</th><th>detail</th><th>endpoint</th></tr>'+
  d.map(s=>{const [c,t]=s.ok==null?['warn','○ n/a']:(s.ok?['ok','● up']:['bad','● down']);
   return `<tr><td>${s.name}</td><td class="${c}">${t}</td><td>${s.detail||''}</td><td>${s.url||''}</td></tr>`;}).join('')+'</table>';
}
// ---- Devices: the ESP32 clients the gateway has seen. Online state is live; name + last
// heard/said are persisted in Redis by the gateway, so they survive a restart.
function ago(ts){
 if(!ts)return '—';
 const s=Math.max(0,Math.floor(Date.now()/1000-ts));
 if(s<60)return s+'s ago';
 if(s<3600)return Math.floor(s/60)+'m ago';
 if(s<86400)return Math.floor(s/3600)+'h ago';
 return Math.floor(s/86400)+'d ago';
}
function dnLocked(mac,name){
 const t=name?`<b>${esc(name)}</b>`:'<i style="color:#888">— unnamed —</i>';
 return `${t} <button class="refresh vdbtn" onclick="dnEdit('${esc(mac)}')">✏</button>`;
}
function dnEdit(mac){
 const td=document.getElementById('dn-'+mac);
 td.innerHTML='<input class=dnedit style="width:110px;background:#111;color:#eee;border:1px solid #444;border-radius:4px;padding:4px 6px">'+
   ` <button class="refresh vdbtn" style="background:#4caf50" onclick="dnSave('${esc(mac)}')">✓</button>`+
   ` <button class="refresh vdbtn" style="background:#888" onclick="dnCancel('${esc(mac)}')">✗</button>`;
 const inp=td.querySelector('.dnedit');inp.value=td.dataset.name||'';inp.focus();
 inp.onkeydown=e=>{if(e.key==='Enter')dnSave(mac);if(e.key==='Escape')dnCancel(mac);};
}
function dnCancel(mac){const td=document.getElementById('dn-'+mac);td.innerHTML=dnLocked(mac,td.dataset.name||'');}
function dnSave(mac){
 const td=document.getElementById('dn-'+mac);
 const val=td.querySelector('.dnedit').value.trim();
 td.innerHTML='⏳';
 fetch('/api/clients/rename',{method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({mac,name:val})})
  .then(r=>r.json()).then(d=>{
   if(!d.ok){td.innerHTML=dnLocked(mac,td.dataset.name||'')+` <span class=bad>✗ ${esc(d.reason||'failed')}</span>`;return;}
   td.dataset.name=d.name;td.innerHTML=dnLocked(mac,d.name)+' <span class=ok>✓</span>';
  }).catch(e=>{td.innerHTML=dnLocked(mac,td.dataset.name||'')+` <span class=bad>✗ ${esc(e)}</span>`;});
}
async function loadDevices(){
 const d=await j('/api/clients');
 const devs=d.devices||[];
 let h='';
 if(d.error)h+=`<p class=warn>⚠ gateway unreachable: ${esc(d.error)} — list may be stale.</p>`;
 h+=`<p><span class=ok>${d.online||0} online</span> · ${devs.length} known device(s). `+
    `Names are cosmetic — the MAC stays each device's conversation key.</p>`;
 if(!devs.length)return h+'<p style="color:#888">No devices yet — power on an ESP32; it appears here on its first OTA/connect.</p>';
 h+='<table><tr><th style="min-width:150px">name</th><th>status</th><th>MAC</th><th>last seen</th>'+
    '<th>IP</th><th style="width:26%">last heard</th><th style="width:26%">last said</th></tr>';
 for(const x of devs){
  const mac=x.mac;
  const st=x.online?'<span class=ok>● online</span>':'<span style="color:#888">○ offline</span>';
  h+=`<tr>`+
     `<td id="dn-${esc(mac)}" data-name="${esc(x.name||'')}">${dnLocked(mac,x.name||'')}</td>`+
     `<td style="white-space:nowrap">${st}</td>`+
     `<td style="font:12px monospace;color:#aac">${esc(mac)}</td>`+
     `<td style="white-space:nowrap">${ago(x.last_seen)}</td>`+
     `<td style="font:12px monospace;color:#9a9">${esc(x.last_ip||'—')}</td>`+
     `<td style="color:#9cf">${x.last_heard?'“'+esc(x.last_heard)+'”':'—'}</td>`+
     `<td style="color:#fc9">${x.last_said?'“'+esc(x.last_said)+'”':'—'}</td>`+
     `</tr>`;
 }
 return h+'</table>';
}
const MASK='••••••••';  // keep in sync with MASK in server.py
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/"/g,'&quot;');}
async function loadConfig(){
 const [cfg,env,drift]=await Promise.all([j('/api/config'),j('/api/env'),j('/api/drift')]);
 // drift map: "service|KEY" -> running value (differs from .env/compose)
 const dmap={};
 if(drift.available)for(const f of drift.findings)dmap[f.service+'|'+f.key]=f.actual;
 let h='';
 // drift banner — containers whose running env no longer matches .env/compose
 if(drift.available&&drift.affected.length){
  h+=`<div style="background:#3a1f1f;border:1px solid #f44336;border-radius:6px;padding:10px 12px;margin-bottom:12px">`+
     `<b class=bad>⚠ ${drift.affected.length} container(s) need recreating</b> — edited since last start, running a stale value: `+
     `<b>${esc(drift.affected.join(', '))}</b>. Recreate them to apply (a restart is not enough — env is read at create time).</div>`;
 }else if(!drift.available){
  h+=`<p style="color:#888">drift check unavailable: ${esc(drift.reason||'')}</p>`;
 }
 // validator strip (referenced-but-undefined ${VARS})
 h+=cfg.findings&&cfg.findings.length
   ?'<table><tr><th>level</th><th>finding</th></tr>'+
     cfg.findings.map(f=>`<tr><td class="${f.level==='error'?'bad':'warn'}">${f.level}</td><td>${f.msg}</td></tr>`).join('')+'</table>'
   :'<p class=ok>● no validation issues</p>';
 h+=`<p style="color:#888;margin:10px 0">Edits write <code>${esc(env.env_path)}</code>; recreate the affected container(s) to apply (values are read at start). `+
   `Greyed rows are hardcoded in compose — convert to <code>\\${VAR}</code> to make them editable.</p>`;
 if(!env.writable)h+='<p class=warn>⚠ .env is mounted read-only — saving will fail until the mount is set read-write.</p>';
 for(const g of env.groups){
  h+=`<h3 style="color:#ffcc00;margin:18px 0 4px;font-size:14px">${esc(g.service)}</h3>`;
  h+='<table><tr><th style="width:34%">variable</th><th>value</th><th style="width:90px">source</th></tr>';
  for(const f of g.fields){
   const src=f.source==='env'?'<span class=ok>.env</span>'
     :f.source==='default'?'<span class=warn>default</span>'
     :'<span style="color:#888">compose</span>';
   let cell;
   if(f.editable){
    const t=f.secret?'password':'text';
    cell=`<input data-envkey="${esc(f.env_key)}" data-secret="${f.secret}" type="${t}" value="${esc(f.value)}" `+
      `placeholder="(unset)" style="width:100%;box-sizing:border-box;background:#111;color:#eee;border:1px solid #444;border-radius:4px;padding:5px 7px;font:12px monospace">`;
   }else{
    cell=`<span style="color:#888">${f.secret&&f.has_value?'••••':esc(f.value)||'—'}</span>`;
   }
   // drift badge: this field's running value differs from .env/compose
   const dk=g.service+'|'+f.key;
   if(dk in dmap){
    cell+=`<div class=bad style="font-size:11px;margin-top:3px">⚠ running: <code>${esc(dmap[dk]||'(empty)')}</code> — recreate to apply</div>`;
   }
   h+=`<tr><td><code>${esc(f.key)}</code></td><td>${cell}</td><td>${src}</td></tr>`;
  }
  h+='</table>';
 }
 h+=`<div style="margin-top:18px"><button class=refresh onclick="saveEnv()">💾 Save .env</button>`+
    `<span id=env-save-msg style="margin-left:12px"></span></div>`;
 return h;
}
async function saveEnv(){
 const msg=document.getElementById('env-save-msg');msg.textContent='saving…';
 const updates={};
 document.querySelectorAll('input[data-envkey]').forEach(i=>{
  if(i.dataset.secret==='true'&&(i.value===MASK||i.value===''))return; // unchanged/blank secret
  updates[i.dataset.envkey]=i.value;
 });
 try{
  const r=await fetch('/api/env',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({updates})});
  const d=await r.json();
  if(d.ok){
   const w=d.written.length?d.written.join(', '):'(no changes)';
   msg.innerHTML=`<span class=ok>saved: ${esc(w)}</span>`+
     (d.rejected&&d.rejected.length?` <span class=warn>rejected: ${esc(d.rejected.join(', '))}</span>`:'');
  }else{msg.innerHTML=`<span class=bad>error: ${esc(d.reason)}</span>`;}
 }catch(e){msg.innerHTML=`<span class=bad>error: ${esc(e)}</span>`;}
}
async function loadVoices(){
 const d=await j('/api/voices');const c=(d.characters||[]).slice();
 const clip=c.filter(x=>x.clip_on_disk).length;
 // Sort by model (F5 clips first, then Parler), then alphabetically by name.
 c.sort((a,b)=>{
  const ka=a.clip_on_disk?0:1, kb=b.clip_on_disk?0:1;
  if(ka!==kb)return ka-kb;
  return a.name.localeCompare(b.name);
 });
 return `<p>${d.total} voices · <span class=ok>${clip} with clip (F5)</span> · ${c.length-clip} Parler-only `+
  `<button class="refresh vdbtn" style="margin-left:8px" onclick="f5Warmup(this)">🔥 Warm up F5</button></p>`+
  '<table><tr><th>name</th><th style="min-width:90px">engine</th><th>response_type</th><th>register</th>'+
  '<th style="width:30%">parler description</th><th style="min-width:320px">play</th></tr>'+
  c.map(x=>{
   const n=x.name;
   // Parler description shown (and editable) ONLY when there's no F5 clip; F5 rows don't use it.
   const vd=x.clip_on_disk
     ? '<span style="color:#666">—</span>'
     : `<div id="vd-${n}" data-desc="${esc(x.voice_description||'')}">${vdLockedHTML(n,x.voice_description||'')}</div>`;
   return `<tr><td>${n.replace(/_/g,' ')}</td>`+
    `<td style="white-space:nowrap"><span class="pill ${x.clip_on_disk?'clip':'parler'}">${x.clip_on_disk?'F5 clip':'Parler'}</span></td>`+
    `<td>${(x.response_type||[]).join(', ')}</td><td>${(x.response_register||[]).join(', ')}</td>`+
    `<td>${vd}</td>`+
    `<td id="pv-${n}"><button class="refresh vdbtn" onclick="vpPlay('${n}')">▶ Play</button></td></tr>`;
  }).join('')+'</table>';
}
// Voice preview: generate a fresh random in-character line, synthesize it, and offer
// BOTH the vintage-filtered and the raw audio to play. First Play after an F5 restart
// pays the one-time ~20-25s init — use 🔥 Warm up F5 to get that out of the way.
function vpPlay(n){
 const cell=document.getElementById('pv-'+n);
 cell.innerHTML='⏳ generating…';
 fetch('/api/voice-preview',{method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({name:n})})
  .then(r=>r.json()).then(d=>{
   if(!d.ok){cell.innerHTML=`<span class=bad>✗ ${esc(d.reason||'failed')}</span> `+
     `<button class="refresh vdbtn" onclick="vpPlay('${n}')">↻ retry</button>`;return;}
   const aud=f=>`/api/voice-preview-audio?file=${encodeURIComponent(f)}`;
   const raw=d.file_raw?`<audio id="pvraw-${n}" src="${aud(d.file_raw)}"></audio>`+
     ` <button class="refresh vdbtn" onclick="document.getElementById('pvraw-${n}').play()">▶ Raw</button>`:'';
   cell.innerHTML=`<audio id="pvflt-${n}" src="${aud(d.file)}" autoplay></audio>`+
     `<span class="pill ${d.engine==='f5'?'clip':'parler'}">${esc(d.engine||'?')}</span> `+
     `<button class="refresh vdbtn" onclick="document.getElementById('pvflt-${n}').play()">▶ Filtered</button>`+raw+
     ` <button class="refresh vdbtn" style="background:#888" onclick="vpPlay('${n}')">↻ New line</button>`+
     `<div style="color:#fc9;font-size:12px;margin-top:4px">“${esc(d.text||'')}”</div>`;
  }).catch(e=>{cell.innerHTML=`<span class=bad>✗ ${esc(e)}</span>`;});
}
function f5Warmup(btn){
 const orig=btn.innerHTML;btn.disabled=true;btn.innerHTML='🔥 warming…';
 fetch('/api/f5-warmup',{method:'POST'}).then(r=>r.json()).then(d=>{
  btn.disabled=false;
  btn.innerHTML=d.ok?`🔥 warmed (${(d.ms/1000).toFixed(1)}s)`:`✗ ${esc(d.reason||'failed')}`;
  setTimeout(()=>{btn.innerHTML=orig;},6000);
 }).catch(e=>{btn.disabled=false;btn.innerHTML=`✗ ${esc(e)}`;});
}
// Parler description: locked read-only by default; ✏ Edit unlocks JUST that cell so it
// can't be changed by an accidental click. The live value lives in the cell's data-desc
// attribute, so Cancel always restores it. Persisted via /api/voice-description → orchestrator.
function vdLockedHTML(n,desc,extra){
 const t=desc?`<span class=vdtext>${esc(desc)}</span>`:'<i style="color:#666">— no description —</i>';
 return `${t} <button class="refresh vdbtn" onclick="vdEdit('${n}')">✏ Edit</button>${extra||''}`;
}
function vdRenderLocked(n,extra){
 const td=document.getElementById('vd-'+n);
 td.innerHTML=vdLockedHTML(n,td.dataset.desc||'',extra);
}
function vdEdit(n){
 const td=document.getElementById('vd-'+n);
 td.innerHTML='<textarea class=vdedit rows=3></textarea>'+
   '<div style="margin-top:4px">'+
   `<button class="refresh vdbtn" style="background:#4caf50" onclick="vdSave('${n}')">✓ Save</button> `+
   `<button class="refresh vdbtn" style="background:#888" onclick="vdCancel('${n}')">✗ Cancel</button>`+
   '</div>';
 const ta=td.querySelector('.vdedit');
 ta.value=td.dataset.desc||'';ta.focus();
}
function vdCancel(n){vdRenderLocked(n);}
function vdSave(n){
 const td=document.getElementById('vd-'+n);
 const ta=td.querySelector('.vdedit');
 const val=ta.value.trim();
 const bar=td.querySelector('div');
 if(!val){bar.innerHTML='<span class=warn>description must not be empty</span> '+bar.innerHTML;return;}
 bar.innerHTML='⏳ saving…';
 fetch('/api/voice-description',{method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({name:n,voice_description:val})})
  .then(r=>r.json()).then(d=>{
   if(!d.ok){bar.innerHTML=`<span class=bad>✗ ${esc(d.reason||'failed')}</span> `+
     `<button class="refresh vdbtn" style="background:#4caf50" onclick="vdSave('${n}')">↻ retry</button> `+
     `<button class="refresh vdbtn" style="background:#888" onclick="vdCancel('${n}')">✗ Cancel</button>`;return;}
   td.dataset.desc=d.voice_description;
   vdRenderLocked(n,' <span class=ok>saved ✓</span>');
  }).catch(e=>{bar.innerHTML=`<span class=bad>✗ ${esc(e)}</span>`;});
}
// ---- Clip Capture: grab a YouTube snippet per Parler-only voice, preview, accept/reject.
// Row state lives client-side keyed by character; the table only reloads on ↻ refresh.
const clipState={};  // name -> {staged_id, folder}
async function loadClips(){
 const [rdy,d]=await Promise.all([j('/api/clip-ready'),j('/api/voices')]);
 const c=(d.characters||[]).filter(x=>!x.clip_on_disk);  // Parler-only = needs a clip
 let h='';
 if(!rdy.yt_dlp||!rdy.ffmpeg){
  h+=`<div style="background:#3a1f1f;border:1px solid #f44336;border-radius:6px;padding:10px 12px;margin-bottom:12px" class=bad>`+
     `⚠ capture unavailable — ${!rdy.yt_dlp?'yt-dlp ':''}${!rdy.ffmpeg?'ffmpeg ':''}not found in this container (rebuild needed).</div>`;
 }else if(!rdy.references_writable){
  h+=`<div style="background:#3a2f1b;border:1px solid #ffb300;border-radius:6px;padding:10px 12px;margin-bottom:12px" class=warn>`+
     `⚠ accepting will fail — <code>${esc(rdy.references_dir)}</code> is not mounted read-write.</div>`;
 }
 h+=`<p style="color:#888;margin:0 0 12px">${c.length} Parler-only voice(s) still need an F5 clip. `+
    `Paste a YouTube URL, set start + duration, Execute, then preview and Accept to flip the voice to F5.`+
    `${rdy.cookies?' <span class=ok>· cookies loaded</span>':''}</p>`;
 if(!c.length)return h+'<p class=ok>● every voice already has a clip 🎉</p>';
 h+='<table><tr><th style="width:15%">character</th><th>YouTube URL</th>'+
    '<th style="width:64px">start</th><th style="width:56px">secs</th>'+
    '<th style="width:34%">action</th></tr>';
 for(const x of c){
  const n=x.name, folder=x.reference_folder||x.name;
  h+=`<tr id="cliprow-${n}" data-folder="${esc(folder)}">`+
     `<td>${esc(n.replace(/_/g,' '))}</td>`+
     `<td><input id="clipurl-${n}" placeholder="https://youtu.be/…" style="width:100%;box-sizing:border-box;background:#111;color:#eee;border:1px solid #444;border-radius:4px;padding:5px 7px;font:12px monospace"></td>`+
     `<td><input id="clipstart-${n}" value="0:00" style="width:100%;box-sizing:border-box;background:#111;color:#eee;border:1px solid #444;border-radius:4px;padding:5px 5px;font:12px monospace"></td>`+
     `<td><input id="clipdur-${n}" value="30" style="width:100%;box-sizing:border-box;background:#111;color:#eee;border:1px solid #444;border-radius:4px;padding:5px 5px;font:12px monospace"></td>`+
     `<td id="clipact-${n}" style="white-space:nowrap">`+
       `<button class=refresh onclick="clipExec('${n}')">▶ Execute</button></td>`+
     `</tr>`;
 }
 return h+'</table>';
}
function clipExec(n){
 const cell=document.getElementById('clipact-'+n);
 const url=document.getElementById('clipurl-'+n).value;
 const start=document.getElementById('clipstart-'+n).value;
 const dur=document.getElementById('clipdur-'+n).value;
 if(!url.trim()){cell.innerHTML='<span class=warn>enter a URL first</span> '+cell.innerHTML;return;}
 cell.innerHTML='⏳ downloading…';
 fetch('/api/clip-capture',{method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({character:n,url,start,duration:dur})})
  .then(r=>r.json()).then(d=>{
   if(!d.ok){cell.innerHTML=`<span class=bad>✗ ${esc(d.reason||'failed')}</span> `+
     `<button class=refresh onclick="clipExec('${n}')">↻ retry</button>`;return;}
   clipState[n]={staged_id:d.staged_id};
   // Native <audio controls> — a guaranteed, visible play/scrub control (a custom
   // .play() button is fragile: autoplay policy + invisible element). Cache-bust
   // the src so re-Execute on the same row always loads the fresh capture.
   cell.innerHTML=`<audio controls preload="auto" src="/api/clip-preview?staged_id=${d.staged_id}&t=${Date.now()}" `+
     `style="height:30px;vertical-align:middle;width:190px"></audio> `+
     `<button class=refresh style="background:#4caf50" onclick="clipAccept('${n}')">✓ Accept</button> `+
     `<button class=refresh style="background:#888" onclick="clipReject('${n}')">✗ Reject</button> `+
     `<span class=ok>${d.seconds}s · ${(d.size/1024|0)}KB</span>`;
  }).catch(e=>{cell.innerHTML=`<span class=bad>✗ ${esc(e)}</span>`;});
}
function clipAccept(n){
 const st=clipState[n];if(!st)return;
 const cell=document.getElementById('clipact-'+n);
 const folder=document.getElementById('cliprow-'+n).dataset.folder;
 cell.innerHTML='⏳ saving…';
 fetch('/api/clip-accept',{method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({character:n,staged_id:st.staged_id,folder})})
  .then(r=>r.json()).then(d=>{
   if(!d.ok){cell.innerHTML=`<span class=bad>✗ ${esc(d.reason||'failed')}</span>`;return;}
   delete clipState[n];
   const flipped=d.scan&&d.scan.ok?' <span class=ok>→ now F5</span>':
     ` <span class=warn>(saved; re-scan: ${esc((d.scan&&d.scan.reason)||'?')})</span>`;
   cell.innerHTML=`<span class=ok>✓ accepted as <code>${esc(d.name)}</code></span>${flipped}`;
  }).catch(e=>{cell.innerHTML=`<span class=bad>✗ ${esc(e)}</span>`;});
}
function clipReject(n){
 const st=clipState[n];if(!st)return;
 fetch('/api/clip-reject',{method:'POST',headers:{'content-type':'application/json'},
   body:JSON.stringify({staged_id:st.staged_id})}).catch(()=>{});
 delete clipState[n];
 document.getElementById('clipact-'+n).innerHTML=`<button class=refresh onclick="clipExec('${n}')">▶ Execute</button>`;
}

// ---- Workflow I/O: a running log of per-run pipeline traces, tailed by polling -
let wfTimer=null,wfMaxId=0,wfPaused=false;
function wfMs(ms){if(ms==null)return '';return ms>=1000?(ms/1000).toFixed(1)+'s':ms+'ms';}
function wfEng(e){return `<span class="pill ${e==='f5'?'clip':'parler'}">${esc(e||'?')}</span>`;}
function wfEntry(t){
 const fail=t.failed_stage&&t.status!=='success';
 const time=t.started?new Date(t.started).toLocaleTimeString():'';
 const dev=t.device?`<span title="${esc(t.device_raw||'')}">📟 ${esc(t.device)}</span>`:'';
 const lat=t.wall_ms!=null?`⏱ ${wfMs(t.wall_ms)}`+(t.llm_ms?` · LLM ${wfMs(t.llm_ms)}`:''):'';
 const status=fail?`<span class=bad>✕ failed at ${esc(t.failed_stage)}</span>`
   :t.status==='running'?`<span class=warn>● running</span>`:`<span class=ok>✓</span>`;
 let rows=`<div class=wfrow><span class=k>🎤 heard</span><span class=v>${t.heard?esc(t.heard):'<i style=color:#666>—</i>'}</span></div>`;
 if(t.mood)rows+=`<div class=wfrow><span class=k>🧠 mood</span><span class=v>${esc(t.mood)}`+
   `${t.response_type?' · '+esc(t.response_type):''}${t.response_register?' · '+esc(t.response_register):''}</span></div>`;
 if(t.voices&&t.voices.length){
  const names=t.voices.map(v=>`${esc((v.character||'?').replace(/_/g,' '))} ${wfEng(v.engine)}`).join(' · ');
  rows+=`<div class=wfrow><span class=k>🎭 voices</span><span class=v>${names}</span></div>`;
  const said=t.voices.filter(v=>v.text).map(v=>`<div><b>${esc((v.character||'').replace(/_/g,' '))}:</b> ${esc(v.text)}</div>`).join('');
  if(said)rows+=`<div class=wfrow><span class=k>💬 said</span><span class="v said">${said}</span></div>`;
 }
 if(t.steps&&t.steps.length){
  const parts=t.steps.map(s=>`${esc(s.label)} ${wfMs(s.ms)}`).join(' · ');
  rows+=`<div class=wfrow><span class=k>⏱ steps</span><span class=v style="color:#aaa">${parts}</span></div>`;
 }
 if(t.synth&&t.synth.segments&&t.synth.segments.length){
  const segs=t.synth.segments.map((s,i)=>{
   const g=s.generate_ms!=null?`gen ${wfMs(s.generate_ms)}`:'';
   const f=s.filter_ms!=null?`+flt ${wfMs(s.filter_ms)}`:'';
   return `seg${i+1} ${esc(s.engine||'?')} ${g}${f?' '+f:''}${s.ok===false?' ✕':''}`;
  }).join(' · ');
  const cc=t.synth.concat_ms!=null?` · concat ${wfMs(t.synth.concat_ms)}`:'';
  const par=t.synth.parallel?' · ∥ parallel':'';
  rows+=`<div class=wfrow><span class=k>↳ tts</span><span class=v style="color:#9a9">${segs}${cc}${par}</span></div>`;
 }
 if(t.error)rows+=`<div class=wfrow><span class=k>⚠ error</span><span class="v bad">${esc(t.error)}</span></div>`;
 const o=t.output||{};
 if(o.url){const f=o.url.split('/').pop();
  rows+=`<div class=wfrow><span class=k>🔊 out</span><span class=v>${esc(f)} · ${o.count||0} seg${o.count===1?'':'s'}`+
   `${o.skipped?' · '+o.skipped+' skipped':''}</span></div>`;}
 return `<div class="wfentry${fail?' fail':''}" data-id="${t.id}">`+
   `<div class=wfhead><span class=id>#${t.id}</span> ${status} <span>${time}</span> ${dev} <span class=lat>${lat}</span></div>`+
   `${rows}</div>`;
}
async function loadWf(){
 if(wfTimer){clearInterval(wfTimer);wfTimer=null;}
 const d=await j('/api/workflow-trace');
 if(!d.ok)return `<p class=warn>${esc(d.reason)}</p>`;
 const runs=d.runs||[];
 wfMaxId=runs.reduce((m,r)=>Math.max(m,+r.id||0),0);
 const body=runs.length?runs.map(wfEntry).join(''):'<p>no runs yet</p>';
 wfTimer=setInterval(wfPoll,8000);
 return `<div class=wftools><span class="livedot" id=wfdot></span><span id=wflive>live · polling every 8s</span>`+
   `<button class=refresh style="padding:3px 10px" onclick="wfToggle(this)">⏸ pause</button>`+
   `<button class=refresh style="padding:3px 10px;background:#444;color:#eee" onclick="wfClear()">🗑 clear</button></div>`+
   `<div id=wflog>${body}</div>`;
}
function wfToggle(btn){
 wfPaused=!wfPaused;
 const dot=document.getElementById('wfdot'),live=document.getElementById('wflive');
 if(dot)dot.classList.toggle('paused',wfPaused);
 if(live)live.textContent=wfPaused?'paused':'live · polling every 8s';
 btn.textContent=wfPaused?'▶ resume':'⏸ pause';
}
function wfClear(){
 // Empties the on-screen log only. wfMaxId is kept, so polling resumes with the
 // NEXT run — cleared entries don't reappear. ↻ refresh reloads the full tail.
 const log=document.getElementById('wflog');
 if(log)log.innerHTML='<p style="color:#666">cleared — new runs will appear here (↻ refresh to reload history)</p>';
}
async function wfPoll(){
 if(wfPaused)return;
 const log=document.getElementById('wflog');
 if(!log){clearInterval(wfTimer);wfTimer=null;return;}  // tab DOM gone
 try{
  const d=await j('/api/workflow-trace');
  if(!d.ok)return;
  const fresh=(d.runs||[]).filter(r=>(+r.id||0)>wfMaxId).sort((a,b)=>(+a.id)-(+b.id));
  if(fresh.length){const ph=log.querySelector('p');if(ph)ph.remove();}  // drop "no runs yet"
  for(const t of fresh){
   wfMaxId=Math.max(wfMaxId,+t.id||0);
   const tmp=document.createElement('div');tmp.innerHTML=wfEntry(t);
   const el=tmp.firstChild;el.classList.add('new');
   log.insertBefore(el,log.firstChild);
  }
  while(log.children.length>80)log.removeChild(log.lastChild);  // cap scrollback
  const stamp=document.getElementById('updated-wf');
  if(stamp)stamp.textContent='updated '+new Date().toLocaleTimeString();
 }catch(e){/* transient poll error — keep tailing */}
}

// ---- tab registry: reorder these lines to reorder the tabs -------------------
const TABS=[
 {id:'health',  label:'Service health',  load:loadHealth},
 {id:'devices', label:'Devices',         load:loadDevices},
 {id:'config',  label:'Config',          load:loadConfig},
 {id:'voices',  label:'Voices',          load:loadVoices},
 {id:'clips',   label:'Clip Capture',    load:loadClips},
 {id:'wf',      label:'Workflow I/O',    load:loadWf},
];

const loaded=new Set();
function show(id){
 document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('active',b.dataset.id===id));
 document.querySelectorAll('.panel').forEach(p=>p.classList.toggle('active',p.id==='panel-'+id));
 location.hash=id;
 if(!loaded.has(id))refresh(id);
}
async function refresh(id){
 const tab=TABS.find(t=>t.id===id);const body=document.getElementById('body-'+id);
 const stamp=document.getElementById('updated-'+id);
 body.innerHTML='loading…';
 try{
  body.innerHTML=await tab.load();loaded.add(id);
  stamp.textContent='updated '+new Date().toLocaleTimeString();
 }
 catch(e){body.innerHTML=`<p class=bad>error: ${e}</p>`;stamp.textContent='';}
}
function build(){
 const nav=document.getElementById('tabs'),panels=document.getElementById('panels');
 TABS.forEach(t=>{
  const b=document.createElement('button');b.textContent=t.label;b.dataset.id=t.id;
  b.onclick=()=>show(t.id);nav.appendChild(b);
  panels.insertAdjacentHTML('beforeend',
   `<div class=panel id=panel-${t.id}><div class=panelhead><h2>${t.label}</h2>`+
   `<button class=refresh onclick="refresh('${t.id}')">↻ refresh</button>`+
   `<span class=updated id=updated-${t.id}></span></div>`+
   `<div id=body-${t.id}>loading…</div></div>`);
 });
 const start=(location.hash||'').slice(1);
 show(TABS.some(t=>t.id===start)?start:TABS[0].id);
}
build();
</script></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def index():
    return PAGE
