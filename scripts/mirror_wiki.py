"""Mirror docs/ -> the GitHub Wiki, keeping the two in sync.

The repo's docs/ folder is the source of truth for the wiki. This script copies
every docs/*.md page into the wiki repo, rewriting internal links from the
folder form `](Page-Name.md)` to the wiki form `](Page-Name)` so navigation
works on the wiki. Home.md and _Sidebar.md are wiki-native and copied as-is.

Prerequisite (one-time): the wiki repo only exists after the FIRST page is
created via the GitHub web UI (Wiki tab -> "Create the first page" -> Save).

Usage:
    python scripts/mirror_wiki.py            # clone/pull wiki, copy, commit, push
    python scripts/mirror_wiki.py --dry-run  # show what would change, no push
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

WIKI_URL = "https://github.com/rhamblen/bumblebee-bot.wiki.git"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DOCS_DIR = os.path.join(ROOT, "docs")

# `](Some-Page-Name.md)` -> `](Some-Page-Name)`  (leaves external/http links alone)
LINK_RE = re.compile(r"\]\((?!https?://)([A-Za-z0-9_\-]+)\.md\)")


def transform(text: str) -> str:
    return LINK_RE.sub(r"](\1)", text)


def run(cmd, cwd=None, check=True):
    print("  $", " ".join(cmd))
    return subprocess.run(cmd, cwd=cwd, check=check)


def main() -> int:
    ap = argparse.ArgumentParser(description="Mirror docs/ to the GitHub Wiki")
    ap.add_argument("--dry-run", action="store_true", help="copy + show diff, do not push")
    args = ap.parse_args()

    if not os.path.isdir(DOCS_DIR):
        sys.exit(f"docs/ not found at {DOCS_DIR}")

    tmp = tempfile.mkdtemp(prefix="bb-wiki-")
    wiki = os.path.join(tmp, "wiki")
    try:
        try:
            run(["git", "clone", WIKI_URL, wiki])
        except subprocess.CalledProcessError:
            sys.exit(
                "Wiki repo not found. Create the first page via the GitHub Wiki "
                "tab (Wiki -> 'Create the first page' -> Save), then re-run."
            )

        copied = []
        for name in sorted(os.listdir(DOCS_DIR)):
            if not name.endswith(".md"):
                continue
            src = os.path.join(DOCS_DIR, name)
            with open(src, encoding="utf-8") as f:
                content = f.read()
            # _Sidebar/_Footer/Home are wiki-native; content pages get link rewrite
            if not name.startswith("_") and name != "Home.md":
                content = transform(content)
            with open(os.path.join(wiki, name), "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            copied.append(name)

        print(f"\nCopied {len(copied)} page(s): {', '.join(copied)}")
        run(["git", "add", "-A"], cwd=wiki)

        if args.dry_run:
            print("\n--dry-run: diff vs current wiki --")
            run(["git", "--no-pager", "diff", "--cached", "--stat"], cwd=wiki, check=False)
            return 0

        # commit only if something changed
        status = subprocess.run(
            ["git", "status", "--porcelain"], cwd=wiki, capture_output=True, text=True
        )
        if not status.stdout.strip():
            print("Wiki already up to date — nothing to push.")
            return 0

        run(["git", "-c", "user.name=richardh", "-c", "user.email=richardh@iname.com",
             "commit", "-m", "docs: sync wiki from docs/"], cwd=wiki)
        run(["git", "push"], cwd=wiki)
        print("\nWiki updated.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
