"""Update bumblebee n8n workflow: switch to Churchill, fix clip path, fix field name."""
import json, sys, subprocess

import os
# API key retrieved from n8n SQLite (user_api_keys table, oldest persistent key)
api_key = os.environ.get("N8N_API_KEY")

# Fetch current workflow via n8n API
import urllib.request
req = urllib.request.Request(
    "http://192.168.1.47:5678/api/v1/workflows/ykVWvfFBHQpaC2h3",
    headers={"X-N8N-API-KEY": api_key}
)
with urllib.request.urlopen(req) as r:
    workflow = json.loads(r.read())

print(f"Fetched workflow: {workflow['name']} ({len(workflow['nodes'])} nodes)")

NEW_BUILD_JSCODE = r"""const phrase = $('Webhook').item.json.body.text || $('Webhook').item.json.body.phrase;

const systemPrompt = `You are a mood-detection and creative writing system for Bumblebee, a Transformers robot who communicates via generated speech that sounds like intercepted radio and TV broadcasts.

When given a phrase, you must:
1. Detect the emotional mood
2. Write a short, uplifting response (40-70 words, ~15-20 seconds when spoken) that fits the mood
3. Select the best voice persona and TTS engine from the options below

Always reply with ONLY valid JSON, no markdown, no explanation, no extra text.

Available personas (only use these exact reference_clip paths):
- "Winston Churchill": tts_engine "f5", reference_clip "/media/references/Winston_Churchill/winston_speech_ref_01.wav"

JSON schema to return:
{"mood": "...", "tts_engine": "f5", "voice_persona": "Winston Churchill", "reference_clip": "/media/references/Winston_Churchill/winston_speech_ref_01.wav", "message": "..."}

Keep the message in character — like a defiant, inspiring wartime broadcast intercepted from 1940s BBC radio. No emojis.`;

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

// Ollama /api/chat returns response.message.content
const content = response.message?.content || '';

let ttsData;
try {
    ttsData = JSON.parse(content.trim());
} catch(e) {
    ttsData = {
        mood: "unknown",
        tts_engine: "f5",
        voice_persona: "Winston Churchill",
        reference_clip: "/media/references/Winston_Churchill/winston_speech_ref_01.wav",
        message: "We shall not flag or fail. Go forward with everything you have — the finest hour is yet to come."
    };
}

return { json: ttsData };
"""

# Apply updates
for node in workflow['nodes']:
    if node['name'] == 'Build Ollama Request':
        node['parameters']['jsCode'] = NEW_BUILD_JSCODE
        print("  Updated: Build Ollama Request")
    elif node['name'] == 'Parse Ollama Response':
        node['parameters']['jsCode'] = NEW_PARSE_JSCODE
        print("  Updated: Parse Ollama Response")

# PUT updated workflow — n8n API only accepts these fields
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
with urllib.request.urlopen(put_req) as r:
    result = json.loads(r.read())

print(f"Saved. Workflow active: {result.get('active')}")
