"""FileMind - natural + casual command parser (100% read-only).

Understands English, casual, and Hinglish commands:
  "open youtube and search h"            -> YouTube search "h"
  "youtube search python"                -> YouTube search
  "search chatgpt on google"             -> Google search
  "open edge and search chatgpt"         -> Google search in Edge
  "chrome me youtube kholo aur search lo-fi music" -> YouTube search in Chrome
  "github open karo"                     -> open github.com
  "open website instagram"               -> open instagram.com
  "google weather today"                 -> Google search
  "find my pdf files"                    -> local .pdf search
  "open downloads"                       -> open the real Downloads folder
  "open vs code"                         -> launch the app

Allowed intents only: search files, open file, open folder location,
open app, open website, search web. Nothing can modify a file.
"""

import re

import web_launcher as web

# typo correction: rapidfuzz if installed, difflib otherwise
try:
    from rapidfuzz import process as _rf_process
    HAS_RAPIDFUZZ = True
except Exception:
    import difflib as _difflib
    HAS_RAPIDFUZZ = False

# ---------------------------------------------------------------- tokens
BROWSER_TOKENS = {"chrome": "chrome", "edge": "edge", "msedge": "edge",
                  "firefox": "firefox", "brave": "brave"}

ENGINE_TOKENS = {"youtube": "youtube", "yt": "youtube",
                 "google": "google", "web": "google",
                 "bing": "bing", "duckduckgo": "duckduckgo", "ddg": "duckduckgo"}

OPEN_VERBS = {"open", "launch", "start", "run", "kholo", "khol", "chalu",
              "shuru"}
SEARCH_VERBS = {"search", "find", "lookup", "dhundo", "dhoondo", "khojo",
                "dekho", "query"}
CONNECTORS = {"and", "aur", "in", "on", "with", "using", "for", "me",
              "mein", "par", "pe", "at", "then", "to", "se"}
FILLERS = {"please", "pls", "plz", "karo", "kar", "kijiye", "kro", "na",
           "ji", "my", "the", "a", "an", "ka", "ki", "ke", "ko", "do",
           "website", "site", "page"}

# file-word hints -> extension search
EXT_HINTS = {
    "pdf": ".pdf", "pdfs": ".pdf",
    "doc": ".docx", "docs": ".docx", "word": ".docx",
    "excel": ".xlsx", "xlsx": ".xlsx", "sheet": ".xlsx",
    "ppt": ".pptx", "pptx": ".pptx",
    "image": ".jpg", "images": ".jpg", "photo": ".jpg", "photos": ".jpg",
    "video": ".mp4", "videos": ".mp4",
    "song": ".mp3", "songs": ".mp3", "music": ".mp3", "audio": ".mp3",
    "python": ".py", "zip": ".zip", "exe": ".exe", "text": ".txt",
    "txt": ".txt", "iso": ".iso", "apk": ".apk",
}
FILE_WORDS = set(EXT_HINTS) | {"file", "files", "document", "documents",
                               "folder"}

GAME_VERBS = {"play", "khelo"}
PROJECT_WORDS = {"project", "project.", "repo", "codebase"}

# ---------------------------------------------------------------- typo fixing
# every keyword the parser cares about - typos get snapped to these
VOCAB = sorted(
    set(BROWSER_TOKENS) | set(ENGINE_TOKENS) | OPEN_VERBS | SEARCH_VERBS
    | GAME_VERBS
    | {"downloads", "download", "folder", "screenshots", "screenshot",
       "project", "continue", "show", "want", "learn", "study", "make",
       "build", "create", "website", "github", "gmail", "chatgpt",
       "whatsapp", "instagram", "facebook", "linkedin", "amazon",
       "flipkart", "steam", "and", "tutorial", "files", "file"})

_EXACT = set(VOCAB) | set(FILLERS) | CONNECTORS


