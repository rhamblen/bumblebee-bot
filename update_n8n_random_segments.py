"""Restructure the bumblebee n8n workflow for mood-matched RANDOM multi-segment playback.

New flow:
  Webhook -> Read Session
    -> Build Ollama Request   (Code: build MOOD-CLASSIFY prompt)
    -> Ask Ollama             (http: classify mood)
    -> Parse Ollama Response  (Code: filter voices by mood, roll K=1-3, random-pick K,
                               build COMPOSE prompt for the picked characters)
    -> Ask Ollama Compose     (http: NEW - write an in-character snippet per picked voice)
    -> Parse Segments         (Code: NEW - attach reference_clip/voice_description by name)
    -> Write Session
    -> Call Orchestrator       (http: POST /speak-multi with the segments array)
    -> Play on Sonos -> Respond

The workflow object is MUTATED in place (not rebuilt) so credentials, webhookId and
typeVersions on existing nodes are preserved. Run only AFTER the orchestrator has been
rebuilt with the /speak-multi endpoint.
"""
import copy
import json
import os
import sys
import urllib.request
import urllib.error
import uuid

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
DESCRIPTOR_FILE = os.path.join(HERE, "character_descriptor.json")
WF_URL = "http://192.168.1.47:5678/api/v1/workflows/ykVWvfFBHQpaC2h3"
MODEL = "llama3.1:latest"

# ── Embedded character pool (minimal fields for mood filtering + clip lookup) ──
with open(DESCRIPTOR_FILE, encoding="utf-8") as f:
    desc = json.load(f)

CHARACTERS = [
    {
        "name": c["name"],
        "response_type": c["response_type"],
        "response_register": c["response_register"],
        "reference_clip": c["reference_clip"],
        "voice_description": c["voice_description"],
        "clip_on_disk": bool(c.get("clip_on_disk")),
    }
    for c in desc["characters"]
]
CHARS_JSON = json.dumps(CHARACTERS, ensure_ascii=False)

# ── Node code ─────────────────────────────────────────────────────────────────

BUILD_MOOD_JS = r"""const phrase = $('Webhook').item.json.body.text || $('Webhook').item.json.body.phrase;

const systemPrompt = `You are the mood interpreter for Bumblebee. Read the human's phrase and classify it.
Return ONLY valid JSON, no markdown, no explanation:
{
  "mood": "<one or two words describing how they feel>",
  "response_type": "<exactly one of: motivate, console, celebrate, challenge, inform, validate, amuse>",
  "response_register": "<exactly one of: serious, dramatic, deadpan, light>"
}`;

const ollamaRequest = {
  model: "__MODEL__",
  messages: [
    { role: "system", content: systemPrompt },
    { role: "user", content: phrase }
  ],
  stream: false,
  format: "json"
};

return { json: { request_body: JSON.stringify(ollamaRequest) } };
""".replace("__MODEL__", MODEL)

