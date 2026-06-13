"""FileMind - installed application scanner (read-only).

Collects apps from:
  - Start Menu shortcuts (user + all users)
  - Desktop shortcuts (user + OneDrive + public)
  - Program Files / Program Files (x86)  (depth-limited .exe scan)
  - Microsoft Store apps (via PowerShell Get-StartApps, best effort)

Everything is stored in the SQLite `apps` table. Nothing is modified.
"""

import json
import os
import subprocess
import threading

import config
from database import Database

# lower number wins when the same app name appears from several sources
SOURCE_PRIORITY = {"start_menu": 0, "desktop": 1, "store": 2, "program_files": 3}

MAX_PROGRAM_EXES = 2000
PROGRAM_SCAN_DEPTH = 2


def _is_noise_exe(filename):
    low = filename.lower()
    return any(word in low for word in config.EXCLUDED_EXE_WORDS)


class AppScanner:
    def __init__(self, db: Database):
        self.db = db
        self._thread = None

    # ------------------------------------------------------------ public
    def start(self, on_done=None):
        """Scan in a background thread. on_done(app_count)."""
        if self.is_running():
            return
        self._thread = threading.Thread(
            target=self._scan, args=(on_done,), daemon=True)
        self._thread.start()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------ internal
    def _scan(self, on_done):
        found = {}  # normalized name -> (name, path, source, icon)

        def add(name, path, source):
            name = name.strip()
            if not name:
                return
            key = name.lower()
            old = found.get(key)
            if old and SOURCE_PRIORITY[old[2]] <= SOURCE_PRIORITY[source]:
                return
            found[key] = (name, path, source, "🚀")

        self._scan_shortcuts(add)
        self._scan_store_apps(add)
        self._scan_program_files(add)

        self.db.clear_apps()
        self.db.insert_apps(found.values())
        if on_done:
            on_done(len(found))

    def _scan_shortcuts(self, add):
        """Start Menu + Desktop .lnk/.url shortcuts."""
        for base, source in (
                [(d, "start_menu") for d in config.START_MENU_DIRS]
                + [(d, "desktop") for d in config.DESKTOP_DIRS]):
            if not os.path.isdir(base):
                continue
            for root, _dirs, files in os.walk(base, onerror=lambda e: None):
                for f in files:
                    if f.lower().endswith((".lnk", ".url")):
                        name = os.path.splitext(f)[0]
                        if _is_noise_exe(name):
                            continue
                        add(name, os.path.join(root, f), source)

    def _scan_store_apps(self, add):
        """Microsoft Store / UWP apps via PowerShell (best effort)."""
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-StartApps | ConvertTo-Json -Compress"],
                capture_output=True, text=True, timeout=25,
                creationflags=flags)
            if result.returncode != 0 or not result.stdout.strip():
                return
            data = json.loads(result.stdout)
            if isinstance(data, dict):
                data = [data]
            for item in data:
                name = item.get("Name", "")
                app_id = item.get("AppID", "")
                # only AUMIDs (contain '!') are Store/UWP - win32 apps are
                # already covered by the Start Menu shortcut scan
                if name and app_id and "!" in app_id:
                    add(name, app_id, "store")
        except Exception:
            pass  # PowerShell missing / blocked -> just skip Store apps

    def _scan_program_files(self, add):
        """Depth-limited .exe scan of Program Files folders."""
        count = 0
        for base in config.PROGRAM_DIRS:
            if not os.path.isdir(base):
                continue
            base_depth = base.rstrip("\\/").count(os.sep)
            for root, dirs, files in os.walk(base, onerror=lambda e: None):
                if root.count(os.sep) - base_depth >= PROGRAM_SCAN_DEPTH:
                    dirs[:] = []
                for f in files:
                    if not f.lower().endswith(".exe") or _is_noise_exe(f):
                        continue
                    add(os.path.splitext(f)[0], os.path.join(root, f),
                        "program_files")
                    count += 1
                    if count >= MAX_PROGRAM_EXES:
                        return
