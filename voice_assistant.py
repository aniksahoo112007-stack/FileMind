"""FileMind - voice command system (read-only, crash-proof).

Voice is strictly optional: nothing auto-starts, and every entry point
is wrapped in try-except so a missing microphone, SpeechRecognition,
or PyAudio can never crash the app.

Supported commands (read-only):
  "open downloads"          -> open the Downloads folder
  "find pdf"                -> search .pdf files
  "search python files"     -> search .py files
  "open file <name>"        -> open best-matching file
  "find <anything>" / "search <anything>" -> free-text search
"""

import os
import re
import threading

import config
from search_engine import SearchEngine

VOICE_UNAVAILABLE_MSG = "Voice module not available. Install required packages first."

try:
    import speech_recognition as sr
    HAS_SR = True
except Exception:
    HAS_SR = False

try:
    import pyttsx3
    HAS_TTS = True
except Exception:
    HAS_TTS = False

EXT_WORDS = {
    "pdf": ".pdf", "pdfs": ".pdf",
    "python": ".py", "py": ".py",
    "image": ".jpg", "images": ".jpg",
    "video": ".mp4", "videos": ".mp4",
    "music": ".mp3", "song": ".mp3", "songs": ".mp3",
    "word": ".docx", "excel": ".xlsx", "text": ".txt",
    "zip": ".zip", "exe": ".exe",
}


class VoiceAssistant:
    """Parses commands and reports results through callbacks.
    Read-only: never deletes, moves, renames, or modifies files."""

    def __init__(self, search: SearchEngine, on_results=None, on_status=None):
        self.search = search
        self.on_results = on_results or (lambda rows, msg: None)
        self.on_status = on_status or (lambda msg: None)
        self._listening = False
        self._tts = None
        if HAS_TTS:
            try:
                self._tts = pyttsx3.init()
            except Exception:
                self._tts = None  # TTS broken -> text-only feedback

    # ------------------------------------------------------------ speech
    def speak(self, text):
        self.on_status(text)
        if self._tts:
            try:
                self._tts.say(text)
                self._tts.runAndWait()
            except Exception:
                pass  # never let TTS crash the app

    def listen_once(self):
        """Capture one voice command in a background thread. Optional only."""
        try:
            if not HAS_SR:
                self.on_status(VOICE_UNAVAILABLE_MSG)
                return
            if self._listening:
                return
            self._listening = True
            threading.Thread(target=self._listen_worker, daemon=True).start()
        except Exception:
            self._listening = False
            self.on_status(VOICE_UNAVAILABLE_MSG)

    def _listen_worker(self):
        try:
            recognizer = sr.Recognizer()
            with sr.Microphone() as mic:
                self.on_status("Listening...")
                recognizer.adjust_for_ambient_noise(mic, duration=0.4)
                audio = recognizer.listen(mic, timeout=5, phrase_time_limit=6)
            self.on_status("Recognizing...")
            text = recognizer.recognize_google(audio)
            self.on_status(f'Heard: "{text}"')
            self.handle_command(text)
        except sr.WaitTimeoutError:
            self.speak("I didn't hear anything.")
        except sr.UnknownValueError:
            self.speak("Sorry, I couldn't understand that.")
        except sr.RequestError:
            self.on_status("Voice recognition needs an internet connection.")
        except (OSError, AttributeError):
            # no microphone / PyAudio missing
            self.on_status(VOICE_UNAVAILABLE_MSG)
        except Exception as e:
            self.on_status(f"Voice error: {e}")
        finally:
            self._listening = False

    # ------------------------------------------------------------ commands
    def handle_command(self, text):
        """Parse a command string (spoken or typed). Never raises."""
        try:
            self._handle(text)
        except Exception as e:
            self.on_status(f"Command error: {e}")

    def _handle(self, text):
        cmd = re.sub(r"[^\w\s.]", "", (text or "").lower()).strip()
        if not cmd:
            return

        if "organize" in cmd or "delete" in cmd or "remove" in cmd:
            self.speak("Read-only mode is enabled. FileMind will not "
                       "delete or move files.")
            return

        if cmd.startswith("open downloads") or cmd == "downloads":
            if os.path.isdir(config.DOWNLOADS_DIR):
                self.speak("Opening Downloads.")
                os.startfile(config.DOWNLOADS_DIR)
            else:
                self.speak("Downloads folder was not found on this computer.")
            return

        m = re.match(r"open file (.+)", cmd)
        if m:
            self._open_file_by_name(m.group(1).strip())
            return

        m = re.match(r"(?:find|search)\s+(.+)", cmd)
        if m:
            self._search_phrase(m.group(1).strip())
            return

        self.speak("Command not recognized. Try: open downloads, find pdf, "
                   "search python files, or open file name.")

    # ------------------------------------------------------------ helpers
    def _open_file_by_name(self, name):
        rows = self.search.quick_search(name, limit=1)
        if rows:
            self.speak(f"Opening {rows[0]['name']}.")
            self.search.open_file(rows[0]["path"])
        else:
            self.speak(f"No file found matching {name}.")

    def _search_phrase(self, phrase):
        words = phrase.replace("files", "").replace("file", "").split()
        ext = next((EXT_WORDS[w] for w in words if w in EXT_WORDS), None)
        if ext and len(words) <= 2:
            rows = self.search.search(extension=ext)
            msg = f"Found {len(rows)} {ext} files."
        else:
            rows = self.search.quick_search(phrase)
            msg = f'Found {len(rows)} results for "{phrase}".'
        self.speak(msg)
        self.on_results(rows, msg)