PICK_JS = r"""// Parse the mood classification, then RANDOMLY pick 1-3 mood-matching characters
// and build the compose prompt for them.
const resp = $input.item.json;
const content = resp.message?.content || '';
let mood;
try { mood = JSON.parse(content.trim()); }
catch (e) { mood = { mood: "unknown", response_type: "motivate", response_register: "serious" }; }

const phrase = $('Webhook').item.json.body.text || $('Webhook').item.json.body.phrase;

const CHARACTERS = __CHARS__;

const rtype = (mood.response_type || '').toLowerCase();
const rreg  = (mood.response_register || '').toLowerCase();

// Mood match: characters whose response_type includes the detected type...
let pool = CHARACTERS.filter(c => c.response_type.map(x => x.toLowerCase()).includes(rtype));
// ...tightened by register when that still leaves at least one option.
const byReg = pool.filter(c => c.response_register.map(x => x.toLowerCase()).includes(rreg));
if (byReg.length >= 1) pool = byReg;
// Fallback: nothing matched -> everyone is fair game.
if (pool.length === 0) pool = CHARACTERS.slice();

// Fisher-Yates shuffle.
for (let i = pool.length - 1; i > 0; i--) {
  const j = Math.floor(Math.random() * (i + 1));
  [pool[i], pool[j]] = [pool[j], pool[i]];
}

// Roll 1-3 with a slight bias toward 2 and 3, capped at how many
// mood-matching voices we actually have (weights renormalise when capped).
const maxK = Math.min(3, pool.length);
const weights = [0.27, 0.40, 0.33].slice(0, maxK);
const total = weights.reduce((a, b) => a + b, 0);
let roll = Math.random() * total, k = maxK;
for (let i = 0; i < weights.length; i++) { if (roll < weights[i]) { k = i + 1; break; } roll -= weights[i]; }
const picked = pool.slice(0, k);

const charBlock = picked.map((c, i) =>
  `${i + 1}. ${c.name} | register: ${c.response_register.join(',')} | style: ${c.voice_description}`
).join('\n');

const systemPrompt = `You are the Response Composer for Bumblebee, a robot who communicates ONLY by replaying intercepted radio and TV broadcasts from the 1940s-1970s.
The human feels "${mood.mood}" (response type: ${rtype}, register: ${rreg}).
For EACH character below, write what that character would say - 40 to 70 words, period-authentic, in their own voice, as if caught mid-broadcast. It is NOT a direct reply; it is a found recording that loosely fits the mood.

CHARACTERS:
${charBlock}

Return ONLY valid JSON, no markdown:
{ "segments": [ { "character": "<exact name from the list>", "message": "<what they say>" } ] }
Write exactly one segment per character, in the order listed. No emojis.`;

const ollamaRequest = {
  model: "__MODEL__",
  messages: [
    { role: "system", content: systemPrompt },
    { role: "user", content: phrase }
  ],
  stream: false,
  format: "json"
};

return { json: {
  request_body: JSON.stringify(ollamaRequest),
  picked: picked,
  mood: mood.mood,
  response_type: rtype,
  response_register: rreg
} };
""".replace("__CHARS__", CHARS_JSON).replace("__MODEL__", MODEL)

PARSE_SEGMENTS_JS = r"""// Turn the composed messages into orchestrator segments, attaching the clip/style
// for each picked character by name (never trust the LLM to echo paths).
const resp = $input.item.json;
const content = resp.message?.content || '';
let parsed;
try { parsed = JSON.parse(content.trim()); } catch (e) { parsed = { segments: [] }; }

const picked = $('Parse Ollama Response').item.json.picked || [];
const mood = $('Parse Ollama Response').item.json.mood;

const byName = {};
for (const c of picked) byName[c.name] = c;

let segments = (parsed.segments || []).map(s => {
  const c = byName[s.character] || {};
  return {
    character: s.character,
    text: s.message,
    tts_engine: c.clip_on_disk ? 'f5' : 'parler',
    reference_clip: c.reference_clip || null,
    voice_description: c.voice_description || 'A clear expressive voice.',
    exaggeration: 0.7
  };
}).filter(s => s.text && String(s.text).trim());

// Safety net: if the compose step returned nothing usable, speak straight from the
// picked characters so the pipeline never goes silent.
if (segments.length === 0 && picked.length) {
  segments = picked.map(c => ({
    character: c.name,
    text: `This is a message finding its way through the static. Keep going - you are not out there alone.`,
    tts_engine: c.clip_on_disk ? 'f5' : 'parler',
    reference_clip: c.reference_clip || null,
    voice_description: c.voice_description || 'A clear expressive voice.',
    exaggeration: 0.7
  }));
}

const characters = segments.map(s => s.character).join(', ');
const response = segments.map(s => s.text).join(' / ');

return { json: { segments, mood, characters, count: segments.length, response } };
"""

# ── Get API key ───────────────────────────────────────────────────────────────
api_key = os.environ.get("N8N_API_KEY")
if not api_key:
    env_file = os.path.join(HERE, ".env")
    if os.path.exists(env_file):
        for line in open(env_file, encoding="utf-8"):
            if line.startswith("N8N_API_KEY="):
                api_key = line.strip().split("=", 1)[1]
                break
if not api_key:
    raise SystemExit("N8N_API_KEY not set — add it to .env or export it")

# ── Fetch workflow ────────────────────────────────────────────────────────────
req = urllib.request.Request(WF_URL, headers={"X-N8N-API-KEY": api_key})
with urllib.request.urlopen(req) as r:
    wf = json.loads(r.read())

