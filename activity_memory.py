"""FileMind - Activity Memory (read-only, crash-proof, additive).

A lightweight history of read-only actions (opened project/app/website/folder/
file, searches, voice commands). Logging is best-effort and runs off the UI
thread, so it can NEVER block or crash the app. Only the latest 500 rows are
kept (enforced in the database layer).
"""

import json
import threading

# Action types treated as "real work" for Continue-My-Work, in priority order.
_WORK_TYPES = ("opened_project", "opened_folder", "opened_file")
_FALLBACK_TYPES = ("opened_website", "opened_app")


class ActivityMemory:
    def __init__(self, db):
        self.db = db

    # ---------------------------------------------------------------- write
    def log_activity(self, action_type, title, target="", extra=None):
        """Record one activity AFTER a successful action. Never blocks the UI,
        never raises — if anything fails the app just continues."""
        def _work():
            try:
                ej = json.dumps(extra) if extra else ""
                self.db.add_activity(action_type, title, target, ej)
            except Exception:
                pass   # logging must never affect normal operation
        try:
            threading.Thread(target=_work, daemon=True).start()
        except Exception:
            pass

    # ---------------------------------------------------------------- read
    def get_recent_activities(self, limit=5):
        try:
            return self.db.recent_activities(limit)
        except Exception:
            return []

    def get_last_work_activity(self):
        """The most recent meaningful activity to resume.

        Prefers real work (project/folder/file, most-recent first); falls back
        to the last useful website/app if no work item exists. Returns a row
        dict or None."""
        try:
            row = self.db.last_activity_of_types(list(_WORK_TYPES))
            if row:
                return row
            return self.db.last_activity_of_types(list(_FALLBACK_TYPES))
        except Exception:
            return None
