"""FileMind - web launcher (read-only towards the file system).

Opens websites and web searches in Chrome, Edge, Firefox, Brave, or
the default browser. Never crashes if a browser is missing - it falls
back to the default browser with a friendly message.
"""

import os
import re
import subprocess
import webbrowser
from urllib.parse import quote_plus

from launcher import fuzzy_score

# ---------------------------------------------------------------- browsers
_PF = os.environ.get("ProgramFiles", r"C:\Program Files")
_PF86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
_LOCAL = os.environ.get("LOCALAPPDATA", "")

BROWSER_PATHS = {
    "chrome": [
        os.path.join(_PF, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(_PF86, r"Google\Chrome\Application\chrome.exe"),
        os.path.join(_LOCAL, r"Google\Chrome\Application\chrome.exe"),
    ],
    "edge": [
        os.path.join(_PF86, r"Microsoft\Edge\Application\msedge.exe"),
        os.path.join(_PF, r"Microsoft\Edge\Application\msedge.exe"),
    ],
    "firefox": [
        os.path.join(_PF, r"Mozilla Firefox\firefox.exe"),
        os.path.join(_PF86, r"Mozilla Firefox\firefox.exe"),
    ],
    "brave": [
        os.path.join(_PF, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        os.path.join(_PF86, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        os.path.join(_LOCAL, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
    ],
}

BROWSER_ALIASES = {
    "chrome": "chrome", "google chrome": "chrome",
    "edge": "edge", "microsoft edge": "edge",
    "firefox": "firefox", "mozilla firefox": "firefox",
    "brave": "brave", "brave browser": "brave",
    "default": "default", "default browser": "default", "browser": "default",
}

BROWSER_LABELS = {"chrome": "Chrome", "edge": "Edge", "firefox": "Firefox",
                  "brave": "Brave", "default": "your default browser"}

# ---------------------------------------------------------------- sites
# Known website shortcuts with their correct domains (some are NOT .com, e.g.
# wikipedia.org, huggingface.co — so we keep an explicit map instead of always
# guessing ".com").
SITES = {
    # search / Google
    "google": "https://www.google.com",
    "youtube": "https://www.youtube.com",
    "gmail": "https://mail.google.com",
    "maps": "https://maps.google.com",
    "drive": "https://drive.google.com",
    # developer
    "github": "https://github.com",
    "gitlab": "https://gitlab.com",
    "stackoverflow": "https://stackoverflow.com",
    "stack overflow": "https://stackoverflow.com",
    "vercel": "https://vercel.com",
    "netlify": "https://www.netlify.com",
    "kaggle": "https://www.kaggle.com",
    "huggingface": "https://huggingface.co",
    "hugging face": "https://huggingface.co",
    "replit": "https://replit.com",
    "codepen": "https://codepen.io",
    "leetcode": "https://leetcode.com",
    "npm": "https://www.npmjs.com",
    "pypi": "https://pypi.org",
    "dockerhub": "https://hub.docker.com",
    # AI
    "openai": "https://openai.com",
    "chatgpt": "https://chatgpt.com",
    "gemini": "https://gemini.google.com",
    "claude": "https://claude.ai",
    "perplexity": "https://www.perplexity.ai",
    "anthropic": "https://www.anthropic.com",
    # knowledge / reading
    "wikipedia": "https://www.wikipedia.org",
    "reddit": "https://www.reddit.com",
    "medium": "https://medium.com",
    "quora": "https://www.quora.com",
    "notion": "https://www.notion.so",
    # social
    "whatsapp": "https://web.whatsapp.com",
    "whatsapp web": "https://web.whatsapp.com",
    "instagram": "https://www.instagram.com",
    "facebook": "https://www.facebook.com",
    "twitter": "https://twitter.com",
    "x": "https://x.com",
    "linkedin": "https://www.linkedin.com",
    "telegram": "https://web.telegram.org",
    "pinterest": "https://www.pinterest.com",
    # media
    "netflix": "https://www.netflix.com",
    "primevideo": "https://www.primevideo.com",
    "hotstar": "https://www.hotstar.com",
    # shopping
    "amazon": "https://www.amazon.in",
    "flipkart": "https://www.flipkart.com",
}

# ---------------------------------------------------------------- engines
SEARCH_ENGINES = {
    "google": "https://www.google.com/search?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "bing": "https://www.bing.com/search?q={}",
    "duckduckgo": "https://duckduckgo.com/?q={}",
}

ENGINE_ALIASES = {
    "google": "google", "youtube": "youtube", "yt": "youtube",
    "bing": "bing", "duckduckgo": "duckduckgo", "duck duck go": "duckduckgo",
    "ddg": "duckduckgo",
}

_DOMAIN_RE = re.compile(
    r"^(https?://)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+(:\d+)?(/\S*)?$")


# ================================================================ matching
def match_browser(name, threshold=75):
    """Fuzzy-match a browser name -> 'chrome'/'edge'/... or None."""
    name = (name or "").lower().strip()
    if not name:
        return None
    if name in BROWSER_ALIASES:
        return BROWSER_ALIASES[name]
    best, best_score = None, 0
    for alias, key in BROWSER_ALIASES.items():
        s = fuzzy_score(name, alias)
        if s > best_score:
            best, best_score = key, s
    return best if best_score >= threshold else None


def match_engine(name, threshold=75):
    name = (name or "").lower().strip()
    if name in ENGINE_ALIASES:
        return ENGINE_ALIASES[name]
    best, best_score = None, 0
    for alias, key in ENGINE_ALIASES.items():
        s = fuzzy_score(name, alias)
        if s > best_score:
            best, best_score = key, s
    return best if best_score >= threshold else None


def match_site(name, threshold=72):
    """Fuzzy-match a website shortcut -> URL or None."""
    name = (name or "").lower().strip()
    if name in SITES:
        return SITES[name]
    best, best_score = None, 0
    for site, url in SITES.items():
        s = fuzzy_score(name, site)
        if s > best_score:
            best, best_score = url, s
    return best if best_score >= threshold else None


def normalize_url(text):
    """'example.com' -> 'https://example.com'. Returns None if not a URL."""
    t = (text or "").strip()
    if " " in t or not _DOMAIN_RE.match(t):
        return None
    if not t.lower().startswith(("http://", "https://")):
        t = "https://" + t
    return t


# ================================================================ launching
def find_browser_exe(browser_key):
    for path in BROWSER_PATHS.get(browser_key, []):
        if path and os.path.exists(path):
            return path
    return None


def open_url(url, browser=None):
    """Open a URL. Returns (ok, friendly_message). Never raises."""
    try:
        if browser and browser != "default":
            exe = find_browser_exe(browser)
            if exe:
                subprocess.Popen([exe, url])
                return True, f"Opened {url} in {BROWSER_LABELS[browser]}."
            # friendly fallback instead of crashing
            webbrowser.open(url)
            return True, (f"{BROWSER_LABELS.get(browser, browser)} was not "
                          f"found on this PC - opened in your default "
                          f"browser instead.")
        webbrowser.open(url)
        return True, f"Opened {url} in your default browser."
    except Exception as e:
        return False, f"Could not open the browser: {e}"


def open_site(name, browser=None):
    """Open a known site shortcut or a raw URL/domain."""
    url = normalize_url(name) or match_site(name)
    if not url:
        return False, f'No website found matching "{name}".'
    return open_url(url, browser)


def open_browser(browser):
    """Just open a browser window."""
    if browser == "default":
        return open_url("https://www.google.com", None)
    exe = find_browser_exe(browser)
    if exe:
        try:
            subprocess.Popen([exe])
            return True, f"Opened {BROWSER_LABELS[browser]}."
        except Exception as e:
            return False, f"Could not open {BROWSER_LABELS[browser]}: {e}"
    return open_url("https://www.google.com", None)


def search_web(query, engine="google", browser=None):
    """Search the web. Returns (ok, friendly_message)."""
    if not query:
        return False, "Nothing to search for."
    engine = engine if engine in SEARCH_ENGINES else "google"
    url = SEARCH_ENGINES[engine].format(quote_plus(query))
    ok, msg = open_url(url, browser)
    if ok:
        where = (f" in {BROWSER_LABELS[browser]}"
                 if browser and browser != "default" else "")
        return True, f'Searching {engine.title()} for "{query}"{where}.'
    return ok, msg


# ================================================================ generic sites
def pretty_site(name_or_url):
    """A human-friendly site name for status text ('Wikipedia', 'Vercel')."""
    s = (name_or_url or "").strip()
    if s.lower().startswith(("http://", "https://")):
        host = s.split("//", 1)[-1].split("/", 1)[0]
        if host.lower().startswith("www."):
            host = host[4:]
        s = host.split(".")[0]
    return s[:1].upper() + s[1:] if s else s


def guess_site_url(name):
    """Build a best-guess URL for an unknown single-word site.

    'vercel' -> https://www.vercel.com  ·  returns None for multi-word text."""
    n = (name or "").lower().strip()
    if re.fullmatch(r"[a-z0-9][a-z0-9-]*", n):
        return f"https://www.{n}.com"
    return None


def open_or_search(name, browser=None):
    """Open a website for a bare name. Read-only, uses webbrowser (no CMD).

    Order:
      1. known site shortcut (SITES, exact or fuzzy)
      2. constructed https://www.<name>.com for a single word
      3. fallback → Google '<name> official website'
    Returns (ok, friendly_message)."""
    name = (name or "").strip()
    if not name:
        return False, "Nothing to open."

    url = (SITES.get(name.lower()) or match_site(name)
           or guess_site_url(name))
    if url:
        ok, msg = open_url(url, browser)
        if ok:
            return True, f"Opening website: {pretty_site(url)}"
        # browser genuinely failed to launch → fall through to a search
    ok, msg = search_web(f"{name} official website", "google", browser)
    if ok:
        return True, f"Searching for official website: {name}"
    return ok, msg
