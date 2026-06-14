"""FileMind - smart search over the SQLite index.

Free-text matching is RANKED (scoring), not exact: it tolerates partial words,
word-order changes, voice/typing mistakes (fuzzy), and multi-token queries.
Read-only: every method only runs SELECTs.
"""

import difflib
import os
import re
import subprocess
from itertools import combinations

from database import Database

MAX_RESULTS = 500
_CANDIDATE_CEILING = 120000   # crash-guard only; realistic queries match far fewer
_EXT_BROWSE_LIMIT = 20000     # cap for "list every file of a type" queries

# words that also imply a file extension (used as a soft ranking bonus, never
# as a hard filter — so "python notes" still matches by name, not just .py).
_EXT_HINTS = {
    "pdf": [".pdf"], "doc": [".doc", ".docx"], "docx": [".docx"],
    "word": [".doc", ".docx"], "txt": [".txt"], "text": [".txt", ".md"],
    "md": [".md"], "note": [".txt", ".md", ".pdf"],
    "notes": [".txt", ".md", ".pdf"],
    "ppt": [".ppt", ".pptx"], "pptx": [".pptx"], "slides": [".ppt", ".pptx"],
    "excel": [".xls", ".xlsx"], "xls": [".xls", ".xlsx"], "xlsx": [".xlsx"],
    "csv": [".csv"], "image": [".jpg", ".jpeg", ".png"],
    "images": [".jpg", ".jpeg", ".png"], "photo": [".jpg", ".jpeg", ".png"],
    "jpg": [".jpg", ".jpeg"], "jpeg": [".jpeg"], "png": [".png"],
    "py": [".py"], "python": [".py"], "js": [".js"], "java": [".java"],
    "html": [".html", ".htm"], "css": [".css"], "json": [".json"],
    "video": [".mp4", ".mkv", ".mov"], "mp4": [".mp4"],
    "music": [".mp3", ".wav"], "song": [".mp3"], "mp3": [".mp3"],
    "zip": [".zip", ".rar", ".7z"], "exe": [".exe"], "apk": [".apk"],
}

_SELECT = """name, path, folder, extension, size,
             created_date, modified_date, file_type"""

# Tokens that are literal file extensions — these appear as the ".ext" suffix in
# most filenames, so using them for NAME candidate generation would flood the
# results with every file of that type. They are used as an extension bonus
# only (never to generate candidates). Format *words* like "python"/"notes"
# are NOT here, so they still match by name.
_LITERAL_EXTS = {
    "pdf", "doc", "docx", "txt", "rtf", "odt", "xls", "xlsx", "csv", "ppt",
    "pptx", "md", "png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "ico",
    "mp4", "mkv", "mov", "avi", "wmv", "webm", "mp3", "wav", "flac", "aac",
    "zip", "rar", "7z", "tar", "gz", "iso", "exe", "msi", "apk", "dmg",
    "py", "js", "ts", "html", "htm", "css", "json", "xml", "yaml", "yml",
    "java", "cpp", "c", "h", "cs", "go", "rs", "php", "rb", "sql", "sh",
    "bat", "ipynb", "dll", "log",
}


def _tokens(text):
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _trigram_clause(token):
    """SQL fragment matching names that share >=2 trigrams with `token`
    (typo tolerance done in the DB, so we never scan the whole index in
    Python). Returns (sql_fragment, params) or (None, []) for short tokens."""
    if len(token) < 4:
        return None, []
    mid = len(token) // 2
    tris = []
    for tri in (token[0:3], token[mid - 1:mid + 2], token[-3:]):
        if len(tri) == 3 and tri not in tris:
            tris.append(tri)
    if len(tris) < 2:
        return None, []
    parts, params = [], []
    for a, b in combinations(tris, 2):
        parts.append("(LOWER(name) LIKE ? AND LOWER(name) LIKE ?)")
        params += [f"%{a}%", f"%{b}%"]
    return "(" + " OR ".join(parts) + ")", params


