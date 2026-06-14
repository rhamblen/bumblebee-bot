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
  4. Workflow I/O     — last N n8n executions (input -> output) via the n8n REST API.

Not built yet: client/wake-word panel, bumblebee-scoped container status, a live
config store for the tunable values (apply without a recreate), and brain config
(voice-count/weighting/persona/model->role) on the Voices tab.
"""

import os
import re
import json
import asyncio
import logging

import httpx
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(title="Bumblebee Admin Console")

ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://bumblebee-orchestrator:5005")

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


async def workflow_runs(limit: int = 10) -> dict:
    """Last N n8n executions with input/output, via the n8n REST API."""
    if not (N8N_API_URL and N8N_API_KEY):
        return {"ok": False, "reason": "N8N_API_URL / N8N_API_KEY not configured"}
    headers = {"X-N8N-API-KEY": N8N_API_KEY, "accept": "application/json"}
    params = {"limit": limit, "includeData": "true"}
    if N8N_WORKFLOW_ID:
        params["workflowId"] = N8N_WORKFLOW_ID
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{N8N_API_URL.rstrip('/')}/api/v1/executions",
                                  headers=headers, params=params)
            r.raise_for_status()
            return {"ok": True, "data": r.json().get("data", [])}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "reason": f"{e} (macvlan? see N8N_API_URL note in server.py)"}


# --------------------------------------------------------------------------- API

@app.get("/api/health")
async def api_health():
    return await service_health()


@app.get("/api/voices")
async def api_voices():
    return await voices()


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


@app.get("/api/workflow-runs")
async def api_workflow_runs(limit: int = 10):
    return await workflow_runs(limit)


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
 pre{white-space:pre-wrap;word-break:break-word;margin:0;font-size:12px}
 button.refresh{background:#ffcc00;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;font-weight:600}
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
 const d=await j('/api/voices');const c=d.characters||[];
 const clip=c.filter(x=>x.clip_on_disk).length;
 return `<p>${d.total} voices · <span class=ok>${clip} with clip (F5)</span> · ${c.length-clip} Parler-only</p>`+
  '<table><tr><th>name</th><th>engine</th><th>response_type</th><th>register</th></tr>'+
  c.map(x=>`<tr><td>${x.name.replace(/_/g,' ')}</td>`+
   `<td><span class="pill ${x.clip_on_disk?'clip':'parler'}">${x.clip_on_disk?'F5 clip':'Parler'}</span></td>`+
   `<td>${(x.response_type||[]).join(', ')}</td><td>${(x.response_register||[]).join(', ')}</td></tr>`).join('')+'</table>';
}
async function loadWf(){
 const d=await j('/api/workflow-runs');
 if(!d.ok)return `<p class=warn>${d.reason}</p>`;
 const rows=(d.data||[]).map(e=>`<tr><td>${e.id}</td><td>${e.startedAt||''}</td><td class="${e.finished?'ok':'warn'}">${e.status||(e.finished?'ok':'running')}</td></tr>`).join('');
 return rows?'<table><tr><th>id</th><th>started</th><th>status</th></tr>'+rows+'</table>':'<p>no runs</p>';
}

// ---- tab registry: reorder these lines to reorder the tabs -------------------
const TABS=[
 {id:'health', label:'Service health',  load:loadHealth},
 {id:'config', label:'Config',          load:loadConfig},
 {id:'voices', label:'Voices',          load:loadVoices},
 {id:'wf',     label:'Workflow I/O',    load:loadWf},
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
