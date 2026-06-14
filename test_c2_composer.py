"""C2+C3 Response Composer test.

Given a phrase:
  1. C1 (llama3.1) infers mood metadata
  2. Shortlist matching characters from character_voices.json
  3. C3 (mistral) writes what each character would say — period-authentic broadcast style
  4. Shows which clip would be used in the live pipeline

Usage: python test_c2_composer.py "your phrase here"
"""
import json, sys, urllib.request

OLLAMA_URL = "http://192.168.1.32:11434/api/chat"
C1_MODEL = "llama3.1:latest"
C3_MODEL = "mistral:latest"
CHARACTER_VOICES = r"D:\backup\richard\Documents\bumblebee bot\character_voices.json"

# ── C1 prompt ────────────────────────────────────────────────────────────────

C1_SYSTEM = """You are C1, the mood inference engine for Bumblebee — a Transformers robot who communicates by replaying intercepted radio and TV broadcasts.

Read the human's phrase and output a structured JSON object describing their emotional state and what kind of response would suit them.

Output ONLY valid JSON. No markdown, no explanation, no extra text.

Fields:
- primary_mood: happy / sad / anxious / frustrated / proud / excited / tired / angry / reflective / grateful / bored / overwhelmed / hopeful / defeated / playful
- energy_level: low / medium / high
- intensity: mild / moderate / strong
- sentiment: positive / negative / neutral / mixed
- topic: work / relationships / love / loss / health / money / home / world_news / sport / entertainment / food / travel / philosophy / general
- audience: self / other / group
- directedness: inward / outward
- formality: casual / formal / slang
- urgency: low / medium / high
- humour_present: true / false
- sarcasm_detected: true / false
- response_type: motivate / console / celebrate / challenge / inform / amuse / validate
- response_register: serious / dramatic / deadpan / light
- confidence: float 0.0-1.0
- mood_trajectory: improving / worsening / stable / new_session
- session_turn: 1"""


def call_ollama(model, system, user, as_json=True):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "stream": False,
    }
    if as_json:
        body["format"] = "json"
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        resp = json.loads(r.read())
    content = resp["message"]["content"].strip()
    if as_json:
        return json.loads(content)
    return content


def shortlist_characters(mood, voices):
    """Return characters that match response_type + response_register, ordered by clip availability."""
    rt = mood.get("response_type")
    rr = mood.get("response_register")

    matches = []
    for v in voices:
        if "response_type" not in v or "response_register" not in v:
            continue
        rt_match = rt in v["response_type"]
        rr_match = rr in v["response_register"]
        if not (rt_match and rr_match):
            continue
        matches.append({**v, "_rt_match": rt_match, "_rr_match": rr_match})

    matches.sort(key=lambda x: x["name"])
    return matches


def compose_message(character, mood, phrase):
    """Ask Mistral to write what this character would say."""
    system = f"""You are writing a short spoken response for Bumblebee, a Transformers robot.

Bumblebee cannot speak normally — he communicates only by replaying intercepted recordings from Earth's radio and TV broadcasts, primarily from the 1940s to 1970s. He has found a recording that feels relevant to the human's situation.

You are writing what that recording sounds like — as if it was genuinely said by {character['name'].replace('_', ' ')} during a real broadcast of the era.

Character: {character['name'].replace('_', ' ')}
Voice style: {character['voice_archetype']}
Example of their speech: "{character.get('sample_script', '')[:200]}"

The human's situation:
- Mood: {mood['primary_mood']} | Sentiment: {mood['sentiment']} | Energy: {mood['energy_level']}
- Topic: {mood['topic']} | Urgency: {mood['urgency']}
- What they need: {mood['response_type']} in a {mood['response_register']} register

Rules:
- Write the spoken text ONLY — no stage directions, no quotes, no attribution, no character name
- STRICT LENGTH: 40-70 words maximum. Count carefully. Stop before 70 words.
- Period-authentic language and rhythm for {character.get('era', '1940s-1970s')}
- Do NOT directly reference the human's exact words — this is an intercepted broadcast, not a reply
- Do NOT start with "I" — start mid-thought, as if caught mid-broadcast
- Do NOT use modern language, slang, or emojis
- Tone: {mood['response_register']} — this is critical, honour it throughout"""

    return call_ollama(C3_MODEL, system, f"Human said: {phrase!r}\nMood data: {json.dumps(mood)}", as_json=False)


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    phrase = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else input("Enter phrase: ")

    print(f"\nPhrase: {phrase!r}")
    print("\n--- C1: Mood inference ---")
    mood = call_ollama(C1_MODEL, C1_SYSTEM, phrase)
    print(json.dumps(mood, indent=2))
    print(f"\nresponse_type: {mood['response_type']}  |  register: {mood['response_register']}  |  confidence: {mood['confidence']}")

    with open(CHARACTER_VOICES, encoding="utf-8") as f:
        voices = json.load(f)["voices"]

    shortlist = shortlist_characters(mood, voices)

    print(f"\n--- C2: Character shortlist ({len(shortlist)} matches for {mood['response_type']} + {mood['response_register']}) ---")
    for i, c in enumerate(shortlist[:8]):
        print(f"  {i+1}. {c['name']}")

    candidates = shortlist[:3]

    print(f"\n--- C3: What they would say ---")
    for c in candidates:
        print(f"\n  [{c['name'].replace('_', ' ')}]")
        text = compose_message(c, mood, phrase)
        print(f"  {text}")
        print(f"  (clip: {c.get('clip_status', 'check disk at runtime')})")