print(f"Fetched: {wf['name']} ({len(wf['nodes'])} nodes)")
nodes = {n["name"]: n for n in wf["nodes"]}


def need(name):
    if name not in nodes:
        raise SystemExit(f"Expected node '{name}' not found — aborting (workflow drifted).")
    return nodes[name]


# ── Mutate existing nodes ─────────────────────────────────────────────────────
need("Build Ollama Request")["parameters"]["jsCode"] = BUILD_MOOD_JS
print("  Updated: Build Ollama Request (mood classify)")

need("Parse Ollama Response")["parameters"]["jsCode"] = PICK_JS
print("  Updated: Parse Ollama Response (filter + random pick + compose prompt)")

# Call Orchestrator -> /speak-multi with a raw JSON segments body.
call = need("Call Orchestrator")
call["parameters"] = {
    "method": "POST",
    "url": "http://bumblebee-orchestrator:5005/speak-multi",
    "sendBody": True,
    "contentType": "raw",
    "rawContentType": "application/json",
    "body": "={{ JSON.stringify({ segments: $json.segments }) }}",
    "options": {},
}
print("  Updated: Call Orchestrator (-> /speak-multi)")

# Write Session: pull input from text/phrase and response from the segment summary.
write = need("Write Session")
write["parameters"]["value"] = (
    "={{  JSON.stringify(([...(JSON.parse($('Read Session').item.json.session_history || '[]')), "
    "{ input: ($('Webhook').item.json.body.text || $('Webhook').item.json.body.phrase), "
    "mood: $json.mood, response: $json.response }]).slice(-5)) }} }}"
)
print("  Updated: Write Session (response summary)")

# Respond: report what actually played.
resp_node = need("Respond")
resp_node["parameters"]["responseBody"] = (
    "={{ JSON.stringify({ status: 'playing', "
    "count: $('Parse Segments').item.json.count, "
    "characters: $('Parse Segments').item.json.characters, "
    "mood: $('Parse Segments').item.json.mood }) }}"
)
print("  Updated: Respond")

# ── Add the two new nodes (deep-copied to inherit typeVersion/credentials shape) ──
ask = need("Ask Ollama")
ask_compose = copy.deepcopy(ask)
ask_compose["name"] = "Ask Ollama Compose"
ask_compose["id"] = str(uuid.uuid4())
ask_compose["position"] = [1180, 460]
# body already = '={{ $json.request_body }}' — same as mood call

build = need("Build Ollama Request")  # a code node, copy for typeVersion
parse_segments = copy.deepcopy(build)
parse_segments["name"] = "Parse Segments"
parse_segments["id"] = str(uuid.uuid4())
parse_segments["position"] = [1360, 460]
parse_segments["parameters"] = {"jsCode": PARSE_SEGMENTS_JS}

wf["nodes"].append(ask_compose)
wf["nodes"].append(parse_segments)
print("  Added: Ask Ollama Compose, Parse Segments")

# ── Rewire connections ────────────────────────────────────────────────────────
conns = wf["connections"]
def link(src, dst):
    conns[src] = {"main": [[{"node": dst, "type": "main", "index": 0}]]}

link("Parse Ollama Response", "Ask Ollama Compose")  # was -> Write Session
link("Ask Ollama Compose", "Parse Segments")
link("Parse Segments", "Write Session")
print("  Rewired: Parse -> Ask Ollama Compose -> Parse Segments -> Write Session")

# ── Push ──────────────────────────────────────────────────────────────────────
put_body = {
    "name": wf["name"],
    "nodes": wf["nodes"],
    "connections": wf["connections"],
    "settings": {"executionOrder": wf["settings"].get("executionOrder", "v1")},
    "staticData": wf.get("staticData"),
}
body = json.dumps(put_body).encode()
put_req = urllib.request.Request(
    WF_URL, data=body,
    headers={"X-N8N-API-KEY": api_key, "Content-Type": "application/json"},
    method="PUT",
)
try:
    with urllib.request.urlopen(put_req) as r:
        result = json.loads(r.read())
    print(f"\nSaved. Active: {result.get('active')}  Nodes: {len(result.get('nodes', []))}")
    print("Random mood-matched multi-segment flow is live.")
except urllib.error.HTTPError as e:
    print(f"ERROR {e.code}: {e.read().decode()}")
