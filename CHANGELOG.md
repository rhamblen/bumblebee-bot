# Changelog

All notable changes to the Bumblebee Bot project are recorded here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

Convention: work is committed locally during a session and recorded here under
**[Unreleased]**. When a session is confirmed finished, the entry is versioned,
dated, and pushed to GitHub along with any docs/Wiki updates — so the repository
always reflects the project's true current status and the choices made.

## [Unreleased]
### Added
- Wiki landing page (`docs/Home.md`) and navigation sidebar (`docs/_Sidebar.md`) — `docs/`
  is now a complete, ready-to-publish mirror of the GitHub Wiki.
- `scripts/mirror_wiki.py` — one-command sync of `docs/` → the GitHub Wiki (rewrites
  internal `*.md` links to wiki form). Run once the wiki's first page exists.

## [0.2.0] - 2026-06-14
### Added
- Public-facing `README.md` (project intro, Mermaid architecture diagram, stack, status).
- `docs/` write-up (mirrors the GitHub Wiki): Concept & Lore, Architecture & Workflow
  (system / sequence / multi-device diagrams), Docker Containers, Unraid Template,
  STT Options, TTS Options, Voice Input (Alexa → ESP32/Xiaozhi), Input Metadata Schema,
  Character & Response Table.
- `CHANGELOG.md` and versioning convention.

### Changed
- **Reorganised the repository into folders by work area** for readability:
  `data/` (character tables, presets, CSV/XLSX), `scripts/n8n/`, `scripts/clips/`,
  `scripts/character/`, `tests/`, `notes/`. `docker/` and `docs/` unchanged.
- Updated path references inside the moved scripts so `.env` resolves to the repo root
  and data files resolve to `data/` (verified: scripts compile, paths resolve).

## [0.1.0] - 2026-06-14
### Added
- Initial commit: working end-to-end pipeline source — Docker stack (8 services:
  orchestrator, F5/Parler/XTTS/Chatterbox TTS, audio-converter, whisper-stt,
  xiaozhi-gateway), n8n update scripts, character data, and admin/test tooling.
- `.gitignore` scrubbing all secrets (`.env`, `.venv/`, `.claude/settings.local.json`,
  `cookies.txt`, `*.ovpn`, generated audio).
