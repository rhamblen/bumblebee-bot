"""Bumblebee response explorer.

Type a phrase → see the candidate responses Bumblebee could play.

Flow (mirrors the live pipeline, minus TTS/playback):
  1. C1 (llama3.1)  infers mood metadata from the phrase
  2. C2 shortlists characters whose response_type + response_register match
  3. C3 (mistral)   writes what each shortlisted character would "say"
                    (an intercepted period broadcast, not a direct reply)

Output: the mood read, then a numbered list of possible responses with the
voice that would speak each one and whether it's an F5 clone or a Parler fallback.

Usage:
    python test_responses.py "I've had a long day and I can't keep going"
    python test_responses.py            # prompts for input, loops
"""
import json, sys, urllib.request

OLLAMA_URL = "http://192.168.1.32:11434/api/chat"
C1_MODEL   = "llama3.1:latest"
C3_MODEL   = "mistral:latest"
DESCRIPTOR = r"D:\backup\richard\Documents\bumblebee bot\character_descriptor.json"
N_RESPONSES = 5   # how many candidate responses to draft

# ── C1: mood inference ────────────────────────────────────────────────────────

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
- humour_present: JSON boolean true or false (NOT the string "true"/"false" — no quotes)
- sarcasm_detected: JSON boolean true or false (NOT the string "true"/"false" — no quotes)
- response_type: motivate / console / celebrate / challenge / inform / amuse / validate
- response_register: serious / dramatic / deadpan / light
- confidence: JSON number between 0.0 and 1.0 (no quotes)
- mood_trajectory: improving / worsening / stable / new_session
- session_turn: always the integer 1 (no quotes)"""


# Fields that must be true JSON booleans, and the values we accept as truthy.
_BOOL_FIELDS = ("humour_present", "sarcasm_detected")


def normalize_mood(mood):
    """Coerce types that Ollama's JSON mode gets wrong (string booleans,
    string numbers, drifting session_turn)."""
    for k in _BOOL_FIELDS:
        v = mood.get(k)
        if isinstance(v, str):
            mood[k] = v.strip().lower() in ("true", "yes", "1")
    try:
        mood["confidence"] = float(mood.get("confidence", 0.0))
    except (TypeError, ValueError):
        mood["confidence"] = 0.0
    mood["session_turn"] = 1  # always 1 until session history is wired in
    return mood


def call_ollama(model, system, user, as_json=True):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
    }
    if as_json:
        body["format"] = "json"
    req = urllib.request.Request(
        OLLAMA_URL,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        resp = json.loads(r.read())
    content = resp["message"]["content"].strip()
    return json.loads(content) if as_json else content


# ── C2: character shortlist ───────────────────────────────────────────────────

def shortlist_characters(mood, characters):
    """Characters whose response_type AND response_register both contain the
    inferred values. Clips-on-disk (F5 clones) are ranked first."""
    rt = mood.get("response_type")
    rr = mood.get("response_register")

    matches = [
        c for c in characters
        if rt in c.get("response_type", []) and rr in c.get("response_register", [])
    ]
    # F5 clones (real clip) before Parler-only fallbacks, then alphabetical
    matches.sort(key=lambda c: (not c.get("clip_on_disk", False), c["name"]))
    return matches


# ── C3: compose what each character would say ─────────────────────────────────

def compose_message(character, mood, phrase):
    name = character["name"].replace("_", " ")
    system = f"""You write a single line of intercepted broadcast audio for Bumblebee, a Transformers robot who cannot speak and instead replays old radio/TV recordings (1940s-1970s).

Write what one such recording sounds like — a fragment that {name} might genuinely have said on air in that era. It is NOT a reply to anyone. The human will never be addressed or referenced.

Voice to imitate: {name} — {character.get('voice_archetype', '')}
The human privately feels: {mood['primary_mood']} ({mood['sentiment']}, {mood['energy_level']} energy), about {mood['topic']}.
The recording should land as: {mood['response_type']} in a {mood['response_register']} register.

HARD RULES — breaking any one makes the output unusable:
1. Output the spoken words ONLY. No name, no attribution, no "from {name}", no quotation marks, no stage directions, no line breaks — one flowing block of prose.
2. NEVER address the listener with "you", "dear friend", "my friend", "dear one", "dear listener" or any second-person greeting. This is overheard, not spoken to anyone.
3. NEVER echo or paraphrase the human's words or situation. Do not mention their day, their tiredness, their specific circumstance. Speak about something of your own.
4. Do NOT begin with "I". Start mid-thought, as if caught mid-broadcast.
5. 40-70 words. Period-authentic language and rhythm. No modern slang, no emojis.
6. Honour the register: {mood['response_register']}.

Write the fragment now."""

    text = call_ollama(
        C3_MODEL, system,
        "Write the intercepted broadcast fragment. Remember: no greeting, no 'you', "
        "no reference to any listener or their situation. One block of prose, 40-70 words.",
        as_json=False,
    )
    # Strip wrapping quotes the model sometimes adds, and collapse whitespace.
    text = " ".join(text.split())
    if len(text) >= 2 and text[0] in "\"'“‘" and text[-1] in "\"'”’":
        text = text[1:-1].strip()
    return text


# ── Driver ────────────────────────────────────────────────────────────────────

def run(phrase, characters):
    print(f"\n{'='*70}\nINPUT: {phrase!r}\n{'='*70}")

    print("\n--- C1: mood metadata ---")
    mood = normalize_mood(call_ollama(C1_MODEL, C1_SYSTEM, phrase))
    print(json.dumps(mood, indent=2))
    print(f"\n>> needs: {mood['response_type'].upper()} "
          f"in a {mood['response_register'].upper()} register "
          f"(confidence {mood['confidence']})")

    shortlist = shortlist_characters(mood, characters)
    print(f"\n--- C2: {len(shortlist)} characters match "
          f"[{mood['response_type']} + {mood['response_register']}] ---")
    for c in shortlist:
        tag = "F5 clone" if c.get("clip_on_disk") else "Parler"
        print(f"    - {c['name']:<22} ({tag})")

    candidates = shortlist[:N_RESPONSES]
    print(f"\n--- C3: possible responses (top {len(candidates)}) ---")
    for i, c in enumerate(candidates, 1):
        engine = "F5 clone" if c.get("clip_on_disk") else "Parler fallback"
        text = compose_message(c, mood, phrase)
        print(f"\n  [{i}] {c['name'].replace('_', ' ')}  ({engine})")
        print(f"      {text}")
    print()


if __name__ == "__main__":
    with open(DESCRIPTOR, encoding="utf-8") as f:
        characters = json.load(f)["characters"]

    if len(sys.argv) > 1:
        run(" ".join(sys.argv[1:]), characters)
    else:
        while True:
            try:
                phrase = input("\nEnter phrase (Ctrl-C to quit): ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if phrase:
                run(phrase, characters)
