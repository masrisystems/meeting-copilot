# Ticket: Windows packaging and CI

## Meta
- ID:             010
- Typ:            chore
- Status:         proposed
- Erstellt:       2026-10-23
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     pyproject.toml · tests/ · README.md

## Kontext
Setting up the copilot needs uv, a terminal and manual model downloads, which is a barrier for non-developers trying the open-source release. There is no CI, so pull requests aren't tested.

## Ziel
Non-technical users install the copilot with a Windows installer. Every pull request is tested and linted automatically.

## Anforderungen
- GitHub Actions on `windows-latest`: `uv sync`, `ruff check`, `uv run pytest` (no GPU/audio needed).
- A release workflow that builds an installer (PyInstaller or Briefcase bundle + Inno Setup) and attaches it to GitHub Releases.
- A first-run wizard: CUDA check, model download with progress, device selection, optional Gemini key.
- A CPU-only mode (`WHISPER_DEVICE=cpu`, `small`/`base` model) for machines without NVIDIA.
- Issue/PR templates and CONTRIBUTING.md.

## Akzeptanzkriterien
- [ ] CI is green on main and runs on every PR.
- [ ] A fresh Windows 11 VM installs and runs the copilot from the installer without a terminal.
- [ ] CPU mode transcribes with ≤ 2 s latency per chunk on a modern laptop CPU.

## Risiken
- The installer size with CUDA DLLs is large (> 1 GB); offer a separate CUDA download.

## Offene Fragen
- Should builds be code-signed to avoid SmartScreen warnings? That has a cost.

## Erste Umsetzungsidee
Start with CI and ruff, then CPU mode, then the installer.

## Kommentare
