"""
Admin script: convert all non-WAV reference clips to WAV and update character_voices.json.
Also supports downloading audio from YouTube (or any yt-dlp source) as a reference clip.

Run this whenever new clips are added to a character's reference folder.
Converted WAVs are written alongside the originals with the same stem.
character_voices.json clip_status is updated to reflect the conversion.

Usage:
    # Convert existing files in a character folder
    python convert_reference_clips.py --character John_Wayne

    # Download from YouTube and save as a named reference clip
    python convert_reference_clips.py --character Winston_Churchill --youtube-url https://youtube.com/... --filename winston_ref_01

    # Preview without writing anything
    python convert_reference_clips.py --dry-run

Defaults:
    --references-dir  \\\\SERVER-UR1\\media\\bumblebee\\references
    --converter-url   http://192.168.1.33:5007
"""

import argparse
import json
import os
import sys
import requests

SUPPORTED_EXTENSIONS = {".mp3", ".mp4", ".ogg", ".m4a", ".aac", ".flac", ".webm"}

DEFAULT_REFERENCES_DIR = r"\\SERVER-UR1\media\bumblebee\references"
DEFAULT_CONVERTER_URL = "http://192.168.1.33:5007"
CHARACTER_VOICES_PATH = os.path.join(os.path.dirname(__file__), "character_voices.json")

# Inside the Docker container the share is mounted at /media — map the Windows UNC path
CONTAINER_REFERENCES_ROOT = "/media/references"


def unc_to_container_path(unc_path: str, references_dir: str) -> str:
    """Convert a Windows UNC file path to its equivalent container path."""
    relative = os.path.relpath(unc_path, references_dir)
    return CONTAINER_REFERENCES_ROOT + "/" + relative.replace("\\", "/")


