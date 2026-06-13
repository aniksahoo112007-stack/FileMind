"""FileMind - real Windows app icon extraction (read-only).

Extracts the real icon from .exe files (and .lnk shortcut targets)
using pywin32 + Pillow, and caches the result as a small PNG inside
assets/icons_cache. Everything is optional:

  pip install pywin32 Pillow

If pywin32 or Pillow is missing, every function quietly returns None
and the UI falls back to emoji icons. No source file is ever modified -
icons are only READ from executables and written to our own cache.
"""

import hashlib
import os

import config

CACHE_DIR = config.PROJECT_DIR / "assets" / "icons_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

ICON_SIZE = 24  # px, fits the table row height

try:
    import win32api
    import win32con
    import win32gui
    import win32ui
    from PIL import Image
    HAS_ICON_SUPPORT = True
except Exception:
    HAS_ICON_SUPPORT = False

try:
    import win32com.client
    _shell = None
    HAS_SHELL = True
except Exception:
    HAS_SHELL = False


def _cache_path(source_path):
    key = hashlib.md5(source_path.lower().encode("utf-8", "ignore")).hexdigest()
    return str(CACHE_DIR / f"{key}.png")


def resolve_lnk(path):
    """Return the target .exe of a .lnk shortcut, or the path itself."""
    if not path.lower().endswith(".lnk") or not HAS_SHELL:
        return path
    global _shell
    try:
        if _shell is None:
            _shell = win32com.client.Dispatch("WScript.Shell")
        target = _shell.CreateShortCut(path).Targetpath
        return target if target else path
    except Exception:
        return path


def _extract_to_png(exe_path, out_png):
    """Draw the exe's large icon into a PNG. Returns True on success."""
    large, small = win32gui.ExtractIconEx(exe_path, 0, 1)
    # always clean up whatever we got
    for handles in (large, small):
        for h in handles[1:]:
            win32gui.DestroyIcon(h)
    if not large:
        for h in small:
            win32gui.DestroyIcon(h)
        return False
    hicon = large[0]
    if small:
        win32gui.DestroyIcon(small[0])
    try:
        size = win32api.GetSystemMetrics(win32con.SM_CXICON)  # usually 32
        hdc_screen = win32gui.GetDC(0)
        dc = win32ui.CreateDCFromHandle(hdc_screen)
        memdc = dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(dc, size, size)
        memdc.SelectObject(bmp)
        memdc.FillSolidRect((0, 0, size, size), 0x10141F)  # app background
        memdc.DrawIcon((0, 0), hicon)

        info = bmp.GetInfo()
        data = bmp.GetBitmapBits(True)
        img = Image.frombuffer(
            "RGBA", (info["bmWidth"], info["bmHeight"]), data,
            "raw", "BGRA", 0, 1)
        img = img.resize((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
        img.save(out_png, "PNG")
        return True
    finally:
        win32gui.DestroyIcon(hicon)
        try:
            memdc.DeleteDC()
            dc.DeleteDC()
            win32gui.ReleaseDC(0, hdc_screen)
            win32gui.DeleteObject(bmp.GetHandle())
        except Exception:
            pass


def get_icon_png(path):
    """Return a cached PNG path for this app's real icon, or None.

    Safe to call from worker threads. Never raises.
    """
    if not HAS_ICON_SUPPORT or not path:
        return None
    try:
        cached = _cache_path(path)
        if os.path.exists(cached):
            return cached
        target = resolve_lnk(path)
        if not target.lower().endswith((".exe", ".dll", ".ico")):
            return None
        if not os.path.exists(target):
            return None
        if _extract_to_png(target, cached):
            return cached
    except Exception:
        pass
    return None
