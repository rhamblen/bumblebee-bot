"""
Batch-process archetype-person.csv: download and trim each YouTube clip to WAV.

Two modes:
  --local   Run yt-dlp + ffmpeg directly on this machine (use when VPN is active locally).
            Saves WAV directly to \\\\SERVER-UR1\\media\\bumblebee\\references\\<person>\\
            Requires: yt-dlp and ffmpeg on PATH.

  (default) Call the audio-converter service on UR1 (/download endpoint).
            Use when running without local VPN.

Output files are named:  <person_snake_case>_clip_<N>.wav

Usage:
    python process_archetype_csv.py --local
    python process_archetype_csv.py --local --dry-run
    python process_archetype_csv.py --local --person "Julia Child"
    python process_archetype_csv.py --csv path/to/other.csv
"""

import argparse
import csv
import re
import subprocess
import sys
import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

DEFAULT_CSV = os.path.join(os.path.dirname(__file__), "..", "..", "data", "archetype-person.csv")
DEFAULT_CONVERTER_URL = "http://192.168.1.33:5007"   # audio-converter
DEFAULT_REFERENCES_DIR = r"\\SERVER-UR1\media\bumblebee\references"
CONTAINER_REFERENCES_ROOT = "/media/references"

TIMESTAMP_SEP = re.compile(r"[–—-]")   # en-dash, em-dash, or plain hyphen


def person_to_folder(person: str) -> str:
    """'Julia Child' → 'Julia_Child'"""
    return re.sub(r"\s+", "_", person.strip())


def parse_timestamp(ts: str) -> tuple[str, str] | None:
    """
    Parse 'MM:SS–MM:SS' (or HH:MM:SS variants) into (start, end).
    Returns None if the cell is empty or PENDING.
    """
    ts = ts.strip()
    if not ts or ts in {"—", "-", "PENDING"}:
        return None
    parts = TIMESTAMP_SEP.split(ts, maxsplit=1)
    if len(parts) != 2:
        return None
    start, end = parts[0].strip(), parts[1].strip()
    if not start or not end:
        return None
    return start, end


def timestamp_to_seconds(ts: str) -> float:
    """Convert MM:SS or HH:MM:SS to total seconds."""
    parts = ts.strip().split(":")
    parts = [float(p) for p in parts]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def download_clip_local(
    url: str,
    folder_name: str,
    filename: str,
    start_time: str,
    end_time: str,
    dry_run: bool,
    output_dir: str = DEFAULT_REFERENCES_DIR,
) -> bool:
    """Download and trim using local yt-dlp + ffmpeg."""
    out_dir = Path(output_dir) / folder_name
    out_path = out_dir / f"{filename}.wav"

    # Skip if this specific output file already exists
    if out_path.exists():
        print(f"  SKIP (already exists): {out_path}")
        return True

    if dry_run:
        print(f"  [dry-run] local yt-dlp  url={url}  out={out_path}  trim={start_time}-{end_time}")
        return True

    # Download to local /tmp first (avoids CIFS write permission issues)
    import tempfile
    tmp_dir = Path(tempfile.mkdtemp())
    tmp_audio = tmp_dir / f"_{filename}_tmp"

    start_sec = timestamp_to_seconds(start_time)
    duration_sec = timestamp_to_seconds(end_time) - start_sec

    ytdlp_cmd = [
        "yt-dlp",
        "--no-playlist",
        "-x",
        "--audio-format", "wav",
        "--audio-quality", "0",
        "--postprocessor-args", f"ffmpeg:-ss {start_sec} -t {duration_sec}",
        "-o", str(tmp_audio) + ".%(ext)s",
        url,
    ]

    print(f"  Downloading: {url}  [{start_time} → {end_time}]")
    result = subprocess.run(ytdlp_cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ERROR (yt-dlp): {result.stderr[-500:]}")
        return False

    # Find yt-dlp output file
    tmp_wav = tmp_audio.with_suffix(".wav")
    if not tmp_wav.exists():
        candidates = list(tmp_dir.glob(f"_{filename}_tmp*"))
        if not candidates:
            print("  ERROR: yt-dlp output file not found")
            return False
        tmp_wav = candidates[0]

    # Convert to standard WAV: 22050Hz mono 16-bit, save to final tmp location
    final_tmp = tmp_dir / f"{filename}.wav"
    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", str(tmp_wav),
        "-ar", "22050",
        "-ac", "1",
        "-sample_fmt", "s16",
        str(final_tmp),
    ]
    result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
    tmp_wav.unlink(missing_ok=True)

    if result.returncode != 0:
        print(f"  ERROR (ffmpeg): {result.stderr[-300:]}")
        return False

    # Copy to final destination (works around CIFS write restrictions)
    out_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    shutil.copy2(str(final_tmp), str(out_path))
    shutil.rmtree(tmp_dir, ignore_errors=True)

    print(f"  Saved: {out_path}")
    return True