def convert_file(input_container_path: str, converter_url: str, dry_run: bool) -> str | None:
    """
    Ask the audio-converter service to convert the file.
    Returns the container output path on success, None on failure.
    """
    output_dir = "/".join(input_container_path.split("/")[:-1])
    if dry_run:
        stem = os.path.splitext(input_container_path)[0]
        print(f"  [dry-run] would convert → {stem}.wav")
        return stem + ".wav"

    try:
        resp = requests.post(
            f"{converter_url}/convert",
            json={"input_path": input_container_path, "output_dir": output_dir},
            timeout=60,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ERROR calling converter: {e}")
        return None

    uuid_output = resp.json()["output_path"]

    # Rename UUID output to deterministic stem.wav (mirrors orchestrator logic)
    stem = os.path.splitext(input_container_path)[0]
    target_container_path = stem + ".wav"

    if uuid_output != target_container_path:
        # The converter wrote a UUID file — rename it via the Windows share
        uuid_unc = uuid_output.replace(CONTAINER_REFERENCES_ROOT, references_dir_global).replace("/", "\\")
        target_unc = target_container_path.replace(CONTAINER_REFERENCES_ROOT, references_dir_global).replace("/", "\\")
        if os.path.exists(uuid_unc):
            os.rename(uuid_unc, target_unc)
        else:
            print(f"  WARNING: expected UUID file not found at {uuid_unc}")

    return target_container_path


def process_folder(folder_path: str, references_dir: str, converter_url: str, dry_run: bool) -> dict:
    """
    Convert all non-WAV files in folder_path that don't already have a cached WAV.
    Returns counts: {total, already_wav, already_cached, converted, failed, skipped}
    """
    counts = {"total": 0, "already_wav": 0, "already_cached": 0, "converted": 0, "failed": 0, "skipped": 0}

    if not os.path.isdir(folder_path):
        print(f"  Folder not found: {folder_path}")
        return counts

    for filename in sorted(os.listdir(folder_path)):
        ext = os.path.splitext(filename)[1].lower()
        if ext not in SUPPORTED_EXTENSIONS and ext != ".wav":
            continue

        counts["total"] += 1
        filepath = os.path.join(folder_path, filename)

        if ext == ".wav":
            counts["already_wav"] += 1
            continue

        # Check if a cached WAV already exists
        wav_path = os.path.splitext(filepath)[0] + ".wav"
        if os.path.exists(wav_path):
            counts["already_cached"] += 1
            continue

        if ext not in SUPPORTED_EXTENSIONS:
            counts["skipped"] += 1
            continue

        container_path = unc_to_container_path(filepath, references_dir)
        print(f"  Converting: {filename}")
        result = convert_file(container_path, converter_url, dry_run)
        if result:
            counts["converted"] += 1
        else:
            counts["failed"] += 1

    return counts


def update_clip_status(character: dict, folder_path: str, counts: dict) -> bool:
    """Update clip_status in the character entry to reflect conversion state. Returns True if changed."""
    if counts["failed"] > 0:
        return False  # Don't update status if any conversions failed

    total_wavs = counts["already_wav"] + counts["already_cached"] + counts["converted"]
    if total_wavs == 0:
        return False

    new_status = f"✅ {total_wavs} clips (WAV ready)"
    if character.get("clip_status") == new_status:
        return False

    character["clip_status"] = new_status
    return True


def download_youtube(url: str, character_name: str, filename: str, references_dir: str, converter_url: str, dry_run: bool) -> bool:
    """Download audio from a YouTube URL into the character's reference folder as a named WAV."""
    folder_path = os.path.join(references_dir, character_name)
    os.makedirs(folder_path, exist_ok=True)

    container_folder = f"{CONTAINER_REFERENCES_ROOT}/{character_name}"
    output_path = f"{container_folder}/{filename}.wav"

    print(f"\nDownloading YouTube audio for {character_name}")
    print(f"  URL:    {url}")
    print(f"  Output: {filename}.wav")

    if dry_run:
        print(f"  [dry-run] would POST /download → {output_path}")
        return True

    try:
        resp = requests.post(
            f"{converter_url}/download",
            json={"url": url, "output_dir": container_folder, "filename": filename},
            timeout=300,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ERROR: {e}")
        return False

    result_path = resp.json()["output_path"]
    unc_path = result_path.replace(CONTAINER_REFERENCES_ROOT, references_dir).replace("/", "\\")
    print(f"  Saved: {unc_path}")
    return True


def main():
    global references_dir_global

    parser = argparse.ArgumentParser(description="Convert reference clips to WAV and update character_voices.json")
    parser.add_argument("--references-dir", default=DEFAULT_REFERENCES_DIR)
    parser.add_argument("--converter-url", default=DEFAULT_CONVERTER_URL)
    parser.add_argument("--character", help="Only process this character name (e.g. John_Wayne)")
    parser.add_argument("--youtube-url", help="Download audio from this YouTube URL as a reference clip")
    parser.add_argument("--filename", help="Output filename (no extension) when using --youtube-url, e.g. winston_ref_01")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without converting")
    args = parser.parse_args()

    references_dir_global = args.references_dir

    # Validate YouTube mode args
    if args.youtube_url and not args.character:
        print("ERROR: --character is required when using --youtube-url")
        sys.exit(1)
    if args.youtube_url and not args.filename:
        print("ERROR: --filename is required when using --youtube-url (e.g. --filename winston_ref_01)")
        sys.exit(1)

    # Health check
    try:
        r = requests.get(f"{args.converter_url}/health", timeout=5)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"ERROR: audio-converter not reachable at {args.converter_url}: {e}")
        sys.exit(1)

    # YouTube download mode — download then fall through to convert the folder as normal
    if args.youtube_url:
        ok = download_youtube(
            url=args.youtube_url,
            character_name=args.character,
            filename=args.filename,
            references_dir=args.references_dir,
            converter_url=args.converter_url,
            dry_run=args.dry_run,
        )
        if not ok:
            sys.exit(1)

    with open(CHARACTER_VOICES_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    json_changed = False
    total_converted = 0
    total_failed = 0

    for character in data["voices"]:
        folder_name = character.get("reference_folder")
        if not folder_name:
            continue
        if args.character and character["name"] != args.character:
            continue

        folder_path = os.path.join(args.references_dir, folder_name)
        print(f"\n{character['name']} ({folder_name})")

        counts = process_folder(folder_path, args.references_dir, args.converter_url, args.dry_run)

        print(f"  → {counts['total']} audio files | "
              f"{counts['already_wav']} native WAV | "
              f"{counts['already_cached']} cached | "
              f"{counts['converted']} converted | "
              f"{counts['failed']} failed")

        total_converted += counts["converted"]
        total_failed += counts["failed"]

        if not args.dry_run and update_clip_status(character, folder_path, counts):
            json_changed = True
            print(f"  clip_status updated → {character['clip_status']}")

    print(f"\n{'='*60}")
    print(f"Total converted: {total_converted}  |  Failed: {total_failed}")

    if json_changed and not args.dry_run:
        with open(CHARACTER_VOICES_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print("character_voices.json updated.")
    elif args.dry_run:
        print("[dry-run] No files written.")
    else:
        print("No changes to character_voices.json.")

    if total_failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
