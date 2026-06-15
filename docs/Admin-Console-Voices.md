# Admin Console · Voices tab

> One of the five tabs of the [Admin Console](Admin-Console.md). The live character roster.

## What it shows

The live character table, read straight from the orchestrator's [`GET /voices`](Service-Orchestrator.md#api) (i.e. `character_descriptor.json`). For each character it shows the **F5-clip vs Parler split** — whether a reference clip exists on disk — alongside the `response_type` / `response_register` it's cast for.

Because the console reads the *same* source the live pipeline reads, the roster here is exactly what [character selection](Workflow-Character-Selection.md) will cast from — there's no separate copy to drift out of sync (the console's [core design principle](Admin-Console.md)).

## Why the clip split matters

A character with **no clip on disk** is never offered to the cloner — it renders via Parler (described voice) instead. So the clip column is the at-a-glance view of **voice quality coverage**: the more characters with clips, the more of the roster renders as high-quality cloned voices. As clips are added and the orchestrator's [`/admin/scan-references`](Service-Orchestrator.md#reference-scan-adminscan-references) is run, characters "upgrade" from Parler to F5 automatically.

Full casting mechanics and the clip gate: [Character & Response Table](Character-Response-Table.md).

## Editing a Parler description

For every character **without** a clip on disk (the Parler-only rows), the table shows that character's `voice_description` — the style prompt Parler synthesises from — in an extra **parler description** column. F5/clip rows show `—`, since their description is unused.

The field is **locked (read-only) by default** so it can't be changed by a stray click or keystroke. Press **✏ Edit** on a row to unlock just that cell into a text box, then **✓ Save** to commit or **✗ Cancel** to revert. Saving posts to the orchestrator ([`POST /admin/voice-description`](Service-Orchestrator.md#api)), which updates that character's `voice_description` in `character_descriptor.json` and persists it atomically — the same single source the live pipeline reads, so there's no separate copy to drift. (Re-running `build_character_descriptor.py` would regenerate the descriptor and overwrite a hand-edited description.)

## Hearing a voice (▶ Play)

The **▶ Play** button sits in the last column (the widest). Pressing it generates a **fresh random in-character line** via Ollama (the `mistral` C3 personality model), synthesizes it with that character's engine (**F5 + reference clip** if a clip's on disk, else **Parler + description**), and plays it inline. Because the orchestrator renders with `keep_raw`, you get **both** the **▶ Filtered** (vintage broadcast sound, what the pipeline actually outputs) and **▶ Raw** (un-filtered, for judging clone fidelity) audio, plus the generated text and a **↻ New line** button to roll another. Each press is a new line — nothing is cached.

The audio is streamed back **same-origin** through the console (`GET /api/voice-preview-audio`, reading the orchestrator's shared `/media/generated` mount) — the same trick [Clip Capture](Admin-Console-Clip-Capture.md) uses, so the browser never has to reach the orchestrator's `:5005` directly.

The roster is **sorted by model then name**: F5-clip voices first, then Parler-only, each group A–Z. The sort re-applies on every ↻ refresh.

### 🔥 Warm up F5

F5 has **no per-voice cache or warming** — once its container is up, every voice renders at ~3–9s indefinitely. The *only* cold start is a one-time ~20–25s service init (F5 lazy-loads its auto-transcription model) on the **first render after the container (re)starts** — paid once per container, not per voice. The **🔥 Warm up F5** button fires one throwaway F5 render to pay that init up front, so your first real ▶ Play is fast. (A future enhancement moves this into orchestrator startup.)

## Roadmap for this tab

- **F5 delete & replace (Phase 2)** — a per-F5-row "replace clip" action: delete the reference clip from `references/<folder>/`, re-scan (flips the voice back to Parler), then re-source via [Clip Capture](Admin-Console-Clip-Capture.md). No cache to clear, since F5 keeps none.
- **F5 transcript pre-build (optional)** — F5 currently re-transcribes the reference clip on *every* render (we pass an empty `ref_text`). Precomputing and storing each clip's transcript would shave that per-render cost and make a "pre-build all" pass meaningful. Larger change (touches the F5 service).
- **Warm-up at startup** — move 🔥 Warm up F5 into the orchestrator's startup so the one-time init never lands on a user's first request.
- **Brain config belongs here** — voice-count weighting, persona, and the Ollama model→role map are *brain* settings, not infra; the plan is to surface them on this tab rather than the [Config tab](Admin-Console-Config.md).

> Like every tab, this one **lazy-loads** on first open with its own **↻ refresh** and "last updated" timestamp.
