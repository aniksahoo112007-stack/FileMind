"""FileMind V2 - Personal AI Laptop Command Center (READ-ONLY).

Sections: Dashboard · Explorer · Drives · Apps · Projects · Games · Web · Suggestions
One command bar with typo-tolerant casual + Hinglish commands and intent cards.

Safe mode: no delete, no move, no rename, no relocate - ever.

Run:  python main.py
"""

import ctypes
import os
import shutil
import threading
import tkinter as tk
from datetime import datetime
from tkinter import ttk, filedialog, messagebox

import customtkinter as ctk

import config
import game_scanner as games_mod
import icon_extractor
import web_launcher as web
import ai_service
import lmstudio_client
import system_monitor
from app_scanner import AppScanner
from command_parser import parse, COMMAND_HELP
from database import Database
from document_indexer import DocumentIndexer
from game_scanner import GameScanner
from launcher import AppLauncher
from projects import ProjectRegistry
from scanner import Scanner
from search_engine import SearchEngine
from voice_assistant import VoiceAssistant

RESULT_LIMIT = 500
LIVE_SEARCH_DELAY_MS = 300

FILTERS = ["All", "Folders", "Images", "Videos", "PDFs", "Documents",
           "Code", "Installers", "Archives", "Music", "Others"]

SOURCE_LABELS = {"start_menu": "Start Menu", "desktop": "Desktop",
                 "store": "Microsoft Store", "program_files": "Program Files"}

# ── colour palette ───────────────────────────────────────────────────────────
GLASS_BG     = "#1a2235"
GLASS_BORDER = "#2e3d5c"
GLASS_HOVER  = "#222d44"
ACCENT       = "#4c5fd5"

NEON_CYAN    = "#00e5ff"
NEON_PURPLE  = "#b44dff"
NEON_GREEN   = "#39ff14"
NEON_ORANGE  = "#ff7043"
NEON_PINK    = "#ff3cac"

# Header gradient left → right (dark navy → deep violet)
GRAD_LEFT  = (8, 12, 30)
GRAD_RIGHT = (55, 18, 95)

NAV_ITEMS = [
    ("🏠", "Dashboard"), ("🗂️", "Explorer"), ("💾", "Drives"),
    ("🚀", "Apps"), ("📂", "Projects"), ("🎮", "Games"),
    ("🌐", "Web"), ("💡", "Suggestions"),
]

HINDI_EXAMPLES = (
    '🇮🇳  "youtube kholo aur python search karo"  •  '
    '"chrome kholo"  •  "downloads kholo"  •  "mera project kholo"')
EXAMPLES = (
    '💡  "i want to learn python"  •  "play forza"  •  '
    '"find ai pdf"  •  "opn yutube serch lofi"  •  "open my filemind project"')

_HOME = os.path.expanduser("~")


# ─────────────────────────────────────────── known-folder detection ──────────

# Windows FOLDERID GUIDs
_DESKTOP_GUID   = "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}"
_PICTURES_GUID  = "{33E28130-4E1E-4676-835A-98395C3BC3BB}"
_DOCUMENTS_GUID = "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"
_VIDEOS_GUID    = "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}"


def _detect_known_folder(rel_name, guid_str=None, onedrive_sub=None):
    """Return the real path of a known Windows folder, or None if not found.

    Tries (in order):
      1. os.path.expanduser('~/<rel_name>')
      2. OneDrive variants (~/OneDrive/<sub>  and  ~/OneDrive - Personal/<sub>)
      3. SHGetKnownFolderPath via the GUID (respects folder relocation)
    """
    # 1 – standard profile path
    std = os.path.join(_HOME, rel_name)
    if os.path.isdir(std):
        return std

    # 2 – OneDrive variants
    od_sub = onedrive_sub or rel_name
    for od_root in (
        os.path.join(_HOME, "OneDrive"),
        os.path.join(_HOME, "OneDrive - Personal"),
        os.path.join(_HOME, "OneDrive - Business"),
    ):
        od = os.path.join(od_root, od_sub)
        if os.path.isdir(od):
            return od

    # 3 – Windows shell API
    if guid_str:
        try:
            import uuid

            class _GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_ulong),
                    ("Data2", ctypes.c_ushort),
                    ("Data3", ctypes.c_ushort),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            g = uuid.UUID(guid_str)
            guid = _GUID(
                g.time_low, g.time_mid, g.time_hi_version,
                (ctypes.c_ubyte * 8)(*g.bytes[8:]),
            )
            buf = ctypes.c_wchar_p()
            hr = ctypes.windll.shell32.SHGetKnownFolderPath(
                ctypes.byref(guid), 0, None, ctypes.byref(buf))
            if hr == 0 and buf.value:
                p = buf.value
                ctypes.windll.ole32.CoTaskMemFree(buf)
                if os.path.isdir(p):
                    return p
        except Exception:
            pass
    return None


EXPLORER_HOMES = [
    ("📥 Downloads", lambda: config.DOWNLOADS_DIR),
    ("🖥️ Desktop",
     lambda: _detect_known_folder("Desktop", _DESKTOP_GUID)),
    ("📄 Documents",
     lambda: _detect_known_folder("Documents", _DOCUMENTS_GUID)),
    ("🖼️ Pictures",
     lambda: _detect_known_folder("Pictures", _PICTURES_GUID, "Pictures")),
    ("🎬 Videos",
     lambda: _detect_known_folder("Videos", _VIDEOS_GUID)),
]


# ─────────────────────────────────────────────────── drive helpers ───────────

def _get_drives():
    """All accessible drive roots via GetLogicalDrives bitmask."""
    try:
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        return [chr(65 + i) + ":\\" for i in range(26)
                if (bitmask >> i) & 1 and os.path.isdir(chr(65 + i) + ":\\")]
    except Exception:
        return [d for d in ("C:\\", "D:\\", "E:\\") if os.path.isdir(d)]


def _drive_label(drive):
    letter = drive[0].upper()
    icons = {"C": "💻", "D": "💾", "E": "💿", "F": "🔌", "G": "🔌"}
    icon = icons.get(letter, "🖴")
    try:
        total = shutil.disk_usage(drive).total
        return f"{icon} {drive}  ({human_size(total)})"
    except Exception:
        return f"{icon} {drive}"


# ─────────────────────────────────────────────────── utilities ───────────────

def human_size(n):
    n = n or 0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} PB"


