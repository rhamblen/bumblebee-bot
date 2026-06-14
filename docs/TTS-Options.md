# TTS Options (Text-to-Speech)

Bumblebee ships **four** TTS engines because no single one is best for every voice. They fall into two families:

- **Voice cloning** — give it a short reference clip of a real person, it speaks new text in that voice. (F5, XTTS, Chatterbox)
- **Described voice** — give it a *text description* of a voice ("a deep, authoritative 1950s newsreader"), no clip needed. (Parler)

The orchestrator picks per segment: if a reference clip exists on disk for the character, it uses a cloner (default **F5**); otherwise it uses **Parler** with a written description. If an expected clip is missing at render time, it **auto-falls back to Parler**.

## Engine comparison

| Engine | Port | Type | Strengths | Use for |
|---|---|---|---|---|
| **F5-TTS** | 5003 | Clone | Best clone fidelity ⭐ | Iconic, recognisable real voices |
| **XTTS v2** (Coqui) | 5002 | Clone | Solid alternative cloner, multilingual | Fallback / variety when F5 struggles |
| **Chatterbox** | 5006 | Clone | Emotion & exaggeration control | Dramatic register, expressive delivery |
| **Parler (mini)** | 5004 | Described | No clip required, fast to add a voice | Archetypes with no real-person audio |

### Quality notes (tested with a John Wayne reference)

| Engine | Verdict |
|---|---|
| F5-TTS | "Close enough" — the best cloner of the four |
| XTTS v2 | "Sounds OK" — usable alternative |
| Chatterbox | "Not as good" for pure cloning, but adds emotion control |
| Parler **mini** | Works well for described styles |
| Parler **large** | ❌ Generates garbage noise — **do not use** |

## Why have all four?

- **Coverage.** Only a fraction of characters have a usable reference clip. Parler lets every archetype have a voice immediately; cloners make the ones with clips sound far more authentic.
- **Pick your hardware.** On a single modest GPU you can run **just Parler** (no clips, no per-voice setup) and still have a working bot. Add F5 when you want real cloned voices.
- **Register matters.** A grief-console line and a slapstick line want different engines; Chatterbox's exaggeration control helps the dramatic end.

## The vintage filter

Every rendered segment passes through an FFmpeg "intercepted broadcast" filter before playback, e.g.:

```
highpass=f=300,lowpass=f=3000,aecho=0.5:0.5:60:0.12,volume=3.0
```

This band-limits the audio like an old radio and adds a short echo. Per-character presets exist (e.g. `bbc_drama_1970s`, `hollywood_1960s`, `us_sitcom_1970s`). A raw-vs-filtered A/B mode is planned so you can tune how heavy the effect is per voice.

## Swapping / reducing the engine set

Each engine is an independent container behind a URL env var on the orchestrator (`F5_TTS_URL`, `PARLER_TTS_URL`, `COQUI_TTS_URL`, `CHATTERBOX_URL`). To run fewer engines, omit the container and don't route to it; to add a new one, expose the same "text (+ optional reference) → WAV" HTTP contract and point a new env var at it.
