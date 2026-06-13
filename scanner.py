"""FileMind - drive scanner.

Walks the configured drives in a background thread, batches rows into
SQLite, and reports progress via callbacks (safe to wire to the UI).
"""

import os
import threading
from datetime import datetime

import config
from database import Database

BATCH_SIZE = 500


class Scanner:
    def __init__(self, db: Database):
        self.db = db
        self._stop = threading.Event()
        self._thread = None
        self.files_indexed = 0

    # ------------------------------------------------------------ public
    def start(self, drives=None, on_progress=None, on_done=None):
        """Start scanning in a background thread.

        on_progress(files_indexed: int, current_path: str)
        on_done(files_indexed: int)
        """
        if self.is_running():
            return
        self._stop.clear()
        self.files_indexed = 0
        self._thread = threading.Thread(
            target=self._scan,
            args=(drives or config.DRIVES, on_progress, on_done),
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop.set()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------ internal
    def _scan(self, drives, on_progress, on_done):
        self.db.clear()  # full re-index
        batch = []
        for drive in drives:
            if not os.path.exists(drive):
                continue
            for root, dirs, files in os.walk(drive, topdown=True, onerror=lambda e: None):
                if self._stop.is_set():
                    break
                # prune excluded / hidden system folders in-place
                dirs[:] = [
                    d for d in dirs
                    if d.lower() not in config.EXCLUDED_DIRS and not d.startswith("$")
                ]
                # index folders too (category "Folders")
                for dname in dirs:
                    row = self._make_folder_row(root, dname)
                    if row:
                        batch.append(row)
                for fname in files:
                    row = self._make_row(root, fname)
                    if row:
                        batch.append(row)
                    if len(batch) >= BATCH_SIZE:
                        self._flush(batch)
                        if on_progress:
                            on_progress(self.files_indexed, root)
            if self._stop.is_set():
                break
        self._flush(batch)
        if on_done:
            on_done(self.files_indexed)

    def _make_row(self, root, fname):
        path = os.path.join(root, fname)
        try:
            st = os.stat(path)
        except OSError:
            return None
        ext = os.path.splitext(fname)[1].lower()
        return (
            fname,
            path,
            root,
            ext,
            st.st_size,
            datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
            datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            config.get_category(ext),
        )

    def _make_folder_row(self, root, dname):
        path = os.path.join(root, dname)
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (
            dname,
            path,
            root,
            "",
            0,
            datetime.fromtimestamp(st.st_ctime).strftime("%Y-%m-%d %H:%M:%S"),
            datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            config.FOLDER_CATEGORY,
        )

    def _flush(self, batch):
        if not batch:
            return
        try:
            self.db.insert_many(batch)
            self.files_indexed += len(batch)
        finally:
            batch.clear()
