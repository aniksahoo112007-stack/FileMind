"""FileMind LM Studio Client - AI command understanding. READ-ONLY.

Connects to LM Studio's OpenAI-compatible API at http://127.0.0.1:1234.
Sends the user's raw command and asks the model to return a structured
intent JSON so FileMind can route it to the correct action.

Safety: any intent that could modify, move, rename, or delete files is
blocked here before it ever reaches the rest of FileMind.
"""

import json
from urllib import request as _req

LM_BASE  = "http://127.0.0.1:1234"
MODEL    = "qwen2.5-7b-instruct-1m"
TIMEOUT  = 8    # seconds — fast enough for live command use

# The only intents FileMind will act on from the AI
SAFE_INTENTS = {
    "open_app", "open_website", "web_search",
    "file_search", "open_folder", "suggestion",
}

# Substrings that flag a dangerous intent — all blocked (READ-ONLY safety)
_DANGER_WORDS = {
    "delete", "remove", "erase", "trash", "unlink",
    "move",   "rename", "relocate", "cut",
    "modify", "edit",   "write",    "overwrite",
    "format", "wipe",   "destroy",
}

_SYSTEM = """\
You are the command parser for FileMind, a read-only file-management assistant.
Given the user's natural language command, return ONLY a single JSON object.

Available intents:
  open_app     – launch a desktop application (target = app name)
  open_website – open a website (target = site name or URL)
  web_search   – search the web (query = what to search for)
  file_search  – find files by description (query = file description)
  open_folder  – open a known folder such as Downloads or Desktop (target = folder name)
  suggestion   – user wants ideas or help about a topic (query = topic)

Rules:
• NEVER return any destructive intent: no delete, move, rename, modify, write.
• Output ONLY valid JSON, nothing else — no markdown, no explanation.
• confidence is a float 0.0–1.0 reflecting how sure you are.

Required JSON format:
{"intent": "...", "target": "...", "query": "...", "confidence": 0.85}
"""


# ── availability ──────────────────────────────────────────────────────────────

def is_available() -> bool:
    """Return True if LM Studio is reachable.

    Sends GET /v1/models, checks HTTP 200, and confirms the response
    JSON contains a "data" key (standard OpenAI model-list format).
    """
    try:
        req = _req.Request(
            f"{LM_BASE}/v1/models",
            headers={"Accept": "application/json"})
        with _req.urlopen(req, timeout=3) as resp:
            if resp.status != 200:
                return False
            body = json.loads(resp.read())
            return "data" in body
    except Exception:
        return False


# ── main API call ─────────────────────────────────────────────────────────────

def ask_lmstudio(user_command: str) -> dict | None:
    """Send user_command to LM Studio; return a validated intent dict or None.

    Return dict keys: intent, target, query, confidence.
    Returns None when LM Studio is offline, times out, or the response
    would violate the read-only safety rules.
    """
    payload = {
        "model":       MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": user_command.strip()},
        ],
        "temperature": 0.1,
        "max_tokens":  150,
        "stream":      False,
    }

    try:
        data = json.dumps(payload).encode()
        req  = _req.Request(
            f"{LM_BASE}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"})
        with _req.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read())

        content = body["choices"][0]["message"]["content"].strip()

        # strip markdown fences if the model wrapped the JSON
        if content.startswith("```"):
            lines = content.splitlines()
            content = "\n".join(
                l for l in lines
                if not l.strip().startswith("```") and l.strip() != "json")

        result = json.loads(content)
        return _validate(result)

    except Exception:
        return None


# ── validation / safety gate ──────────────────────────────────────────────────

def _validate(result: dict) -> dict | None:
    """Validate the AI response and enforce read-only safety.

    Returns a clean dict or None if the response is unsafe / malformed.
    """
    if not isinstance(result, dict):
        return None

    intent = str(result.get("intent", "")).lower().strip()

    # block any dangerous intent string
    if intent in _DANGER_WORDS or any(d in intent for d in _DANGER_WORDS):
        return {"_blocked": True}   # caller shows safety message

    # only act on known safe intents
    if intent not in SAFE_INTENTS:
        return None

    try:
        conf = float(result.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5

    return {
        "intent":     intent,
        "target":     str(result.get("target", "") or "").strip(),
        "query":      str(result.get("query",  "") or "").strip(),
        "confidence": min(1.0, max(0.0, conf)),
    }


# ── AI file explainer ─────────────────────────────────────────────────────────

_EXPLAIN_SYSTEM = """You are FileMind's AI assistant. The user has selected a file.
Given the filename and a short text snippet from the file, write a 3-5 line
plain-English summary of what the file is about and what it contains.
Be concise and helpful. Do NOT mention that you are an AI.
Output plain text only — no markdown, no bullet symbols.
"""


def explain_file(filename: str, snippet: str, max_chars: int = 600) -> str | None:
    """Ask LM Studio to summarise a file from its name + a text snippet.

    Returns a plain-text summary string, or None if LM Studio is offline
    or the request fails.
    """
    snippet = (snippet or "").strip()[:max_chars]
    user_msg = (
        f"File: {filename}\n\n"
        f"Content preview:\n{snippet or '(no text preview available)'}"
    )
    payload = {
        "model":       MODEL,
        "messages": [
            {"role": "system", "content": _EXPLAIN_SYSTEM},
            {"role": "user",   "content": user_msg},
        ],
        "temperature": 0.3,
        "max_tokens":  180,
        "stream":      False,
    }
    try:
        data = json.dumps(payload).encode()
        req  = _req.Request(
            f"{LM_BASE}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"})
        with _req.urlopen(req, timeout=TIMEOUT) as resp:
            body = json.loads(resp.read())
        return body["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