# ════════════════════════════════════════════════════════════════════════════
class FileMindApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme(config.UI["color_theme"])

        self.title(f"{config.APP_NAME} V2  –  Command Center  (read-only)")
        self.geometry(config.UI["window_size"])
        self.minsize(*config.UI["min_size"])
        self.configure(fg_color="#0d1117")

        # core services
        self.db          = Database()
        self.scanner     = Scanner(self.db)
        self.app_scanner = AppScanner(self.db)
        self.game_scanner = GameScanner(self.db)
        self.search      = SearchEngine(self.db)
        self.launcher    = AppLauncher(self.db)
        self.projects    = ProjectRegistry(self.db)

        try:
            self.voice = VoiceAssistant(
                self.search,
                on_results=self._voice_results,
                on_status=self._voice_status,
            )
        except Exception:
            self.voice = None

        self._rows_by_id    = {}
        self._current_rows  = []
        self._sort_state    = {}
        self._live_job      = None
        self._busy          = False
        self._pulse_state   = 0
        self._img_cache     = {}
        self.current_view   = "Dashboard"
        self._explorer_path = None
        self._preview_labels = {}

        # AI search
        self._doc_indexer    = DocumentIndexer(self.db)
        self._lmstudio_online = False
        self._ai_search_mode  = False   # cached; refreshed in background
        self._ai_selected_row = None    # selection inside the AI Search panel
        self._ai_thinking     = False   # drives the "Thinking…" animation

        # ── navigation history (browser-style back / forward) ──────────────
        self.back_stack    = []   # list of paths, most-recent-last
        self.forward_stack = []   # cleared on normal navigation

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_area()
        self.sync_ai_mode_ui()       # start in a known, synced OFF state
        self._refresh_stats()
        self._pulse()
        self._start_system_monitor()  # live read-only metrics (after() loop)

        self.after(120, lambda: self.switch_view("Dashboard"))

    # ═══════════════════════════════════════════════════════════ sidebar ══════
    def _build_sidebar(self):
        sb = ctk.CTkScrollableFrame(
            self, width=config.UI["sidebar_width"],
            corner_radius=0, fg_color="#111827",
            scrollbar_fg_color="#111827",
            scrollbar_button_color="#1e2a40",
            scrollbar_button_hover_color="#2a3a52",
        )
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(flag=False)
        self._sb = sb

        # logo
        ctk.CTkLabel(sb, text="🧠 FileMind",
                     font=ctk.CTkFont(size=22, weight="bold"),
                     text_color=NEON_CYAN).pack(pady=(20, 0))
        ctk.CTkLabel(sb, text="Command Center",
                     font=ctk.CTkFont(size=11),
                     text_color="#607090").pack()
        ctk.CTkLabel(sb, text="🔒 100% read-only",
                     font=ctk.CTkFont(size=11),
                     text_color="#39ff80").pack(pady=(2, 10))

        self._sep(sb)

        # nav
        self.nav_buttons = {}
        for emoji, name in NAV_ITEMS:
            b = ctk.CTkButton(
                sb, text=f"{emoji}  {name}", anchor="w", height=34,
                corner_radius=10, fg_color="transparent",
                hover_color=GLASS_HOVER,
                command=lambda n=name: self.switch_view(n))
            b.pack(fill="x", padx=10, pady=2)
            self.nav_buttons[name] = b

        self._sep(sb)

        # actions
        def btn(text, cmd, **kw):
            fg    = kw.pop("fg_color", GLASS_BG)
            hover = kw.pop("hover_color", GLASS_HOVER)
            b = ctk.CTkButton(sb, text=text, anchor="w", height=30,
                              corner_radius=10, fg_color=fg,
                              hover_color=hover, command=cmd, **kw)
            b.pack(fill="x", padx=10, pady=2)
            return b

        self.scan_btn      = btn("🔄 Scan Drives",  self.start_scan)
        self.app_scan_btn  = btn("🚀 Scan Apps",    self.start_app_scan)
        self.game_scan_btn = btn("🎮 Scan Games",   self.start_game_scan)
        btn("➕ Add Project", self.add_project_dialog)
        btn("🎤 Voice",       self.voice_command,
            fg_color="#7a3db8", hover_color="#5e2f8e")

        self._sep(sb)

        # file filter
        ctk.CTkLabel(sb, text="FILTER (files)",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#607090").pack(pady=(4, 0))
        self.category_menu = ctk.CTkOptionMenu(
            sb, values=FILTERS, height=28,
            fg_color=GLASS_BG, button_color=ACCENT,
            command=self.filter_by_category)
        self.category_menu.pack(fill="x", padx=10, pady=(2, 4))

        self._sep(sb)

        # ── drives section ──
        ctk.CTkLabel(sb, text="DRIVES",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#607090").pack(pady=(4, 2))
        self._drive_buttons_frame = ctk.CTkFrame(sb, fg_color="transparent")
        self._drive_buttons_frame.pack(fill="x", padx=10, pady=(0, 6))
        self._populate_drive_buttons()

        self._sep(sb)

        # ── AI search section ──
        ctk.CTkLabel(sb, text="AI SEARCH",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#607090").pack(pady=(4, 2))

        self._ai_status_lbl = ctk.CTkLabel(
            sb, text="⏳ Checking AI Brain…",
            font=ctk.CTkFont(size=10), text_color="#607090",
            wraplength=160, justify="left")
        self._ai_status_lbl.pack(pady=(0, 4), padx=10, anchor="w")

        self._ai_index_btn = btn("🤖 Index Docs",  self.start_doc_index,
            fg_color="#0a2a1a", hover_color="#0d3d22")
        self._ai_search_btn = btn("🔍 AI Search",   self._toggle_ai_search,
            fg_color="#180a2a", hover_color="#2d1050")

        self._brain_status_lbl = ctk.CTkLabel(
            sb, text="🧠 AI Brain: checking…",
            font=ctk.CTkFont(size=10), text_color="#607090",
            wraplength=160, justify="left")
        self._brain_status_lbl.pack(pady=(2, 6), padx=10, anchor="w")

        # check LM Studio status 1 s after UI builds, then every 5 s
        self.after(1000, self._update_ai_status)

    def filter_by_category(self, category=None):
        """Filter the file list by the sidebar category dropdown selection."""
        if category is None:
            category = self.category_menu.get()
        if category == "All":
            self._load_async(
                lambda: self.db.recent_files(RESULT_LIMIT), "All Files")
        else:
            self._load_async(
                lambda: self.search.search(
                    file_type=category, limit=RESULT_LIMIT),
                f"Filter: {category}",
                group_as_files=False)

    def _sep(self, parent):
        ctk.CTkFrame(parent, height=1, fg_color="#1e2a40").pack(
            fill="x", padx=10, pady=6)

    def _populate_drive_buttons(self):
        for w in self._drive_buttons_frame.winfo_children():
            w.destroy()
        drives = _get_drives()
        for d in drives:
            label = _drive_label(d)
            ctk.CTkButton(
                self._drive_buttons_frame,
                text=label, anchor="w", height=28, corner_radius=8,
                fg_color=GLASS_BG, hover_color=GLASS_HOVER,
                border_width=1, border_color=GLASS_BORDER,
                font=ctk.CTkFont(size=11),
                command=lambda p=d: self._explore_drive(p),
            ).pack(fill="x", pady=2)
        if not drives:
            ctk.CTkLabel(self._drive_buttons_frame, text="No drives detected",
                         font=ctk.CTkFont(size=10), text_color="#607090").pack()

    def _explore_drive(self, path):
        self.explore(path)

    # ═══════════════════════════════════════════════════════════ header ═══════
    def _build_header(self, parent):
        self.header = tk.Canvas(parent, height=70, highlightthickness=0, bd=0,
                                bg="#0d1117")
        self.header.grid(row=0, column=0, columnspan=2, sticky="ew",
                         pady=(0, 10))
        self.header.bind("<Configure>", lambda e: self._draw_header())

    def _draw_header(self):
        c = self.header
        c.delete("all")
        w = max(c.winfo_width(), 400)
        h = 70

        # gradient background
        steps = 80
        seg = w / steps
        for i in range(steps):
            f = i / (steps - 1)
            r = int(GRAD_LEFT[0] + (GRAD_RIGHT[0] - GRAD_LEFT[0]) * f)
            g = int(GRAD_LEFT[1] + (GRAD_RIGHT[1] - GRAD_LEFT[1]) * f)
            b = int(GRAD_LEFT[2] + (GRAD_RIGHT[2] - GRAD_LEFT[2]) * f)
            c.create_rectangle(i * seg, 0, (i + 1) * seg + 1, h,
                               fill=f"#{r:02x}{g:02x}{b:02x}", width=0)

        # neon bottom glow line (multi-layer for glow effect)
        for offset, alpha in ((3, "#1a0040"), (2, "#3a0080"),
                              (1, "#7700cc"), (0, "#b44dff")):
            c.create_line(0, h - offset, w, h - offset,
                          fill=alpha, width=1 + offset // 2)

        # circuit-board decorative dots
        for x in range(20, w - 20, 60):
            c.create_oval(x - 2, h - 9, x + 2, h - 5,
                          fill=NEON_PURPLE, outline="")

        # title
        c.create_text(22, h // 2 - 11, anchor="w",
                      text="🧠 FileMind V2",
                      font=("Segoe UI", 20, "bold"), fill="#ffffff")
        # neon glow shadow for title
        c.create_text(22, h // 2 + 13, anchor="w",
                      text="Personal AI Laptop Command Center",
                      font=("Segoe UI", 10), fill=NEON_CYAN)

        # right badge
        c.create_rectangle(w - 120, h // 2 - 14, w - 10, h // 2 + 14,
                           fill="#0a1a08", outline=NEON_GREEN, width=1)
        c.create_text(w - 65, h // 2, anchor="center",
                      text="🔒 READ-ONLY",
                      font=("Segoe UI", 9, "bold"), fill=NEON_GREEN)

    # ═══════════════════════════════════════════════════════════ cards ════════
    def _make_card(self, parent, col, emoji, title, on_click):
        card = ctk.CTkFrame(
            parent, corner_radius=16, fg_color=GLASS_BG,
            border_width=1, border_color=NEON_PURPLE)
        card.grid(row=0, column=col, sticky="nsew",
                  padx=(0 if col == 0 else 6, 0))
        parent.grid_columnconfigure(col, weight=1)

        icon  = ctk.CTkLabel(card, text=emoji, font=ctk.CTkFont(size=26))
        icon.pack(pady=(10, 0))
        value = ctk.CTkLabel(card, text="-",
                             font=ctk.CTkFont(size=20, weight="bold"),
                             text_color=NEON_CYAN)
        value.pack()
        cap   = ctk.CTkLabel(card, text=title, font=ctk.CTkFont(size=11),
                             text_color="#8090b0")
        cap.pack(pady=(0, 10))

        for widget in (card, icon, value, cap):
            widget.bind("<Button-1>", lambda e: on_click())
            widget.bind("<Enter>",
                        lambda e, c=card: c.configure(
                            fg_color=GLASS_HOVER, border_color=NEON_CYAN))
            widget.bind("<Leave>",
                        lambda e, c=card: c.configure(
                            fg_color=GLASS_BG, border_color=NEON_PURPLE))
        return value

    # ════════════════════════════════════ live system monitor (Dashboard) ═════
    def _hover_glow(self, widget, base_border, hot_border,
                    base_bg=None, hot_bg=None):
        """Lightweight hover effect: brighter border (+ optional bg) on hover."""
        def enter(_e):
            kw = {"border_color": hot_border}
            if hot_bg:
                kw["fg_color"] = hot_bg
            widget.configure(**kw)

        def leave(_e):
            kw = {"border_color": base_border}
            if base_bg:
                kw["fg_color"] = base_bg
            widget.configure(**kw)
        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)

    def _metric_card(self, parent, col, emoji, title, accent, with_bar=True):
        """A compact live-metric card with a value line and a thin progress bar."""
        card = ctk.CTkFrame(parent, corner_radius=12, fg_color=GLASS_BG,
                            border_width=1, border_color=GLASS_BORDER)
        card.grid(row=0, column=col, sticky="nsew", padx=3, pady=2)
        parent.grid_columnconfigure(col, weight=1)

        top = ctk.CTkLabel(card, text=f"{emoji}  {title}",
                           font=ctk.CTkFont(size=11, weight="bold"),
                           text_color="#8090b0")
        top.pack(anchor="w", padx=10, pady=(8, 0))
        value = ctk.CTkLabel(card, text="—",
                             font=ctk.CTkFont(size=16, weight="bold"),
                             text_color=accent)
        value.pack(anchor="w", padx=10)
        sub = ctk.CTkLabel(card, text="", font=ctk.CTkFont(size=10),
                           text_color="#607090")
        sub.pack(anchor="w", padx=10)

        bar = None
        if with_bar:
            bar = ctk.CTkProgressBar(card, height=6, corner_radius=4,
                                     progress_color=accent)
            bar.set(0)
            bar.pack(fill="x", padx=10, pady=(2, 8))
        else:
            ctk.CTkFrame(card, height=6, fg_color="transparent").pack(
                pady=(2, 8))

        self._hover_glow(card, GLASS_BORDER, accent)
        return {"value": value, "sub": sub, "bar": bar}

    def _build_dashboard_monitor(self, parent):
        """Build the live-monitor strip + AI Command Center inside the cards
        frame (rows 1 & 2). Hidden until the Dashboard is shown."""
        # ── live metric strip (row 1 of the cards frame) ──
        strip = ctk.CTkFrame(parent, fg_color="transparent")
        strip.grid(row=1, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        self._monitor_strip = strip
        self._metrics = {
            "cpu":     self._metric_card(strip, 0, "🧮", "CPU", NEON_CYAN),
            "ram":     self._metric_card(strip, 1, "💾", "RAM", NEON_PURPLE),
            "gpu":     self._metric_card(strip, 2, "🎮", "GPU", NEON_GREEN),
            "disk_c":  self._metric_card(strip, 3, "🗄️", "DISK C", NEON_ORANGE),
            "disk_d":  self._metric_card(strip, 4, "🗄️", "DISK D", NEON_ORANGE),
            "disk_e":  self._metric_card(strip, 5, "🗄️", "DISK E", NEON_ORANGE),
            "battery": self._metric_card(strip, 6, "🔋", "BATTERY", NEON_GREEN),
        }

        # ── AI Command Center (row 2 of the cards frame) ──
        cc = ctk.CTkFrame(parent, corner_radius=12, fg_color="#12182e",
                          border_width=1, border_color=NEON_PURPLE)
        cc.grid(row=2, column=0, columnspan=5, sticky="ew", pady=(8, 0))
        cc.grid_columnconfigure(0, weight=1)
        cc.grid_columnconfigure(1, weight=1)
        cc.grid_columnconfigure(2, weight=1)
        self._cmd_center = cc

        ctk.CTkLabel(cc, text="🛰  AI Command Center",
                     font=ctk.CTkFont(size=13, weight="bold"),
                     text_color=NEON_CYAN).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 2))

        # column 0 — live status
        stat = ctk.CTkFrame(cc, fg_color="transparent")
        stat.grid(row=1, column=0, sticky="nw", padx=12, pady=(0, 10))
        ctk.CTkLabel(stat, text="STATUS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#506080").pack(anchor="w")
        self._cc_brain = ctk.CTkLabel(stat, text="🧠 AI Brain: …",
                                      font=ctk.CTkFont(size=11),
                                      text_color="#c8d4f0")
        self._cc_brain.pack(anchor="w", pady=1)
        self._cc_aisearch = ctk.CTkLabel(stat, text="🔍 AI Search: Off",
                                         font=ctk.CTkFont(size=11),
                                         text_color="#c8d4f0")
        self._cc_aisearch.pack(anchor="w", pady=1)
        self._cc_net = ctk.CTkLabel(stat, text="🌐 Network: …",
                                    font=ctk.CTkFont(size=11),
                                    text_color="#c8d4f0")
        self._cc_net.pack(anchor="w", pady=1)
        self._cc_project = ctk.CTkLabel(stat, text="📂 Active Project: —",
                                        font=ctk.CTkFont(size=11),
                                        text_color="#c8d4f0",
                                        wraplength=210, justify="left")
        self._cc_project.pack(anchor="w", pady=1)

        # column 1 — recent AI commands
        recent = ctk.CTkFrame(cc, fg_color="transparent")
        recent.grid(row=1, column=1, sticky="nw", padx=12, pady=(0, 10))
        ctk.CTkLabel(recent, text="RECENT COMMANDS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#506080").pack(anchor="w")
        self._cc_recent_box = ctk.CTkFrame(recent, fg_color="transparent")
        self._cc_recent_box.pack(anchor="w", fill="x")

        # column 2 — suggested actions + project shortcuts
        acts = ctk.CTkFrame(cc, fg_color="transparent")
        acts.grid(row=1, column=2, sticky="nw", padx=12, pady=(0, 10))
        ctk.CTkLabel(acts, text="SUGGESTED ACTIONS",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color="#506080").pack(anchor="w")
        self._cc_actions_box = ctk.CTkFrame(acts, fg_color="transparent")
        self._cc_actions_box.pack(anchor="w", fill="x")

        # hidden until the Dashboard is active
        strip.grid_remove()
        cc.grid_remove()

    def _cc_button(self, parent, text, cmd):
        b = ctk.CTkButton(parent, text=text, anchor="w", height=26,
                          corner_radius=8, fg_color=GLASS_BG,
                          hover_color=GLASS_HOVER, border_width=1,
                          border_color=GLASS_BORDER,
                          font=ctk.CTkFont(size=11), command=cmd)
        b.pack(anchor="w", fill="x", pady=2)
        self._hover_glow(b, GLASS_BORDER, NEON_CYAN)
        return b

    def _show_dashboard_monitor(self, show):
        if not hasattr(self, "_monitor_strip"):
            return
        if show:
            self._monitor_strip.grid()
            self._cmd_center.grid()
            self._refresh_command_center()
        else:
            self._monitor_strip.grid_remove()
            self._cmd_center.grid_remove()

    def _refresh_command_center(self):
        """Rebuild the recent-commands list + suggested actions / shortcuts.
        Cheap, called only when the Dashboard becomes visible."""
        if not hasattr(self, "_cc_recent_box"):
            return

        # AI Search status label
        on = getattr(self, "_ai_search_mode", False)
        self._cc_aisearch.configure(
            text="🔍 AI Search: " + ("On" if on else "Off"))

        # recent commands
        for w in self._cc_recent_box.winfo_children():
            w.destroy()
        try:
            cmds = self.db.recent_commands(5)
        except Exception:
            cmds = []
        if cmds:
            for h in cmds[:4]:
                txt = h.get("command", "")[:26]
                self._cc_button(self._cc_recent_box, f"⌨  {txt}",
                                lambda c=h.get("command", ""): self._rerun(c))
        else:
            ctk.CTkLabel(self._cc_recent_box, text="No commands yet.",
                         font=ctk.CTkFont(size=11),
                         text_color="#506080").pack(anchor="w")

        # suggested actions + project shortcuts
        for w in self._cc_actions_box.winfo_children():
            w.destroy()
        self._cc_button(self._cc_actions_box, "🕒  Recent files",
                        self.show_recent)
        self._cc_button(self._cc_actions_box, "📥  Downloads",
                        self.show_downloads)
        self._cc_button(self._cc_actions_box, "🔍  AI Search",
                        self._toggle_ai_search)
        try:
            recent_projects = self.projects.recent(2)
        except Exception:
            recent_projects = []
        for p in recent_projects:
            nm = p.get("name", "")
            self._cc_button(self._cc_actions_box, f"📂  Open {nm[:18]}",
                            lambda n=nm: self.execute_command_text(
                                f"open {n} project"))

        # active project label
        try:
            act = self.projects.recent(1)
        except Exception:
            act = []
        self._cc_project.configure(
            text="📂 Active Project: " + (act[0]["name"] if act else "—"))

    def execute_command_text(self, text):
        """Run a command string through the normal routing (used by shortcuts)."""
        self.search_var.set(text)
        self.execute_command()

    def _rerun(self, command):
        if command:
            self.execute_command_text(command)

    # ── background-safe update loop (after(), never a while loop) ─────────────
    def _start_system_monitor(self):
        self._sysmon_busy = False
        self._check_gpu = True
        self._cpu_samples = []      # last 5 CPU readings (rolling)
        self._cpu_cores = None      # cached physical/logical core counts
        try:
            system_monitor.prime_cpu()   # prime psutil's CPU counter once
        except Exception:
            pass
        self.after(800, self._poll_system)     # first read shortly after launch

    def _poll_system(self):
        """Schedule a background snapshot, then reschedule. Never blocks UI."""
        if not getattr(self, "_sysmon_busy", False):
            self._sysmon_busy = True

            def work():
                try:
                    data = system_monitor.snapshot(
                        check_gpu=getattr(self, "_check_gpu", True))
                except Exception:
                    data = {}
                self.after(0, self._apply_metrics, data)

            threading.Thread(target=work, daemon=True).start()
        # reschedule regardless (every 2 s)
        self.after(2000, self._poll_system)

    def _apply_metrics(self, data):
        """Apply a snapshot to the dashboard widgets (main thread)."""
        self._sysmon_busy = False
        if not hasattr(self, "_metrics"):
            return
        psutil_ok = system_monitor.psutil_available()
        try:
            if not psutil_ok:
                # CPU & RAM need psutil — tell the user exactly what to do
                for k in ("cpu", "ram"):
                    m = self._metrics.get(k)
                    if m:
                        m["value"].configure(text=system_monitor.INSTALL_HINT)
                        m["sub"].configure(text="psutil not installed")
                        if m["bar"]:
                            m["bar"].set(0)
            else:
                self._apply_cpu(data.get("cpu"), data.get("cores"))
                ram = data.get("ram")
                self._update_metric(
                    "ram", ram.get("percent") if ram else None, "%",
                    sub=(f"{human_size(ram['used'])} / "
                         f"{human_size(ram['total'])}"
                         if ram else "Unavailable"))

            # Disk C / D / E (works with or without psutil — shutil fallback)
            disks = data.get("disks") or {}
            for letter, key in (("C", "disk_c"), ("D", "disk_d"),
                                ("E", "disk_e")):
                self._apply_disk(key, disks.get(letter), letter)

            self._apply_gpu(data.get("gpu"))                 # unchanged
            self._apply_battery(data.get("battery"), psutil_ok)
            self._apply_cc_status(data.get("network"), psutil_ok)
        except Exception:
            pass   # a sensor hiccup must never crash the UI

    def _apply_cpu(self, raw, cores):
        """Smoothed, Task-Manager-like CPU %.

        Keeps the last 5 raw samples and shows a trimmed mean (one min and one
        max dropped) so a single 0% or 100% spike never jumps the card."""
        m = self._metrics.get("cpu")
        if not m:
            return
        if cores and not self._cpu_cores:
            self._cpu_cores = cores
        if raw is not None:
            self._cpu_samples.append(float(raw))
            if len(self._cpu_samples) > 5:
                self._cpu_samples = self._cpu_samples[-5:]
        samples = self._cpu_samples
        if not samples:
            m["value"].configure(text="…")
            return
        if len(samples) >= 3:                 # drop one min + one max (de-spike)
            trimmed = sorted(samples)[1:-1]
            disp = sum(trimmed) / len(trimmed)
        else:
            disp = sum(samples) / len(samples)
        m["value"].configure(text=f"{disp:.0f}%")
        if m["bar"]:
            m["bar"].set(max(0.0, min(1.0, disp / 100.0)))
        c = self._cpu_cores or {}
        if c.get("physical") and c.get("logical"):
            m["sub"].configure(
                text=f"{c['physical']} cores · {c['logical']} threads")

    def _apply_disk(self, key, d, letter):
        m = self._metrics.get(key)
        if not m:
            return
        if not d:
            m["value"].configure(text="Unavailable")
            m["sub"].configure(text=f"{letter}:\\ not present")
            if m["bar"]:
                m["bar"].set(0)
            return
        pct = d.get("percent", 0)
        m["value"].configure(text=f"{pct:.0f}%")
        if m["bar"]:
            m["bar"].set(max(0.0, min(1.0, pct / 100.0)))
        m["sub"].configure(
            text=f"{human_size(d['used'])} / {human_size(d['total'])}")

    def _update_metric(self, key, percent, unit, sub=None):
        m = self._metrics.get(key)
        if not m:
            return
        if percent is None:
            m["value"].configure(text="Unavailable")
            if m["bar"]:
                m["bar"].set(0)
        else:
            m["value"].configure(text=f"{percent:.0f}{unit}")
            if m["bar"]:
                m["bar"].set(max(0.0, min(1.0, percent / 100.0)))
        if sub is not None:
            m["sub"].configure(text=sub)

    def _apply_gpu(self, gpu):
        m = self._metrics.get("gpu")
        if not m:
            return
        if not gpu:
            # no NVIDIA GPU detected — stop probing and show clearly
            self._check_gpu = False
            m["value"].configure(text="Not available")
            m["sub"].configure(text="No NVIDIA GPU")
            if m["bar"]:
                m["bar"].set(0)
            return
        load = gpu.get("load")
        if load is None:
            m["value"].configure(text="Not available")
            if m["bar"]:
                m["bar"].set(0)
        else:
            m["value"].configure(text=f"{load:.0f}%")
            if m["bar"]:
                m["bar"].set(max(0.0, min(1.0, load / 100.0)))
        temp = gpu.get("temp")
        m["sub"].configure(
            text=(f"{temp:.0f}°C" if temp is not None else "Temp N/A")
            + "  ·  FPS N/A")

    def _apply_battery(self, bat, psutil_ok=True):
        m = self._metrics.get("battery")
        if not m:
            return
        if not psutil_ok:
            m["value"].configure(text=system_monitor.INSTALL_HINT)
            m["sub"].configure(text="psutil not installed")
            if m["bar"]:
                m["bar"].set(0)
            return
        if bat is None:                       # sensor error
            m["value"].configure(text="Unavailable")
            m["sub"].configure(text="")
            if m["bar"]:
                m["bar"].set(0)
            return
        if not bat.get("present", False):     # desktop / no battery
            m["value"].configure(text="No battery detected")
            m["sub"].configure(text="On AC power")
            if m["bar"]:
                m["bar"].set(0)
            return
        pct = bat.get("percent", 0)
        m["value"].configure(text=f"{pct:.0f}%")
        if m["bar"]:
            m["bar"].set(max(0.0, min(1.0, pct / 100.0)))
        m["sub"].configure(text="Charging" if bat.get("plugged") else "On battery")

    def _apply_cc_status(self, net, psutil_ok=True):
        # network + AI Brain + AI Search live status in the command center
        if hasattr(self, "_cc_net"):
            if not psutil_ok:
                self._cc_net.configure(
                    text="🌐 Network: pip install psutil")
            elif net is None:
                self._cc_net.configure(text="🌐 Network: Unavailable")
            elif net.get("online"):
                self._cc_net.configure(text="🌐 Network: Online")
            else:
                self._cc_net.configure(text="🌐 Network: Offline")
        if hasattr(self, "_cc_brain"):
            online = getattr(self, "_lmstudio_online", False)
            self._cc_brain.configure(
                text="🧠 AI Brain: " + ("Online" if online else "Offline"))
        if hasattr(self, "_cc_aisearch"):
            on = getattr(self, "_ai_search_mode", False)
            self._cc_aisearch.configure(
                text="🔍 AI Search: " + ("On" if on else "Off"))

    # ══════════════════════════════════════════════════════════ main area ═════
    def _build_main_area(self):
        main = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=14, pady=14)
        main.grid_columnconfigure(0, weight=1)
        main.grid_columnconfigure(1, weight=0)   # preview panel (fixed width)
        main.grid_rowconfigure(7, weight=1)
        self._main = main

        self._build_header(main)

        # ── command bar ──────────────────────────────────────────────────────
        bar = ctk.CTkFrame(main, fg_color="transparent")
        bar.grid(row=1, column=0, columnspan=2, sticky="ew")
        bar.grid_columnconfigure(0, weight=1)

        self.search_var = tk.StringVar()
        self.entry = ctk.CTkEntry(
            bar, textvariable=self.search_var, height=46,
            corner_radius=14, border_color=NEON_PURPLE,
            border_width=2, fg_color=GLASS_BG,
            placeholder_text=(
                '⌨️  Command center…  '
                '"youtube kholo aur python search karo"  '
                '"play forza"  "i want to learn python"  '
                '"find ai pdf"'))
        self.entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<KP_Enter>", self._on_enter)
        self.bind("<Return>", self._on_enter_global)
        self.entry.bind("<KeyRelease>", self._on_type)

        ctk.CTkButton(
            bar, text="▶  Run", width=84, height=46,
            corner_radius=14, font=ctk.CTkFont(weight="bold"),
            fg_color=ACCENT, hover_color="#3a4db0",
            command=self.execute_command,
        ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(
            bar, text="🎤", width=48, height=46, corner_radius=14,
            fg_color="#7a3db8", hover_color="#5e2f8e",
            command=self.voice_command,
        ).grid(row=0, column=2)

        # ── suggestion panel ─────────────────────────────────────────────────
        self.suggest_frame = ctk.CTkFrame(
            main, corner_radius=14, fg_color="#12182e",
            border_width=1, border_color=NEON_PURPLE)
        self.suggest_frame.grid(row=2, column=0, columnspan=2,
                                sticky="ew", pady=(10, 0))
        self.suggest_frame.grid_remove()

        # ── example strips ───────────────────────────────────────────────────
        strip_frame = ctk.CTkFrame(main, fg_color="transparent")
        strip_frame.grid(row=3, column=0, columnspan=2, sticky="ew",
                         pady=(6, 0))
        self.examples_label = ctk.CTkLabel(
            strip_frame, text=EXAMPLES,
            font=ctk.CTkFont(size=11), text_color="#6070a0", anchor="w")
        self.examples_label.pack(anchor="w")
        self.hindi_label = ctk.CTkLabel(
            strip_frame, text=HINDI_EXAMPLES,
            font=ctk.CTkFont(size=11), text_color="#8870c0", anchor="w")
        self.hindi_label.pack(anchor="w")

        # ── stat cards (Dashboard) ───────────────────────────────────────────
        self.cards_frame = ctk.CTkFrame(main, fg_color="transparent")
        self.cards_frame.grid(row=4, column=0, columnspan=2,
                              sticky="ew", pady=(10, 0))
        self.card_files    = self._make_card(self.cards_frame, 0, "📁",
                                             "Indexed Files", self.show_recent)
        self.card_apps     = self._make_card(self.cards_frame, 1, "🚀",
                                             "Installed Apps",
                                             lambda: self.switch_view("Apps"))
        self.card_games    = self._make_card(self.cards_frame, 2, "🎮", "Games",
                                             lambda: self.switch_view("Games"))
        self.card_web      = self._make_card(self.cards_frame, 3, "🌐",
                                             "Websites",
                                             lambda: self.switch_view("Web"))
        self.card_projects = self._make_card(self.cards_frame, 4, "📂",
                                             "Projects",
                                             lambda: self.switch_view("Projects"))

        # ── live system monitor + AI command center (Dashboard only) ─────────
        self._build_dashboard_monitor(self.cards_frame)

        # ── quick buttons (Dashboard) ────────────────────────────────────────
        self.quick_frame = ctk.CTkFrame(main, fg_color="transparent")
        self.quick_frame.grid(row=5, column=0, columnspan=2,
                              sticky="ew", pady=(10, 0))

        def qbtn(text, cmd):
            ctk.CTkButton(
                self.quick_frame, text=text, height=30, width=10,
                corner_radius=10, fg_color=GLASS_BG, hover_color=GLASS_HOVER,
                border_width=1, border_color=GLASS_BORDER,
                command=cmd,
            ).pack(side="left", padx=(0, 6))

        qbtn("🕒 Recent",      self.show_recent)
        qbtn("🐘 Large Files", self.show_large)
        qbtn("📥 Downloads",   self.show_downloads)
        qbtn("👯 Duplicates",  self.show_duplicates)
        qbtn("📸 Screenshots", self.show_screenshots)
        qbtn("📊 Summary",     self.show_type_summary)
        qbtn("🕘 History",     self.show_history)
        qbtn("📂 Resume",      self.show_workspace_resume)

        # ── explorer bar (Explorer / Drives views) ───────────────────────────
        self.explorer_bar = ctk.CTkFrame(
            main, corner_radius=12, fg_color=GLASS_BG,
            border_width=1, border_color=GLASS_BORDER)

        self.btn_back = ctk.CTkButton(
            self.explorer_bar, text="◀ Back", width=74, height=28,
            corner_radius=8, fg_color="#1a2540", hover_color=GLASS_HOVER,
            state="disabled", command=self.explorer_back,
        )
        self.btn_back.pack(side="left", padx=(8, 2), pady=6)

        self.btn_fwd = ctk.CTkButton(
            self.explorer_bar, text="▶ Fwd", width=68, height=28,
            corner_radius=8, fg_color="#1a2540", hover_color=GLASS_HOVER,
            state="disabled", command=self.explorer_forward,
        )
        self.btn_fwd.pack(side="left", padx=(0, 2), pady=6)

        ctk.CTkButton(
            self.explorer_bar, text="🏠 Home", width=76, height=28,
            corner_radius=8, fg_color="#1a2540", hover_color=GLASS_HOVER,
            command=self.go_explorer_home,
        ).pack(side="left", padx=(0, 6), pady=6)

        self.breadcrumb = ctk.CTkLabel(
            self.explorer_bar, text="", anchor="w",
            font=ctk.CTkFont(size=12), text_color=NEON_CYAN)
        self.breadcrumb.pack(side="left", fill="x", expand=True, padx=6)

        # home shortcut buttons (right side)
        for label, getter in reversed(EXPLORER_HOMES):
            ctk.CTkButton(
                self.explorer_bar, text=label, height=28, width=10,
                corner_radius=8, fg_color="#1a2540", hover_color=GLASS_HOVER,
                command=lambda g=getter: self._safe_explore(g()),
            ).pack(side="right", padx=3, pady=6)

        ctk.CTkButton(
            self.explorer_bar, text="💾 Drives", height=28, width=10,
            corner_radius=8, fg_color="#1a2540", hover_color=GLASS_HOVER,
            command=lambda: self.switch_view("Drives"),
        ).pack(side="right", padx=3, pady=6)

        # ── progress bar ─────────────────────────────────────────────────────
        self.progress = ctk.CTkProgressBar(
            main, mode="indeterminate", height=5,
            corner_radius=4, progress_color=NEON_PURPLE)
        self.progress.grid(row=6, column=0, columnspan=2,
                           sticky="ew", pady=(10, 4))
        self.progress.set(0)

        # ── result table ─────────────────────────────────────────────────────
        table_frame = ctk.CTkFrame(
            main, corner_radius=14, fg_color=GLASS_BG,
            border_width=1, border_color=GLASS_BORDER)
        table_frame.grid(row=7, column=0, sticky="nsew")
        self._table_frame = table_frame   # kept so AI Search panel can replace it
        table_frame.grid_columnconfigure(0, weight=1)
        table_frame.grid_rowconfigure(0, weight=0)   # AI banner
        table_frame.grid_rowconfigure(1, weight=1)   # tree

        # (The old in-table "AI Search Mode ON" banner was removed — it was a
        #  second, out-of-sync source of truth. AI-mode UI is now driven solely
        #  by sync_ai_mode_ui(); the Exit button lives in the AI Search panel.)

        self._style_treeview()
        cols = ("name", "type", "extension", "size", "modified", "folder")
        self.tree = ttk.Treeview(
            table_frame, columns=cols, show="tree headings",
            selectmode="browse")
        self.tree.column("#0", width=46, minwidth=46, stretch=False)
        self.tree.heading("#0", text="")
        headings = {
            "name":     ("Name", 270),
            "type":     ("Type", 105),
            "extension": ("Ext", 56),
            "size":     ("Size", 84),
            "modified": ("Modified", 140),
            "folder":   ("Location", 270),
        }
        for col, (text, width) in headings.items():
            self.tree.heading(col, text=text,
                              command=lambda c=col: self.sort_by(c))
            self.tree.column(col, width=width, anchor="w",
                             stretch=col in ("name", "folder"))
        self.tree.tag_configure("header", foreground="#6070a0")
        self.tree.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)

        vsb = ttk.Scrollbar(table_frame, orient="vertical",
                            command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=1, column=1, sticky="ns", pady=6)

        # events
        self.tree.bind("<Double-1>",        lambda e: self.open_selected())
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        # ── right-side preview panel ─────────────────────────────────────────
        self._build_preview_panel(main)

        # ── AI Search panel (replaces the table when AI Search is ON) ─────────
        self._build_ai_panel(main)

        # ── action bar ───────────────────────────────────────────────────────
        actions = ctk.CTkFrame(main, fg_color="transparent")
        actions.grid(row=8, column=0, columnspan=2, sticky="ew", pady=(8, 0))

        ctk.CTkButton(
            actions, text="Open / Launch", corner_radius=10,
            fg_color=ACCENT, hover_color="#3a4db0",
            command=self.open_selected,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            actions, text="Open Folder Location", corner_radius=10,
            fg_color=GLASS_BG, hover_color=GLASS_HOVER,
            border_width=1, border_color=GLASS_BORDER,
            command=self.open_selected_folder,
        ).pack(side="left", padx=(0, 8))

        self.status_dot = ctk.CTkLabel(
            actions, text="●", text_color=NEON_GREEN,
            font=ctk.CTkFont(size=14))
        self.status_dot.pack(side="left", padx=(10, 4))
        self.status_label = ctk.CTkLabel(
            actions, text="Ready.", anchor="w", text_color="#8090b0")
        self.status_label.pack(side="left", fill="x", expand=True)

    # ── preview panel ────────────────────────────────────────────────────────
    def _build_preview_panel(self, parent):
        panel = ctk.CTkFrame(
            parent, width=264, corner_radius=14, fg_color=GLASS_BG,
            border_width=2, border_color=NEON_PURPLE)
        panel.grid(row=7, column=1, sticky="nsew", padx=(8, 0))
        panel.grid_propagate(flag=False)

        ctk.CTkLabel(
            panel, text="📋  FILE DETAILS",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=NEON_CYAN,
        ).pack(pady=(14, 2), padx=14, anchor="w")
        ctk.CTkFrame(panel, height=1, fg_color=NEON_PURPLE).pack(
            fill="x", padx=10, pady=(0, 6))

        fields = [
            ("name",     "🏷️  Name"),
            ("type",     "📁  Type"),
            ("size",     "💾  Size"),
            ("modified", "🕐  Modified"),
            ("path",     "📍  Path"),
        ]
        for key, label_text in fields:
            ctk.CTkLabel(
                panel, text=label_text,
                font=ctk.CTkFont(size=10, weight="bold"),
                text_color="#506080",
            ).pack(anchor="w", padx=14, pady=(6, 0))
            val = ctk.CTkLabel(
                panel, text="—",
                font=ctk.CTkFont(size=10),
                text_color="#c8d4f0",
                wraplength=236, anchor="w", justify="left")
            val.pack(anchor="w", padx=18, pady=(0, 2))
            self._preview_labels[key] = val

        self._preview_hint = ctk.CTkLabel(
            panel,
            text="← Select any item\nto see details",
            font=ctk.CTkFont(size=11),
            text_color="#2a3550",
            justify="center")
        self._preview_hint.pack(expand=True)

        # AI Explainer section
        ctk.CTkFrame(panel, height=1, fg_color=NEON_PURPLE).pack(
            fill="x", padx=10, pady=(6, 0))
        self._explain_btn = ctk.CTkButton(
            panel, text="🧠 Explain with AI", height=28,
            corner_radius=8, fg_color="#180a2a", hover_color="#2d1050",
            command=self._explain_selected_file)
        self._explain_btn.pack(fill="x", padx=10, pady=(6, 4))
        self._explain_lbl = ctk.CTkLabel(
            panel, text="",
            font=ctk.CTkFont(size=10), text_color="#a0b8d0",
            wraplength=236, justify="left", anchor="w")
        self._explain_lbl.pack(anchor="w", padx=14, pady=(0, 8))

    def _reset_preview(self):
        for lbl in self._preview_labels.values():
            lbl.configure(text="—")
        self._preview_hint.configure(
            text="← Select any item\nto see details")
        if hasattr(self, "_explain_lbl"):
            self._explain_lbl.configure(text="")

    def _on_tree_select(self, event=None):
        sel = self.tree.selection()
        if not sel:
            return
        row = self._rows_by_id.get(sel[0])
        if row:
            self._show_preview(row)

    def _show_preview(self, row):
        no_size = row["file_type"] in (
            config.FOLDER_CATEGORY, "App", "Game", "Project", "Web")
        self._preview_hint.configure(text="")
        self._preview_labels["name"].configure(
            text=row.get("name") or "—")
        self._preview_labels["type"].configure(
            text=row.get("file_type") or "—")
        self._preview_labels["size"].configure(
            text="—" if no_size else human_size(row.get("size", 0)))
        self._preview_labels["modified"].configure(
            text=row.get("modified_date") or "—")
        self._preview_labels["path"].configure(
            text=row.get("path") or "—")

    def _current_selected_row(self):
        """Selected row from the tree, or from the AI Search panel."""
        sel = self.tree.selection()
        if sel:
            return self._rows_by_id.get(sel[0])
        if getattr(self, "_ai_search_mode", False):
            return getattr(self, "_ai_selected_row", None)
        return None

    def open_selected(self):
        """Open / launch the currently selected item (tree or AI panel)."""
        row = self._current_selected_row()
        if not row:
            self._set_status("Select an item first.")
            return
        self._open_row(row)

    def _open_row(self, row):
        """Open / launch a result row. READ-ONLY."""
        if not row:
            return
        path = row.get("path", "")
        ftype = row.get("file_type", "")

        # folders → navigate in Explorer
        if ftype == config.FOLDER_CATEGORY or (path and os.path.isdir(path)):
            self.explore(path)
            return

        # apps → launch via launcher
        if ftype == "App":
            ok, msg = self.launcher.launch(row)
            self._set_status(("Command executed: " if ok else "Error: ") + msg)
            return

        # games → launch
        if ftype == "Game":
            import game_scanner as _gm
            ok, msg = _gm.launch_game(row)
            self._set_status(("Command executed: " if ok else "Error: ") + msg)
            return

        # projects → open in editor
        if ftype == "Project":
            project = self.projects.best(row.get("name", ""))
            if project:
                ok, msg = self.projects.open_project(project)
                self._set_status(("Command executed: " if ok else "Error: ") + msg)
            return

        # web URLs → open browser
        if ftype == "Web" or (path and path.startswith("http")):
            ok, msg = __import__("web_launcher").open_url(path)
            self._set_status(("Command executed: " if ok else "Error: ") + msg)
            return

        # regular files → confirmation popup (READ-ONLY, never modify)
        if not path or not os.path.isfile(path):
            self._set_status("Cannot open: file not found.")
            return
        self._confirm_open(row)

    def _confirm_open(self, row):
        """Show a confirmation popup before opening a file."""
        dlg = ctk.CTkToplevel(self)
        dlg.title("Open File")
        dlg.geometry("420x160")
        dlg.resizable(False, False)
        dlg.configure(fg_color="#141c2e")
        dlg.grab_set()
        dlg.lift()

        ctk.CTkLabel(
            dlg,
            text=("Open this file?\n" + row.get('name', '')),
            font=ctk.CTkFont(size=12),
            text_color="#c8d4f0",
            wraplength=380,
        ).pack(pady=(24, 16), padx=20)

        btn_frame = ctk.CTkFrame(dlg, fg_color="transparent")
        btn_frame.pack()

        def do_open():
            dlg.destroy()
            path = row.get("path", "")
            try:
                os.startfile(path)
                self._set_status(f"Opened: {row.get('name', path)}")
            except Exception as e:
                self._set_status(f"Error opening file: {e}")

        ctk.CTkButton(
            btn_frame, text="✅ Open", width=100,
            fg_color="#1a4a1a", hover_color="#226622",
            command=do_open,
        ).pack(side="left", padx=8)
        ctk.CTkButton(
            btn_frame, text="❌ Cancel", width=100,
            fg_color="#3a1a1a", hover_color="#662222",
            command=dlg.destroy,
        ).pack(side="left", padx=8)

    def open_selected_folder(self):
        """Open the containing folder of the selected item in Windows Explorer."""
        sel = self.tree.selection()
        if not sel:
            self._set_status("Select an item first.")
            return
        row = self._rows_by_id.get(sel[0])
        if not row:
            return
        path   = row.get("path", "")
        folder = row.get("folder", "")
        # for folders/drives, open the path itself; for files use the parent
        target = path if os.path.isdir(path) else (folder or os.path.dirname(path))
        if not target or not os.path.isdir(target):
            self._set_status("Folder not found.")
            return
        try:
            os.startfile(target)
            self._set_status(f"Opened folder: {target}")
        except Exception as e:
            self._set_status(f"Error: {e}")

    def _style_treeview(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Treeview", background=GLASS_BG, fieldbackground=GLASS_BG,
            foreground="#d0d8f0", rowheight=28, borderwidth=0,
            font=("Segoe UI", 10))
        style.configure(
            "Treeview.Heading", background="#1a2540",
            foreground=NEON_CYAN, relief="flat",
            font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", "#2a3a70")])

    # ═══════════════════════════════════════════════════════════ views ════════
    def switch_view(self, name):
        # navigating to any normal view always leaves AI Search mode
        self._leave_ai_mode()
        self.current_view = name
        for n, b in self.nav_buttons.items():
            b.configure(fg_color=ACCENT if n == name else "transparent")

        is_explorer_like = name in ("Explorer", "Drives")

        if is_explorer_like:
            self.cards_frame.grid_remove()
            self.quick_frame.grid_remove()
            self.explorer_bar.grid(row=5, column=0, columnspan=2,
                                   sticky="ew", pady=(10, 0))
        else:
            self.explorer_bar.grid_remove()
            self.cards_frame.grid()
            self.quick_frame.grid()

        # live monitor + AI command center only on the Dashboard
        self._show_dashboard_monitor(name == "Dashboard")

        if name != "Suggestions":
            self.suggest_frame.grid_remove()

        if name == "Dashboard":
            self.show_recent()
        elif name == "Explorer":
            # Sidebar Explorer button always lands on home, not a previous drive
            self._show_explorer_home()
            self._update_nav_buttons()
        elif name == "Drives":
            self.show_drives_view()
        elif name == "Apps":
            self.show_all_apps()
        elif name == "Projects":
            self.show_all_projects()
        elif name == "Games":
            self.show_all_games()
        elif name == "Web":
            self.show_commands_help()
        elif name == "Suggestions":
            topic = self.search_var.get().strip() or "python"
            self.show_suggestions(topic)
            self._set_status(
                'Suggestion cards - type a goal like "i want to learn python" '
                "and press Enter.")

    # ── Drives view ───────────────────────────────────────────────────────────
    def show_drives_view(self):
        drives = _get_drives()
        rows = []
        for d in drives:
            try:
                usage = shutil.disk_usage(d)
                total = usage.total
            except Exception:
                total = 0
            rows.append({
                "name": d, "path": d, "folder": "Drive",
                "extension": "", "size": total,
                "created_date": "", "modified_date": "",
                "file_type": config.FOLDER_CATEGORY,
            })
        self.show_grouped([(f"💾 DRIVES ({len(rows)})", rows)])
        self.breadcrumb.configure(text="  💾 Available Drives")
        self._set_status(
            f"{len(drives)} drive(s) detected. "
            "Double-click a drive to explore it.")

    # ═══════════════════════════════════════════════════════════ explorer ═════
    def _safe_explore(self, path):
        """Explore a known folder, showing a friendly error if missing."""
        if not path:
            self._set_status(
                "⚠️  Folder not found on this computer. "
                "Check if OneDrive is synced, or use Drives view.")
            messagebox.showinfo(
                config.APP_NAME,
                "This folder was not found on your computer.\n\n"
                "It may be stored in OneDrive, or the path has moved.\n"
                "Use 'Drives' in the sidebar to browse manually.")
            return
        if not os.path.isdir(path):
            self._set_status(f"⚠️  Folder not found: {path}")
            messagebox.showinfo(
                config.APP_NAME,
                f"Folder not found:\n{path}\n\n"
                "Try connecting a drive or checking folder location.")
            return
        self.explore(path)

    def _show_explorer_home(self):
        """Explorer Home: drives + quick-access locations. No scanning, no recursion."""
        self._explorer_path = None          # signals "we are at home"
        self.breadcrumb.configure(text="  🏠 Explorer Home")
        drives = _get_drives()
        quick_folders = []
        for d in drives:
            quick_folders.append({
                "name": d, "path": d, "folder": "Drive",
                "extension": "", "size": 0,
                "created_date": "", "modified_date": "",
                "file_type": config.FOLDER_CATEGORY,
            })
        for label, getter in EXPLORER_HOMES:
            p = getter()
            if p and os.path.isdir(p):
                quick_folders.append({
                    "name": label.split()[-1],  # strip emoji
                    "path": p, "folder": "Quick Access",
                    "extension": "", "size": 0,
                    "created_date": "", "modified_date": "",
                    "file_type": config.FOLDER_CATEGORY,
                })
        self.show_grouped([(f"🏠 EXPLORER HOME ({len(quick_folders)})", quick_folders)])
        self._set_status("Double-click any drive or folder to explore it.")

    def go_explorer_home(self):
        """Navigate to Explorer Home and add it to history so Back works."""
        self._activate_explorer_ui()
        if self._explorer_path:                   # only push if we were somewhere
            self.back_stack.append(self._explorer_path)
            self.forward_stack.clear()
        self._show_explorer_home()
        self._update_nav_buttons()

    def explore(self, path):
        """Activate Explorer nav then navigate (adds to history). No recursion."""
        self._activate_explorer_ui()
        self.navigate_to_path(path, add_to_history=True)

    def _activate_explorer_ui(self):
        """Switch UI into Explorer mode without triggering any navigation."""
        self._leave_ai_mode()
        self.current_view = "Explorer"
        for n, b in self.nav_buttons.items():
            b.configure(fg_color=ACCENT if n == "Explorer" else "transparent")
        self.cards_frame.grid_remove()
        self.quick_frame.grid_remove()
        self.explorer_bar.grid(row=5, column=0, columnspan=2,
                               sticky="ew", pady=(10, 0))
        self.suggest_frame.grid_remove()

    def _do_explore(self, path):
        """Folder-first listing of one directory (live, read-only). No UI side-effects."""
        path = os.path.abspath(path)
        self._explorer_path = path
        self.breadcrumb.configure(text=f"  📍 {path}")

        def fetch():
            folders, files = [], []
            try:
                with os.scandir(path) as it:
                    for e in it:
                        try:
                            st     = e.stat()
                            is_dir = e.is_dir()
                        except OSError:
                            continue
                        ext = "" if is_dir else os.path.splitext(e.name)[1].lower()
                        row = {
                            "name":          e.name,
                            "path":          e.path,
                            "folder":        path,
                            "extension":     ext,
                            "size":          0 if is_dir else st.st_size,
                            "created_date":  "",
                            "modified_date": datetime.fromtimestamp(
                                st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                            "file_type":     (config.FOLDER_CATEGORY if is_dir
                                              else config.get_category(ext)),
                        }
                        (folders if is_dir else files).append(row)
            except OSError as exc:
                return None, str(exc)
            folders.sort(key=lambda r: r["name"].lower())
            files.sort(key=lambda r: r["name"].lower())
            return folders, files

        def done(result):
            folders, files = result
            if folders is None:
                self._set_status(f"Cannot open folder: {files}")
                messagebox.showinfo(config.APP_NAME,
                                    f"Cannot open this folder:\n{files}")
                return
            self.show_grouped([
                (f"📁 FOLDERS ({len(folders)})", folders),
                (f"📄 FILES ({len(files)})", files),
            ])
            self._set_status(
                f"Explorer: {len(folders)} folders, {len(files)} files  –  "
                "single-click = details  |  double-click folder = enter  "
                "| double-click file = open prompt")

        threading.Thread(
            target=lambda: self.after(0, done, fetch()), daemon=True).start()

    # ─── navigation history ───────────────────────────────────────────────────

    def navigate_to_path(self, path, add_to_history=True):
        """Central entry point for all explorer navigation.

        path=None means Explorer Home.
        add_to_history=True  → normal forward nav (push old path, clear fwd).
        add_to_history=False → back/forward replay (stacks already managed).
        """
        if path is None:
            # Navigating to home
            if add_to_history:
                if self._explorer_path is not None:
                    self.back_stack.append(self._explorer_path)
                self.forward_stack.clear()
            self._show_explorer_home()
            self._update_nav_buttons()
            return

        if not os.path.isdir(path):
            self._set_status(f"⚠️  Path not accessible: {path}")
            return

        if add_to_history:
            # push current (may be None = home)
            self.back_stack.append(self._explorer_path)
            self.forward_stack.clear()

        self._do_explore(path)
        self._update_nav_buttons()

    def _update_nav_buttons(self):
        """Enable / disable Back and Forward based on stack state."""
        self.btn_back.configure(
            state="normal" if self.back_stack else "disabled")
        self.btn_fwd.configure(
            state="normal" if self.forward_stack else "disabled")

    def explorer_back(self):
        if not self.back_stack:
            return
        self.forward_stack.append(self._explorer_path)
        prev = self.back_stack.pop()
        self.navigate_to_path(prev, add_to_history=False)

    def explorer_forward(self):
        if not self.forward_stack:
            return
        self.back_stack.append(self._explorer_path)
        nxt = self.forward_stack.pop()
        self.navigate_to_path(nxt, add_to_history=False)

    # ═════════════════════════════════════════════════════════ suggestions ════
    def show_suggestions(self, topic):
        for w in self.suggest_frame.winfo_children():
            w.destroy()

        ctk.CTkLabel(
            self.suggest_frame,
            text=f'💡 Suggestions for "{topic}"',
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=NEON_CYAN,
        ).pack(anchor="w", padx=14, pady=(10, 4))

        row = ctk.CTkFrame(self.suggest_frame, fg_color="transparent")
        row.pack(fill="x", padx=10, pady=(0, 10))

        def card(text, cmd, col):
            b = ctk.CTkButton(
                row, text=text, height=54, corner_radius=12,
                fg_color="#1a2540", hover_color=GLASS_HOVER,
                border_width=1, border_color=NEON_PURPLE,
                font=ctk.CTkFont(size=12), command=cmd)
            b.grid(row=0, column=col, sticky="ew", padx=4)
            row.grid_columnconfigure(col, weight=1)

        def run_and_status(fn, *args):
            ok, msg = fn(*args)
            self._set_status(("Command executed: " if ok else "Error: ") + msg)

        card("🔍 Search Google\nfor this topic",
             lambda: run_and_status(web.search_web, topic, "google", None), 0)
        card("▶️ YouTube\ntutorials",
             lambda: run_and_status(web.search_web, f"{topic} tutorial",
                                    "youtube", None), 1)
        card("🤖 Ask ChatGPT\nabout it",
             lambda: run_and_status(
                 web.open_url,
                 "https://chatgpt.com/?q=" + web.quote_plus(topic)), 2)
        card("💻 Open\nVS Code",
             lambda: self._open_best("vs code", prefer_apps=True), 3)
        card("📁 Find local\nfiles & projects",
             lambda: self._find_local_for_topic(topic), 4)

        self.suggest_frame.grid()

    def _find_local_for_topic(self, topic):
        def work():
            project = self.projects.best(topic)
            files   = self.search.quick_search(topic, 100)
            self.after(0, done, project, files)

        def done(project, files):
            sections = []
            if project:
                sections.append(("📂 PROJECTS (1)",
                                 [self._project_to_row(project)]))
            sections.append((f"📄 FILES ({len(files)})", files))
            self.show_grouped(sections)
            self._set_status(f'Local matches for "{topic}".')

        threading.Thread(target=work, daemon=True).start()

    # ═══════════════════════════════════════════ command execution ═══════════
    def _on_enter(self, event=None):
        self.execute_command()
        return "break"

    def _on_enter_global(self, event=None):
        if self.focus_get() is not None and str(self.focus_get()).startswith(
                str(self.entry)):
            self.execute_command()

    # file extensions that, when typed bare ("main.py", "report.pdf"),
    # mean a local-file lookup — NOT a web address to navigate to.
    _FILE_EXT_HINTS = {
        ".py", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
        ".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm", ".css",
        ".js", ".ts", ".java", ".c", ".cpp", ".h", ".cs", ".rb", ".go",
        ".rs", ".php", ".sql", ".sh", ".bat", ".ipynb", ".png", ".jpg",
        ".jpeg", ".gif", ".bmp", ".svg", ".mp3", ".mp4", ".mov", ".avi",
        ".mkv", ".wav", ".zip", ".rar", ".7z", ".iso", ".exe", ".apk",
        ".log", ".ini", ".cfg", ".yml", ".yaml",
    }

    @staticmethod
    def _looks_like_local_filename(text):
        """True for a bare filename like 'main.py' (no spaces, no scheme,
        no path separators) whose extension is a known file type."""
        t = (text or "").strip()
        if not t or " " in t or "/" in t or "\\" in t:
            return False
        if t.lower().startswith(("http://", "https://", "www.")):
            return False
        return os.path.splitext(t)[1].lower() in FileMindApp._FILE_EXT_HINTS

    def execute_command(self):
        text = self.search_var.get().strip()
        if not text:
            self.show_recent()
            return
        if self._live_job:
            self.after_cancel(self._live_job)
            self._live_job = None

        # ── explicit AI / semantic document-search prefixes ─────────────────
        lower = text.lower()
        for prefix in ("ai search ", "ai find ", "semantic search ",
                        "semantic find ", "semantic "):
            if lower.startswith(prefix):
                self._run_ai_search(text[len(prefix):].strip())
                return
        if lower.startswith("find my "):
            self._run_ai_search(text[8:].strip())
            return

        # ── understand the command (rule parser → intent) ───────────────────
        try:
            intent = parse(text)
        except Exception as e:
            self._set_status(f"Error: could not understand command ({e}).")
            return

        action = intent["action"]

        # A bare filename like "main.py" parses as a URL; treat it as a
        # filename lookup, never a web navigation.
        if action == "open_url" and self._looks_like_local_filename(text):
            if self._ai_search_mode:
                self._run_ai_search(text)          # show in the AI panel
            else:
                self._load_async(
                    lambda: self.search.quick_search(text, RESULT_LIMIT),
                    f'Find "{text}"', group_as_files=True)
            return

        # AI Search Mode ON: search-type inputs go to the AI Search panel,
        # but real commands (open app/website/project, web search, remember/
        # continue, …) ALWAYS pass through to the command pipeline.
        if self._ai_search_mode and action in (
                "find_files", "find_screenshots", "query"):
            self._run_ai_search(text)
            return

        threading.Thread(target=self.db.add_history,
                         args=(text, action), daemon=True).start()
        if action != "intent":
            self.suggest_frame.grid_remove()

        if action == "intent":
            self.show_suggestions(intent["topic"])
            self._find_local_for_topic(intent["topic"])
            self._set_status(
                f'Showing suggestions for "{intent["topic"]}" – pick an action.')
        elif action == "show_downloads":
            self.show_downloads()
            self._set_status("Command executed: show downloads.")
        elif action == "open_downloads":
            self.open_downloads_folder()
            self._set_status("Command executed: open Downloads folder.")
        elif action == "open_folder":
            self._open_folder_by_name(intent["name"])
        elif action == "play_game":
            self._play_game(intent["name"], intent.get("fallback_app", False))
        elif action == "open_project":
            self._open_project(intent["name"])
        elif action == "remember_project":
            self._remember_project_dialog(intent["name"])
        elif action == "find_screenshots":
            self.show_screenshots()
            self._set_status("Command executed: showing screenshots.")
        elif action == "find_files":
            q, ext = intent.get("query", ""), intent.get("extension")
            if ext:
                self._load_async(
                    lambda: self.search.search(
                        name=q or None, extension=ext, limit=RESULT_LIMIT),
                    f'Find "{(q + " " if q else "")}{ext}"',
                    group_as_files=True)
            else:
                self._load_async(
                    lambda: self.search.quick_search(q, RESULT_LIMIT),
                    f'Find "{q}"', group_as_files=True)
        elif action == "open_url":
            ok, msg = web.open_url(intent["url"], intent.get("browser"))
            self._set_status(("Command executed: " if ok else "Error: ") + msg)
        elif action == "open_browser":
            ok, msg = web.open_browser(intent["browser"])
            self._set_status(("Command executed: " if ok else "Error: ") + msg)
        elif action == "web_search":
            ok, msg = web.search_web(intent["query"],
                                     intent.get("engine", "google"),
                                     intent.get("browser"))
            self._set_status(("Command executed: " if ok else "Error: ") + msg)
        elif action == "open_app":
            self._open_best(intent["name"], prefer_apps=True)
        else:
            self._try_ai_then_best(text)

    def _open_best(self, query, prefer_apps):
        def work():
            app  = self.launcher.best(query)
            rows = self.search.quick_search(query, RESULT_LIMIT)
            if app:
                app = dict(app)
                app["icon_png"] = icon_extractor.get_icon_png(app["path"])
            self.after(0, done, app, rows)

        def done(app, rows):
            self._show_search_groups(
                query, [self._app_to_row(app)] if app else [], rows)
            if app:
                ok, msg = self.launcher.launch(app)
                self._set_status(("Command executed: " if ok else "Error: ") + msg)
            elif rows and prefer_apps:
                if self.search.open_file(rows[0]["path"]):
                    self._set_status(
                        f"Command executed: opened {rows[0]['name']}.")
                else:
                    self._set_status(
                        f"Error: could not open {rows[0]['name']}.")
            elif rows:
                self._set_status(f'{len(rows):,} results for "{query}".')
            else:
                ok, msg = web.search_web(query, "google", None)
                self._set_status(
                    ("Command executed: nothing local matched - " if ok
                     else "Error: ") + msg)

        threading.Thread(target=work, daemon=True).start()

    def _open_folder_by_name(self, name):
        def work():
            rows = self.db.query(
                """SELECT name, path, folder, extension, size,
                          created_date, modified_date, file_type
                   FROM files WHERE file_type = 'Folders' AND name LIKE ?
                   ORDER BY LENGTH(path) LIMIT 25""", (f"%{name}%",))
            self.after(0, done, rows)

        def done(rows):
            if rows:
                self.show_results(rows)
                try:
                    os.startfile(rows[0]["path"])
                    self._set_status(
                        f"Command executed: opened {rows[0]['path']}")
                except Exception as e:
                    self._set_status(f"Error: could not open folder ({e}).")
            else:
                self._set_status(
                    f'Error: no folder named "{name}" in the index.')

        threading.Thread(target=work, daemon=True).start()

    # ═══════════════════════════════════════════════════ AI search ══════════

    def _update_ai_status(self):
        """Check LM Studio via GET /v1/models; update AI Brain label; reschedule every 5 s."""
        def check():
            try:
                lm_ok = lmstudio_client.is_available()
            except Exception:
                lm_ok = False
            print("LM Studio status:", lm_ok)
            self._lmstudio_online = lm_ok
            if lm_ok:
                brain_txt   = "🧠 AI Brain: Online"
                brain_color = NEON_GREEN
            else:
                brain_txt   = "⚠ AI Brain: Offline\nStart LM Studio Local Server"
                brain_color = NEON_ORANGE

            def _upd():
                if hasattr(self, "_brain_status_lbl"):
                    self._brain_status_lbl.configure(
                        text=brain_txt, text_color=brain_color)
                # reschedule next poll on the main thread
                self.after(5000, self._update_ai_status)
            self.after(0, _upd)

        threading.Thread(target=check, daemon=True).start()

    def _try_ai_then_best(self, text):
        """Try LM Studio AI Brain first; fall back to _open_best if unavailable."""
        if not self._lmstudio_online:
            self._open_best(text, prefer_apps=False)
            return

        self._set_status("🧠 AI Brain thinking…")

        def work():
            result = lmstudio_client.ask_lmstudio(text)
            self.after(0, done, result)

        def done(result):
            if result is None:
                # no response or unknown intent → regular search
                self._open_best(text, prefer_apps=False)
            elif result.get("_blocked"):
                self._set_status(
                    "🔒 Blocked by read-only safety mode.")
            else:
                self._execute_ai_intent(result, text)

        threading.Thread(target=work, daemon=True).start()

    def _execute_ai_intent(self, result, original_text):
        """Route a validated LM Studio intent to the correct FileMind action."""
        intent = result["intent"]
        target = result["target"]
        query  = result["query"]
        conf   = result["confidence"]
        conf_pct = int(conf * 100)

        if intent == "open_app":
            name = target or original_text
            self._set_status(f"🧠 AI Brain ({conf_pct}%): opening app '{name}'...")
            self._open_best(name, prefer_apps=True)

        elif intent == "open_website":
            site = target or query or original_text
            ok, msg = web.open_url(site) if site.startswith("http")                 else web.search_web(site, "google", None)
            self._set_status(
                f"🧠 AI Brain ({conf_pct}%): "
                + ("Command executed: " if ok else "Error: ") + msg)

        elif intent == "web_search":
            q = query or target or original_text
            ok, msg = web.search_web(q, "google", None)
            self._set_status(
                f"🧠 AI Brain ({conf_pct}%): "
                + ("Command executed: " if ok else "Error: ") + msg)

        elif intent == "file_search":
            q = query or target or original_text
            self._set_status(f"🧠 AI Brain ({conf_pct}%): searching files for '{q}'...")
            self._load_async(
                lambda: self.search.quick_search(q, RESULT_LIMIT),
                f'AI file search: "{q}"',
                group_as_files=True)

        elif intent == "open_folder":
            name = target or query or original_text
            self._set_status(f"🧠 AI Brain ({conf_pct}%): opening folder '{name}'...")
            self._open_folder_by_name(name)

        elif intent == "suggestion":
            topic = query or target or original_text
            self.show_suggestions(topic)
            self._find_local_for_topic(topic)
            self._set_status(
                f"🧠 AI Brain ({conf_pct}%): showing suggestions for '{topic}'.")

        else:
            # should not reach here (validator already filtered), but be safe
            self._open_best(original_text, prefer_apps=False)

    def start_doc_index(self):
        """Start or stop background document indexing."""
        # ── stop if already running ───────────────────────────────────────
        if self._doc_indexer.is_running():
            self._doc_indexer.stop()
            self._set_status("⏹ Indexing stopped by user.")
            try:
                self._ai_index_btn.configure(text="🤖 Index Docs")
            except Exception:
                pass
            self.progress.stop()
            self.progress.set(0)
            return

        # ── start indexing (no Ollama needed — text-only extraction) ──────
        self._set_status("🤖 Indexing started — extracting text from docs…")
        self.progress.start()
        try:
            self._ai_index_btn.configure(text="⏹ Stop Indexing")
        except Exception:
            pass

        def on_progress(count, name, fps):
            fps_str = f"  {fps:.1f} files/sec" if fps > 0 else ""
            self.after(0, self._set_status,
                       f"🤖 Indexed {count}{fps_str}  ·  {name[:55]}")

        def on_done(total):
            def _finish():
                self.progress.stop()
                self.progress.set(0)
                self._set_status(
                    f"✅ Index complete: {total} file(s). "
                    "Click 'AI Search' to search semantically.")
                try:
                    self._ai_index_btn.configure(text="🤖 Index Docs")
                except Exception:
                    pass
                self.after(500, self._update_ai_status)
            self.after(0, _finish)

        self._doc_indexer.start(on_progress=on_progress, on_done=on_done)

    # ── AI Search mode toggle ────────────────────────────────────────────────

    def show_ai_search_panel(self):
        """Bring the AI Search panel to the front: hide the dashboard widgets,
        the file table and the explorer bar, then show the panel. Works from
        ANY page (Dashboard included)."""
        for name in ("cards_frame", "quick_frame", "explorer_bar"):
            w = getattr(self, name, None)
            if w is not None:
                w.grid_remove()
        # monitor + command center live inside cards_frame; hide them too
        self._show_dashboard_monitor(False)
        if hasattr(self, "_table_frame"):
            self._table_frame.grid_remove()
        if hasattr(self, "_ai_panel"):
            self._ai_panel.grid()
        if hasattr(self, "_ai_search_btn"):
            self._ai_search_btn.configure(
                text="🔴 AI Search: ON",
                fg_color="#2a0050", hover_color="#3d0070")

    def hide_ai_search_panel(self):
        """Hide the AI Search panel and reset the button (visibility only)."""
        if hasattr(self, "_ai_panel"):
            self._ai_panel.grid_remove()
        if hasattr(self, "_ai_search_btn"):
            self._ai_search_btn.configure(
                text="🔍 AI Search",
                fg_color="#180a2a", hover_color="#2d1050")

    def show_dashboard(self):
        """Restore the normal main-area widgets for the current view.

        Never overrides the AI Search panel while AI mode is ON (so a stray
        dashboard refresh can't hide the panel)."""
        if self._ai_search_mode:
            return
        explorer_like = self.current_view in ("Explorer", "Drives")
        if explorer_like:
            self.cards_frame.grid_remove()
            self.quick_frame.grid_remove()
            self.explorer_bar.grid(row=5, column=0, columnspan=2,
                                   sticky="ew", pady=(10, 0))
        else:
            self.explorer_bar.grid_remove()
            self.cards_frame.grid()
            self.quick_frame.grid()
        self._show_dashboard_monitor(self.current_view == "Dashboard")
        if hasattr(self, "_table_frame"):
            self._table_frame.grid()

    def sync_ai_mode_ui(self):
        """Single source of truth → make every AI-mode UI element match
        self._ai_search_mode. Safe to call any time, any number of times."""
        if self._ai_search_mode:
            self.show_ai_search_panel()
        else:
            self._ai_thinking = False
            self._ai_selected_row = None
            self.hide_ai_search_panel()
            self.show_dashboard()

    def _toggle_ai_search(self):
        """Toggle AI Search mode ON/OFF from the sidebar button."""
        if self._ai_search_mode:
            self._exit_ai_search_mode()
        else:
            self._enter_ai_search_mode()

    def _enter_ai_search_mode(self):
        self._ai_search_mode = True
        self._ai_selected_row = None
        self.show_ai_search_panel()     # bring panel to front from any page
        self.sync_ai_mode_ui()
        self._ai_panel_intro()
        self._set_status(
            "🧠 AI Search Mode ON — ask naturally and press Enter "
            "(e.g. 'find my voter pdf').")

    def _exit_ai_search_mode(self):
        was_on = self._ai_search_mode
        self._ai_search_mode = False
        self.hide_ai_search_panel()
        self.show_dashboard()
        self.sync_ai_mode_ui()
        if was_on:
            self._set_status("AI Search Mode OFF — back to normal command mode.")
            if self.current_view == "Dashboard":
                self.show_recent()

    def _leave_ai_mode(self):
        """Silently drop out of AI Search mode (used when navigating views)."""
        if self._ai_search_mode:
            self._ai_search_mode = False
            self.sync_ai_mode_ui()

    # tokens that signal a web/app command (not a document query)
    _AI_BLOCK_PREFIXES = (
        "open ", "launch ", "start ", "run ",
        "search google", "google ", "youtube ", "yt ",
        "chrome ", "edge ", "firefox ", "brave ",
        "website ", "url ", "http", "www.",
    )
    _AI_BLOCK_EXACT = {
        "google", "youtube", "chrome", "edge", "firefox", "brave",
        "instagram", "twitter", "github", "reddit", "netflix",
    }

    @staticmethod
    def _looks_like_app_web_cmd(text: str) -> bool:
        """Return True if the text looks like an open/web/app command."""
        low = text.lower().strip()
        if low in FileMindApp._AI_BLOCK_EXACT:
            return True
        return any(low.startswith(p) for p in FileMindApp._AI_BLOCK_PREFIXES)

    def _ai_filename_search(self, query: str) -> list:
        """Search the files table for filename/path/extension matches.

        Works fully offline — no AI model required.
        Returns result dicts compatible with _ai_result_to_row.
        """
        q    = query.strip()
        seen = {}   # path -> result (dedup, keep best score)

        def _add(row, label, score):
            p = row["path"]
            if p not in seen or score > seen[p]["score"]:
                seen[p] = {
                    "name":      row["name"],
                    "path":      p,
                    "file_type": row.get("file_type", ""),
                    "score":     score,
                    "label":     label,
                }

        # 1 — exact filename match
        for row in self.db.query(
                "SELECT name, path, file_type, size FROM files "
                "WHERE name = ? COLLATE NOCASE LIMIT 50", (q,)):
            _add(row, "Filename match", 1.0)

        # 2 — filename contains query
        for row in self.db.query(
                "SELECT name, path, file_type, size FROM files "
                "WHERE name LIKE ? COLLATE NOCASE LIMIT 100",
                (f"%{q}%",)):
            _add(row, "Filename match", 0.9)

        # 3 — extension match  (handles "pdf", ".pdf", "py")
        ext = q if q.startswith(".") else f".{q.lower()}"
        for row in self.db.query(
                "SELECT name, path, file_type, size FROM files "
                "WHERE extension = ? COLLATE NOCASE LIMIT 100", (ext,)):
            _add(row, "Extension match", 0.7)

        # 4 — path contains query
        for row in self.db.query(
                "SELECT name, path, file_type, size FROM files "
                "WHERE path LIKE ? COLLATE NOCASE LIMIT 100",
                (f"%{q}%",)):
            _add(row, "Path match", 0.6)

        results = sorted(seen.values(), key=lambda r: r["score"], reverse=True)
        return results[:RESULT_LIMIT]

    # ── natural-language query understanding ─────────────────────────────────

    # words stripped out of a natural query so only keywords remain
    _AI_STOPWORDS = {
        "find", "search", "show", "open", "get", "list", "give", "want",
        "need", "please", "pls", "plz", "the", "a", "an", "me", "my", "mine",
        "for", "of", "all", "any", "some", "to", "in", "on", "with", "and",
        "file", "files", "document", "documents", "doc", "named", "called",
        "ai", "semantic",
        # Hinglish
        "mujhe", "mera", "meri", "mere", "ko", "ka", "ki", "kholo", "khol",
        "dhoondo", "dhundo", "dhundho", "khojo", "dikhao", "batao", "chahiye",
    }

    # natural words → file extensions (read-only file-type intelligence)
    _AI_EXT_MAP = {
        "pdf": [".pdf"], "pdfs": [".pdf"],
        "doc": [".doc", ".docx", ".txt", ".md"],
        "docs": [".doc", ".docx", ".txt", ".md"],
        "word": [".doc", ".docx"],
        "text": [".txt", ".md"], "txt": [".txt"], "note": [".txt", ".md"],
        "notes": [".txt", ".md"],
        "image": [".jpg", ".jpeg", ".png"], "images": [".jpg", ".jpeg", ".png"],
        "photo": [".jpg", ".jpeg", ".png"], "photos": [".jpg", ".jpeg", ".png"],
        "pic": [".jpg", ".jpeg", ".png"], "pics": [".jpg", ".jpeg", ".png"],
        "picture": [".jpg", ".jpeg", ".png"],
        "video": [".mp4", ".mkv", ".mov", ".avi"],
        "videos": [".mp4", ".mkv", ".mov", ".avi"],
        "music": [".mp3", ".wav"], "song": [".mp3"], "songs": [".mp3"],
        "audio": [".mp3", ".wav"],
        "excel": [".xlsx", ".xls", ".csv"], "sheet": [".xlsx", ".xls", ".csv"],
        "spreadsheet": [".xlsx", ".xls", ".csv"],
        "ppt": [".pptx", ".ppt"], "slides": [".pptx", ".ppt"],
        "presentation": [".pptx", ".ppt"],
        "python": [".py"], "py": [".py"],
        "zip": [".zip", ".rar", ".7z"], "archive": [".zip", ".rar", ".7z"],
        "exe": [".exe"], "installer": [".exe", ".msi"],
    }
    _AI_SCREENSHOT_WORDS = {"screenshot", "screenshots", "ss"}

    def _ai_understand(self, raw):
        """Turn a natural query into keywords + extensions (case-insensitive).

        'find anik voter pdf' -> keywords=['anik','voter'], exts={'.pdf'}.
        Everything is lowercase-normalised so matching never depends on case.
        """
        import re
        low = (raw or "").lower().strip()
        toks = [t for t in re.split(r"[^a-z0-9]+", low) if t]
        keywords, exts, display = [], set(), []
        screenshot = False
        for t in toks:
            if t in self._AI_SCREENSHOT_WORDS:
                screenshot = True
                display.append(t)
                continue
            if t in self._AI_EXT_MAP:
                exts.update(self._AI_EXT_MAP[t])
                display.append(t)
                continue
            if t in self._AI_STOPWORDS:
                continue
            keywords.append(t)
            display.append(t)
        # query was nothing but noise → fall back to the literal text
        if not keywords and not exts and not screenshot and low:
            keywords = [low]
            display = [low]
        interpreted = ("search files for " + ", ".join(display)) if display \
            else f'search files for "{low}"'
        return {"keywords": keywords, "exts": exts, "screenshot": screenshot,
                "display": display, "interpreted": interpreted}

    def _ai_search_files(self, info, query):
        """Case-insensitive keyword search → Best / Related / Suggestions.

        Read-only: only SELECTs against the index, never touches files.
        """
        kws, exts = info["keywords"], info["exts"]
        best, related, suggestions = [], [], []
        seen = set()

        def _take(row, label, bucket):
            if row["path"] in seen:
                return
            seen.add(row["path"])
            row = dict(row)
            row["label"] = label
            row.setdefault("folder", os.path.dirname(row["path"]))
            bucket.append(row)

        # screenshots are a special "type" request
        if info["screenshot"]:
            for r in self.db.screenshots(200):
                _take(r, "Screenshot", best)

        if kws or exts:
            sql = ("SELECT name, path, folder, extension, size, "
                   "modified_date, file_type FROM files "
                   "WHERE file_type != 'Folders'")
            params = []
            for kw in kws:
                sql += " AND (LOWER(name) LIKE ? OR LOWER(path) LIKE ?)"
                params += [f"%{kw}%", f"%{kw}%"]
            if exts:
                sql += " AND LOWER(extension) IN (%s)" % ",".join("?" * len(exts))
                params += [e.lower() for e in exts]
            sql += " ORDER BY modified_date DESC LIMIT 500"
            for r in self.db.query(sql, params):
                nm = (r["name"] or "").lower()
                if kws and all(kw in nm for kw in kws):
                    _take(r, "Best match", best)
                elif kws:
                    _take(r, "Related", related)
                else:                       # extension-only request
                    _take(r, "Match", best)

        # indexed document content (works offline via keyword fallback)
        if kws and self.db.chunk_count() > 0:
            try:
                use_sem = self._lmstudio_online or ai_service.ollama_available()
                raw = (ai_service.semantic_search(self.db, " ".join(kws))
                       if use_sem
                       else ai_service.keyword_search(self.db, " ".join(kws)))
                for d in raw[:20]:
                    d = dict(d)
                    d["folder"] = os.path.dirname(d["path"])
                    d["extension"] = os.path.splitext(d["path"])[1].lower()
                    _take(d, "Content match" + (" (AI)" if use_sem else ""),
                          related)
            except Exception:
                pass

        # suggestions: other files of the requested type
        if exts and len(best) < 50:
            ph = ",".join("?" * len(exts))
            for r in self.db.query(
                    "SELECT name, path, folder, extension, size, "
                    "modified_date, file_type FROM files "
                    "WHERE file_type != 'Folders' "
                    f"AND LOWER(extension) IN ({ph}) "
                    "ORDER BY modified_date DESC LIMIT 200",
                    [e.lower() for e in exts]):
                if len(suggestions) >= 10:
                    break
                _take(r, "Same type", suggestions)

        return {"best": best[:50], "related": related[:50],
                "suggestions": suggestions[:10]}

    def _run_ai_search(self, query=None):
        """Understand a natural query, then search the index (case-insensitive)."""
        if query is None:
            query = self.search_var.get().strip()
        if not query:
            self._set_status("AI Search: please type a question first.")
            return

        info = self._ai_understand(query)
        if self._ai_search_mode:
            self._ai_panel_thinking(query, info)
        else:
            self._set_status(
                f'🧠 Thinking with local Qwen — searching for "{query}"…')

        def work():
            try:
                groups = self._ai_search_files(info, query)
            except Exception as e:
                self.after(0, self._set_status, f"AI Search error: {e}")
                return
            self.after(0, self._present_ai_results, query, info, groups)

        threading.Thread(target=work, daemon=True).start()

    def _present_ai_results(self, query, info, groups):
        """Route results to the AI panel (AI mode) or the table (normal mode)."""
        if self._ai_search_mode:
            self._ai_panel_render(query, info, groups)
            return
        sections = []
        if groups["best"]:
            sections.append((f"🎯 BEST MATCHES ({len(groups['best'])})",
                             [self._ai_file_to_row(r) for r in groups["best"]]))
        if groups["related"]:
            sections.append((f"🔗 RELATED FILES ({len(groups['related'])})",
                             [self._ai_file_to_row(r) for r in groups["related"]]))
        if groups["suggestions"]:
            sections.append((f"💡 SUGGESTIONS ({len(groups['suggestions'])})",
                             [self._ai_file_to_row(r)
                              for r in groups["suggestions"]]))
        if sections:
            self.show_grouped(sections)
            total = sum(len(v) for v in groups.values())
            self._set_status(
                f'AI Search: {total} result(s) for "{query}" '
                f'(understood: {info["interpreted"]}).')
        else:
            self.show_results([])
            self._set_status(
                f'AI Search: no matches for "{query}". '
                "Try fewer words or check spelling.")

    def _ai_file_to_row(self, result):
        """Normalise an AI result dict into a tree-row dict."""
        path = result.get("path", "")
        return {
            "name":          result.get("name", os.path.basename(path)),
            "path":          path,
            "folder":        result.get("folder") or os.path.dirname(path),
            "extension":     result.get("extension")
                             or os.path.splitext(path)[1].lower(),
            "size":          result.get("size", 0) or 0,
            "created_date":  "",
            "modified_date": result.get("label", ""),
            "file_type":     result.get("file_type") or "Document",
        }

    # ── AI Search panel (distinct UI, not the file table) ────────────────────

    def _build_ai_panel(self, parent):
        panel = ctk.CTkFrame(parent, corner_radius=14, fg_color=GLASS_BG,
                             border_width=1, border_color=NEON_PURPLE)
        panel.grid(row=7, column=0, sticky="nsew")
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(2, weight=1)
        panel.grid_remove()                 # hidden until AI Search is ON
        self._ai_panel = panel

        head = ctk.CTkFrame(panel, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 2))
        head.grid_columnconfigure(0, weight=1)

        titlebox = ctk.CTkFrame(head, fg_color="transparent")
        titlebox.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(titlebox, text="🧠 AI Search Mode  ·  ON",
                     font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=NEON_GREEN).pack(anchor="w")
        ctk.CTkLabel(
            titlebox,
            text=("Ask naturally:  find my voter PDF  ·  explain this file  ·  "
                  "find python notes"),
            font=ctk.CTkFont(size=12), text_color="#8090b0").pack(anchor="w",
                                                                  pady=(2, 0))
        self._ai_think_lbl = ctk.CTkLabel(
            titlebox, text="", font=ctk.CTkFont(size=12, weight="bold"),
            text_color=NEON_CYAN)
        self._ai_think_lbl.pack(anchor="w", pady=(6, 0))

        ctk.CTkButton(
            head, text="✕ Exit AI Mode", width=120, height=30,
            corner_radius=8, fg_color="#2a0a1a", hover_color="#4a1030",
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._exit_ai_search_mode,
        ).grid(row=0, column=1, sticky="ne", padx=(8, 0))

        interp = ctk.CTkFrame(panel, fg_color="#0d1a2e", corner_radius=10)
        interp.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 6))
        self._ai_typed_lbl = ctk.CTkLabel(
            interp, text="", font=ctk.CTkFont(size=11), text_color="#c8d4f0",
            anchor="w", justify="left", wraplength=620)
        self._ai_typed_lbl.pack(anchor="w", padx=10, pady=(6, 0))
        self._ai_understood_lbl = ctk.CTkLabel(
            interp, text="", font=ctk.CTkFont(size=11), text_color=NEON_CYAN,
            anchor="w", justify="left", wraplength=620)
        self._ai_understood_lbl.pack(anchor="w", padx=10, pady=(0, 6))
        self._ai_interp_frame = interp
        interp.grid_remove()

        self._ai_results = ctk.CTkScrollableFrame(
            panel, fg_color="transparent",
            scrollbar_button_color="#1e2a40",
            scrollbar_button_hover_color="#2a3a52")
        self._ai_results.grid(row=2, column=0, sticky="nsew", padx=10,
                              pady=(0, 10))
        self._ai_results.grid_columnconfigure(0, weight=1)

    def _show_ai_panel(self):
        if hasattr(self, "_table_frame"):
            self._table_frame.grid_remove()
        if hasattr(self, "_ai_panel"):
            self._ai_panel.grid()

    def _hide_ai_panel(self):
        if hasattr(self, "_ai_panel"):
            self._ai_panel.grid_remove()
        if hasattr(self, "_table_frame"):
            self._table_frame.grid()

    def _clear_ai_results(self):
        for w in self._ai_results.winfo_children():
            w.destroy()

    def _ai_panel_intro(self):
        self._ai_thinking = False
        self._ai_think_lbl.configure(text="")
        self._ai_interp_frame.grid_remove()
        self._clear_ai_results()
        ctk.CTkLabel(
            self._ai_results,
            text=("Type a question above and press Enter.\n\n"
                  "Try:  find my voter pdf  ·  find python notes  ·  "
                  "resume screenshots  ·  main.py"),
            font=ctk.CTkFont(size=12), text_color="#506080",
            justify="left").grid(row=0, column=0, sticky="w", padx=8, pady=14)

    def _ai_panel_thinking(self, query, info):
        self.show_ai_search_panel()
        self._ai_interp_frame.grid()
        self._ai_typed_lbl.configure(text=f"You typed:  {query}")
        self._ai_understood_lbl.configure(
            text=f"AI understood:  {info['interpreted']}")
        self._clear_ai_results()
        self._ai_thinking = True
        self._ai_think_dots = 0
        self._animate_thinking()

    def _animate_thinking(self):
        if not self._ai_thinking:
            return
        self._ai_think_dots = (self._ai_think_dots + 1) % 4
        self._ai_think_lbl.configure(
            text="🧠 Thinking with local Qwen" + "." * self._ai_think_dots)
        self.after(350, self._animate_thinking)

    def _ai_panel_render(self, query, info, groups):
        self._ai_thinking = False
        self._clear_ai_results()
        total = sum(len(v) for v in groups.values())
        self._ai_think_lbl.configure(
            text=f"✓ Done — {total} result(s) from your local index")
        r = 0
        r = self._render_ai_group("🎯 Best Matches", groups["best"], r)
        r = self._render_ai_group("🔗 Related Files", groups["related"], r)
        r = self._render_ai_group("💡 Suggestions", groups["suggestions"], r)
        if total == 0:
            ctk.CTkLabel(
                self._ai_results,
                text=(f'No matches for "{query}".\n'
                      "Try fewer words, check spelling, or index your docs "
                      "(🤖 Index Docs) for content search."),
                font=ctk.CTkFont(size=12), text_color="#8090b0",
                justify="left").grid(row=r, column=0, sticky="w", padx=8,
                                     pady=10)
            r += 1
            self._add_ai_action_card(
                f'🌐 Search the web for "{query}"',
                lambda q=query: self._run_status(web.search_web, q,
                                                 "google", None), r)

    def _render_ai_group(self, title, items, row):
        if not items:
            return row
        ctk.CTkLabel(
            self._ai_results, text=f"{title}  ({len(items)})",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=NEON_PURPLE).grid(row=row, column=0, sticky="w",
                                         padx=8, pady=(8, 2))
        row += 1
        for it in items:
            self._add_ai_file_card(it, row)
            row += 1
        return row

    def _add_ai_file_card(self, item, row):
        name = item.get("name", "")
        label = item.get("label", "")
        folder = item.get("folder", "")
        sub = "  ·  ".join(x for x in (label, folder) if x)
        text = f"📄  {name}" + (f"\n        {sub}" if sub else "")
        b = ctk.CTkButton(
            self._ai_results, text=text, anchor="w", height=46,
            corner_radius=8, fg_color="#1a2540", hover_color=GLASS_HOVER,
            border_width=1, border_color=GLASS_BORDER,
            font=ctk.CTkFont(size=12),
            command=lambda r=item: self._ai_select_card(r))
        b.grid(row=row, column=0, sticky="ew", padx=6, pady=2)
        b.bind("<Double-Button-1>", lambda e, r=item: self._open_row(r))

    def _add_ai_action_card(self, text, callback, row):
        ctk.CTkButton(
            self._ai_results, text=text, anchor="w", height=38,
            corner_radius=8, fg_color="#180a2a", hover_color="#2d1050",
            font=ctk.CTkFont(size=12),
            command=callback).grid(row=row, column=0, sticky="ew", padx=6,
                                   pady=2)

    def _run_status(self, fn, *args):
        ok, msg = fn(*args)
        self._set_status(("Command executed: " if ok else "Error: ") + msg)

    def _ai_select_card(self, row):
        self._ai_selected_row = row
        self._show_preview(row)
        self._set_status(
            f"Selected: {row.get('name', '')} — use 'Open / Launch' or "
            "'🧠 Explain with AI'.")

    # games
    # ── remember project dialog ──────────────────────────────────────────────

    def _remember_project_dialog(self, name):
        """Open a modal to collect folder path + description, then save project."""
        from tkinter import filedialog
        win = ctk.CTkToplevel(self)
        win.title("Remember Project")
        win.geometry("480x320")
        win.resizable(False, False)
        win.grab_set()

        ctk.CTkLabel(
            win, text=f"Remember Project: {name}",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=NEON_CYAN,
        ).pack(pady=(18, 4), padx=20, anchor="w")

        # folder row
        folder_var = ctk.StringVar()
        folder_frame = ctk.CTkFrame(win, fg_color="transparent")
        folder_frame.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(folder_frame, text="Folder:", width=80,
                     anchor="w").pack(side="left")
        folder_entry = ctk.CTkEntry(folder_frame, textvariable=folder_var,
                                    width=290)
        folder_entry.pack(side="left", padx=(0, 6))
        ctk.CTkButton(
            folder_frame, text="Browse", width=70,
            command=lambda: folder_var.set(
                filedialog.askdirectory(title="Select project folder") or folder_var.get())
        ).pack(side="left")

        # editor row
        editor_var = ctk.StringVar(value="code")
        editor_frame = ctk.CTkFrame(win, fg_color="transparent")
        editor_frame.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(editor_frame, text="Editor:", width=80,
                     anchor="w").pack(side="left")
        ctk.CTkOptionMenu(editor_frame, variable=editor_var,
                          values=["code", "notepad", "pycharm",
                                  "sublime", "idea"],
                          width=160).pack(side="left")

        # description row
        desc_var = ctk.StringVar()
        desc_frame = ctk.CTkFrame(win, fg_color="transparent")
        desc_frame.pack(fill="x", padx=20, pady=4)
        ctk.CTkLabel(desc_frame, text="Note:", width=80,
                     anchor="w").pack(side="left")
        ctk.CTkEntry(desc_frame, textvariable=desc_var, width=300,
                     placeholder_text="e.g. POS billing system, React app").pack(side="left")

        msg_lbl = ctk.CTkLabel(win, text="", text_color=NEON_GREEN,
                               font=ctk.CTkFont(size=11))
        msg_lbl.pack(pady=4)

        def save():
            ok, msg = self.projects.add(
                name, folder_var.get().strip(),
                editor=editor_var.get(),
                description=desc_var.get().strip())
            msg_lbl.configure(
                text=msg, text_color=NEON_GREEN if ok else NEON_ORANGE)
            if ok:
                self._set_status(f"Project memory saved: {msg}")
                win.after(1200, win.destroy)

        btn_row = ctk.CTkFrame(win, fg_color="transparent")
        btn_row.pack(pady=12)
        ctk.CTkButton(btn_row, text="Save Project", width=130,
                      fg_color=ACCENT, command=save).pack(side="left", padx=8)
        ctk.CTkButton(btn_row, text="Cancel", width=90,
                      fg_color=GLASS_BG,
                      command=win.destroy).pack(side="left")

    # ── AI file explainer ─────────────────────────────────────────────────────

    def _explain_selected_file(self):
        """Explain the currently selected file using LM Studio AI."""
        row = self._current_selected_row()
        if not row:
            self._explain_lbl.configure(
                text="Select a file first.", text_color=NEON_ORANGE)
            return
        path = row.get("path", "")
        name = row.get("name", os.path.basename(path))

        self._explain_btn.configure(state="disabled", text="Thinking...")
        self._explain_lbl.configure(
            text="🧠 Thinking with local Qwen…", text_color="#607090")

        def work():
            # fresh check — picks up LM Studio even if it was started just now
            online = self._lmstudio_online
            if not online:
                try:
                    online = lmstudio_client.is_available()
                    self._lmstudio_online = online
                except Exception:
                    online = False
            if not online:
                self.after(0, _offline)
                return

            # get a text snippet from the DB if indexed, else read directly
            snippet = ""
            chunk = self.db.query_one(
                "SELECT text FROM document_chunks dc "
                "JOIN documents d ON dc.doc_id=d.id "
                "WHERE d.path=? ORDER BY dc.chunk_idx LIMIT 1", (path,))
            if chunk:
                snippet = chunk["text"]
            else:
                # try reading a small slice directly
                try:
                    from document_indexer import extract_text
                    raw = extract_text(path)
                    snippet = raw[:600] if raw else ""
                except Exception:
                    pass

            summary = lmstudio_client.explain_file(name, snippet)
            self.after(0, _done, summary)

        def _offline():
            self._explain_btn.configure(state="normal",
                                        text="🧠 Explain with AI")
            self._explain_lbl.configure(
                text=("LM Studio Local Server required.\n"
                      "Start it and load qwen2.5-7b-instruct at "
                      "http://127.0.0.1:1234, then retry."),
                text_color=NEON_ORANGE)

        def _done(summary):
            self._explain_btn.configure(state="normal",
                                        text="🧠 Explain with AI")
            if summary:
                self._explain_lbl.configure(
                    text=summary, text_color="#a0b8d0")
            else:
                self._explain_lbl.configure(
                    text=("Could not generate a summary. "
                          "Is LM Studio still running at 127.0.0.1:1234?"),
                    text_color=NEON_ORANGE)

        threading.Thread(target=work, daemon=True).start()

    # ── workspace resume (dashboard) ──────────────────────────────────────────

    def show_workspace_resume(self):
        """Show recent projects as resume cards above the result table."""
        recent = self.projects.recent(5)
        if not recent:
            self._set_status(
                "No project history yet. "
                "Type 'remember project <name>' to save a project.")
            return
        rows = [self._project_to_row(p) for p in recent]
        self.show_results(rows,
                          f"Workspace Resume — {len(rows)} recent project(s). "
                          "Double-click to open.")

    def _play_game(self, name, fallback_app=False):
        def work():
            game = games_mod.find_game(self.db, name)
            self.after(0, done, game)

        def done(game):
            if game:
                ok, msg = games_mod.launch_game(game)
                self._set_status(("Command executed: " if ok else "Error: ") + msg)
                self.show_results([self._game_to_row(game)])
            elif fallback_app:
                self._open_best(name, prefer_apps=True)
            else:
                self._set_status(
                    f'Error: no game found matching "{name}". '
                    "Click 'Scan Games' first.")

        threading.Thread(target=work, daemon=True).start()

    # projects
    def _open_project(self, name):
        project = self.projects.best(name)
        if project:
            ok, msg = self.projects.open_project(project)
            self._set_status(("Command executed: " if ok else "Error: ") + msg)
            self.show_results([self._project_to_row(project)])
        else:
            self._set_status(
                f'No project named "{name}" - searching instead. '
                "(Use 'Add Project' to register it.)")
            self._open_best(name, prefer_apps=True)

    def add_project_dialog(self):
        name_dlg = ctk.CTkInputDialog(
            title="Add Project (1/2)",
            text="Project name (e.g. POS Project, FileMind):")
        name = (name_dlg.get_input() or "").strip()
        if not name:
            return
        folder = filedialog.askdirectory(title=f'Folder for "{name}"')
        if not folder:
            return
        ok, msg = self.projects.add(name, folder, "code")
        self._set_status(msg)
        if ok:
            self._refresh_stats()
            self.show_all_projects()

    # live search
    def _on_type(self, event):
        if event.keysym in ("Return", "KP_Enter", "Up", "Down",
                            "Left", "Right", "Tab"):
            return
        if self._live_job:
            self.after_cancel(self._live_job)
        self._live_job = self.after(LIVE_SEARCH_DELAY_MS, self._live_search)

    def _live_search(self):
        self._live_job = None
        text = self.search_var.get().strip()
        if len(text) < 2:
            return
        try:
            intent = parse(text)
        except Exception:
            return
        action = intent["action"]
        if action in ("show_downloads", "open_downloads", "open_folder",
                      "open_url", "open_browser", "web_search",
                      "play_game", "open_project", "find_screenshots",
                      "intent"):
            self._set_status(f'Press Enter to run: "{text}"')
            return
        if action == "open_app":
            query = intent["name"] or text
        elif action == "find_files":
            query = intent.get("query") or text
        else:
            query = text

        def work():
            apps = self.launcher.search(query, limit=6)
            for a in apps:
                a["icon_png"] = icon_extractor.get_icon_png(a["path"])
            files = self.search.quick_search(query, 200)
            self.after(0, done, apps, files)

        def done(apps, files):
            self._show_search_groups(query,
                                     [self._app_to_row(a) for a in apps],
                                     files)
            self._set_status(
                f"{len(apps)} apps + {len(files):,} files match - "
                "Enter runs the command.")

        threading.Thread(target=work, daemon=True).start()

    def _show_search_groups(self, query, app_rows, file_rows):
        folders = [r for r in file_rows
                   if r["file_type"] == config.FOLDER_CATEGORY]
        files   = [r for r in file_rows
                   if r["file_type"] != config.FOLDER_CATEGORY]
        sections = []
        if app_rows:
            sections.append((f"🚀 APPS ({len(app_rows)})", app_rows))
        if folders:
            sections.append((f"📁 FOLDERS ({len(folders)})", folders))
        if files:
            sections.append((f"📄 FILES ({len(files)})", files))
        site_url = web.match_site(query)
        if site_url:
            sections.append(("🌐 WEBSITES (1)", [{
                "name":          site_url.split("//")[1].split("/")[0],
                "path":          site_url, "folder": "Web",
                "extension":     "", "size": 0,
                "created_date":  "", "modified_date": "",
                "file_type":     "Web",
            }]))
        if sections:
            self.show_grouped(sections)
        else:
            self.show_results([])

    # row builders
    def _app_to_row(self, app):
        return {"name": app["name"], "path": app["path"],
                "folder": SOURCE_LABELS.get(app["source"], app["source"]),
                "extension": "", "size": 0, "created_date": "",
                "modified_date": "", "file_type": "App",
                "source": app["source"],
                "icon_png": app.get("icon_png")}

    def _game_to_row(self, game):
        return {"name": game["name"], "path": game["path"],
                "folder": game["source"].title(), "extension": "",
                "size": 0, "created_date": "", "modified_date": "",
                "file_type": "Game",
                "launch_cmd": game.get("launch_cmd", ""),
                "source": game["source"]}

    def _project_to_row(self, p):
        last = self.projects.friendly_time(p.get("last_opened", ""))
        return {"name": p["name"], "path": p["folder"],
                "folder": p["folder"], "extension": "",
                "size": 0, "created_date": "",
                "modified_date": last,
                "file_type": "Project",
                "editor": p.get("editor", "code")}

    # table
    def _get_img(self, png_path):
        if png_path not in self._img_cache:
            try:
                self._img_cache[png_path] = tk.PhotoImage(file=png_path)
            except Exception:
                self._img_cache[png_path] = None
        return self._img_cache[png_path]

    def _insert_row(self, row):
        no_size = row["file_type"] in (
            config.FOLDER_CATEGORY, "App", "Game", "Project", "Web")
        img = None
        if row.get("icon_png"):
            img = self._get_img(row["icon_png"])
        kwargs = {"text": "" if img else config.get_icon(row["file_type"])}
        if img:
            kwargs["image"] = img
        iid = self.tree.insert("", "end", values=(
            row["name"], row["file_type"], row["extension"],
            "" if no_size else human_size(row["size"]),
            row["modified_date"], row["folder"]), **kwargs)
        self._rows_by_id[iid] = row
        return iid

    def show_results(self, rows, message=""):
        self.tree.delete(*self.tree.get_children())
        self._rows_by_id.clear()
        self._current_rows = rows
        self._reset_preview()
        for row in rows:
            self._insert_row(row)
        if message:
            self._set_status(message)

    def show_grouped(self, sections):
        self.tree.delete(*self.tree.get_children())
        self._rows_by_id.clear()
        self._current_rows = [r for _, rows in sections for r in rows]
        self._reset_preview()
        for label, rows in sections:
            if not rows:
                continue
            hid = self.tree.insert("", "end", text="", values=(
                label, "", "", "", "", ""), tags=("header",))
            self._rows_by_id[hid] = None
            for row in rows:
                self._insert_row(row)

    def sort_by(self, col):
        keymap = {"name": "name", "type": "file_type",
                  "extension": "extension", "size": "size",
                  "modified": "modified_date", "folder": "folder"}
        key = keymap.get(col)
        if not key or not self._current_rows:
            return
        desc = not self._sort_state.get(col, False)
        self._sort_state = {col: desc}
        rows = sorted(self._current_rows,
                      key=lambda r: (r[key] is None, r[key]), reverse=desc)
        self.show_results(rows)

    # data views
    def show_all_apps(self):
        def fetch():
            rows = []
            for a in self.db.all_apps():
                a = dict(a)
                a["icon_png"] = icon_extractor.get_icon_png(a["path"])
                rows.append(self._app_to_row(a))
            return rows

        def done(rows):
            self.show_results(rows)
            self._set_status(
                (f"Apps: {len(rows):,} indexed"
                 + ("" if icon_extractor.HAS_ICON_SUPPORT
                    else " (install pywin32+Pillow for real icons)")
                 + ". Double-click to launch.")
                if rows else "No apps indexed yet - click 'Scan Apps'.")

        threading.Thread(
            target=lambda: self.after(0, done, fetch()), daemon=True).start()

    def show_all_games(self):
        def fetch():
            return [self._game_to_row(g) for g in self.db.all_games()]

        def done(rows):
            self.show_results(rows)
            self._set_status(
                f"Games: {len(rows):,} found. Double-click to play."
                if rows else "No games found - click 'Scan Games'.")

        threading.Thread(
            target=lambda: self.after(0, done, fetch()), daemon=True).start()

    def show_all_projects(self):
        rows = [self._project_to_row(p) for p in self.projects.all()]
        self.show_results(rows)
        self._set_status(
            f"Projects: {len(rows)} registered. Double-click to open."
            if rows else
            "No projects yet - click 'Add Project'.")

    def show_commands_help(self):
        self.tree.delete(*self.tree.get_children())
        self._rows_by_id.clear()
        self._current_rows = []
        self._reset_preview()
        for cmd, desc in COMMAND_HELP:
            self.tree.insert("", "end", text="⌨️",
                             values=(cmd, "Command", "", "", "", desc))
        self._set_status("Type any of these (typos OK, Hindi OK) and press Enter.")

    def show_history(self):
        def fetch():
            return self.db.recent_commands(50)

        def done(items):
            self.tree.delete(*self.tree.get_children())
            self._rows_by_id.clear()
            self._current_rows = []
            self._reset_preview()
            for h in items:
                iid = self.tree.insert("", "end", text="⌨️", values=(
                    h["command"], "Command", "", "", h["ran_at"],
                    f"action: {h['action']}"))
                self._rows_by_id[iid] = {
                    "file_type": "Command", "name": h["command"],
                    "path": "", "folder": "", "extension": "",
                    "size": 0, "modified_date": ""}
            self._set_status(
                f"Recent commands: {len(items)}. Double-click to run again."
                if items else "No commands run yet.")

        threading.Thread(
            target=lambda: self.after(0, done, fetch()), daemon=True).start()

    # scans
    def start_scan(self):
        if self.scanner.is_running():
            self.scanner.stop()
            self._set_status("Stopping scan...")
            return
        self.scan_btn.configure(text="⏹ Stop Scan")
        self._set_busy(True)
        self._set_status("Scanning drives...")
        self.scanner.start(
            on_progress=lambda n, p: self.after(
                0, self._set_status, f"Indexed {n:,} files...  {p[:60]}"),
            on_done=lambda n: self.after(0, self._scan_done, n),
        )

    def _scan_done(self, n):
        self._set_busy(False)
        self.scan_btn.configure(text="🔄 Scan Drives")
        self._refresh_stats()
        if self.current_view == "Dashboard":
            self.show_recent()
        self._set_status(f"Scan complete. {n:,} items indexed.")

    def start_app_scan(self):
        if self.app_scanner.is_running():
            return
        self.app_scan_btn.configure(text="🚀 Scanning...")
        self._set_busy(True)
        self.app_scanner.start(
            on_done=lambda n: self.after(0, self._app_scan_done, n))

    def _app_scan_done(self, n):
        self._set_busy(False)
        self.app_scan_btn.configure(text="🚀 Scan Apps")
        self._refresh_stats()
        self._set_status(f"App scan complete. {n:,} apps indexed.")
        if self.current_view == "Apps":
            self.show_all_apps()

    def start_game_scan(self):
        if self.game_scanner.is_running():
            return
        self.game_scan_btn.configure(text="🎮 Scanning...")
        self._set_busy(True)
        self.game_scanner.start(
            on_done=lambda n: self.after(0, self._game_scan_done, n))

    def _game_scan_done(self, n):
        self._set_busy(False)
        self.game_scan_btn.configure(text="🎮 Scan Games")
        self._refresh_stats()
        self._set_status(f"Game scan complete. {n:,} games found.")
        if self.current_view == "Games":
            self.show_all_games()

    # downloads
    def open_downloads_folder(self):
        try:
            if os.path.isdir(config.DOWNLOADS_DIR):
                os.startfile(config.DOWNLOADS_DIR)
            else:
                messagebox.showinfo(
                    config.APP_NAME,
                    "Downloads folder was not found on this computer.\n"
                    f"Looked in: {config.DOWNLOADS_DIR}")
        except Exception as e:
            messagebox.showinfo(config.APP_NAME,
                                f"Could not open Downloads folder.\n{e}")

    def show_downloads(self):
        if os.path.isdir(config.DOWNLOADS_DIR):
            self.explore(config.DOWNLOADS_DIR)
        else:
            self.show_results([])
            self._set_status("Downloads folder was not found on this computer.")

    # voice
    def voice_command(self):
        try:
            if self.voice is None:
                self._set_status("Voice module unavailable")
                return
            self.voice.listen_once()
        except Exception:
            self._set_status("Voice module unavailable")

    # async + dashboard views
    def _load_async(self, fetch, label, group_as_files=False):
        self._set_status(f"Loading {label}...")

        def worker():
            try:
                rows = fetch()
            except Exception as e:
                self.after(0, self._set_status, f"Error: {e}")
                return
            self.after(0, done, rows)

        def done(rows):
            if group_as_files:
                self._show_search_groups(label, [], rows)
            else:
                self.show_results(rows)
            total = self.db.count()
            if total == 0:
                self._set_status(
                    "No files indexed yet - click 'Scan Drives' to start.")
            else:
                self._set_status(
                    f"{label}: showing {len(rows):,} of {total:,} indexed.")

        threading.Thread(target=worker, daemon=True).start()

    def show_recent(self):
        self._load_async(lambda: self.db.recent_files(RESULT_LIMIT), "Recent Files")

    def show_large(self):
        self._load_async(lambda: self.db.largest_files(200), "Large Files")

    def show_duplicates(self):
        self._load_async(lambda: self.db.duplicate_suspects(RESULT_LIMIT),
                         "Duplicate Suspects")

    def show_screenshots(self):
        self._load_async(lambda: self.db.screenshots(RESULT_LIMIT), "Screenshots")

    def show_type_summary(self):
        def fetch():
            return self.db.stats_by_type()

        def done(rows):
            display = []
            for r in rows:
                display.append({
                    "name":          r["file_type"],
                    "path":          "",
                    "folder":        f"{r['count']:,} files",
                    "extension":     "",
                    "size":          r["total_size"] or 0,
                    "created_date":  "",
                    "modified_date": "",
                    "file_type":     r["file_type"],
                })
            self.show_results(display)
            self._set_status(
                f"Type summary: {len(rows)} categories, "
                f"{self.db.count():,} total items.")

        threading.Thread(
            target=lambda: self.after(0, done, fetch()), daemon=True).start()

    # ══════════════════════════════════════════ utility / callbacks ═══════════

    def _set_status(self, text):
        """Update the bottom status label (safe to call from any thread via after())."""
        try:
            self.status_label.configure(text=str(text))
        except Exception:
            pass

    def _set_busy(self, busy):
        """Show or hide the indeterminate progress bar."""
        self._busy = busy
        try:
            if busy:
                self.progress.start()
            else:
                self.progress.stop()
                self.progress.set(0)
        except Exception:
            pass

    def _refresh_stats(self):
        """Update dashboard stat cards in the background."""
        def fetch():
            return (self.db.count(), self.db.app_count(),
                    self.db.game_count(), self.db.project_count())

        def done(counts):
            n_files, n_apps, n_games, n_proj = counts
            try:
                self.card_files.configure(text=f"{n_files:,}")
                self.card_apps.configure(text=f"{n_apps:,}")
                self.card_games.configure(text=f"{n_games:,}")
                self.card_projects.configure(text=f"{n_proj:,}")
            except Exception:
                pass

        threading.Thread(
            target=lambda: self.after(0, done, fetch()), daemon=True).start()

    # ── voice callbacks ───────────────────────────────────────────────────────

    def _voice_results(self, rows):
        """Called by VoiceAssistant when it has search results."""
        self.show_results(rows)
        self._set_status(f"Voice: {len(rows):,} results found.")

    def _voice_status(self, text):
        """Called by VoiceAssistant for status updates (listening, processing…)."""
        self._set_status(f"🎤 {text}")
        dot_color = NEON_PURPLE if "listen" in text.lower() else NEON_GREEN
        try:
            self.status_dot.configure(text_color=dot_color)
        except Exception:
            pass

    # ── pulse animation ───────────────────────────────────────────────────────

    def _pulse(self):
        """Slowly cycle the status dot between two colours to show life."""
        self._pulse_state = (self._pulse_state + 1) % 60
        if not self._busy:
            color = NEON_GREEN if self._pulse_state < 30 else "#1a6020"
            try:
                self.status_dot.configure(text_color=color)
            except Exception:
                pass
        self.after(500, self._pulse)


# ═════════════════════════════════════════════════════════════ entry point ════

if __name__ == "__main__":
    app = FileMindApp()
    app.mainloop()
