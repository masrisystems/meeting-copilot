# Ticket: Knowledge base and meeting profiles

## Meta
- ID:             009
- Typ:            feature
- Status:         proposed
- Erstellt:       2026-10-23
- Erstellt von:   Mohamad Masri
- Verantwortlich: Mohamad Masri
- Referenzen:     copilot/rag.py · copilot/facts.py · copilot/prompts/system.md · examples/

## Kontext
The knowledge base reads `.md`/`.json` from `knowledge_base/` plus the extra paths in `KB_EXTRA_PATHS`, and has to be re-indexed by command. facts.json is global, so facts from one meeting leak into the next. The system prompt is written for client sales calls; other meeting types, such as job interviews or internal meetings at work, need a different role and rules.

## Ziel
Each meeting type or client has its own profile (knowledge sources, facts, system prompt), selected at the start of a session. Documents can be added in the UI in common formats.

## Anforderungen
- Profiles in `profiles/<name>/` containing `knowledge/` or a list of source paths, `facts.json` and `system.md`, e.g. `sales`, `job-interview`, `team-meeting`; profile picker in the UI; `--profile` CLI flag.
- PDF, DOCX, PPTX and HTML ingestion via MarkItDown into Markdown before chunking.
- Drag-and-drop upload in the UI with incremental re-indexing (no full rebuild).
- Sources shown on suggestion cards (which knowledge chunk was used).

## Akzeptanzkriterien
- [ ] Switching profile changes the facts, knowledge and prompt without a restart.
- [ ] A dropped PDF is searchable within 10 s.
- [ ] No facts are shared between profiles.
- [ ] A `job-interview` profile answers interviewer questions in first person from the candidate's prep notes.

## Risiken
- Large PDFs can make embedding (CPU fastembed) slow, so indexing should run in a background task with progress.

## Offene Fragen
- Is a global "company" knowledge base shared by all profiles also needed?

## Erste Umsetzungsidee
Make config paths profile-relative. Add one LanceDB table per profile, and upsert by (source, chunk_index).

## Kommentare
