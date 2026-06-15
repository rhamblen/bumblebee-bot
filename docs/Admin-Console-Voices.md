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

## Roadmap for this tab

- **Brain config belongs here** — voice-count weighting, persona, and the Ollama model→role map are *brain* settings, not infra; the plan is to surface them on this tab rather than the [Config tab](Admin-Console-Config.md).

> Like every tab, this one **lazy-loads** on first open with its own **↻ refresh** and "last updated" timestamp.
