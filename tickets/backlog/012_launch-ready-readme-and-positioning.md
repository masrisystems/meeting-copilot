# Ticket: Launch-ready README, repo metadata and positioning

## Meta
- ID:             012
- Typ:            chore
- Status:         proposed
- Erstellt:       2026-10-28
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     README.md · docs/screenshot.webp · tickets/README.md · tickets/backlog/010_windows-packaging-and-ci.md · https://github.com/masrisystems/meeting-copilot

## Kontext
The public repo `masrisystems/meeting-copilot` has 0 stars and will be launched on r/LocalLLaMA, r/Python and Show HN. Most visitors can't run it (Windows + NVIDIA GPU only), so the README has to show the app working. Right now there's only a static screenshot. The repo's homepage field points to `masrisystems.com/brain` (the Echo format page), not to anything about Meeting Copilot. The interview mode ("read-aloud answers in job interviews") is likely to be read as cheating on HN/Reddit and can derail a launch thread.

## Ziel
A visitor who can't install the app still sees it working within 30 seconds, knows the requirements upfront, and reads it as a sales-call / translation / meeting-memory tool that runs locally.

## Anforderungen
- 30–60 s demo video or GIF of a real (or staged) call at the top of the README: live transcript, a suggestion card appearing, DE↔EN translation.
- Requirements line directly under the intro: "Windows 10/11 + NVIDIA GPU. CPU mode: see #010."
- Homepage field points to the repo or a Meeting Copilot page instead of `masrisystems.com/brain`.
- "Roadmap" section in the README linking `tickets/README.md`.
- Extra topics: `meeting-copilot`, `local-first`, `privacy`, `translation`, `claude`, `openai`, `sales`.
- Intro and feature order lead with client/sales calls, live translation, meeting memory and local processing; interview mode is described as "practice with your prep notes" or moved lower.
- Recording consent note in the README (participants are transcribed; local laws on call recording apply).

## Akzeptanzkriterien
- [ ] Demo video/GIF plays on the GitHub README page.
- [ ] `gh repo view masrisystems/meeting-copilot --json homepageUrl` no longer returns `masrisystems.com/brain`.
- [ ] Requirements and roadmap are visible without scrolling past the features list.
- [ ] The first screen of the README does not mention job interviews.

## Risiken
- A staged demo call can look fake; use a real call with consent or a clearly labelled demo.
- Without #010 (installer / CPU mode) many interested visitors can't try it; launch comments will ask for it.

## Offene Fragen
- Ship #010 (at least CPU mode) before the launch, or launch first and point to #010 as "next"?

## Erste Umsetzungsidee
Record a 2-person test call with OBS, cut it to 45 s, upload the MP4 by dragging it into a GitHub issue to get a hosted URL, and embed it in the README. Then `gh repo edit masrisystems/meeting-copilot --homepage https://github.com/masrisystems/meeting-copilot --add-topic local-first,...`.

## Kommentare
