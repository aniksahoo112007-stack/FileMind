"""FileMind AI Service - local Ollama semantic search. READ-ONLY.

Connects to Ollama at http://localhost:11434.
Primary embedding model : nomic-embed-text  (lightweight, fast)
Chat/understanding model: qwen2.5:7b

Falls back to keyword search when Ollama is unavailable.
Never modifies any file.
"""

import json
import math
from urllib import request as _req

# ── configuration ─────────────────────────────────────────────────────────────
OLLAMA_BASE  = "http://localhost:11434"
EMBED_MODEL  = "nomic-embed-text"   # best lightweight embedding model
CHAT_MODEL   = "qwen2.5:7b"         # used for query understanding (optional)
MAX_RESULTS  = 20


# ── low-level Ollama HTTP client ──────────────────────────────────────────────

def _post(endpoint: str, payload: dict, timeout: int = 30) -> dict:
    """POST JSON to Ollama and return parsed response. Raises on error."""
    url  = f"{OLLAMA_BASE}{endpoint}"
    data = json.dumps(payload).encode()
    req  = _req.Request(url, data=data,
                        headers={"Content-Type": "application/json"})
    with _req.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def ollama_available() -> bool:
    """Return True if the Ollama daemon is reachable (3-second timeout)."""
    try:
        _req.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=3)
        return True
    except Exception:
        return False


def list_models() -> list:
    """Return names of locally available Ollama models."""
    try:
        result = _post("/api/tags", {})
        return [m["name"] for m in result.get("models", [])]
    except Exception:
        return []


def embed(text: str, model: str = EMBED_MODEL):
    """Get embedding vector for text.

    Tries the new /api/embed endpoint (Ollama ≥ 0.5) then falls back
    to the legacy /api/embeddings endpoint.

    Returns list[float] or None on failure.
    """
    text = (text or "").strip()
    if not text:
        return None

    # new API  (/api/embed, Ollama ≥ 0.5)
    try:
        r    = _post("/api/embed", {"model": model, "input": text})
        vecs = r.get("embeddings")
        if vecs:
            return vecs[0]
    except Exception:
        pass

    # legacy API  (/api/embeddings)
    try:
        r = _post("/api/embeddings", {"model": model, "prompt": text})
        return r.get("embedding")
    except Exception:
        return None


# ── vector maths ──────────────────────────────────────────────────────────────

def cosine(a: list, b: list) -> float:
    """Cosine similarity between two equal-length vectors."""
    dot = sum(x * y for x, y in zip(a, b))
    ma  = math.sqrt(sum(x * x for x in a))
    mb  = math.sqrt(sum(x * x for x in b))
    return dot / (ma * mb) if ma and mb else 0.0


# ── semantic search ───────────────────────────────────────────────────────────

def semantic_search(db, query: str, model: str = EMBED_MODEL,
                    limit: int = MAX_RESULTS) -> list:
    """Embed query and rank documents by cosine similarity.

    Chunks that have no stored embedding are embedded lazily on-the-fly
    and cached back to SQLite so repeat searches are faster.

    Returns a list of dicts:
        {name, path, file_type, score (0–1), snippet}
    sorted highest-score first.
    """
    q_vec = embed(query, model)
    if q_vec is None:
        return []

    # fetch all chunks (with or without pre-computed embeddings)
    rows = db.query(
        """SELECT dc.id, dc.doc_id, dc.text, dc.embedding,
                  d.name, d.path, d.file_type
           FROM document_chunks dc
           JOIN documents d ON dc.doc_id = d.id""")

    best: dict = {}          # doc_id → best-scoring result so far
    for row in rows:
        c_vec = None

        # use cached embedding if available
        if row["embedding"]:
            try:
                c_vec = json.loads(row["embedding"])
            except Exception:
                pass

        # embed lazily and cache for next time
        if c_vec is None:
            c_vec = embed(row["text"] or "", model)
            if c_vec:
                try:
                    db.update_chunk_embedding(row["id"], json.dumps(c_vec))
                except Exception:
                    pass

        if not c_vec:
            continue

        score  = cosine(q_vec, c_vec)
        doc_id = row["doc_id"]
        if doc_id not in best or score > best[doc_id]["score"]:
            best[doc_id] = {
                "name":      row["name"],
                "path":      row["path"],
                "file_type": row["file_type"],
                "score":     score,
                "snippet":   (row["text"] or "")[:200],
            }

    results = sorted(best.values(), key=lambda r: r["score"], reverse=True)
    return results[:limit]


# ── keyword fallback (no Ollama required) ─────────────────────────────────────

def keyword_search(db, query: str, limit: int = MAX_RESULTS) -> list:
    """Simple TF-style keyword search over indexed document chunks.

    Works even when Ollama is offline. Uses chunk text stored in SQLite.
    Returns same dict format as semantic_search, with score = hit_ratio.
    """
    terms = [t for t in query.lower().split() if len(t) >= 3]
    if not terms:
        return []

    rows = db.query(
        """SELECT d.name, d.path, d.file_type, dc.text
           FROM document_chunks dc
           JOIN documents d ON dc.doc_id = d.id""")

    best: dict = {}          # path → best result
    for row in rows:
        text = (row["text"] or "").lower()
        hits = sum(1 for t in terms if t in text)
        if hits == 0:
            continue
        score = hits / len(terms)
        path  = row["path"]
        if path not in best or score > best[path]["score"]:
            best[path] = {
                "name":      row["name"],
                "path":      path,
                "file_type": row["file_type"],
                "score":     score,
                "snippet":   "",
            }

    results = sorted(best.values(), key=lambda r: r["score"], reverse=True)
    return results[:limit]
