"""FileMind - smart search over the SQLite index."""

import os
import subprocess

from database import Database

MAX_RESULTS = 500


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
        """Free-text search. '.pdf' style queries match by extension,
        otherwise match name or folder."""
        text = (text or "").strip()
        if not text:
            return []
        if text.startswith(".") and " " not in text:
            return self.search(extension=text, limit=limit)
        return self.db.query(
            """SELECT name, path, folder, extension, size,
                      created_date, modified_date, file_type
               FROM files
               WHERE name LIKE ? OR folder LIKE ?
               ORDER BY modified_date DESC LIMIT ?""",
            (f"%{text}%", f"%{text}%", limit),
        )

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
