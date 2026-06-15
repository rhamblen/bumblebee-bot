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

### Speed notes (measured 2026-06-15)

Controlled head-to-head — the **same 35-word line** through each engine via the orchestrator's `/speak` (so output length doesn't skew it):

| Engine (GPU) | Warm generate | Notes |
|---|---|---|
| **Parler mini** (RTX 3090, bf16) | ~14.7s | very consistent; autoregressive (time ∝ output length) |
| **F5-TTS** (RTX 3060) | ~3–9s (per voice) | ~1.8× faster than Parler **despite the slower card** |

Takeaways:
- **F5 is faster than Parler** — it's non-autoregressive (flow-matching) vs Parler's token-by-token decode. So clips win on **both** quality and speed.
- **No per-voice cold start, no expiry.** A voice never "goes cold" — verified: a never-rendered voice's first call was ~5s, and one used much earlier was ~3s. The only cold start is a **one-time ~20–25s service init on the first request after the container (re)starts** (F5 lazy-loads its internal auto-transcription model); paid once per container lifetime, not per voice. The F5 model itself is loaded at startup and never unloaded. *(Optional: fire one throwaway render at startup so that one-time init doesn't land on a user's first request.)*
- **F5 and Parler sit on different GPUs** (3060 vs 3090), so a mixed multi-voice run synthesises across **both cards in true parallel** — clip characters don't just render faster, they offload the 3090. See [GPU split](Docker-Containers.md#gpu-split).
- Parler generation is dominated by **output length**, not raw GPU power — the lever there is shorter lines / a token cap, not a faster card (moving it to the 3090 + bf16 gave only ~17%, though it halved its VRAM).

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
