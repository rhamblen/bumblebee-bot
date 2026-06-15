# Architecture & Workflow

Three views: the **system architecture** (what runs where), the **request lifecycle** (what happens to one message), and the **multi-device topology** (how several ESP32 devices share the pipeline).

This page is the overview. The n8n workflow itself is broken into four stage pages, each covering the nodes, inputs, and outputs of that step:

1. [Mood Classification (C1)](Workflow-Mood-Classification.md) — phrase → structured mood reading
2. [Character Selection](Workflow-Character-Selection.md) — mood → 1–3 cast voices
3. [Composition (C2/C3)](Workflow-Composition.md) — cast → in-character lines → render-ready segments
4. [Orchestration & Playback](Workflow-Orchestration-and-Playback.md) → audio URL → Sonos / ESP32

---

## 1. System architecture

```mermaid
flowchart TB
    subgraph Devices
      ESP[ESP32-S3 · Xiaozhi firmware]
      CURL[Any HTTP client / curl]
    end

    subgraph Edge[Public edge]
      CF[Cloudflare Tunnel<br/>your public webhook]
    end

    subgraph N8NHOST[n8n host · 192.168.1.47]
      N8N[n8n workflow engine]
      REDIS[(Redis<br/>session store)]
    end

    subgraph UR1[Unraid · UR1 · 2× NVIDIA GPU]
      GW[xiaozhi-gateway :5010]
      WHISP[whisper-stt :5009]
      ORC[orchestrator :5005]
      F5[f5-tts :5003]
      PAR[parler-tts :5004]
      XT[coqui-tts / XTTS :5002]
      CB[chatterbox :5006]
      CONV[audio-converter :5007]
      OLL[Ollama :11434]
    end

    subgraph HAHOST[Home Assistant · 192.168.1.64]
      HA[Home Assistant] --> SONOS[Sonos Roam]
    end

    ESP -->|Opus WS| GW --> WHISP
    GW -->|transcript over HTTPS| CF
    CURL --> CF
    CF --> N8N
    N8N <--> REDIS
    N8N <--> OLL
    N8N --> ORC
    ORC --> F5 & PAR & XT & CB
    ORC --> CONV
    N8N --> HA
    ORC -->|WAV URL| SONOS
    ORC -->|Opus WS| GW --> ESP
```

All TTS/LLM inference stays **local** on Unraid. The two GPUs are split: the **RTX 3060** runs F5/Parler/Chatterbox, the **RTX 3090** runs XTTS, Whisper, and Ollama.

> **Networking gotcha — the gateway reaches n8n via the public webhook, not the LAN IP.** n8n runs on an Unraid **macvlan (`br0`)** interface, while `xiaozhi-gateway` is on the **`bumblebee_default` bridge**. Unraid blocks macvlan↔bridge traffic on the *same host*, so the gateway cannot hit n8n's `192.168.1.47:5678` directly (it fails with `All connection attempts failed`). It instead posts to the **Cloudflare Tunnel** URL (`https://<your-tunnel-domain>/webhook/bumblebee`), which dials outbound and sidesteps the isolation. The orchestrator's WAV URL (`192.168.1.33:5005`) *is* reachable from the bridge — that's UR1's own host IP, not a macvlan peer.

---

## 2. Request lifecycle (one message)

```mermaid
sequenceDiagram
    participant U as User (text/voice)
    participant N as n8n
    participant R as Redis
    participant O as Ollama
    participant C as Orchestrator
    participant T as TTS engine(s)
    participant S as Sonos / ESP32

    U->>N: POST /webhook/bumblebee {text}
    N->>R: read session (last 5 turns)
    N->>O: C1 — classify mood → JSON
    O-->>N: {primary_mood, response_type, response_register, ...}
    N->>N: filter characters by type+register, shuffle,<br/>weighted roll K = 1–3
    N->>O: C2/C3 — compose in-character text per segment
    O-->>N: segment texts
    N->>R: write session (TTL 300s)
    N->>C: POST /speak-multi {segments[]}
    loop each segment
        C->>T: synthesize (F5 clone or Parler described)
        T-->>C: WAV
        C->>C: FFmpeg vintage filter
    end
    C->>C: concat segments → one WAV (0.6s gaps)
    C-->>N: {url, characters, mood}
    N->>S: play WAV URL
    S-->>U: 🔊 plays back
```

**Two-stage LLM:** the first Ollama call only *classifies* mood. The character pick is done in **JavaScript** in n8n (genuine weighted randomness — `P(1)=27% / P(2)=40% / P(3)=33%`, renormalised when fewer voices match), then a second Ollama call writes the in-character lines for the chosen voices.

**Engine routing & fallback:** each segment is tagged `f5` (a reference clip exists on disk) or `parler` (no clip → synthesize a described voice). If an `f5` clip is missing at render time, the orchestrator **auto-falls back to Parler** instead of erroring.

---

## 3. Multi-device topology (ESP32 voice I/O)

```mermaid
flowchart TB
    subgraph Devices
      D1[ESP32 · Office]
      D2[ESP32 · Kitchen]
      D3[ESP32 · ...]
    end

    OTA[/ota endpoint<br/>device self-registration/]
    GW[xiaozhi-gateway<br/>device registry]

    D1 & D2 & D3 -.boot: fetch WS URL + token.-> OTA
    OTA --> GW
    D1 & D2 & D3 -->|Opus WS, per device| GW
    GW --> PIPE[STT → n8n → TTS pipeline]
    PIPE --> ROUTE{output_target<br/>per device}
    ROUTE -->|self| D1
    ROUTE -->|sonos:entity| SON[Sonos]
    ROUTE -->|both| BOTH[device + Sonos]
```

Design decisions (locked):
- **Output routing is per-device** (`self` | `sonos:<entity>` | `both`).
- **Onboarding via `/ota`** — a new device self-registers; no re-flash to add one.
- **One shared persona** — mood-driven only; no per-device voice override.

See [Voice Input: Alexa → ESP32/Xiaozhi](Voice-Input-Alexa-vs-ESP32.md) for the firmware/protocol detail.

---

## n8n node order (current)

The full node list in one place (each group is detailed on its stage page — see the four links at the top of this page):

```
Webhook
  → Read Session (Redis GET)
  → Build Ollama Request (mood classify prompt)
  → Ask Ollama
  → Parse Ollama Response  (filter by response_type, tighten by register,
                            Fisher–Yates shuffle, weighted roll 1..3,
                            build compose prompt)
  → Ask Ollama Compose
  → Parse Segments  (attach reference_clip / voice_description / tts_engine by name)
  → Write Session (Redis SET, TTL 300s)
  → Call Orchestrator (POST /speak-multi)
  → Respond (returns {status, count, characters, mood, url})
  → Play on Sonos
```

`Respond` is placed **before** `Play on Sonos` deliberately: Home Assistant's `play_media` returns an empty array, which would otherwise stop the workflow before the webhook responded. RespondToWebhook passes its input through, so Sonos still receives the `url`.
