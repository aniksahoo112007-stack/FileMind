"""FileMind - project memory system (read-only towards files).

Stores project name -> folder -> editor -> description/tags/last_opened in SQLite.
"continue filemind" / "open my pos project" opens the folder in Explorer AND
launches the editor. last_opened is updated every time a project is opened.

Only registry rows are ever written — project folders are NEVER touched.
"""

import os
import shutil
import subprocess
from datetime import datetime

from database import Database
from launcher import fuzzy_score

PROJECT_FUZZY_THRESHOLD = 45

EDITOR_COMMANDS = {
    "code":    ["code", "code.cmd"],
    "notepad": ["notepad.exe", "notepad"],
    "pycharm": ["pycharm64.exe", "pycharm"],
    "sublime": ["subl", "sublime_text"],
    "idea":    ["idea64.exe", "idea"],
}


class ProjectRegistry:
    def __init__(self, db: Database):
        self.db = db

    # ---- registry -------------------------------------------------------
    def add(self, name, folder, editor="code", description="", tags=""):
        name   = (name   or "").strip()
        folder = (folder or "").strip()
        if not name or not folder:
            return False, "Project needs a name and a folder."
        if not os.path.isdir(folder):
            return False, f"Folder does not exist: {folder}"
        self.db.upsert_project(name, folder, editor or "code", description, tags)
        return True, f'Project "{name}" saved — folder: {folder}'

    def remove(self, name):
        self.db.remove_project(name)
        return True, f'Project "{name}" removed from registry (folder untouched).'

    def all(self):
        return self.db.all_projects()

    def recent(self, limit=5):
        return self.db.recent_projects(limit)

    def best(self, query):
        best, best_score = None, 0
        for p in self.all():
            s = fuzzy_score(query, p["name"])
            if s > best_score:
                best, best_score = p, s
        return best if best_score >= PROJECT_FUZZY_THRESHOLD else None

    def touch(self, name):
        self.db.touch_project(name)

    # ---- opening --------------------------------------------------------
    @staticmethod
    def _find_editor_exe(editor):
        for cand in EDITOR_COMMANDS.get((editor or "code").lower(), [editor or "code"]):
            exe = shutil.which(cand)
            if exe:
                return exe
        return None

    def open_project(self, project):
        """Open folder in Explorer + launch editor. Updates last_opened. READ-ONLY."""
        folder = project["folder"]
        if not os.path.isdir(folder):
            return False, f'Project folder not found: {folder}'
        try:
            os.startfile(folder)
        except Exception as e:
            return False, f"Could not open folder: {e}"

        self.touch(project["name"])

        editor = project.get("editor") or "code"
        exe    = self._find_editor_exe(editor)
        if exe:
            try:
                subprocess.Popen([exe, folder])
                return True, (f'Opened "{project["name"]}" '
                              f"({os.path.basename(exe)} + Explorer).")
            except Exception:
                pass
        return True, (f'Opened "{project["name"]}" folder. '
                      f"(Editor '{editor}' not found on PATH.)")

    # ---- helpers --------------------------------------------------------
    @staticmethod
    def friendly_time(ts: str) -> str:
        if not ts:
            return "Never opened"
        try:
            dt   = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
            diff = (datetime.now().date() - dt.date()).days
            if diff == 0:
                return f"Today {dt.strftime('%H:%M')}"
            if diff == 1:
                return "Yesterday"
            if diff < 7:
                return f"{diff} days ago"
            return dt.strftime("%b %d")
        except Exception:
            return ts