def _correct_token(tok):
    """'opn'->'open', 'yutube'->'youtube', 'serch'->'search'.
    Leaves unknown topic words (like 'python') untouched."""
    if len(tok) < 3 or tok in _EXACT or "." in tok or tok.isdigit():
        return tok
    if tok in web.SITES:           # never "correct" a known site (openai, vercel…)
        return tok
    if HAS_RAPIDFUZZ:
        hit = _rf_process.extractOne(tok, VOCAB, score_cutoff=80)
        return hit[0] if hit else tok
    matches = _difflib.get_close_matches(tok, VOCAB, n=1, cutoff=0.78)
    return matches[0] if matches else tok


def autocorrect(text):
    """Fix typos word-by-word. 'opn yutube and serch python' ->
    'open youtube and search python'. Returns corrected text."""
    return " ".join(_correct_token(w) for w in (text or "").split())


# ---------------------------------------------------------------- intents
# broad goals -> show suggestion cards instead of instantly searching
INTENT_STARTERS = ("i want to ", "i want ", "i wanna ", "how to ", "how do i ",
                   "learn ", "study ", "make a ", "make an ", "build a ",
                   "build an ", "build ", "create a ", "create an ",
                   "teach me ", "help me ")
_INTENT_NOISE = {"i", "want", "wanna", "to", "how", "do", "learn", "study",
                 "make", "build", "create", "a", "an", "the", "teach",
                 "help", "me", "my", "please"}


def _intent_topic(text):
    words = [w for w in text.lower().split() if w not in _INTENT_NOISE]
    return " ".join(words)

# ---------------------------------------------------------------- regexes
_PROJECT_CMD = re.compile(
    r"^(?:open|continue|resume)\s+(?:my\s+)?(.+?)(?:\s+project)?$",
    re.IGNORECASE)
_OPEN_AND_SEARCH = re.compile(
    r"^open\s+(.+?)\s+(?:and|aur)\s+search\s+(?:for\s+)?(.+)$", re.IGNORECASE)
_SEARCH_IN = re.compile(
    r"^(?:search|find|look\s*up)\s+(.+?)\s+(?:in|on|with|using)\s+(.+)$",
    re.IGNORECASE)
_OPEN_IN = re.compile(
    r"^open\s+(.+?)\s+(?:in|with|on)\s+(.+)$", re.IGNORECASE)


# ---------------------------------------------------------------- helpers
def _strip(tokens, *extra_sets):
    """Remove verbs/connectors/fillers (and extra sets) from tokens."""
    drop = OPEN_VERBS | SEARCH_VERBS | CONNECTORS | FILLERS
    for s in extra_sets:
        drop = drop | set(s)
    return [t for t in tokens if t not in drop]


def _ext_query(query):
    """'my pdf files' -> '.pdf' when the meaningful part is one ext word."""
    meaningful = [t for t in query.lower().split()
                  if t not in FILLERS and t not in ("file", "files")]
    if len(meaningful) == 1 and meaningful[0] in EXT_HINTS:
        return EXT_HINTS[meaningful[0]]
    return query


def _strip_leading_search(q):
    words = q.split()
    while words and words[0].lower() in (SEARCH_VERBS | {"for"}):
        words = words[1:]
    return " ".join(words)


def _semantic_find(tokens):
    """'find ai pdf' -> text 'ai' + extension '.pdf'.
    'find machine learning report' -> text 'machine learning report'.
    'find screenshots' -> screenshots view."""
    words = _strip(tokens)
    if any(w in ("screenshot", "screenshots") for w in words):
        return {"action": "find_screenshots"}
    ext = None
    text_words = []
    for w in words:
        if ext is None and w in EXT_HINTS:
            ext = EXT_HINTS[w]
        elif w not in ("file", "files", "document", "documents"):
            text_words.append(w)
    return {"action": "find_files", "query": " ".join(text_words),
            "extension": ext}


