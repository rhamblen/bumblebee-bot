"""Update n8n bumblebee workflow to use full 36-character descriptor.

Changes:
  - Build Ollama Request: injects all 36 characters (F5 clip-based + Parler style-based)
  - Parse Ollama Response: handles both reference_clip (F5) and voice_description (Parler)
  - Call Orchestrator: maps voice_description from correct field
"""
import json, os, urllib.request, sqlite3, sys

sys.stdout.reconfigure(encoding="utf-8")

DESCRIPTOR_FILE = r"D:\backup\richard\Documents\bumblebee bot\data\character_descriptor.json"

# ── Load character descriptor ────────────────────────────────────────────────

with open(DESCRIPTOR_FILE, encoding="utf-8") as f:
    desc = json.load(f)

# Build compact character table for the prompt
# Every character includes both reference_clip path and voice_description style.
# The orchestrator decides which engine to use at runtime by checking if the file exists.
lines = []
for c in desc["characters"]:
    rt = ",".join(c["response_type"])
    rr = ",".join(c["response_register"])
    vd = c["voice_description"][:100]
    lines.append(f'{c["name"]} | {rt} | {rr} | {c["reference_clip"]} | {vd}')

char_table = "\n".join(lines)

# ── Node code ────────────────────────────────────────────────────────────────

NEW_BUILD_JSCODE = r"""const phrase = $('Webhook').item.json.body.text || $('Webhook').item.json.body.phrase;

const characterList = `""" + char_table + r"""`;

const systemPrompt = `You are the Response Composer for Bumblebee, a Transformers robot who communicates only by replaying intercepted radio and TV broadcasts from the 1940s–1970s.

Given a human's phrase, you must:
1. Detect their mood and what kind of response would help them
2. Pick the single best character from the list below whose response_type and response_register best fit the mood
3. Write what that character would say — 40-70 words, period-authentic, as if caught mid-broadcast. NOT a direct reply. A found recording.

CHARACTER LIST (format: Name | response_types | response_registers | reference_clip_path | voice_style):
${characterList}

Return ONLY valid JSON, no markdown, no explanation:
{
  "mood": "...",
  "response_type": "...",
  "response_register": "...",
  "character": "...",
  "reference_clip": "<exact reference_clip_path from the list above>",
  "voice_description": "<exact voice_style from the list above>",
  "message": "..."
}

The message must sound like a genuine period broadcast intercept — not a chatbot reply. No emojis.`;

const ollamaRequest = {
    model: "llama3.1:latest",
    messages: [
      { role: "system", content: systemPrompt },
      { role: "user", content: phrase }
    ],
    stream: false,
    format: "json"
};

return { json: { request_body: JSON.stringify(ollamaRequest) } };
"""

NEW_PARSE_JSCODE = r"""const response = $input.item.json;
const content = response.message?.content || '';

let ttsData;
try {
    ttsData = JSON.parse(content.trim());
} catch(e) {
    // Fallback to Churchill — orchestrator will auto-select F5 if clip exists
    ttsData = {
        mood: "unknown",
        response_type: "motivate",
        response_register: "serious",
        character: "Winston_Churchill",
        reference_clip: "/media/references/Winston_Churchill/winston_speech_ref_01.wav",
        voice_description: "Commanding baritone with clipped aristocratic vowels. Recording style: 1940s BBC wartime broadcast, slight crackle.",
        message: "We shall not flag or fail. Go forward with everything you have — the finest hour is yet to come."
    };
}

// Ensure both fields are always present
if (!ttsData.reference_clip) ttsData.reference_clip = null;
if (!ttsData.voice_description) ttsData.voice_description = "A clear expressive voice.";

return { json: ttsData };
"""

# ── Get API key ──────────────────────────────────────────────────────────────

api_key = os.environ.get("N8N_API_KEY")
if not api_key:
    env_file = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    if os.path.exists(env_file):
        for line in open(env_file):
            if line.startswith("N8N_API_KEY="):
                api_key = line.strip().split("=", 1)[1]
                break
if not api_key:
    raise SystemExit("N8N_API_KEY not set — add it to .env or export it")

# ── Fetch workflow ───────────────────────────────────────────────────────────

req = urllib.request.Request(
    "http://192.168.1.47:5678/api/v1/workflows/ykVWvfFBHQpaC2h3",
    headers={"X-N8N-API-KEY": api_key}
)
with urllib.request.urlopen(req) as r:
    workflow = json.loads(r.read())

print(f"Fetched: {workflow['name']} ({len(workflow['nodes'])} nodes)")

# ── Apply node updates ───────────────────────────────────────────────────────

for node in workflow["nodes"]:
    if node["name"] == "Build Ollama Request":
        node["parameters"]["jsCode"] = NEW_BUILD_JSCODE
        print("  Updated: Build Ollama Request")

    elif node["name"] == "Parse Ollama Response":
        node["parameters"]["jsCode"] = NEW_PARSE_JSCODE
        print("  Updated: Parse Ollama Response")

    elif node["name"] == "Call Orchestrator":
        # Fix voice_description mapping: was $json.voice_persona, now $json.voice_description
        params = node["parameters"].get("bodyParameters", {}).get("parameters", [])
        for p in params:
            if p.get("name") == "voice_description":
                p["value"] = "={{ $json.voice_description }}"
                print("  Updated: Call Orchestrator voice_description field")

# ── Push updated workflow ────────────────────────────────────────────────────

put_body = {
    "name": workflow["name"],
    "nodes": workflow["nodes"],
    "connections": workflow["connections"],
    "settings": {"executionOrder": workflow["settings"].get("executionOrder", "v1")},
    "staticData": workflow.get("staticData"),
}

body = json.dumps(put_body).encode()
put_req = urllib.request.Request(
    "http://192.168.1.47:5678/api/v1/workflows/ykVWvfFBHQpaC2h3",
    data=body,
    headers={"X-N8N-API-KEY": api_key, "Content-Type": "application/json"},
    method="PUT"
)

try:
    with urllib.request.urlopen(put_req) as r:
        result = json.loads(r.read())
    print(f"Saved. Active: {result.get('active')}")
    print(f"\nCharacters: {desc['total']} total ({desc.get('clips_on_disk',0)} with clips → F5, {desc.get('parler_only',0)} style-only → Parler)")
except urllib.error.HTTPError as e:
    print(f"ERROR {e.code}: {e.read().decode()}")
