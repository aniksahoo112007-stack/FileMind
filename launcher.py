"""FileMind - app launcher with fuzzy search + casual command parser.

Read-only towards the file system: it only LAUNCHES apps and OPENS
folders/files. No delete, no move, no rename.

Fuzzy examples:
  "forza horizontal" -> "Forza Horizon 5"
  "chrome"           -> "Google Chrome"
  "vs code"          -> "Visual Studio Code"
"""

import difflib
import os
import subprocess

from database import Database

FUZZY_THRESHOLD = 45  # minimum score (0-100) to count as a match


# ================================================================ fuzzy
def fuzzy_score(query, name):
    """Score 0-100 for how well `query` matches app `name`."""
    q = " ".join(query.lower().split())
    n = " ".join(name.lower().split())
    if not q or not n:
        return 0
    if q == n:
        return 100

    # whole-string similarity (0-50)
    score = difflib.SequenceMatcher(None, q, n).ratio() * 50

    # substring boosts
    if n.startswith(q):
        score += 30
    elif q in n:
        score += 20

    # token-level similarity (0-40): every query word should match
    # SOME word of the name ("forza horizontal" ~ "forza horizon 5")
    q_tokens, n_tokens = q.split(), n.split()
    token_scores = []
    for qt in q_tokens:
        best = 0.0
        for nt in n_tokens:
            if nt == qt:
                best = 1.0
                break
            if nt.startswith(qt):
                best = max(best, 0.9)
            else:
                best = max(best, difflib.SequenceMatcher(None, qt, nt).ratio())
        token_scores.append(best)
    score += (sum(token_scores) / len(token_scores)) * 40

    # acronym boost: "vs code" -> "vsc..." of "Visual Studio Code"
    acronym = "".join(w[0] for w in n_tokens)
    q_compact = q.replace(" ", "")
    if len(q_compact) >= 2 and (q_compact.startswith(acronym)
                                or acronym.startswith(q_compact)):
        score += 15

    return min(100, round(score))


# ================================================================ launcher
class AppLauncher:
    def __init__(self, db: Database):
        self.db = db

    def search(self, query, limit=20, threshold=FUZZY_THRESHOLD):
        """Return apps ranked by fuzzy score: [{name, path, source, score}]."""
        results = []
        for app in self.db.all_apps():
            s = fuzzy_score(query, app["name"])
            if s >= threshold:
                app = dict(app)
                app["score"] = s
                results.append(app)
        results.sort(key=lambda a: a["score"], reverse=True)
        return results[:limit]

    def best(self, query):
        hits = self.search(query, limit=1)
        return hits[0] if hits else None

    @staticmethod
    def launch(app):
        """Launch an app entry (dict with path + source). Returns (ok, msg)."""
        try:
            if app.get("source") == "store":
                # AUMID -> launch through the shell apps folder
                subprocess.Popen(
                    ["explorer.exe", f"shell:AppsFolder\\{app['path']}"])
            else:
                if not os.path.exists(app["path"]):
                    return False, f"Not found anymore: {app['path']}"
                os.startfile(app["path"])
            return True, f"Launched {app['name']}."
        except Exception as e:
            return False, f"Could not launch {app.get('name', '?')}: {e}"


# NOTE: command parsing moved to command_parser.py (web + app + file
# intents). This module now only does fuzzy app matching + launching.
