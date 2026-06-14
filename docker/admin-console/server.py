"""Bumblebee Admin Console — P1 scaffold.

A read-mostly operator pane for the Bumblebee stack. It never holds its own copy
of state: it reads the SAME sources the live containers read (the orchestrator's
voice descriptor, the compose file, the .env) and reports on them. Writing config
into running containers is deliberately OUT of scope for P1 — this surface explains
what's wrong; you (or Claude via MCP) fix it the normal build way.

P1 panels:
  1. Service health   — pings each bumblebee service /health on bumblebee_default.
  2. Voice/character  — live table from the orchestrator GET /voices (clip vs Parler).
  3. Config validator — parses docker-compose.yml + .env, flags ${VARS} that are
                        referenced but undefined, and missing .env entirely.
  4. Workflow I/O     — last N n8n executions (input -> output) via the n8n REST API.

P2 (not built yet): client/wake-word panel, bumblebee-scoped container status,
optional .env / compose *generation* (still built by the user, never hot-pushed).
"""

import os
import re
import json
import asyncio
import logging

import httpx
import yaml
from fastapi import FastAPI
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

    # Which referenced vars carry an inline default (${VAR:-...})?
    defaulted = set(re.findall(r"\$\{([A-Z0-9_]+):-", raw))
    referenced = set(_VAR_RE.findall(raw))

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
async function loadConfig(){
 const d=await j('/api/config');
 let h=`<p>services: ${(d.services||[]).join(', ')||'—'}</p>`;
 h+=d.findings.length?'<table><tr><th>level</th><th>finding</th></tr>'+
   d.findings.map(f=>`<tr><td class="${f.level==='error'?'bad':'warn'}">${f.level}</td><td>${f.msg}</td></tr>`).join('')+'</table>'
   :'<p class=ok>● no config issues</p>';
 return h;
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
