"""FileMind - SQLite index of files.

Thread-safe: every public method opens a short-lived connection,
so the scanner thread and the UI thread never share one.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    path          TEXT NOT NULL UNIQUE,
    folder        TEXT NOT NULL,
    extension     TEXT NOT NULL,
    size          INTEGER NOT NULL,
    created_date  TEXT NOT NULL,
    modified_date TEXT NOT NULL,
    file_type     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_name      ON files(name);
CREATE INDEX IF NOT EXISTS idx_files_extension ON files(extension);
CREATE INDEX IF NOT EXISTS idx_files_type      ON files(file_type);
CREATE INDEX IF NOT EXISTS idx_files_folder    ON files(folder);
CREATE INDEX IF NOT EXISTS idx_files_size      ON files(size);
CREATE INDEX IF NOT EXISTS idx_files_modified  ON files(modified_date);
CREATE INDEX IF NOT EXISTS idx_files_name_size ON files(name, size);

CREATE TABLE IF NOT EXISTS apps (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name   TEXT NOT NULL,
    path   TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    icon   TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_apps_name ON apps(name);

CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    folder      TEXT NOT NULL,
    editor      TEXT DEFAULT 'code',
    description TEXT DEFAULT '',
    tags        TEXT DEFAULT '',
    last_opened TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS games (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    path       TEXT NOT NULL UNIQUE,
    source     TEXT NOT NULL,
    launch_cmd TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_games_name ON games(name);

CREATE TABLE IF NOT EXISTS command_history (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    command TEXT NOT NULL,
    action  TEXT NOT NULL,
    ran_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT    UNIQUE NOT NULL,
    name        TEXT    NOT NULL,
    file_type   TEXT    DEFAULT '',
    size        INTEGER DEFAULT 0,
    modified_at TEXT    DEFAULT '',
    indexed_at  TEXT    NOT NULL,
    chunk_count INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_docs_path ON documents(path);

CREATE TABLE IF NOT EXISTS document_chunks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id    INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_idx INTEGER NOT NULL,
    text      TEXT,
    embedding TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON document_chunks(doc_id);
"""

SELECT_COLS = """name, path, folder, extension, size,
                 created_date, modified_date, file_type"""


