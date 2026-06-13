"""FileMind Document Indexer - text extraction pipeline. READ-ONLY.

Scans the files table for supported documents (.txt .md .pdf .docx),
extracts plain text, splits into chunks, and stores them in SQLite.

NO Ollama / embeddings are generated here. Embedding happens lazily
when the user runs AI Search, so indexing is fast and memory-light.

SAFETY: This module NEVER writes to, moves, renames, or deletes any
        indexed file. All operations are strictly read-only on user data.

Skip rules: node_modules, AppData, .git, .venv, __pycache__,
            browser caches, and files larger than 25 MB.
"""

import os
import threading
import time
from datetime import datetime
from pathlib import Path

SUPPORTED_EXTS  = {".txt", ".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg"}
CHUNK_SIZE      = 800      # characters per chunk
CHUNK_OVERLAP   = 100      # overlap so context isn't lost at boundaries
MAX_FILE_BYTES  = 25 * 1024 * 1024   # 25 MB hard cap
PDF_MAX_PAGES   = 20       # never read more than 20 pages per PDF

# Directory names that are never worth indexing
_SKIP_DIR_NAMES = {
    "node_modules", "__pycache__", ".git", ".svn", ".hg",
    ".venv", "venv", "env", ".env",
    "AppData", "Application Data",
    "cache", "Cache", "Caches",
    "GPUCache", "Code Cache", "CachedData",
    "Temp", "tmp", "temp",
    ".mypy_cache", ".pytest_cache", ".tox",
    "dist", "build", ".next", ".nuxt",
    "site-packages", "dist-packages",
}

# Path substrings that signal a cache / junk folder (case-insensitive check)
_SKIP_PATH_PARTS = {
    "\\appdata\\local\\temp",
    "\\appdata\\local\\microsoft",
    "\\appdata\\roaming\\microsoft",
    "/appdata/local/temp",
    "\\google\\chrome\\user data",
    "\\mozilla\\firefox\\profiles",
    "\\edge\\user data",
}


def _should_skip(path: str) -> bool:
    """Return True if this path is inside a folder that should be skipped."""
    parts = Path(path).parts
    # check every directory component against the skip-name set
    for part in parts[:-1]:   # exclude the filename itself
        if part in _SKIP_DIR_NAMES:
            return True
    # check for known cache path patterns
    low = path.lower()
    for fragment in _SKIP_PATH_PARTS:
        if fragment in low:
            return True
    return False


# ── text extraction ───────────────────────────────────────────────────────────

