"""FileMind - installed game detection + launching (read-only).

Detects games from Steam, Epic Games, and Xbox Game folders and
stores them in the SQLite `games` table. Launching uses launcher
protocols (steam://, com.epicgames.launcher://) or os.startfile -
nothing on disk is ever modified.
"""

import json
import os
import re
import threading

import config
from database import Database
from launcher import fuzzy_score

GAME_FUZZY_THRESHOLD = 45

_STEAM_KV = re.compile(r'"(appid|name|path)"\s+"(.+?)"')


class GameScanner:
    def __init__(self, db: Database):
        self.db = db
        self._thread = None

    # ------------------------------------------------------------ public
    def start(self, on_done=None):
        if self.is_running():
            return
        self._thread = threading.Thread(
            target=self._scan, args=(on_done,), daemon=True)
        self._thread.start()

    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------ scan
    def _scan(self, on_done):
        games = {}  # path -> (name, path, source, launch_cmd)

        def add(name, path, source, launch_cmd):
            name = (name or "").strip()
            if name and path not in games:
                games[path] = (name, path, source, launch_cmd)

        try:
            self._scan_steam(add)
        except Exception:
            pass
        try:
            self._scan_epic(add)
        except Exception:
            pass
        try:
            self._scan_xbox(add)
        except Exception:
            pass

        self.db.clear_games()
        self.db.insert_games(games.values())
        if on_done:
            on_done(len(games))

    def _scan_steam(self, add):
        """Parse Steam library manifests (.acf) - read-only text parsing."""
        libraries = set()
        for root in config.STEAM_DIRS:
            steamapps = os.path.join(root, "steamapps")
            if os.path.isdir(steamapps):
                libraries.add(steamapps)
                vdf = os.path.join(steamapps, "libraryfolders.vdf")
                if os.path.isfile(vdf):
                    try:
                        with open(vdf, encoding="utf-8", errors="ignore") as f:
                            for key, value in _STEAM_KV.findall(f.read()):
                                if key == "path":
                                    extra = os.path.join(
                                        value.replace("\\\\", "\\"), "steamapps")
                                    if os.path.isdir(extra):
                                        libraries.add(extra)
                    except OSError:
                        pass

        for lib in libraries:
            try:
                manifests = [f for f in os.listdir(lib)
                             if f.startswith("appmanifest_") and f.endswith(".acf")]
            except OSError:
                continue
            for mf in manifests:
                try:
                    with open(os.path.join(lib, mf), encoding="utf-8",
                              errors="ignore") as f:
                        kv = dict(_STEAM_KV.findall(f.read()))
                    appid, name = kv.get("appid"), kv.get("name")
                    if appid and name and "Steamworks" not in name:
                        add(name, os.path.join(lib, mf), "steam",
                            f"steam://rungameid/{appid}")
                except OSError:
                    continue

    def _scan_epic(self, add):
        """Epic Games manifests (.item JSON files)."""
        mdir = config.EPIC_MANIFEST_DIR
        if not os.path.isdir(mdir):
            return
        for fn in os.listdir(mdir):
            if not fn.endswith(".item"):
                continue
            try:
                with open(os.path.join(mdir, fn), encoding="utf-8",
                          errors="ignore") as f:
                    data = json.load(f)
                name = data.get("DisplayName")
                app_name = data.get("AppName")
                location = data.get("InstallLocation", os.path.join(mdir, fn))
                if name and app_name:
                    add(name, location, "epic",
                        f"com.epicgames.launcher://apps/{app_name}"
                        f"?action=launch&silent=true")
            except (OSError, json.JSONDecodeError):
                continue

    def _scan_xbox(self, add):
        """Xbox / Game Pass installs (folder names under XboxGames)."""
        for base in config.XBOX_DIRS:
            if not os.path.isdir(base):
                continue
            try:
                entries = os.listdir(base)
            except OSError:
                continue
            for name in entries:
                folder = os.path.join(base, name)
                if os.path.isdir(folder):
                    add(name, folder, "xbox", "")


# ================================================================ launching
def find_game(db: Database, query):
    """Best fuzzy game match or None."""
    best, best_score = None, 0
    for game in db.all_games():
        s = fuzzy_score(query, game["name"])
        if s > best_score:
            best, best_score = game, s
    return best if best_score >= GAME_FUZZY_THRESHOLD else None


def launch_game(game):
    """Launch via protocol URL or open the install folder. Returns (ok, msg)."""
    try:
        cmd = game.get("launch_cmd") or ""
        if cmd.startswith(("steam://", "com.epicgames.launcher://")):
            os.startfile(cmd)
            return True, f"Launching {game['name']}..."
        if os.path.isdir(game.get("path", "")):
            os.startfile(game["path"])
            return True, (f"Opened {game['name']} install folder "
                          "(launch it from there).")
        return False, f"Could not find a way to launch {game['name']}."
    except Exception as e:
        return False, f"Could not launch {game['name']}: {e}"
