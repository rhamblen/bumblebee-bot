# Concept & Lore

## The premise

Bumblebee is an extra-terrestrial who lost his voice box. He cannot speak in his own words. Instead, he communicates by replaying recordings he has intercepted from Earth's radio and television broadcasts — primarily 1950s, 60s and 70s content stored in his memory.

When you talk to him, he doesn't give a literal reply. He finds the closest relevant clip from everything he's heard, and plays it back as though he's tuned to exactly the right channel. The LLM in this project generates text that *sounds like* a genuine period quote or broadcast snippet; TTS clones it into the right voice; and an FFmpeg filter makes it sound like an intercepted recording.

## The core principle (this drives the whole design)

Bumblebee's clip selection is **deliberate, not random**. Across the Bay films, *Transformers: Prime*, and the IDW comics, the logic is consistent:

> He thinks in normal language. The radio clips are a *translation layer* he consciously manipulates. He knows exactly what he wants to say — the challenge is finding audio that communicates it.

The process is **Intent → Meaning → Search memory → Select fragment → Broadcast**, *not* Emotion → Random lyric. His cognition is intact; only the output-rendering layer is damaged.

### Priority order when choosing a clip

| Priority | Layer | Description |
|---|---|---|
| 1 | **Literal meaning** | Can a clip directly say what he intends? Always preferred. |
| 2 | **Situation / context** | Does it fit what's happening right now? |
| 3 | **Emotional tone** | Does the *feel* of the clip match the mood? |
| 4 | **Relationship / audience** | Who's he talking to? Playful with friends, precise with strangers. |
| 5 | **Humour / personality** | A funnier clip can beat a technically accurate one. |

## The design rules these produce

- **The "wrong channel" mismatch is canon-faithful, not a defect.** When the mood read is slightly off, Bumblebee played the closest thing he had — and that gap is funny *because* it's in character. The LLM is told to be **accurate, not funny**; the comedy emerges from the mismatch on its own.
- **Personality filtering is real.** Each character has things they simply *won't* say. Churchill won't do slapstick. Attenborough won't do combat urgency. Mickey Mouse won't console grief. The character table encodes best-fit moods *and* the no-go zones.
- **One voice is the default.** A single, perfectly cast character beats a forced multi-voice split. Extra segments must be earned (see [Character & Response Table](Character-Response-Table.md)).

## Era

**1950s / 60s / 70s broadcasting is the default palette.** Permitted exceptions are timeless fictional classics agreed case-by-case — e.g. Gandalf (LOTR/Hobbit), Darth Vader, James Bond (Sean Connery era). These widen the roster without breaking the "intercepted vintage broadcast" feel.

## How the lore maps to the system

| Canon layer | This system |
|---|---|
| Understand the situation | C1 mood inference ([Input Metadata Schema](Input-Metadata-Schema.md)) |
| Decide communicative intent | `response_type` + `response_register` fields |
| Filter by emotion + context | Character-selection prompt |
| Personality alignment | Character descriptor table |
| Clip stitching | Multi-segment response (1–3 segments) |
| Delivery with flair | FFmpeg vintage filter + segment ordering |