class Database:
    def __init__(self, db_path=None):
        self.db_path = str(db_path or config.DB_PATH)
        with self._connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    # ------------------------------------------------------------ writes
    def insert_many(self, rows):
        """rows: iterable of (name, path, folder, ext, size, created, modified, type)."""
        with self._connect() as con:
            con.executemany(
                """INSERT INTO files
                   (name, path, folder, extension, size,
                    created_date, modified_date, file_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                     name=excluded.name, folder=excluded.folder,
                     extension=excluded.extension, size=excluded.size,
                     created_date=excluded.created_date,
                     modified_date=excluded.modified_date,
                     file_type=excluded.file_type""",
                rows,
            )

    def update_path(self, old_path, new_path, new_folder):
        with self._connect() as con:
            con.execute(
                "UPDATE files SET path=?, folder=? WHERE path=?",
                (new_path, new_folder, old_path),
            )

    def remove_path(self, path):
        with self._connect() as con:
            con.execute("DELETE FROM files WHERE path=?", (path,))

    def clear(self):
        with self._connect() as con:
            con.execute("DELETE FROM files")

    # ------------------------------------------------------------ reads
    def query(self, sql, params=()):
        with self._connect() as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]

    def count(self):
        return self.query("SELECT COUNT(*) AS n FROM files")[0]["n"]

    def stats_by_type(self):
        return self.query(
            """SELECT file_type, COUNT(*) AS count, SUM(size) AS total_size
               FROM files GROUP BY file_type ORDER BY count DESC"""
        )

    def total_size(self):
        row = self.query("SELECT SUM(size) AS s FROM files")[0]
        return row["s"] or 0

    # ------------------------------------------------------------ apps
    def clear_apps(self):
        with self._connect() as con:
            con.execute("DELETE FROM apps")

    def insert_apps(self, rows):
        """rows: iterable of (name, path, source, icon)."""
        with self._connect() as con:
            con.executemany(
                """INSERT INTO apps (name, path, source, icon)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                     name=excluded.name, source=excluded.source,
                     icon=excluded.icon""",
                rows,
            )

    def all_apps(self):
        return self.query(
            "SELECT name, path, source, icon FROM apps ORDER BY name COLLATE NOCASE")

    def app_count(self):
        return self.query("SELECT COUNT(*) AS n FROM apps")[0]["n"]

    # ------------------------------------------------------------ projects
    def upsert_project(self, name, folder, editor="code",
                       description="", tags="", last_opened=""):
        with self._connect() as con:
            # ensure new columns exist (safe migration for existing DBs)
            for col, default in [("description", "''"), ("tags", "''"),
                                  ("last_opened", "''")]:
                try:
                    con.execute(
                        f"ALTER TABLE projects ADD COLUMN {col} TEXT DEFAULT {default}")
                except Exception:
                    pass   # column already exists
            con.execute(
                """INSERT INTO projects
                       (name, folder, editor, description, tags, last_opened)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     folder=excluded.folder, editor=excluded.editor,
                     description=excluded.description, tags=excluded.tags""",
                (name, folder, editor, description, tags, last_opened))

    def remove_project(self, name):
        """Removes only the registry entry - never touches the folder."""
        with self._connect() as con:
            con.execute("DELETE FROM projects WHERE name=?", (name,))

    def all_projects(self):
        return self.query(
            "SELECT name, folder, editor, description, tags, last_opened "
            "FROM projects ORDER BY last_opened DESC, name COLLATE NOCASE")

    def touch_project(self, name: str):
        """Update last_opened timestamp for a project."""
        from datetime import datetime as _dt
        ts = _dt.now().strftime("%Y-%m-%d %H:%M:%S")
        with self._connect() as con:
            con.execute(
                "UPDATE projects SET last_opened=? WHERE name=? COLLATE NOCASE",
                (ts, name))

    def recent_projects(self, limit=5):
        """Return projects ordered by last_opened (most recent first)."""
        return self.query(
            "SELECT name, folder, editor, description, tags, last_opened "
            "FROM projects WHERE last_opened != '' "
            "ORDER BY last_opened DESC LIMIT ?", (limit,))

    def project_count(self):
        return self.query("SELECT COUNT(*) AS n FROM projects")[0]["n"]

    # ------------------------------------------------------------ games
    def clear_games(self):
        with self._connect() as con:
            con.execute("DELETE FROM games")

    def insert_games(self, rows):
        """rows: iterable of (name, path, source, launch_cmd)."""
        with self._connect() as con:
            con.executemany(
                """INSERT INTO games (name, path, source, launch_cmd)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(path) DO UPDATE SET
                     name=excluded.name, source=excluded.source,
                     launch_cmd=excluded.launch_cmd""",
                rows)

    def all_games(self):
        return self.query(
            "SELECT name, path, source, launch_cmd FROM games "
            "ORDER BY name COLLATE NOCASE")

    def game_count(self):
        return self.query("SELECT COUNT(*) AS n FROM games")[0]["n"]

    # ------------------------------------------------------------ history
    def add_history(self, command, action):
        from datetime import datetime as _dt
        with self._connect() as con:
            con.execute(
                "INSERT INTO command_history (command, action, ran_at) "
                "VALUES (?, ?, ?)",
                (command, action, _dt.now().strftime("%Y-%m-%d %H:%M:%S")))
            # keep the table small
            con.execute(
                """DELETE FROM command_history WHERE id NOT IN
                   (SELECT id FROM command_history ORDER BY id DESC LIMIT 300)""")

    def recent_commands(self, limit=50):
        return self.query(
            "SELECT command, action, ran_at FROM command_history "
            "ORDER BY id DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------ dashboard
    def recent_count(self, days=7):
        cutoff = (datetime.now() - timedelta(days=days)).strftime(
            "%Y-%m-%d %H:%M:%S")
        return self.query(
            """SELECT COUNT(*) AS n FROM files
               WHERE file_type != 'Folders' AND modified_date >= ?""",
            (cutoff,))[0]["n"]

    def recent_files(self, limit=500):
        return self.query(
            f"""SELECT {SELECT_COLS} FROM files
                WHERE file_type != 'Folders'
                ORDER BY modified_date DESC LIMIT ?""", (limit,))

    def largest_files(self, limit=200):
        return self.query(
            f"""SELECT {SELECT_COLS} FROM files
                WHERE file_type != 'Folders'
                ORDER BY size DESC LIMIT ?""", (limit,))

    def duplicate_suspects(self, limit=500):
        """Files sharing the same name AND size (likely copies)."""
        return self.query(
            f"""SELECT f.name, f.path, f.folder, f.extension, f.size,
                       f.created_date, f.modified_date, f.file_type
                FROM files f
                JOIN (SELECT name, size FROM files
                      WHERE file_type != 'Folders' AND size > 0
                      GROUP BY name, size HAVING COUNT(*) > 1) d
                  ON f.name = d.name AND f.size = d.size
                ORDER BY f.size DESC, f.name LIMIT ?""", (limit,))

    def downloads_cleanup(self, downloads_path, limit=500):
        """Biggest files sitting in Downloads - prime cleanup targets."""
        return self.query(
            f"""SELECT {SELECT_COLS} FROM files
                WHERE folder LIKE ? AND file_type != 'Folders'
                ORDER BY size DESC LIMIT ?""",
            (f"{downloads_path}%", limit))

    def screenshots(self, limit=500):
        return self.query(
            f"""SELECT {SELECT_COLS} FROM files
                WHERE file_type = 'Images'
                  AND (name LIKE '%screenshot%' OR name LIKE '%screen shot%'
                       OR name LIKE '%capture%' OR name LIKE 'Screenshot%')
                ORDER BY modified_date DESC LIMIT ?""", (limit,))

    # ------------------------------------------------------------ AI documents
    def query_one(self, sql, params=()):
        """Return first result row as a dict, or None."""
        with self._connect() as con:
            row = con.execute(sql, params).fetchone()
            return dict(row) if row else None

    def upsert_document(self, path, name, file_type, size,
                        modified_at, indexed_at, chunk_count):
        """Insert or update a document record. Returns the document id."""
        with self._connect() as con:
            con.execute(
                """INSERT INTO documents
                       (path, name, file_type, size,
                        modified_at, indexed_at, chunk_count)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET
                     name=excluded.name, file_type=excluded.file_type,
                     size=excluded.size, modified_at=excluded.modified_at,
                     indexed_at=excluded.indexed_at,
                     chunk_count=excluded.chunk_count""",
                (path, name, file_type, size,
                 modified_at, indexed_at, chunk_count))
            return con.execute(
                "SELECT id FROM documents WHERE path=?", (path,)
            ).fetchone()["id"]

    def store_chunks(self, doc_id, chunks):
        """Replace all chunks for doc_id with new data.

        chunks: list of (chunk_idx, text, embedding_json_or_None)
        """
        with self._connect() as con:
            con.execute(
                "DELETE FROM document_chunks WHERE doc_id=?", (doc_id,))
            if chunks:
                con.executemany(
                    """INSERT INTO document_chunks
                           (doc_id, chunk_idx, text, embedding)
                       VALUES (?,?,?,?)""",
                    [(doc_id, ci, t, e) for ci, t, e in chunks])

    def stream_chunks(self, doc_id, chunk_iter) -> int:
        """Delete old chunks for doc_id, then stream new ones from an iterator.

        chunk_iter yields (chunk_idx, text) pairs.
        All inserts share one transaction — memory stays flat.
        Returns the total number of chunks written.
        """
        count = 0
        with self._connect() as con:
            con.execute(
                "DELETE FROM document_chunks WHERE doc_id=?", (doc_id,))
            for chunk_idx, text in chunk_iter:
                con.execute(
                    "INSERT INTO document_chunks "
                    "(doc_id, chunk_idx, text, embedding) VALUES (?,?,?,NULL)",
                    (doc_id, chunk_idx, text))
                count += 1
        return count

    def update_chunk_embedding(self, chunk_id: int, embedding_json: str):
        """Cache an embedding for a chunk that was embedded lazily."""
        with self._connect() as con:
            con.execute(
                "UPDATE document_chunks SET embedding=? WHERE id=?",
                (embedding_json, chunk_id))

    def doc_count(self):
        return self.query("SELECT COUNT(*) AS n FROM documents")[0]["n"]

    def chunk_count(self):
        return self.query(
            "SELECT COUNT(*) AS n FROM document_chunks")[0]["n"]

    def indexed_docs(self, limit=500):
        return self.query(
            "SELECT name, path, file_type, size, indexed_at, chunk_count "
            "FROM documents ORDER BY indexed_at DESC LIMIT ?", (limit,))
