"""FileMind - central configuration."""

import os
from pathlib import Path

APP_NAME = "FileMind"
APP_VERSION = "1.0.0"

# ---------------------------------------------------------------- paths
PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
LOG_DIR = PROJECT_DIR / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "filemind.db"

# Read-only mode: the app never deletes, moves, renames, or modifies files.
READ_ONLY = True
SAFE_MODE = True
TRASH_DIR = DATA_DIR / "FileMind_Trash"   # kept for compatibility, unused in UI
TRASH_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- downloads
# The real Downloads location is detected from Windows itself (shell
# known-folder API, then registry). The default profile Downloads
# (C:\Users\<user>\Downloads) is deliberately NOT used - if detection
# only finds that, we use the fallback below instead.
DOWNLOADS_FALLBACK = r"E:\user folders\download backups"

_DEFAULT_PROFILE_DOWNLOADS = os.path.normcase(
    os.path.normpath(os.path.join(os.path.expanduser("~"), "Downloads")))


def _downloads_from_known_folder():
    """Ask the Windows shell for FOLDERID_Downloads (respects relocation)."""
    try:
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD),
                        ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        # {374DE290-123F-4565-9164-39C4925E467B} = Downloads
        guid = GUID(0x374DE290, 0x123F, 0x4565,
                    (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4,
                                         0x92, 0x5E, 0x46, 0x7B))
        out = ctypes.c_wchar_p()
        res = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(guid), 0, None, ctypes.byref(out))
        if res == 0 and out.value:
            path = out.value
            ctypes.windll.ole32.CoTaskMemFree(out)
            return path
    except Exception:
        pass
    return None


def _downloads_from_registry():
    """Read the Downloads GUID from User Shell Folders in the registry."""
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(
                key, "{374DE290-123F-4565-9164-39C4925E467B}")
            return os.path.expandvars(value)
    except Exception:
        return None


def _detect_downloads():
    for candidate in (_downloads_from_known_folder(),
                      _downloads_from_registry()):
        if not candidate:
            continue
        norm = os.path.normcase(os.path.normpath(candidate))
        if norm == _DEFAULT_PROFILE_DOWNLOADS:
            continue  # explicitly excluded by user
        if os.path.isdir(candidate):
            return candidate
    return DOWNLOADS_FALLBACK


DOWNLOADS_DIR = _detect_downloads()

# ---------------------------------------------------------------- app scanning
_APPDATA = os.environ.get("APPDATA", "")
_PROGRAMDATA = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
_PUBLIC = os.environ.get("PUBLIC", r"C:\Users\Public")
_HOME = os.path.expanduser("~")

PF = os.environ.get(
    "ProgramFiles",
    r"C:\Program Files"
)
PF86 = os.environ.get(
    "ProgramFiles(x86)",
    r"C:\Program Files (x86)"
)

START_MENU_DIRS = [
    os.path.join(_APPDATA, r"Microsoft\Windows\Start Menu\Programs"),
    os.path.join(_PROGRAMDATA, r"Microsoft\Windows\Start Menu\Programs"),
]
DESKTOP_DIRS = [
    os.path.join(_HOME, "Desktop"),
    os.path.join(_HOME, "OneDrive", "Desktop"),
    os.path.join(_PUBLIC, "Desktop"),
]
PROGRAM_DIRS = [PF, PF86]

# exe names containing these words are skipped (not real apps)
EXCLUDED_EXE_WORDS = {
    "unins", "uninst", "setup", "install", "update", "updater", "crash",
    "report", "helper", "repair", "redist", "vc_redist", "elevate",
    "service", "daemon", "diagnostic",
}

# ---------------------------------------------------------------- game scanning
STEAM_DIRS = [
    os.path.join(PF86, "Steam"),
    os.path.join(PF, "Steam"),
    r"D:\Steam", r"E:\Steam", r"D:\SteamLibrary", r"E:\SteamLibrary",
]
EPIC_MANIFEST_DIR = os.path.join(
    _PROGRAMDATA, r"Epic\EpicGamesLauncher\Data\Manifests")
XBOX_DIRS = [r"C:\XboxGames", r"D:\XboxGames", r"E:\XboxGames"]

# ---------------------------------------------------------------- scanning
DRIVES = ["C:\\", "D:\\", "E:\\"]

# Folders skipped while scanning (system / noise)
EXCLUDED_DIRS = {
    "$recycle.bin", "system volume information", "windows", "programdata",
    "appdata", "node_modules", ".git", "__pycache__", ".venv", "venv",
    "recovery", "perflogs", "$windows.~bt", "msocache",
}

# ---------------------------------------------------------------- categories
CATEGORIES = {
    "Images":     {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg",
                   ".ico", ".tiff", ".heic", ".raw"},
    "Videos":     {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
                   ".m4v", ".3gp", ".mpeg"},
    "PDFs":       {".pdf"},
    "Documents":  {".doc", ".docx", ".txt", ".rtf", ".odt", ".xls", ".xlsx",
                   ".csv", ".ppt", ".pptx", ".md", ".epub"},
    "Code":       {".py", ".js", ".ts", ".html", ".css", ".java", ".c",
                   ".cpp", ".h", ".cs", ".php", ".rb", ".go", ".rs", ".json",
                   ".xml", ".yaml", ".yml", ".sql", ".sh", ".bat", ".ipynb"},
    "Archives":   {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz", ".iso"},
    "Installers": {".exe", ".msi", ".apk", ".dmg", ".deb", ".appx"},
    "Music":      {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma",
                   ".opus", ".mid"},
}
OTHER_CATEGORY = "Others"
FOLDER_CATEGORY = "Folders"

# Emoji icons per category (used in the table's Icon column)
ICONS = {
    "Folders": "📁",
    "Images": "🖼️",
    "Videos": "🎬",
    "PDFs": "📕",
    "Documents": "📄",
    "Code": "💻",
    "Archives": "🗜️",
    "Installers": "⚙️",
    "Music": "🎵",
    "Others": "📦",
    "App": "🚀",
    "Command": "⌨️",
    "Game": "🎮",
    "Project": "📂",
    "Web": "🌐",
}


def get_icon(file_type: str) -> str:
    return ICONS.get(file_type, "📦")


def get_category(extension: str) -> str:
    """Return the category name for an extension like '.pdf'."""
    ext = extension.lower()
    for category, extensions in CATEGORIES.items():
        if ext in extensions:
            return category
    return OTHER_CATEGORY


# ---------------------------------------------------------------- UI theme
UI = {
    "appearance": "dark",          # dark mode by default
    "color_theme": "blue",
    "window_size": "1280x760",
    "min_size": (1080, 640),
    "sidebar_width": 215,
    "accent": "#1f6aa5",
}