# ---------------------------------------------------------------- parse
def parse(text):
    """Return an intent dict: {"action": ..., plus action-specific keys}.

    Actions: show_downloads, open_downloads, open_folder, find_files,
             open_url, open_browser, web_search, open_app, query
    """
    t = autocorrect(" ".join((text or "").split()))
    low = t.lower()
    if not low:
        return {"action": "query", "text": ""}
    tokens = low.split()

    # ---------------- downloads ----------------
    if low in ("show downloads", "downloads"):
        return {"action": "show_downloads"}
    if low in ("open downloads", "open download", "downloads kholo",
               "open downloads folder"):
        return {"action": "open_downloads"}

    # ---------------- folders ----------------
    if low.startswith("open folder "):
        return {"action": "open_folder", "name": t[12:].strip()}

    # ---------------- broad intents -> suggestion cards ----------------
    # "i want to learn python", "learn machine learning", "make a website"
    if low.startswith(INTENT_STARTERS):
        topic = _intent_topic(low)
        if topic:
            return {"action": "intent", "topic": topic, "text": t}

    # ---------------- games: "play forza", "launch valorant" ----------------
    if tokens[0] in GAME_VERBS and len(tokens) > 1:
        return {"action": "play_game",
                "name": " ".join(_strip(tokens[1:], GAME_VERBS))
                or " ".join(tokens[1:])}
    if tokens[0] == "launch" and len(tokens) > 1:
        # try game first, fall back to app (handled by the UI)
        return {"action": "play_game",
                "name": " ".join(tokens[1:]), "fallback_app": True}

    # ---------------- remember / save project ----------------
    # "remember project filemind", "save project pos", "add project catalog"
    for pfx in ("remember project ", "save project ", "add project ",
                "remember ", "yaad rakh project "):
        if low.startswith(pfx):
            name = t[len(pfx):].strip()
            if name:
                return {"action": "remember_project", "name": name}

    # ---------------- continue / resume my work ----------------
    # Must come BEFORE projects, else "continue my work" → open_project.
    if (low in ("continue my work", "resume my work", "continue last work",
                "resume last work", "resume last session", "continue work",
                "resume work", "continue session", "resume session",
                "last work", "continue my last work", "resume my last work")
            or (tokens[0] in ("continue", "resume")
                and ("work" in tokens or "session" in tokens))):
        return {"action": "continue_work"}

    # ---------------- projects ----------------
    # "open my pos project", "continue filemind", "open project jarvis"
    if (low.startswith(("continue ", "resume "))
            or (low.startswith("open ") and
                (" project" in low or low.startswith(("open my ",
                                                      "open project "))))):
        m = _PROJECT_CMD.match(t)
        if m:
            name = m.group(1).strip()
            if name.lower().startswith("project "):
                name = name[8:].strip()
            if name:
                return {"action": "open_project", "name": name}

    # ---------------- "open X and search Y" ----------------
    m = _OPEN_AND_SEARCH.match(t)
    if m:
        target, query = m.group(1).strip(), m.group(2).strip()
        query = _strip_leading_search(query)
        browser = web.match_browser(target)
        if browser:
            return {"action": "web_search", "query": query,
                    "engine": "google", "browser": browser}
        engine = ENGINE_TOKENS.get(target.lower())
        if engine:
            return {"action": "web_search", "query": query,
                    "engine": engine, "browser": None}
        if web.match_site(target):
            # unknown-engine site -> google it
            return {"action": "web_search", "query": query,
                    "engine": "google", "browser": None}
        return {"action": "open_app", "name": target}

    # ---------------- "search Y in/on X" ----------------
    m = _SEARCH_IN.match(t)
    if m:
        query, tail = m.group(1).strip(), m.group(2).strip()
        engine = web.match_engine(tail)
        if engine:
            return {"action": "web_search", "query": query,
                    "engine": engine, "browser": None}
        browser = web.match_browser(tail)
        if browser:
            return {"action": "web_search", "query": query,
                    "engine": "google", "browser": browser}
        return {"action": "find_files",
                "query": _ext_query(f"{query} {tail}".strip())}

    # ---------------- engine as verb: "google ai tools", "youtube search python"
    first, _, rest = t.partition(" ")
    if rest and first.lower() in ENGINE_TOKENS:
        query = _strip_leading_search(rest.strip())
        if query:
            return {"action": "web_search", "query": query,
                    "engine": ENGINE_TOKENS[first.lower()], "browser": None}

    # ---------------- casual keyword routing ----------------
    browser = next((BROWSER_TOKENS[tok] for tok in tokens
                    if tok in BROWSER_TOKENS), None)
    engine = next((ENGINE_TOKENS[tok] for tok in tokens
                   if tok in ENGINE_TOKENS), None)
    has_open = any(tok in OPEN_VERBS for tok in tokens)
    has_search = any(tok in SEARCH_VERBS for tok in tokens)

    # engine mentioned anywhere: "chrome me youtube kholo aur search lo-fi"
    if engine:
        query = " ".join(_strip(tokens, BROWSER_TOKENS, ENGINE_TOKENS))
        if has_search and query:
            return {"action": "web_search", "query": query,
                    "engine": engine, "browser": browser}
        if not query:  # "open youtube", "youtube kholo", "open google"
            url = web.SITES.get(engine, "https://www.google.com")
            return {"action": "open_url", "url": url, "browser": browser}
        if query:  # "youtube lofi mix" without search verb
            return {"action": "web_search", "query": query,
                    "engine": engine, "browser": browser}

    # browser mentioned: "open chrome", "chrome me github kholo"
    if browser:
        remaining = " ".join(_strip(tokens, BROWSER_TOKENS))
        if not remaining:
            return {"action": "open_browser", "browser": browser}
        url = web.normalize_url(remaining) or web.match_site(remaining)
        if url:
            return {"action": "open_url", "url": url, "browser": browser}
        if has_search:
            return {"action": "web_search", "query": remaining,
                    "engine": "google", "browser": browser}
        return {"action": "web_search", "query": remaining,
                "engine": "google", "browser": browser}

    # file intent: "find ai pdf", "find machine learning report",
    # "find screenshots", "find invoice"
    if has_search or low.startswith(("find ", "search ", "show ")):
        intent = _semantic_find(tokens)
        if intent["action"] == "find_screenshots":
            return intent
        if intent.get("query") or intent.get("extension"):
            return intent

    # open intent (incl. Hinglish "github open karo"): site/url/app
    if has_open:
        candidate = " ".join(_strip(tokens))
        if not candidate:
            return {"action": "query", "text": t}
        url = web.normalize_url(candidate)
        if url:
            return {"action": "open_url", "url": url, "browser": None}
        site = web.match_site(candidate)
        if site:
            return {"action": "open_url", "url": site, "browser": None}
        return {"action": "open_app", "name": candidate}

    # bare domain: "example.com"
    url = web.normalize_url(t)
    if url:
        return {"action": "open_url", "url": url, "browser": None}

    # ---------------- fallback: unified search (then Google if unsure)
    return {"action": "query", "text": t}


