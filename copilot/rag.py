"""Local knowledge base: LanceDB vector index over knowledge_base/ plus keyword flashcards.

Port of legacy/indexer.js + legacy/retriever.js (same chunking, same FAQ handling,
same German embedding model, via fastembed/ONNX instead of transformers.js).

    python -m copilot.rag index [--watch]
    python -m copilot.rag search "Was kostet eine Website?"
    python -m copilot.rag download   (fetch the embedding model, used by the installers)
"""

import asyncio
import json
import sys

from . import config

CHUNK_SIZE = 500
CHUNK_OVERLAP = 50
_SEPARATORS = ["\n\n", "\n", " ", ""]

_embedder = None


def _embed(texts):
    global _embedder
    if _embedder is None:
        import warnings

        from fastembed import TextEmbedding

        # Harmless notice that the model now runs in fp32 instead of fp16.
        warnings.filterwarnings("ignore", message=".*now it runs with the original fp32 model")

        try:
            _embedder = TextEmbedding(config.EMBEDDING_MODEL, local_files_only=True)
        except Exception:
            _embedder = TextEmbedding(config.EMBEDDING_MODEL)  # first run: download
    return [v.tolist() for v in _embedder.embed(texts)]


def split_text(text, size=CHUNK_SIZE, overlap=CHUNK_OVERLAP, seps=_SEPARATORS):
    """Recursive character splitter (same idea as LangChain's): split on the coarsest
    separator that works, then merge pieces back up to `size` with `overlap`."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []
    sep = next((s for s in seps if s and s in text), "")
    pieces = text.split(sep) if sep else list(text)
    rest = seps[seps.index(sep) + 1:] if sep in seps else [""]

    parts = []
    for p in pieces:
        parts.extend(split_text(p, size, overlap, rest) if len(p) > size else [p])

    chunks, cur = [], ""
    for p in parts:
        candidate = f"{cur}{sep}{p}" if cur else p
        if len(candidate) <= size:
            cur = candidate
            continue
        if cur.strip():
            chunks.append(cur.strip())
        tail = cur[-overlap:] if overlap else ""
        cur = f"{tail}{sep}{p}" if tail and len(tail) + len(sep) + len(p) <= size else p
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def _json_texts(data):
    faq = data.get("faq", {}).get("list") if isinstance(data, dict) else None
    if faq:
        texts = []
        for item in (faq.values() if isinstance(faq, dict) else faq):
            text = f"Frage: {item.get('question', '')}\nAntwort: "
            for ans in item.get("answer", []) if isinstance(item.get("answer"), list) else []:
                if ans.get("type") == "paragraph":
                    text += ans.get("content", "") + "\n"
                if ans.get("type") == "list" and isinstance(ans.get("items"), list):
                    text += "\n".join(ans["items"]) + "\n"
            texts.append(text.strip())
        return texts

    def strings(o):
        if isinstance(o, str):
            return [o]
        vals = o.values() if isinstance(o, dict) else o if isinstance(o, list) else []
        return [s for v in vals for s in strings(v)]

    return split_text("\n".join(strings(data)))


def _sources():
    files = []
    for root in [config.KB_DIR, *config.KB_EXTRA_PATHS]:
        if root.is_file():
            files.append(root)
        elif root.is_dir():
            files += sorted(p for p in root.rglob("*") if p.is_file())
        else:
            print(f"Knowledge source not found: {root}")
    return [p for p in files if p.suffix in (".md", ".json")]


def _signature(files):
    """Changes when a file or the embedding model changes."""
    return [config.EMBEDDING_MODEL] + [[str(p), p.stat().st_mtime_ns, p.stat().st_size] for p in files]


def index():
    import lancedb

    files = _sources()
    rows = []
    for path in files:
        content = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            try:
                texts = _json_texts(json.loads(content))
            except json.JSONDecodeError as e:
                print(f"Error parsing JSON in {path.name}: {e}")
                continue
        else:
            texts = split_text(content)
        print(f"{path.name}: {len(texts)} chunks")
        for i, (text, vec) in enumerate(zip(texts, _embed(texts))):
            rows.append({"vector": vec, "text": text, "source": path.name, "chunk_index": i})

    db = lancedb.connect(str(config.DB_DIR))
    if rows:
        db.create_table(config.DB_TABLE, rows, mode="overwrite")
        print(f"Indexed {len(rows)} chunks into {config.DB_DIR}/{config.DB_TABLE}")
    else:
        db.drop_table(config.DB_TABLE, ignore_missing=True)  # all notes removed: no stale answers
    (config.DB_DIR / "sources.json").write_text(json.dumps(_signature(files)), encoding="utf-8")


def sync():
    """Re-index only when the knowledge files changed since the last index (also while the app was closed).
    Returns the number of knowledge files."""
    files = _sources()
    try:
        last = json.loads((config.DB_DIR / "sources.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = None
    if last != _signature(files):
        index()
    return len(files)


class KnowledgeBase:
    """Used in-process by the assistant. Missing index or flashcards just yield nothing."""

    def __init__(self):
        self.reload()

    def reload(self):
        self.table = None
        try:
            import lancedb

            self.table = lancedb.connect(str(config.DB_DIR)).open_table(config.DB_TABLE)
        except Exception:
            pass  # no notes yet
        self.flashcards = []
        if config.FLASHCARDS_FILE.exists():
            self.flashcards = json.loads(config.FLASHCARDS_FILE.read_text(encoding="utf-8")).get("flashcards", [])

    def search(self, query, k=config.RAG_TOP_K):
        if not self.table or not query.strip():
            return []
        try:
            return self.table.search(_embed([query])[0]).limit(k).to_list()
        except Exception as e:  # index replaced mid-query by a re-index: skip knowledge this once
            print(f"Knowledge search failed: {e}")
            return []

    def context(self, query):
        lower = query.lower()
        lines = [f"[FLASHCARD {c['keyword']}]: {c['content']}" for c in self.flashcards
                 if c["keyword"].lower() in lower]
        lines += [f"[{r['source']}]: {r['text']}" for r in self.search(query)]
        return "\n\n".join(lines) or "(keine Treffer)"


async def watch(kb, emit):
    """In the app: bring the index up to date, then re-index on every change, so edits apply
    without a restart. Emits {"type": "kb", "files": n} (0 = show the first-run hint)."""
    from watchfiles import awatch

    config.KB_DIR.mkdir(exist_ok=True)
    emit({"type": "kb", "files": await asyncio.to_thread(sync), "dir": str(config.KB_DIR)})
    kb.reload()
    await asyncio.to_thread(kb.context, "warmup")  # loads the embedder once the index is current
    roots = [p for p in [config.KB_DIR, *config.KB_EXTRA_PATHS] if p.exists()]
    async for changes in awatch(*roots, debounce=1000):
        if any(p.endswith((".md", ".json")) for _, p in changes):
            files = await asyncio.to_thread(sync)
            kb.reload()
            emit({"type": "kb", "files": files, "dir": str(config.KB_DIR)})
            print(f"Knowledge base re-indexed ({files} files).")


def _watch():
    from watchfiles import watch

    index()
    roots = [p for p in [config.KB_DIR, *config.KB_EXTRA_PATHS] if p.exists()]
    print(f"Watching {', '.join(map(str, roots))} ...")
    for changes in watch(*roots, debounce=1000):
        if any(p.endswith((".md", ".json")) for _, p in changes):
            index()


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["index"]:
        _watch() if "--watch" in args else index()
    elif args[:1] == ["download"]:
        _embed(["warmup"])
    elif args[:1] == ["search"] and len(args) > 1:
        for i, r in enumerate(KnowledgeBase().search(" ".join(args[1:]))):
            print(f"--- Result {i + 1} (distance {r['_distance']:.4f}) {r['source']} #{r['chunk_index']}")
            print(r["text"], "\n")
    else:
        print(__doc__)