def download_clip_remote(
    url: str,
    folder_name: str,
    filename: str,
    start_time: str,
    end_time: str,
    converter_url: str,
    dry_run: bool,
    proxy: str | None = None,
) -> bool:
    """Download via the audio-converter service on UR1."""
    container_folder = f"{CONTAINER_REFERENCES_ROOT}/{folder_name}"

    if dry_run:
        proxy_note = f"  proxy={proxy.split('@')[-1]}" if proxy else ""
        print(f"  [dry-run] POST /download  url={url}  out={filename}.wav  trim={start_time}-{end_time}{proxy_note}")
        return True

    body = {
        "url": url,
        "output_dir": container_folder,
        "filename": filename,
        "start_time": start_time,
        "end_time": end_time,
    }
    if proxy:
        body["proxy"] = proxy

    try:
        resp = requests.post(
            f"{converter_url}/download",
            json=body,
            timeout=300,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"  ERROR: {e}")
        return False

    result_path = resp.json()["output_path"]
    unc = result_path.replace(CONTAINER_REFERENCES_ROOT, DEFAULT_REFERENCES_DIR).replace("/", "\\")
    print(f"  Saved: {unc}")
    return True


def download_clip(
    url: str,
    folder_name: str,
    filename: str,
    start_time: str,
    end_time: str,
    converter_url: str,
    dry_run: bool,
    local: bool = False,
    output_dir: str = DEFAULT_REFERENCES_DIR,
    proxy: str | None = None,
) -> bool:
    if local:
        return download_clip_local(url, folder_name, filename, start_time, end_time, dry_run, output_dir)
    return download_clip_remote(url, folder_name, filename, start_time, end_time, converter_url, dry_run, proxy)


def main():
    parser = argparse.ArgumentParser(description="Batch-download YouTube clips from archetype-person.csv")
    parser.add_argument("--csv", default=DEFAULT_CSV, help="Path to the CSV file")
    parser.add_argument("--converter-url", default=DEFAULT_CONVERTER_URL)
    parser.add_argument("--person", help="Only process this person (e.g. 'Julia Child')")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--local", action="store_true",
                        help="Run yt-dlp + ffmpeg locally (use when VPN is active on this machine).")
    parser.add_argument("--output-dir", default=DEFAULT_REFERENCES_DIR,
                        help="Root references folder. Override when running from WSL "
                             "(e.g. /mnt/ur1/media/bumblebee/references)")
    args = parser.parse_args()
    args.proxy = None

    if args.local:
        print(f"Mode: LOCAL — yt-dlp + ffmpeg on this machine → {args.output_dir}")
        if not args.dry_run:
            import shutil
            if not shutil.which("yt-dlp"):
                print("ERROR: yt-dlp not found on PATH. Install with: pip install yt-dlp")
                sys.exit(1)
            if not shutil.which("ffmpeg"):
                print("ERROR: ffmpeg not found on PATH.")
                sys.exit(1)
    else:
        print(f"Mode: REMOTE — audio-converter at {args.converter_url}")
        if not args.dry_run:
            try:
                r = requests.get(f"{args.converter_url}/health", timeout=5)
                r.raise_for_status()
            except requests.RequestException as e:
                print(f"ERROR: audio-converter not reachable at {args.converter_url}: {e}")
                sys.exit(1)

    ok_count = 0
    fail_count = 0
    skip_count = 0

    with open(args.csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            person = row.get("Person", "").strip()
            clip_num = row.get("Clip #", "").strip()
            yt_url = row.get("YouTube Link", "").strip()
            timestamp = row.get("Timestamp (10–30s)", "").strip()

            if args.person and person.lower() != args.person.lower():
                continue

            # Strip YouTube sharing parameter (?si=...) which breaks yt-dlp
            if "?si=" in yt_url:
                yt_url = yt_url.split("?si=")[0]

            # Skip PENDING rows
            if not yt_url or yt_url in {"—", "-", "PENDING"} or not yt_url.startswith("http"):
                print(f"SKIP  [{person}] clip {clip_num or '?'} — no URL")
                skip_count += 1
                continue

            ts = parse_timestamp(timestamp)
            if not ts:
                print(f"SKIP  [{person}] clip {clip_num or '?'} — no valid timestamp: {timestamp!r}")
                skip_count += 1
                continue

            start_time, end_time = ts
            folder_name = person_to_folder(person)
            safe_clip = clip_num.zfill(2) if clip_num.isdigit() else clip_num
            filename = f"{folder_name.lower()}_clip_{safe_clip}"

            print(f"\n[{person}] clip {clip_num}  {start_time}-{end_time}")
            print(f"  URL:  {yt_url}")
            print(f"  File: {folder_name}/{filename}.wav")

            ok = download_clip(
                url=yt_url,
                folder_name=folder_name,
                filename=filename,
                start_time=start_time,
                end_time=end_time,
                converter_url=args.converter_url,
                dry_run=args.dry_run,
                local=args.local,
                output_dir=args.output_dir,
                proxy=args.proxy,
            )
            if ok:
                ok_count += 1
            else:
                fail_count += 1

    print(f"\n{'='*60}")
    print(f"Downloaded: {ok_count}  |  Failed: {fail_count}  |  Skipped: {skip_count}")
    if fail_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