COMMAND_HELP = [
    ("play <game>", "'play forza', 'play gta' - Steam / Epic / Xbox"),
    ("launch <game or app>", "'launch valorant' - game first, then app"),
    ("open my <name> project", "open project folder + editor"),
    ("continue <project>", "'continue filemind' - same as above"),
    ("find <topic> <type>", "'find ai pdf' - topic + file type together"),
    ("find screenshots", "all screenshots from the index"),
    ("open youtube and search <q>", "YouTube search ('...and search hindi songs')"),
    ("youtube search <q>", "YouTube search ('youtube search python')"),
    ("google <anything>", "Google search ('google weather today')"),
    ("search <q> on <engine>", "google / youtube / bing / duckduckgo"),
    ("search <q> in <browser>", "'search chatgpt in chrome'"),
    ("open edge and search <q>", "Google search inside Edge"),
    ("chrome me <site> kholo", "Hinglish works too"),
    ("<site> open karo", "'github open karo' -> github.com"),
    ("open <website>", "youtube, gmail, github, chatgpt, whatsapp..."),
    ("open <domain.com>", "any URL - https:// added automatically"),
    ("open <browser>", "chrome / edge / firefox / brave"),
    ("open <app name>", "fuzzy app launch ('open vs code')"),
    ("find <file / pdf / images>", "'find pdf' -> all .pdf files"),
    ("open downloads", "open the real Downloads folder"),
    ("show downloads", "list Downloads in the table"),
    ("open folder <name>", "open an indexed folder"),
]
