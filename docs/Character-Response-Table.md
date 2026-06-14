# Character & Response Table

This is how Bumblebee decides **who** speaks. The mood reading from [C1](Input-Metadata-Schema.md) is matched against a roster of characters, each tagged with the response types and registers they fit. The result is 1–3 voices and the in-character lines they say.

## The two driving fields

Selection keys on two C1 fields:

- **`response_type`** — *what kind of reply*: `motivate / console / celebrate / challenge / inform / amuse / validate`
- **`response_register`** — *the tone*: `serious / light / dramatic / deadpan`

Every character declares which `response_type[]` and `response_register[]` values they're cast for. A character is only eligible if both match.

## Coverage matrix (sample)

`response_type` × `response_register`, with example characters:

| response_type | serious | dramatic | deadpan | light |
|---|---|---|---|---|
| **motivate** | Churchill, MLK, Optimus Prime | FDR, Darth Vader | John Wayne, James Bond | Keith Floyd, John Noakes |
| **console** | MLK, Queen Elizabeth, Fred Rogers | King George VI | Alan Whicker | Joyce Grenfell, Mary Berry |
| **celebrate** | JFK, Churchill | Wolfman Jack | James Bond | Mickey Mouse, Goofy, Julia Child |
| **challenge** | Darth Vader, Sgt. Hartman | Russian General | Robin Day, Penelope Keith | John Noakes |
| **inform** | Richard Dimbleby, BBC Newsreader | Newsreel Narrator | Barry Bucknell | David Attenborough, Johnny Ball |
| **validate** | Churchill, MLK, Queen Elizabeth | King George VI | Alan Whicker, Diana Rigg | Attenborough, Joyce Grenfell |
| **amuse** | *(thin — Attenborough deadpan)* | Wolfman Jack | Soupy Sales, Paul Winchell | Mickey Mouse, Goofy, Graham Kerr |

The full matrix (plus a separate actress sub-matrix) lives in `character_voices.json` and the project's coverage map.

## How a voice is picked (in the pipeline)

1. **Filter** the roster to characters whose `response_type[]` contains the C1 `response_type`.
2. **Tighten** by `response_register`.
3. **Shuffle** (Fisher–Yates) for genuine variety.
4. **Roll K = 1–3** with a weighted distribution — `P(1)=27% / P(2)=40% / P(3)=33%`, renormalised when fewer voices match.
5. **Compose** in-character text for each chosen voice (second Ollama call).

> The random pick happens in **JavaScript in n8n**, not in the LLM — that keeps the randomness real rather than the model favouring the same "best" answer every time.

## Multi-character rules (strict)

A single, well-cast voice is the default. Extra segments must be *earned*:

- **Gravity first** — loss, genuine distress, high intensity, or serious urgency → **single character, no levity, ever.**
- **`response_register` is the gate:**
  - `serious` → single character, no playful segment
  - `dramatic` → single character, no playful segment
  - `deadpan` → subtle wit only, never slapstick
  - `light` → multi-character split allowed
- **Humour must be earned** — only add a lighter segment when `humour_present: true`, or `intensity` is mild/moderate *and* `sentiment` is positive/mixed.
- **Ordering** — heavier/serious segment first, lighter last.
- **No repeated characters** in one response.
- **3 segments only** for genuinely complex mixed moods — don't manufacture complexity.
- **Low C1 `confidence` → be more careful, not more silly.**

## Clone vs described voice

| Kind | Engine | Needs a clip? | Example characters |
|---|---|---|---|
| **Cloned** real person | F5 / XTTS / Chatterbox | ✅ reference WAV on disk | Churchill, John Wayne, MLK |
| **Described** archetype | Parler | ❌ text description only | Noir Detective, French Chef, Russian General |

See [TTS Options](TTS-Options.md) for the engine trade-offs.

## The clip gate (important)

**A character with no reference clip on disk is never offered to the cloner.** The live voice table (`character_descriptor.json`, served at the orchestrator's `GET /voices`) marks each character's clip status. As clips are added and `/admin/scan-references` is run, characters automatically "upgrade" from Parler-described to F5-cloned — the roster gets richer over time without prompt changes.

> Today only a couple of characters have clips on disk, so most rolls render via Parler. Sourcing more reference clips is the single biggest lever on output quality.

## Personality filtering

Matching mood isn't enough — characters also have things they **won't** say. Churchill won't do slapstick; Attenborough won't do combat urgency; Mickey Mouse won't console grief. The descriptor encodes best-fit moods *and* these no-go zones, so a technically-eligible character is still skipped when it would break character. This is straight from the [lore](Concept-and-Lore.md).