class SearchEngine:
    def __init__(self, db: Database):
        self.db = db

    def search(self, name=None, extension=None, file_type=None,
               folder=None, date_from=None, date_to=None, limit=MAX_RESULTS):
        """All filters optional and combinable. Dates are 'YYYY-MM-DD'."""
        sql = """SELECT name, path, folder, extension, size,
                        created_date, modified_date, file_type
                 FROM files WHERE 1=1"""
        params = []

        if name:
            sql += " AND name LIKE ?"
            params.append(f"%{name}%")
        if extension:
            ext = extension if extension.startswith(".") else "." + extension
            sql += " AND extension = ?"
            params.append(ext.lower())
        if file_type:
            sql += " AND file_type = ?"
            params.append(file_type)
        if folder:
            sql += " AND folder LIKE ?"
            params.append(f"%{folder}%")
        if date_from:
            sql += " AND modified_date >= ?"
            params.append(date_from + " 00:00:00")
        if date_to:
            sql += " AND modified_date <= ?"
            params.append(date_to + " 23:59:59")

        sql += " ORDER BY modified_date DESC LIMIT ?"
        params.append(limit)
        return self.db.query(sql, params)

    def quick_search(self, text, limit=MAX_RESULTS):
        """Free-text search — now RANKED (fuzzy, token, word-order tolerant)."""
        return self.ranked_search(text, limit)

    # ------------------------------------------------------------ ranking
    def ranked_search(self, query, limit=MAX_RESULTS):
        """Tokenised, fuzzy, scored search over the ENTIRE index — no fixed
        4000 cap. Stays fast because candidate generation is bounded by name
        relevance (token + trigram-fuzzy in SQL), not by file count.

        Stages:  1) token/substring match  2) trigram fuzzy match
                 3) location weighting      4) final score sort
        Handles exact / case-insensitive / partial / token / multi-token /
        fuzzy (typo) / word-order-independent matching.
        """
        q = (query or "").strip().lower()
        tokens = _tokens(q)
        if not tokens:
            return []

        exts = set()
        for t in tokens:
            if t in _EXT_HINTS:
                exts.update(_EXT_HINTS[t])
        # content tokens drive candidate generation; literal extensions do not
        content = [t for t in tokens if t not in _LITERAL_EXTS]

        # Query is only file-type words ("pdf", "find png") → list that type.
        if not content:
            return self._by_extension(exts, limit) if exts else []

        # ── Stages 1+2: candidate generation in SQL (token OR trigram) ──
        clauses, params = [], []
        for t in content:
            sub = []
            if len(t) >= 2:
                sub.append("LOWER(name) LIKE ?")
                params.append(f"%{t}%")
            frag, fp = _trigram_clause(t)
            if frag:
                sub.append(frag)
                params += fp
            if sub:
                clauses.append("(" + " OR ".join(sub) + ")")
        if not clauses:
            return []
        where = " OR ".join(clauses)
        sql = (f"SELECT {_SELECT} FROM files "
               f"WHERE file_type != 'Folders' AND ({where}) "
               f"LIMIT {_CANDIDATE_CEILING}")
        candidates = self.db.query(sql, params)

        # ── Stages 3+4: score (token + fuzzy + location) and rank ──
        scored = []
        for row in candidates:
            s = self._score(row, q, tokens, exts)
            if s > 0:
                scored.append((s, row))
        scored.sort(key=lambda sr: sr[0], reverse=True)
        return [row for _s, row in scored[:limit]]

    def _by_extension(self, exts, limit):
        """Browse every file of a given type, user files ranked first."""
        placeholders = ",".join("?" * len(exts))
        rows = self.db.query(
            f"SELECT {_SELECT} FROM files "
            f"WHERE file_type != 'Folders' AND LOWER(extension) IN "
            f"({placeholders}) LIMIT {_EXT_BROWSE_LIMIT}",
            [e.lower() for e in exts])

        def keyfn(r):
            path = (r["path"] or "").lower()
            if any(m in path for m in self._SYS_MARKERS):
                loc = 0
            elif any(m in path for m in self._USER_MARKERS):
                loc = 2
            else:
                loc = 1
            return (loc, r.get("modified_date", ""))
        rows.sort(key=keyfn, reverse=True)
        return rows[:limit]

    # path fragments that mark system / IDE / dependency noise (de-ranked) and
    # user-content locations (boosted). Matched as lowercase substrings.
    _SYS_MARKERS = (
        "program files", "\\windows\\", "appdata", "\\.vscode", "node_modules",
        "\\.rustup", "\\.cargo", "\\.codex", "site-packages", "pycharm",
        "\\.bubblewrap", "\\helpers\\", "\\stubs\\", "\\.git\\", "\\.gradle",
        "\\.nuget", "programdata", "\\lib\\", "\\dist\\", "\\build\\",
        "anaconda", "miniconda",
    )
    _USER_MARKERS = ("download", "document", "desktop", "pictures",
                     "onedrive\\")

    @staticmethod
    def _score(row, full_q, tokens, exts):
        """Score one row. Higher = better.

        exact name +100 · full-substring +60 · token +50 · partial +25 ·
        fuzzy +20/35 · extension +30 · all-tokens +40 · unmatched-token −20 ·
        user-folder +25 · system/IDE folder −45.
        """
        name = (row["name"] or "").lower()
        path = (row["path"] or "").lower()
        score = 0
        if name == full_q:
            score += 100
        if full_q in name:
            score += 60
        name_tokens = _tokens(name)
        nt_set = set(name_tokens)
        matched = 0
        for idx, t in enumerate(tokens):
            pos = (len(tokens) - 1 - idx) * 3   # earlier words = primary intent
            if t in nt_set:
                score += 50 + pos
                matched += 1
            elif any((len(t) >= 4 and t in nt) or (len(nt) >= 4 and nt in t)
                     for nt in name_tokens):
                score += 25 + pos              # partial only for >=4-char words
                matched += 1
            elif len(t) >= 4:                  # fuzzy only for longer words
                best = max((difflib.SequenceMatcher(None, t, nt).ratio()
                            for nt in name_tokens if len(nt) >= 4), default=0.0)
                if best >= 0.84:
                    score += 35 + pos
                    matched += 1
                elif best >= 0.78:
                    score += 20 + pos
                    matched += 1
        if exts and (row["extension"] or "").lower() in exts:
            score += 30
        # multi-token coverage: reward all-matched, penalise missing words
        if tokens:
            if matched == len(tokens):
                score += 40
            else:
                score -= 20 * (len(tokens) - matched)
        # location relevance: bury system/IDE/dependency files, lift user files
        if any(m in path for m in SearchEngine._SYS_MARKERS):
            score -= 45
        elif any(m in path for m in SearchEngine._USER_MARKERS):
            score += 25
        return score

    # ------------------------------------------------------------ actions
    @staticmethod
    def open_file(path):
        if os.path.exists(path):
            os.startfile(path)  # Windows default app
            return True
        return False

    @staticmethod
    def open_folder(path):
        """Open Explorer with the file selected (or the folder itself)."""
        if os.path.isfile(path):
            subprocess.Popen(["explorer", "/select,", path])
            return True
        if os.path.isdir(path):
            os.startfile(path)
            return True
        return False