def _read_txt(path: str) -> str:
    for enc in ("utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            with open(path, encoding=enc) as fh:
                return fh.read()
        except (UnicodeDecodeError, LookupError):
            continue
    return ""


def _read_pdf(path: str) -> str:
    """Extract text from PDF (first PDF_MAX_PAGES pages only)."""
    # pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            pages = pdf.pages[:PDF_MAX_PAGES]
            return "\n".join(p.extract_text() or "" for p in pages)
    except ImportError:
        pass
    except Exception:
        pass

    # pdfminer.six
    try:
        from pdfminer.high_level import extract_text_to_fp
        from pdfminer.layout import LAParams
        import io
        buf = io.StringIO()
        with open(path, "rb") as f:
            extract_text_to_fp(
                f, buf,
                page_numbers=list(range(PDF_MAX_PAGES)),
                laparams=LAParams())
        return buf.getvalue()
    except ImportError:
        pass
    except Exception:
        pass

    # PyMuPDF
    try:
        import fitz
        doc = fitz.open(path)
        pages = list(doc)[:PDF_MAX_PAGES]
        return "\n".join(page.get_text() for page in pages)
    except ImportError:
        pass
    except Exception:
        pass

    return ""


def _read_docx(path: str) -> str:
    try:
        import docx
        return "\n".join(p.text for p in docx.Document(path).paragraphs)
    except ImportError:
        return ""
    except Exception:
        return ""


def _read_image(path: str) -> str:
    """Extract text from PNG/JPG/JPEG via OCR (pytesseract).

    Returns '' when pytesseract is not installed or the image has no text.
    Never raises — all errors are silently swallowed.
    """
    try:
        import pytesseract
        from PIL import Image
        img = Image.open(path)
        text = pytesseract.image_to_string(img, timeout=15)
        return text or ""
    except ImportError:
        return ""   # tesseract / pillow not installed — skip silently
    except Exception:
        return ""


def extract_text(path: str) -> str:
    """Extract plain text from a supported file. Returns '' on failure."""
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return ""
        ext = Path(path).suffix.lower()
        if ext in (".txt", ".md"):
            return _read_txt(path)
        if ext == ".pdf":
            return _read_pdf(path)
        if ext == ".docx":
            return _read_docx(path)
        if ext in (".png", ".jpg", ".jpeg"):
            return _read_image(path)
    except Exception:
        pass
    return ""


# ── chunking (generator, no large list in memory) ─────────────────────────────

def iter_chunks(text: str,
                size: int = CHUNK_SIZE,
                overlap: int = CHUNK_OVERLAP):
    """Yield overlapping fixed-size text chunks one at a time."""
    text = " ".join(text.split())   # normalise whitespace
    if not text:
        return
    start = 0
    idx   = 0
    while start < len(text):
        yield idx, text[start : start + size]
        idx   += 1
        start += size - overlap


# ── background indexer ────────────────────────────────────────────────────────

class DocumentIndexer:
    """Background text indexer. No Ollama calls — pure extraction. READ-ONLY."""

    def __init__(self, db):
        self._db     = db
        self._thread = None
        self._stop   = threading.Event()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, model=None, on_progress=None, on_done=None):
        """Start background indexing.

        model is accepted for API compatibility but ignored (no embedding here).

        Callbacks (called on the indexer thread – use after() for UI):
          on_progress(count, filename, files_per_sec)
          on_done(total_indexed)
        """
        if self.is_running():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            args=(on_progress, on_done),
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    # ── internals ─────────────────────────────────────────────────────────────

    def _run(self, on_progress, on_done):
        candidates = self._db.query(
            "SELECT path, name, file_type, size, modified_date FROM files "
            "WHERE extension IN ('.txt','.md','.pdf','.docx','.png','.jpg','.jpeg') "
            "ORDER BY size ASC")

        indexed   = 0
        skipped   = 0
        t_start   = time.monotonic()
        batch_num = 0   # track batches of 20 for periodic GC hint

        for row in candidates:
            if self._stop.is_set():
                break

            path = row["path"]

            # ── skip rules ────────────────────────────────────────────────
            if not os.path.isfile(path):
                continue
            if _should_skip(path):
                skipped += 1
                continue
            try:
                if os.path.getsize(path) > MAX_FILE_BYTES:
                    skipped += 1
                    continue
            except OSError:
                continue

            # skip already-indexed and unmodified files
            mod      = row["modified_date"] or ""
            existing = self._db.query_one(
                "SELECT indexed_at, chunk_count FROM documents WHERE path=?",
                (path,))
            if existing and existing.get("chunk_count", 0) > 0 \
                    and existing.get("indexed_at", "") >= mod:
                continue

            # ── progress report ───────────────────────────────────────────
            if on_progress:
                elapsed = time.monotonic() - t_start
                fps     = indexed / elapsed if elapsed > 0.1 else 0.0
                on_progress(indexed, row["name"], fps)

            # ── extract + store ───────────────────────────────────────────
            try:
                text = extract_text(path)
                if not text or not text.strip():
                    del text
                    continue

                # count chunks without materialising the full list
                chunk_gen   = iter_chunks(text)
                chunk_count = 0
                # peek to count — we iterate once for count, once for storage
                # instead, iterate once and stream directly to DB
                del text    # free extraction memory before DB write

                text2 = extract_text(path)   # second read for streaming
                if not text2:
                    continue

                now    = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # upsert with chunk_count=0 first, update after streaming
                doc_id = self._db.upsert_document(
                    path, row["name"], row["file_type"] or "",
                    row["size"] or 0, mod, now, 0)

                # stream chunks directly into DB — no big list in memory
                chunk_count = self._db.stream_chunks(
                    doc_id, iter_chunks(text2))

                del text2   # free memory immediately

                # update chunk_count now we know it
                self._db.upsert_document(
                    path, row["name"], row["file_type"] or "",
                    row["size"] or 0, mod, now, chunk_count)

                if chunk_count > 0:
                    indexed += 1
                    batch_num += 1

            except Exception:
                continue   # silently skip unreadable files

        if on_done:
            on_done(indexed)
