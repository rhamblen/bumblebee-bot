"""Build character_descriptor.json from character_voices.json.

Every annotated character gets BOTH fields:
  - reference_clip : the standard path where their WAV clip lives (or will live)
  - voice_description : Parler style description used when no clip is on disk

The orchestrator decides at runtime which engine to use by checking whether
reference_clip actually exists on disk — no tts_engine flag needed.

Run this any time new characters are annotated (not when clips are added —
adding a clip is picked up automatically at runtime).
"""
import json, os, pathlib, sys

sys.stdout.reconfigure(encoding="utf-8")

VOICES_FILE    = r"D:\backup\richard\Documents\bumblebee bot\character_voices.json"
OUTPUT_FILE    = r"D:\backup\richard\Documents\bumblebee bot\character_descriptor.json"
REFS_HOST_ROOT = r"\\SERVER-UR1\media\bumblebee\references"   # Windows share path
REFS_CONTAINER = "/media/references"                           # path inside Docker containers

CLIP_PRIORITY = [".wav", ".mp3", ".ogg", ".m4a"]


def find_best_clip(folder_path: str) -> str | None:
    """Return best clip filename from a folder, WAV preferred."""
    p = pathlib.Path(folder_path)
    if not p.exists():
        return None
    candidates = [f for f in p.iterdir() if f.suffix.lower() in CLIP_PRIORITY]
    if not candidates:
        return None
    candidates.sort(key=lambda f: (CLIP_PRIORITY.index(f.suffix.lower()), len(f.name)))
    return candidates[0].name


with open(VOICES_FILE, encoding="utf-8") as f:
    data = json.load(f)

voices = data["voices"]
descriptor = []
skipped = []

for v in voices:
    if "response_type" not in v or "response_register" not in v:
        skipped.append(v["name"])
        continue

    name        = v["name"]
    archetype   = v.get("voice_archetype", "")
    audio_style = v.get("audio_style", "")
    ref_folder  = v.get("reference_folder", name)  # default to character name folder

    # Build standard expected clip path (used even if no file exists yet)
    clip_filename = None
    if ref_folder:
        folder_path = os.path.join(REFS_HOST_ROOT, ref_folder)
        clip_filename = find_best_clip(folder_path)

    # Expected container path — either a real file or the canonical placeholder
    if clip_filename:
        reference_clip = f"{REFS_CONTAINER}/{ref_folder}/{clip_filename}"
        clip_on_disk = True
    else:
        # Canonical name so a file dropped here is immediately picked up
        reference_clip = f"{REFS_CONTAINER}/{ref_folder}/{name.lower()}_ref_01.wav"
        clip_on_disk = False

    # Parler voice description (always built, used as fallback)
    voice_desc = archetype
    if audio_style:
        voice_desc = f"{archetype} Recording style: {audio_style}"

    entry = {
        "name":              name,
        "response_type":     v["response_type"],
        "response_register": v["response_register"],
        "reference_clip":    reference_clip,
        "voice_description": voice_desc,
        "voice_archetype":   archetype,
        "clip_on_disk":      clip_on_disk,
    }
    descriptor.append(entry)

# Sort: characters with clips first, then alphabetical
descriptor.sort(key=lambda x: (not x["clip_on_disk"], x["name"]))

f5_count     = sum(1 for d in descriptor if d["clip_on_disk"])
parler_count = sum(1 for d in descriptor if not d["clip_on_disk"])

output = {
    "generated_from": "character_voices.json",
    "total":          len(descriptor),
    "clips_on_disk":  f5_count,
    "parler_only":    parler_count,
    "characters":     descriptor,
}

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(output, f, indent=2, ensure_ascii=False)

print(f"Written: {OUTPUT_FILE}")
print(f"  Total:          {output['total']}")
print(f"  Clips on disk:  {f5_count}  (will use F5-TTS)")
print(f"  No clip yet:    {parler_count}  (will use Parler until clip is added)")
print(f"  Skipped (bare): {len(skipped)}")
print()
print("Characters with clips on disk:")
for d in descriptor:
    if d["clip_on_disk"]:
        print(f"  {d['name']}: {d['reference_clip']}")
print()
print("Characters using Parler (expected clip path):")
for d in descriptor:
    if not d["clip_on_disk"]:
        print(f"  {d['name']}: {d['reference_clip']}")
        print(f"    -> {d['voice_description'][:80]}...")
