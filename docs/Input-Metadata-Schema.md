# Input Metadata Schema

When you send Bumblebee a phrase, the **first** Ollama call (C1) doesn't write a reply — it *classifies* the input into a structured JSON "mood reading." Every later stage (character selection, message composition) reads these fields. This is a **shared contract**: change a field here and it cascades to the n8n workflow and the composer prompt.

## Input to C1 (with conversation context)

Redis supplies the last few turns so the mood read is context-aware:

```json
{
  "history": [
    {"turn": 1, "input": "I'm really stressed about this presentation", "mood": "anxious"},
    {"turn": 2, "input": "I think I nailed it actually", "mood": "proud"}
  ],
  "current_input": "My boss just said it was the best one this quarter"
}
```

## C1 output schema (v2)

```json
{
  "primary_mood": "frustrated",
  "energy_level": "low",
  "intensity": "moderate",
  "sentiment": "negative",
  "topic": "work",
  "audience": "self",
  "directedness": "inward",
  "formality": "casual",
  "urgency": "medium",
  "humour_present": false,
  "sarcasm_detected": false,
  "response_type": "motivate",
  "response_register": "serious",
  "confidence": 0.85,
  "mood_trajectory": "stable",
  "session_turn": 1
}
```

## Field reference

### Emotional state
| Field | Values |
|---|---|
| `primary_mood` | happy, sad, anxious, frustrated, proud, excited, tired, angry, reflective, grateful, bored, overwhelmed, hopeful, defeated, playful |
| `energy_level` | low / medium / high |
| `intensity` | mild / moderate / strong |
| `sentiment` | positive / negative / neutral / mixed |

### Topic
| Field | Values |
|---|---|
| `topic` | work, relationships, love, loss, health, money, home, world_news, local_news, gossip, sport, entertainment, technology, animals, food, travel, philosophy, general (array allowed when genuinely multiple) |

### Social context
| Field | Values |
|---|---|
| `audience` | self / other / group |
| `directedness` | inward / outward |

### Communication style
| Field | Values |
|---|---|
| `formality` | casual / formal / slang |
| `urgency` | low / medium / high |
| `humour_present` | true / false |
| `sarcasm_detected` | true / false |

### Response posture — **the fields that drive everything downstream**
| Field | Values |
|---|---|
| `response_type` | motivate / console / celebrate / challenge / inform / amuse / validate |
| `response_register` | serious / light / dramatic / deadpan |

### Meta
| Field | Values |
|---|---|
| `confidence` | 0.0–1.0 — how sure C1 is (low confidence → play it safer, not sillier) |
| `mood_trajectory` | improving / worsening / stable / new_session |
| `session_turn` | integer — which turn this is in the current session |

## What each field actually controls

- **`response_type` + `response_register`** are the primary keys into the [Character & Response Table](Character-Response-Table.md): they decide *who* can be cast and whether multiple voices are even allowed.
- **`energy_level` / `intensity`** influence TTS exaggeration and whether a lighter second segment is permitted.
- **`humour_present` / `sentiment`** gate comedy — humour must be *earned* (see the composer rules).
- **`confidence`** is deliberately allowed to be low. **Misreading the mood is part of the character** — Bumblebee "played the wrong channel." C1 is told to infer, not to be cautious to the point of blandness.
- **`mood_trajectory` / `session_turn`** let the composer acknowledge a shift across a short conversation.

## Models

- **C1 mood inference / C2 selection** → `llama3.1` (best structured-JSON output)
- **C3 message composition** → `mistral` (best creative prose)
- Avoid reasoning models (e.g. deepseek-r1) for the JSON stages — they "think out loud" and pollute the output.
