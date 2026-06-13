# FileMind 🧠 — Smart File Organizer for Windows

A modern CustomTkinter desktop app that indexes your drives into SQLite and gives you instant search, one-click open, safe auto-organizing, and voice commands.

## Setup

```
cd "D:\PER . PROJECT\FileMind"
pip install -r requirements.txt
python main.py
```

If PyAudio fails to install: `pip install pipwin && pipwin install pyaudio`
(Voice is optional — the app runs fine without it.)

## First run

Click **Scan Drives** to index C:, D:, E: (system folders are skipped). Progress shows in the status bar. After that, search is instant.

## Search

- Type a name: `report`
- Extension: `.pdf`
- Folder: `downloads`
- Category filter via the sidebar dropdown
- Typed commands also work in the search bar: `open downloads`, `organize downloads`

## Voice commands 🎤

`open downloads` · `find pdf` · `search python files` · `open file <name>` · `organize downloads`

## Safe mode 🛡️

Nothing is ever permanently deleted. "Move to Trash" puts files in `data/FileMind_Trash`, organizing only moves files (collisions get a `(1)` suffix), and every action is logged to `logs/organizer.log`.

## Files

| File | Purpose |
|---|---|
| `main.py` | Dashboard UI (sidebar, search bar, file table, progress bar, dark mode) |
| `scanner.py` | Threaded drive scanner |
| `database.py` | SQLite index |
| `search_engine.py` | Smart search + open file/folder |
| `organizer.py` | Safe organizer + trash/restore |
| `voice_assistant.py` | Voice command system |
| `config.py` | Paths, categories, theme |
