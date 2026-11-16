import ctypes
import difflib
import gzip
import itertools
import json
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
import tkinter as tk
import unicodedata
import wave
import webbrowser
from tkinter import ttk, filedialog


try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

import numpy as np
import pyttsx3
import sounddevice as sd
import pytesseract
from PIL import Image, ImageGrab, ImageOps, ImageStat, ImageTk

try:  
    import pyttsx3.drivers.sapi5  
except Exception:
    pass

try:
    from pynput import keyboard, mouse
    try:  
        import pynput.keyboard._win32  
        import pynput.mouse._win32  
    except Exception:
        pass
    HAVE_PYNPUT = True
except Exception:
    HAVE_PYNPUT = False

SPELL_ERR = ""
try: 
    from spellchecker import SpellChecker
    HAVE_SPELL = True
except Exception as _e:
    HAVE_SPELL = False
    SPELL_ERR = f"import failed: {_e!r}  (python: {sys.executable})"


KEEP_WORDS = """lol lmao lmfao rofl gg ggs wp brb afk omg idk imo imho tbh ngl smh rn irl noob noobs
op nerf nerfed buff buffed xp npc npcs pvp pve dps fps ping lag laggy respawn loot discord vc ok okay
yeah yea yep nah bruh bro dude gonna wanna gotta ty thx pls plz rip lul kek pog poggers sus mb ez
gl hf glhf wtf wth btw fyi dm dms mic""".split()

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".tts_mic.json")


def force_focus(win):
    """Really bring a window to the front, even if a game currently has focus."""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetParent(win.winfo_id()) or win.winfo_id()
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        my_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        attach = bool(fg_thread) and fg_thread != my_thread
        if attach:
            user32.AttachThreadInput(fg_thread, my_thread, True)
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        if attach:
            user32.AttachThreadInput(fg_thread, my_thread, False)
    except Exception:
        pass



def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}



def get_output_devices():
    """Return {display_name: device_index}, preferring the MME host API."""
    hostapis = sd.query_hostapis()
    devices = sd.query_devices()
    mme = next((i for i, h in enumerate(hostapis) if "MME" in h["name"]), None)
    result = {}
    for i, d in enumerate(devices):
        if d["max_output_channels"] < 1:
            continue
        if mme is not None and d["hostapi"] != mme:
            continue
        result[f"{d['name']}  [{i}]"] = i
    return result


def get_voices():
    engine = pyttsx3.init()
    voices = [(v.name, v.id) for v in engine.getProperty("voices")]
    engine.stop()
    del engine
    return voices



def synth_to_wav(text, voice_id, rate, path):
    engine = pyttsx3.init()
    if voice_id:
        engine.setProperty("voice", voice_id)
    engine.setProperty("rate", rate)
    engine.save_to_file(text, path)
    engine.runAndWait()
    engine.stop()


def load_wav(path):
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        width = w.getsampwidth()
        raw = w.readframes(w.getnframes())
    dtype = {1: np.uint8, 2: np.int16, 4: np.int32}[width]
    data = np.frombuffer(raw, dtype=dtype)
    if width == 1:
        data = (data.astype(np.float32) - 128) / 128.0
    else:
        data = data.astype(np.float32) / np.iinfo(dtype).max
    if ch > 1:
        data = data.reshape(-1, ch)
    return data, sr


class Speaker(threading.Thread):
    """Speaks queued messages one at a time so nothing overlaps."""

    def __init__(self, on_error):
        super().__init__(daemon=True)
        self.q = queue.PriorityQueue()  
        self._seq = itertools.count()
        self.gen = 0  
        self.on_error = on_error

    def say(self, text, voice_id, rate, device, monitor, priority=1):
        self.q.put((priority, next(self._seq), self.gen, text, voice_id, rate, device, monitor))

    def stop(self):
        self.gen += 1
        try:
            while True:
                self.q.get_nowait()
        except queue.Empty:
            pass

    def run(self):
        try:
            import comtypes
            comtypes.CoInitialize()
        except Exception:
            pass
        while True:
            _, _, gen, text, voice_id, rate, device, monitor = self.q.get()
            if gen != self.gen:
                continue
            try:
                self._speak(gen, text, voice_id, rate, device, monitor)
            except Exception as e:
                self.on_error(str(e))

    def _speak(self, gen, text, voice_id, rate, device, monitor):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            synth_to_wav(text, voice_id, rate, path)
            if gen != self.gen:
                return
            data, sr = load_wav(path)
            ch = 1 if data.ndim == 1 else data.shape[1]

            streams = [sd.OutputStream(samplerate=sr, device=device, channels=ch, dtype="float32")]
            try:
                default_out = sd.default.device[1]
            except Exception:
                default_out = None
            if monitor and default_out != device:
                try:
                    streams.append(sd.OutputStream(samplerate=sr, device=None, channels=ch, dtype="float32"))
                except Exception:
                    pass

            aborted = False
            try:
                for s in streams:
                    s.start()
                for i in range(0, len(data), 2048):
                    if gen != self.gen:
                        aborted = True
                        break
                    block = np.ascontiguousarray(data[i:i + 2048])
                    for s in streams:
                        s.write(block)
            finally:
                for s in streams:
                    try:
                        s.abort() if aborted else s.stop()
                    finally:
                        s.close()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass



OCR_CONFIG = "--psm 6"


def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


TESS_URL = "https://github.com/UB-Mannheim/tesseract/wiki"
CABLE_URL = "https://vb-audio.com/Cable/"


def find_tesseract(saved=None):
    candidates = [
        saved,
        os.path.join(app_dir(), "tesseract", "tesseract.exe"), 
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        shutil.which("tesseract"),
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


BUILTIN_REPLACEMENTS = {
    "wasnt": "wasn't", "isnt": "isn't", "arent": "aren't", "werent": "weren't",
    "dont": "don't", "doesnt": "doesn't", "didnt": "didn't", "cant": "can't",
    "couldnt": "couldn't", "wouldnt": "wouldn't", "shouldnt": "shouldn't", "wont": "won't",
    "hasnt": "hasn't", "havent": "haven't", "hadnt": "hadn't", "aint": "ain't",
    "im": "I'm", "ive": "I've", "youre": "you're", "theyre": "they're", "thats": "that's",
    "whats": "what's", "theres": "there's", "heres": "here's", "wouldve": "would've",
    "couldve": "could've", "shouldve": "should've", "yall": "y'all",
}


def autocorrect(text, spell, repl=None):
    """Apply replacement rules (wasnt -> wasn't), then fix obvious typos. Leaves names
    (capitalised words), ALL-CAPS, numbers, very short words and KEEP_WORDS slang alone.
    `repl` = {lowercase word: replacement}; it overrides the built-in contractions."""
    rules = dict(BUILTIN_REPLACEMENTS)
    rules.update(repl or {})
    parts = re.split(r"([^A-Za-z0-9']+)", text)
    first_word = True
    out = []
    for p in parts:
        if re.fullmatch(r"[A-Za-z0-9]{2,}", p) and p.lower() in rules and not (p.isupper() and p.isalpha()):
            fix = rules[p.lower()]
            p = fix[0].upper() + fix[1:] if p[0].isupper() else fix
        elif spell is not None and re.fullmatch(r"[A-Za-z]{3,}", p):
            skip = p.isupper() or (p[0].isupper() and not first_word)
            if not skip:
                low = p.lower()
                if low not in KEEP_WORDS and spell.unknown([low]):
                    fix = spell.correction(low)
                    if fix and fix != low:
                        p = fix.capitalize() if p[0].isupper() else fix
        if re.search(r"[A-Za-z0-9]", p):
            first_word = False
        out.append(p)
    return "".join(out)


DICT_NAME = "en.json.gz"
DICT_URL = "https://raw.githubusercontent.com/barrust/pyspellchecker/master/spellchecker/resources/en.json.gz"


def dict_paths():
    """Where the English word list may live: next to the app, then in the user's home folder."""
    return [os.path.join(app_dir(), DICT_NAME),
            os.path.join(os.path.expanduser("~"), ".tts_mic_" + DICT_NAME)]


def _good_dict(raw):
    try:
        data = json.loads(gzip.decompress(raw).decode("utf-8"))
        return isinstance(data, dict) and len(data) > 1000
    except Exception:
        return False


def find_dict_file():
    for p in dict_paths():
        try:
            with open(p, "rb") as f:
                if _good_dict(f.read()):
                    return p
        except OSError:
            pass
    return None


def download_dict():
    """Fetch the English word list once and save it next to the app (or in the home folder
    if that folder isn't writable). Returns the saved path; raises if it can't."""
    import urllib.request
    with urllib.request.urlopen(DICT_URL, timeout=25) as r:
        raw = r.read()
    if not _good_dict(raw):
        raise ValueError("the downloaded file wasn't a valid word list")
    last = None
    for p in dict_paths():
        try:
            with open(p, "wb") as f:
                f.write(raw)
            return p
        except OSError as e:
            last = e
    raise last or OSError("no writable folder")


def mask_grip(img):
    """The orange resize handle sits inside the read area's bottom-right corner; paint it
    over with the background colour so the OCR doesn't read it as junk like "=" or "-"."""
    try:
        from PIL import ImageDraw
        img = img.convert("RGB")
        w, h = img.size
        bg = img.getpixel((max(0, w // 2), max(0, h - 1)))
        left = img.getpixel((0, max(0, h - 1)))
        ImageDraw.Draw(img).rectangle([max(0, w - 18), max(0, h - 18), w, h], fill=left or bg)
    except Exception:
        pass
    return img


def prep_image(img):
    """Make the capture easier for Tesseract: bigger, dark-on-light, padded."""
    g = img.convert("L")
    w, h = g.size
    big = max(w, h)
    scale = 3 if big < 1000 else (2 if big < 1800 else 1)
    if scale > 1:
        g = g.resize((w * scale, h * scale), Image.LANCZOS)
    if ImageStat.Stat(g).mean[0] < 128:  
        g = ImageOps.invert(g)
    g = ImageOps.autocontrast(g, cutoff=1)
    return ImageOps.expand(g, border=12, fill=255)
def ocr_word_lines(prepped):
    """OCR a prepared image -> list of lines, each a list of (word, confidence 0-100)."""
    data = pytesseract.image_to_data(prepped, config=OCR_CONFIG,
                                     output_type=pytesseract.Output.DICT)
    if not any(str(t).strip() for t in data["text"]):
      
        data = pytesseract.image_to_data(prepped, config="--psm 7",
                                         output_type=pytesseract.Output.DICT)
    lines = {}
    tops = {}
    for i, txt in enumerate(data["text"]):
        txt = str(txt).strip()
        if not txt:
            continue
        try:
            conf = float(data["conf"][i])
        except (TypeError, ValueError):
            conf = -1.0
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append((txt, conf))
        try:
            tops[key] = min(tops.get(key, 10 ** 6), int(data["top"][i]))
        except (TypeError, ValueError):
            pass
    out = list(lines.items())
    if len(out) > 1 and tops.get(out[0][0], 10 ** 6) <= 12 + 6:
        out = out[1:]
    return [wl for _, wl in out]



_PUNCT = {0x2018: "'", 0x2019: "'", 0x201C: '"', 0x201D: '"', 0x2013: "-", 0x2014: "-"}


def normalize_text(s, strip_symbols=True):
    """Fancy unicode -> plain text, drop emoji/symbols and invisible characters."""
    s = unicodedata.normalize("NFKC", s).translate(_PUNCT)  
    out = []
    for ch in s:
        o = ord(ch)
        cat = unicodedata.category(ch)
        if cat[0] == "C" and ch not in "\n\t":  
            continue
        if 0xFE00 <= o <= 0xFE0F:              
            continue
        if strip_symbols and o > 127 and cat in ("So", "Sk"):  
            continue
        out.append(ch)
    return re.sub(r"[ \t]+", " ", "".join(out)).strip()
_I_FIXES = [
    (re.compile(r"(?<![\w])[|lI1!](?=['\u2019](?:m|ll|ve|d)\b)"), "I"),  
    (re.compile(r"(?<![\w])[|l](?![\w'\u2019])"), "I"),                 
    (re.compile(r"\|(?=[A-Za-z])"), "I"),                                
]
_I_WORD = re.compile(r"^I(?:['\u2019](?:m|ll|ve|d))?$")


def fix_ocr_text(line):
    for pattern, repl in _I_FIXES:
        line = pattern.sub(repl, line)
    line = re.sub(r"[|\u00a6]+", " ", line) 
    return re.sub(r"\s+", " ", line).strip()


def compile_ignores(entries):
    """Plain = whole word, case-sensitive.  i:text = ignore case.  re:pattern = regex."""
    pats = []
    for e in entries:
        if not e.strip():
            continue
        try:
            if e.startswith("re:"):
                pats.append(re.compile(e[3:]))
            elif e.startswith("i:"):
                pats.append(re.compile(r"(?<!\w)" + re.escape(e[2:].strip()) + r"(?!\w)", re.I))
            else:
                pats.append(re.compile(r"(?<!\w)" + re.escape(e.strip()) + r"(?!\w)"))
        except re.error:
            continue
    return pats


class Filters:
    def __init__(self, min_conf=50, strip_symbols=True, ignore=None):
        self.min_conf = float(min_conf)
        self.strip_symbols = bool(strip_symbols)
        self.set_ignore(ignore or [])

    def set_ignore(self, entries):
        self.ignore = list(entries)
        self.patterns = compile_ignores(self.ignore)


def clean_text(text, flt):
    text = fix_ocr_text(normalize_text(text, flt.strip_symbols))
    for p in flt.patterns:
        text = p.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def keep_word(word, conf, min_conf):
    if conf < 0 or conf >= min_conf:
        return True
      
    fixed = fix_ocr_text(word)
    if conf < min_conf / 2:
        return False
    return bool(_I_WORD.match(fixed)) or (len(fixed) == 1 and fixed.isalnum())


def lines_from_words(word_lines, flt):
    lines = []
    for wl in word_lines:
        kept = [w for w, c in wl if keep_word(w, c, flt.min_conf)]
        line = clean_text(" ".join(kept), flt)
        if sum(c.isalnum() for c in line) >= 1: 
            lines.append(line)
    return lines


def describe_words(word_lines, flt):
    out = []
    for wl in word_lines:
        parts = []
        for w, c in wl:
            t = f"{w}({int(c)})"
            parts.append(t if keep_word(w, c, flt.min_conf) else f"[{t}]")
        out.append(" ".join(parts))
    return "\n".join(out)

def sim(a, b):
    return a == b or difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() >= 0.85


def new_lines(prev, cur):
    """Lines in `cur` that weren't already visible in `prev` (handles scrolling)."""
    if not prev:
        return list(cur)
    for k in range(min(len(prev), len(cur)), 0, -1):
        if all(sim(prev[len(prev) - k + i], cur[i]) for i in range(k)):
            return cur[k:]
   
    return [c for c in cur if not any(sim(c, p) for p in prev)]
RN_TIMEOUT = 8.0 
SETTLE_EXTRA = 2.0  


def same_lines(a, b):
    """True if two OCR scans show (nearly) the same text, i.e. nothing is scrolling."""
    if not a or not b or len(a) != len(b):
        return False
    return all(sim(x, y) for x, y in zip(a, b))


SEEN_MAX = 300  


def norm_line(line):
    """Lowercase letters/digits only, so OCR punctuation noise and spacing don't matter."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", line.lower())).strip()


def is_old(line, seen):
    """True if this line was already seen/read, even if it moved, got cropped at the
    edge of the box, was re-wrapped, or the OCR read a few characters differently."""
    n = norm_line(line)
    if len(n) < 1:
        return True  
    if not seen:
        return False
    blob = " | ".join(seen)
    if n in seen or (len(n) >= 5 and n in blob):  
        return True
    if len(n) < 8:
        return False  
    cutoff = 0.85 if len(n) < 16 else 0.72
    for s in seen:
        if abs(len(s) - len(n)) <= max(len(s), len(n)) * 0.4 and \
                difflib.SequenceMatcher(None, s, n).ratio() >= cutoff:
            return True
    if len(n) >= 8: 
        m = difflib.SequenceMatcher(None, blob, n, autojunk=False).find_longest_match(0, len(blob), 0, len(n))
        if m.size >= 0.8 * len(n):
            return True
    return False


def is_own_echo(line, own_texts):
    """True if the line is (part of) something I typed myself, as shown back in the chat."""
    low = re.sub(r"\s+", " ", line.lower()).strip()
    if len(low) < 4:
        return False
    for own in own_texts:
        if own in low or (len(low) >= 8 and low in own):
            return True
        if len(own) >= 6 and difflib.SequenceMatcher(None, own, low[-len(own) - 6:]).ratio() >= 0.85:
            return True
    return False




MOD_ORDER = ("ctrl", "shift", "alt", "win")


def mod_group(name):
    if name.startswith("ctrl"):
        return "ctrl"
    if name.startswith("shift"):
        return "shift"
    if name.startswith("alt"):
        return "alt"
    if name.startswith(("cmd", "win")):
        return "win"
    return None


def canon(mods):
    return [m for m in MOD_ORDER if m in mods]


def key_name(key):
    """Stable name for a pynput key (letters/digits by virtual-key so Shift doesn't change them)."""
    if isinstance(key, keyboard.Key):
        return key.name
    vk = getattr(key, "vk", None)
    ch = getattr(key, "char", None)
    if vk is not None:
        if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
            return chr(vk).lower()
        if 0x60 <= vk <= 0x69:
            return f"num{vk - 0x60}"
    if ch and ch.isprintable():
        return ch.lower()
    if vk is not None:
        return f"vk{vk}"
    return str(key)


def pretty_bind(b):
    names = {"ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "win": "Win"}
    parts = [names[m] for m in canon(b.get("mods", []))]
    kind, _, name = b.get("key", "").partition(":")
    if kind == "mouse":
        label = {"left": "Left Click", "right": "Right Click", "middle": "Middle Click",
                 "x1": "Mouse 4 (Back)", "x2": "Mouse 5 (Forward)"}.get(name, f"Mouse {name}")
    else:
        side = ""
        if name.endswith("_l") or name.endswith("_r"):
            side = "Left " if name.endswith("_l") else "Right "
            name = name[:-2]
        label = name.replace("_", " ").title() if len(name) > 1 else name.upper()
        label = side + label.replace("Cmd", "Win")
    parts.append(label)
    return " + ".join(parts)


class HotkeyManager:
    """Global key + mouse listener. Calls emit(kind, payload) from its own threads."""

    def __init__(self, emit):
        self.emit = emit
        self.binds = {}
        self.capture_action = None
        self.pressed = set()
        self.lone_mod = None
        self.lock = threading.Lock()
        self._kl = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._ml = mouse.Listener(on_click=self._on_click)

    def start(self):
        self._kl.start()
        self._ml.start()

    def stop(self):
        for listener in (self._kl, self._ml):
            try:
                listener.stop()
            except Exception:
                pass

    def set_binds(self, binds):
        with self.lock:
            self.binds = dict(binds)

    def begin_capture(self, action):
        with self.lock:
            self.capture_action = action
            self.lone_mod = None

    def cancel_capture(self):
        with self.lock:
            self.capture_action = None
            self.lone_mod = None

   
    def _held(self):
        return {g for g in (mod_group(n) for n in self.pressed) if g}

    def _finish(self, mods, key_id):
        action = self.capture_action
        self.capture_action = None
        self.lone_mod = None
        self.emit("captured", (action, {"mods": canon(mods), "key": key_id}))

    def _match(self, key_id, mods):
        for action, b in self.binds.items():
            if b.get("key") == key_id and set(b.get("mods", [])) == set(mods):
                self.emit("fire", action)

  
    def _on_press(self, key):
        name = key_name(key)
        with self.lock:
            if name in self.pressed: 
                return
            self.pressed.add(name)
            g = mod_group(name)
            held = self._held()
            if self.capture_action:
                if g:
                    self.lone_mod = name  
                else:
                    self.lone_mod = None
                    if name == "esc" and not held:
                        self.capture_action = None
                        self.emit("cancel", None)
                    else:
                        self._finish(held, "kb:" + name)
                return
            self._match("kb:" + name, (held - {g}) if g else held)

    def _on_release(self, key):
        name = key_name(key)
        with self.lock:
            g = mod_group(name)
            if self.capture_action and g and self.lone_mod == name:
                self._finish(self._held() - {g}, "kb:" + name)
            self.pressed.discard(name)

    def _on_click(self, x, y, button, pressed):
        if not pressed:
            return
        bname = getattr(button, "name", None) or str(button)
        key_id = "mouse:" + bname
        with self.lock:
            held = self._held()
            if self.capture_action:
                if bname in ("left", "right") and not held:
                    return  
                self._finish(held, key_id)
                return
            self._match(key_id, held)

ACTIONS = [
    ("watch_off", "Auto-read OFF"),
    ("watch_on", "Auto-read ON"),
    ("toggle_watch", "Auto-read ON / OFF toggle"),
    ("stop_all", "Stop everything (read off + silence)"),
    ("stop", "Stop speaking"),
    ("read_now", "Read NEW message (waits chat delay)"),
    ("toggle_area", "Show / hide area"),
    ("toggle_quick", "Quick type window"),
    ("focus_chat", "Focus main chat box"),
]



THEMES = {
    "light": dict(bg="#f0f0f0", fg="#000000", field="#ffffff", sel="#0078d4", btn="#e1e1e1",
                  btn_hi="#d0d0d0", line="#adadad", mute="#777777",
                  you="#1a5fb4", area="#26a269", err="#c01c28"),
    "dark": dict(bg="#1f1f23", fg="#e6e6e6", field="#2b2b31", sel="#2f6fbf", btn="#3a3a42",
                 btn_hi="#4a4a54", line="#55555e", mute="#9a9aa5",
                 you="#6cb0ff", area="#5fd391", err="#ff7b7b"),
}


class RegionOverlay(tk.Toplevel):
    """A see-through frame you can drag and resize. Only the border is drawn,
    so the inside (the area that gets read) is never covered or captured."""

    KEY = "#010203"
    COLOR = "#00c8ff"
    GRIP = "#ff9500"
    B = 5      
    BAR = 22  
    MINW, MINH = 140, 80

    def __init__(self, master, x, y, w, h, on_hide):
        super().__init__(master)
        self.on_hide = on_hide
        self.x, self.y, self.w, self.h = x, y, max(w, self.MINW), max(h, self.MINH)
        self._drag = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        try:
            self.attributes("-transparentcolor", self.KEY)
        except tk.TclError:
            pass
        self.configure(bg=self.KEY)

        bar = tk.Frame(self, bg=self.COLOR, cursor="fleur")
        bar.place(x=0, y=0, relwidth=1, height=self.BAR)
        title = tk.Label(bar, text="READ AREA  -  drag to move, pull edges to resize",
                         bg=self.COLOR, fg="black", font=("Segoe UI", 8, "bold"), cursor="fleur")
        title.pack(side="left", padx=6)
        close = tk.Label(bar, text="X", bg=self.COLOR, fg="black",
                         font=("Segoe UI", 9, "bold"), cursor="hand2")
        close.pack(side="right", padx=8)
        close.bind("<Button-1>", lambda e: self.on_hide())
        for wdg in (bar, title):
            self._bind(wdg, "move")

        left = tk.Frame(self, bg=self.COLOR, cursor="sb_h_double_arrow")
        left.place(x=0, y=self.BAR, width=self.B, relheight=1, height=-self.BAR)
        right = tk.Frame(self, bg=self.COLOR, cursor="sb_h_double_arrow")
        right.place(relx=1, x=-self.B, y=self.BAR, width=self.B, relheight=1, height=-self.BAR)
        bottom = tk.Frame(self, bg=self.COLOR, cursor="sb_v_double_arrow")
        bottom.place(x=0, rely=1, y=-self.B, relwidth=1, height=self.B)
        grip = tk.Frame(self, bg=self.GRIP, cursor="size_nw_se")
        grip.place(relx=1, rely=1, x=-16, y=-16, width=16, height=16)

        self._bind(left, "w")
        self._bind(right, "e")
        self._bind(bottom, "s")
        self._bind(grip, "se")
        self._apply()

    def _bind(self, widget, mode):
        widget.bind("<ButtonPress-1>", lambda e, m=mode: self._start(e, m))
        widget.bind("<B1-Motion>", self._motion)

    def _start(self, e, mode):
        self._drag = (mode, e.x_root, e.y_root, self.x, self.y, self.w, self.h)

    def _motion(self, e):
        if not self._drag:
            return
        mode, mx, my, x0, y0, w0, h0 = self._drag
        dx, dy = e.x_root - mx, e.y_root - my
        x, y, w, h = x0, y0, w0, h0
        if mode == "move":
            x, y = x0 + dx, y0 + dy
        else:
            if "e" in mode:
                w = max(self.MINW, w0 + dx)
            if "s" in mode:
                h = max(self.MINH, h0 + dy)
            if "w" in mode:
                w = max(self.MINW, w0 - dx)
                x = x0 + (w0 - w)
        self.x, self.y, self.w, self.h = x, y, w, h
        self._apply()

    def _apply(self):
        self.geometry(f"{self.w}x{self.h}+{self.x}+{self.y}")

    @property
    def bbox(self):
        """Screen rectangle inside the border = the area that gets read."""
        return (self.x + self.B, self.y + self.BAR,
                self.x + self.w - self.B, self.y + self.h - self.B)



class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TTS to Mic")
        self.geometry("640x740")
        self.minsize(540, 580)

        self.cfg = load_config()
        self.devices = get_output_devices()
        self.voices = get_voices()
        self.speaker = Speaker(lambda m: self.after(0, self.log, "err", m))
        self.speaker.start()

        self.watching = False
        self.scanning = False
        self.prev_lines = None
        self.last_scan = None
        self.pending_since = None
        self._after_id = None
        self.seen = []       
        self.own_texts = []   
        self.skip_own_var = tk.BooleanVar(value=self.cfg.get("skip_own", True))
        self.rn_busy = False
        self.spell = None
        self.spell_err = SPELL_ERR
        self.user_words = sorted({w.lower() for w in self.cfg.get("dictionary", [])
                                  if isinstance(w, str) and re.fullmatch(r"[A-Za-z']{2,}", w.strip())})
        self.user_repl = {k.lower(): v for k, v in self.cfg.get("replacements", {}).items()
                          if isinstance(k, str) and isinstance(v, str) and v.strip()
                          and re.fullmatch(r"[A-Za-z0-9]{2,}", k.strip())}
        self.dict_win = None
        self.help_win = None
        if HAVE_SPELL:
            self.spell = self._load_spell()
        self.quick_spell_var = tk.BooleanVar(value=self.cfg.get("quick_spell", True))
        self.debug_var = tk.BooleanVar(value=False)
        self._dbg_last = None

     
        valid = dict(ACTIONS)
        self.binds = {a: b for a, b in self.cfg.get("binds", {}).items()
                      if a in valid and isinstance(b, dict) and "key" in b}
        self.bind_vars = {a: tk.StringVar(value=pretty_bind(self.binds[a]) if a in self.binds else "(none)")
                          for a, _ in ACTIONS}
        self.kb_status = tk.StringVar(value="")
        self.kb_win = None
        self.flt_win = None
        self.quick = None
        self.quick_entry = None
        self.quick_hide_var = tk.BooleanVar(value=self.cfg.get("quick_hide", True))
        self.delay_var = tk.StringVar(value=str(self.cfg.get("delay", 0)))
        self.theme_name = self.cfg.get("theme", "light") if self.cfg.get("theme") in THEMES else "light"
        self.filters = Filters(self.cfg.get("min_conf", 50), self.cfg.get("strip_symbols", True),
                               self.cfg.get("ignore", []))
        self.hk_queue = queue.Queue()
        self.hk = None

        t = find_tesseract(self.cfg.get("tesseract"))
        if t:
            pytesseract.pytesseract.tesseract_cmd = t

        self._build_ui()

        r = self.cfg.get("region", [200, 200, 420, 280])
        self.overlay = RegionOverlay(self, *r, on_hide=self._overlay_closed)
        self.overlay.withdraw()
        self._build_quick()
        self.apply_theme()

        if HAVE_PYNPUT:
            try:
                self.hk = HotkeyManager(lambda kind, payload: self.hk_queue.put((kind, payload)))
                self.hk.set_binds(self.binds)
                self.hk.start()
                self._poll_hotkeys()
            except Exception as e:
                self.hk = None
                self.log("err", f"Keybinds unavailable: {e}")
        else:
            self.log("err", "Keybinds are off because 'pynput' isn't installed. Run: pip install pynput")

        if not self.spell:
            if HAVE_SPELL: 
                self.log("sys", "Spell check word list not found - downloading it once...")
                threading.Thread(target=self._fetch_dict, daemon=True).start()
            else:
                self.log("err", f"Spell check off - {self.spell_err or 'pyspellchecker not installed'}. "
                                "Replacement rules (like wasnt=wasn't) still work.")

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.entry.focus_set()
        has_cable = any("cable" in n.lower() or "vb-audio" in n.lower() for n in self.devices)
        if not t:  
            self.log("err", f"Tesseract OCR not found (needed for the screen reader). Get it at {TESS_URL}")
        if not has_cable:
            self.log("sys", f"No virtual audio cable found (needed so other apps hear this as a mic). "
                            f"Get VB-Cable at {CABLE_URL}")
        if not t or not has_cable:
            self.after(500, lambda: self._setup_help(not t, not has_cable))

    # ---- ui
    def _build_ui(self):
        pad = {"padx": 10, "pady": 4}

        out = ttk.LabelFrame(self, text="Output")
        out.pack(fill="x", **pad)
        out.columnconfigure(1, weight=1)

        ttk.Label(out, text="Device").grid(row=0, column=0, sticky="w", padx=6, pady=3)
        self.device_var = tk.StringVar()
        names = list(self.devices.keys())
        ttk.Combobox(out, textvariable=self.device_var, values=names, state="readonly") \
            .grid(row=0, column=1, columnspan=2, sticky="ew", padx=6, pady=3)
        saved = self.cfg.get("device")
        cable = next((n for n in names if "cable" in n.lower() or "vb-audio" in n.lower()), None)
        if saved in names:
            self.device_var.set(saved)
        elif cable:
            self.device_var.set(cable)
        elif names:
            self.device_var.set(names[0])

        ttk.Label(out, text="Voice").grid(row=1, column=0, sticky="w", padx=6, pady=3)
        self.voice_var = tk.StringVar()
        vnames = [v[0] for v in self.voices]
        self.voice_box = ttk.Combobox(out, textvariable=self.voice_var, values=vnames, state="readonly")
        self.voice_box.grid(row=1, column=1, columnspan=2, sticky="ew", padx=6, pady=3)
        if self.cfg.get("voice") in vnames:
            self.voice_var.set(self.cfg["voice"])
        elif vnames:
            self.voice_box.current(0)

        ttk.Label(out, text="Speed").grid(row=2, column=0, sticky="w", padx=6, pady=3)
        self.rate_var = tk.IntVar(value=self.cfg.get("rate", 175))
        ttk.Scale(out, from_=80, to=300, variable=self.rate_var, orient="horizontal") \
            .grid(row=2, column=1, sticky="ew", padx=6, pady=3)
        self.monitor_var = tk.BooleanVar(value=self.cfg.get("monitor", True))
        ttk.Checkbutton(out, text="Also play on my speakers", variable=self.monitor_var) \
            .grid(row=2, column=2, padx=6, pady=3)

        area = ttk.LabelFrame(self, text="Screen area reader")
        area.pack(fill="x", **pad)
        row1 = ttk.Frame(area)
        row1.pack(fill="x", padx=6, pady=3)
        self.show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row1, text="Show area", variable=self.show_var,
                        command=self.toggle_overlay).pack(side="left")
        self.watch_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(row1, text="Auto-read new text", variable=self.watch_var,
                        command=self.toggle_watch).pack(side="left", padx=14)
        self.theme_btn = ttk.Button(row1, width=7, command=self.toggle_theme)
        self.theme_btn.pack(side="right")
        ttk.Button(row1, text="?  Help", width=7, command=self.open_help).pack(side="right", padx=(0, 6))
        ttk.Label(row1, text="Delay").pack(side="left")
        ttk.Spinbox(row1, from_=0, to=60, increment=0.5, width=5,
                    textvariable=self.delay_var).pack(side="left", padx=4)
        ttk.Label(row1, text="sec").pack(side="left")

        row2 = ttk.Frame(area)
        row2.pack(fill="x", padx=6, pady=3)
        ttk.Button(row2, text="Read area now", command=self.read_all_now).pack(side="left")
        ttk.Button(row2, text="Preview OCR", command=self.preview_ocr).pack(side="left", padx=6)
        ttk.Button(row2, text="Text filters...", command=self.open_filters).pack(side="left")
        ttk.Checkbutton(row2, text="Debug (log each scan)", variable=self.debug_var).pack(side="left", padx=8)
        row3 = ttk.Frame(area)
        row3.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Button(row3, text="Quick type window", command=self.toggle_quick).pack(side="left")
        ttk.Button(row3, text="Keybinds...", command=self.open_keybinds).pack(side="left", padx=6)
        ttk.Button(row3, text="Tesseract path...", command=self.pick_tesseract).pack(side="left")
        ttk.Checkbutton(row3, text="Skip my own typed messages",
                        variable=self.skip_own_var).pack(side="left", padx=8)
        row4 = ttk.Frame(area)
        row4.pack(fill="x", padx=6, pady=(0, 4))
        ttk.Button(row4, text="Spell check dictionary...", command=self.open_dictionary).pack(side="left")

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, **pad)
        self.chat = tk.Text(frame, wrap="word", state="disabled", font=("Segoe UI", 10))
        sb = ttk.Scrollbar(frame, command=self.chat.yview)
        self.chat.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True)
        self.chat.tag_configure("you", foreground="#1a5fb4")
        self.chat.tag_configure("area", foreground="#26a269")
        self.chat.tag_configure("sys", foreground="#777777")
        self.chat.tag_configure("err", foreground="#c01c28")

        bottom = ttk.Frame(self)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        self.entry = ttk.Entry(bottom, font=("Segoe UI", 11))
        self.entry.pack(side="left", fill="x", expand=True, ipady=3)
        self.entry.bind("<Return>", lambda e: self.send())
        ttk.Button(bottom, text="Send", command=self.send).pack(side="left", padx=(6, 0))
        ttk.Button(bottom, text="Stop", command=self.stop_speech).pack(side="left", padx=(6, 0))

        self.log("sys", "Type a message and press Enter to speak it. "
                        "Use 'Show area' to place the read box over a chat window.")

    def log(self, kind, text):
        prefix = {"you": "You: ", "area": "Area: ", "sys": "- ", "err": "! "}[kind]
        self.chat.config(state="normal")
        self.chat.insert("end", prefix + text + "\n", kind)
        self.chat.see("end")
        self.chat.config(state="disabled")


    def speak(self, text, kind):
        dev = self.device_var.get()
        if dev not in self.devices:
            self.log("err", "Pick an output device first.")
            return False
        spoken = normalize_text(text, self.filters.strip_symbols)
        if not spoken:
            self.log("sys", "Nothing to say (that was only emoji/symbols).")
            return False
        voice_id = None
        if self.voice_box.current() >= 0:
            voice_id = self.voices[self.voice_box.current()][1]
        args = (spoken, voice_id, int(self.rate_var.get()), self.devices[dev], self.monitor_var.get())
        prio = 0 if kind == "you" else 1
        self.speaker.say(*args, priority=prio)
        if kind == "you":
            self.own_texts = (self.own_texts + [re.sub(r"\s+", " ", spoken.lower()).strip()])[-10:]
        self.log(kind, text)
        return True

    def send(self):
        text = self.entry.get().strip()
        if text and self.speak(text, "you"):
            self.entry.delete(0, "end")

    def stop_speech(self):
        self.speaker.stop()
        self.log("sys", "Stopped.")

   
    def toggle_overlay(self):
        if self.show_var.get():
            self.overlay.deiconify()
            self.overlay.lift()
        else:
            self.overlay.withdraw()

    def _overlay_closed(self):
        self.show_var.set(False)
        self.overlay.withdraw()

    def _theme(self):
        return THEMES[self.theme_name]

    def _themed(self, win):
        """Apply the current theme to a window that was just created."""
        self._theme_tree(win, self._theme())
        self._dark_titlebar(win)

    def _theme_tree(self, w, t):
        try:
            cls = w.winfo_class()
            if cls in ("Tk", "Toplevel"):
                w.configure(bg=t["bg"])
            elif cls in ("Text", "Listbox", "Entry"):
                w.configure(bg=t["field"], fg=t["fg"], selectbackground=t["sel"],
                            selectforeground="#ffffff", highlightbackground=t["line"],
                            highlightcolor=t["sel"])
                if cls != "Listbox":
                    w.configure(insertbackground=t["fg"])
        except tk.TclError:
            pass
        for c in w.winfo_children():
            if c is getattr(self, "overlay", None): 
                continue
            self._theme_tree(c, t)

    def _dark_titlebar(self, win):
        try:
            win.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
            val = ctypes.c_int(1 if self.theme_name == "dark" else 0)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(val), 4)
        except Exception:
            pass

    def apply_theme(self):
        t = self._theme()
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        st.configure(".", background=t["bg"], foreground=t["fg"], fieldbackground=t["field"],
                     bordercolor=t["line"], lightcolor=t["bg"], darkcolor=t["bg"],
                     troughcolor=t["field"], insertcolor=t["fg"], focuscolor=t["bg"])
        st.configure("TLabelframe", background=t["bg"], bordercolor=t["line"])
        st.configure("TLabelframe.Label", background=t["bg"], foreground=t["fg"])
        st.configure("TButton", background=t["btn"], foreground=t["fg"], bordercolor=t["line"], padding=3)
        st.map("TButton", background=[("active", t["btn_hi"]), ("pressed", t["btn_hi"])])
        st.configure("TCheckbutton", background=t["bg"], foreground=t["fg"],
                     indicatorbackground=t["field"], indicatorforeground=t["fg"])
        st.map("TCheckbutton", background=[("active", t["bg"])],
               indicatorbackground=[("selected", t["sel"]), ("active", t["btn_hi"])])
        for name in ("TEntry", "TSpinbox", "TCombobox"):
            st.configure(name, fieldbackground=t["field"], foreground=t["fg"], insertcolor=t["fg"],
                         background=t["btn"], arrowcolor=t["fg"], bordercolor=t["line"])
        for name in ("TEntry", "TSpinbox", "TCombobox"):  # override clam's own (white) state colours
            st.map(name, fieldbackground=[("disabled", t["btn"]), ("readonly", t["field"]), ("", t["field"])],
                   foreground=[("disabled", t["mute"]), ("", t["fg"])],
                   selectbackground=[("", t["sel"])], selectforeground=[("", "#ffffff")])
        st.configure("TScrollbar", background=t["btn"], troughcolor=t["bg"], arrowcolor=t["fg"],
                     bordercolor=t["line"])
        st.configure("Horizontal.TScale", background=t["bg"], troughcolor=t["field"])
        self.option_add("*TCombobox*Listbox.background", t["field"])
        self.option_add("*TCombobox*Listbox.foreground", t["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", t["sel"])
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self._theme_tree(self, t)
        for tag, key in (("you", "you"), ("area", "area"), ("sys", "mute"), ("err", "err")):
            self.chat.tag_configure(tag, foreground=t[key])
        self.theme_btn.config(text="Light" if self.theme_name == "dark" else "Dark")
        self._dark_titlebar(self)
        for w in self.winfo_children():
            if w.winfo_class() == "Toplevel" and w is not getattr(self, "overlay", None):
                self._dark_titlebar(w)

    def toggle_theme(self):
        self.theme_name = "light" if self.theme_name == "dark" else "dark"
        self.cfg["theme"] = self.theme_name
        self.apply_theme()

   
    def _interval(self):
        return 0.5  

    def _delay(self):
        try:
            return min(60.0, max(0.0, float(self.delay_var.get())))
        except ValueError:
            return 0.0

    def toggle_watch(self):
        if self.watch_var.get():
            if not self.show_var.get():
                self.show_var.set(True)
                self.toggle_overlay()
            self.watching = True
            self.prev_lines = None
            self.last_scan = None
            self.pending_since = None
            self.log("sys", "Auto-read ON. New text in the area will be spoken.")
            self._tick()
        else:
            self.watching = False
            self.pending_since = None
            if self._after_id:
                self.after_cancel(self._after_id)
                self._after_id = None
            self.log("sys", "Auto-read OFF.")

    def _tick(self):
        if not self.watching:
            return
        if not self.scanning:
            self._start_scan(read_all=False)
        ms = int(self._interval() * 1000)
        if self.pending_since is not None:
            ms = min(ms, 500)  
        self._after_id = self.after(ms, self._tick)

    def read_all_now(self):
        """Button: read everything currently in the box, right now."""
        if not self.scanning:
            self._start_scan(read_all=True)

    def read_now(self):
        """Keybind: for 'press Enter to send a chat message, then read that message'.
        Snapshot the box the instant the key is pressed (the old state), wait the chat
        delay for the message to post and scroll, then read only what is new."""
        if self.rn_busy:
            return
        try:
            bbox = self.overlay.bbox
            img = mask_grip(ImageGrab.grab(bbox=bbox, all_screens=True))
        except Exception as e:
            self.log("err", self._ocr_error_text(e))
            return
        self.rn_busy = True
        threading.Thread(target=self._rn_worker, args=(img, bbox, self._delay()), daemon=True).start()

    def _lines_of(self, img):
        return lines_from_words(ocr_word_lines(prep_image(img)), self.filters)

    def _rn_worker(self, img0, bbox, delay):
        try:
            base = self._lines_of(img0)
            seen = list(self.seen) + [norm_line(l) for l in base]
            if delay > 0:
                time.sleep(delay)
            deadline = time.monotonic() + RN_TIMEOUT
            last, cur, new = None, [], []
            while True:
                cur = self._lines_of(mask_grip(ImageGrab.grab(bbox=bbox, all_screens=True)))
                new = []
                for l in reversed(cur): 
                    if is_old(l, seen):
                        break
                    new.insert(0, l)
                if self.skip_own_var.get():
                    new = [l for l in new if not is_own_echo(l, self.own_texts)]
                if new and last is not None and same_lines(cur, last):
                    break  
                last = cur
                if time.monotonic() > deadline:
                    break
                time.sleep(0.3)
            self.after(0, self._rn_done, base, cur, new[-10:], None)
        except Exception as e:
            self.after(0, self._rn_done, None, None, None, e)

    def _rn_done(self, base, cur, new, err):
        self.rn_busy = False
        if err is not None:
            self.log("err", self._ocr_error_text(err))
            return
        self._mark_seen(base)
        self._mark_seen(cur)
        if self.watching:
            self.prev_lines = cur
            self.last_scan = cur
            self.pending_since = None
        if self.debug_var.get():
            self.log("sys", f"key-read: before={base} after={cur} -> new={new}")
        if new:
            self.speak(". ".join(l.rstrip(".") for l in new), "area")
        else:
            self.log("sys", "No new message showed up in the area.")

    def _start_scan(self, read_all):
        self.scanning = True
        bbox = self.overlay.bbox
        threading.Thread(target=self._scan_worker, args=(bbox, read_all), daemon=True).start()

    def _scan_worker(self, bbox, read_all):
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            img = mask_grip(img)
            words = ocr_word_lines(prep_image(img))
            lines = lines_from_words(words, self.filters)
            self.after(0, self._scan_done, lines, read_all, None)
        except Exception as e:
            self.after(0, self._scan_done, None, read_all, e)

    def _ocr_error_text(self, err):
        if isinstance(err, pytesseract.TesseractNotFoundError):
            return ("Tesseract OCR not found. Install it from "
                    "https://github.com/UB-Mannheim/tesseract/wiki, "
                    "then click 'Tesseract path...' if needed.")
        return f"Screen read failed: {err}"

    def _scan_done(self, lines, read_all, err):
        self.scanning = False
        if err is not None:
            self.log("err", self._ocr_error_text(err))
            if self.watching:
                self.watch_var.set(False)
                self.toggle_watch()
            return

        if read_all:
            if lines:
                self.speak(". ".join(l.rstrip(".") for l in lines), "area")
                self.prev_lines = lines
                self._mark_seen(lines)
            else:
                self.log("sys", "No text found in the area.")
            return

        if not self.watching:
            return
        if self.prev_lines is None: t
            self.prev_lines = lines
            self.last_scan = lines
            self.pending_since = None
            self._mark_seen(lines)
            return
        if not lines:
            return

        stable = same_lines(lines, self.last_scan)  
        self.last_scan = lines
      
        new = []
        for l in reversed(lines):
            if is_old(l, self.seen):
                break
            new.insert(0, l)
        if self.skip_own_var.get():
            new = [l for l in new if not is_own_echo(l, self.own_texts)]
        self._mark_seen([l for l in lines if l not in new])
        new = new[-10:]
        if self.debug_var.get():
            snap = (tuple(lines), tuple(new), stable)
            if snap != self._dbg_last:
                self._dbg_last = snap
                self.log("sys", f"scan: {lines} -> new: {new}  ({'settled' if stable else 'moving'})")
        if not new:
            self.pending_since = None
            if stable:
                self.prev_lines = lines 
                self._mark_seen(lines)
            return

        now = time.monotonic()
        if self.pending_since is None:
            self.pending_since = now
        waited = now - self.pending_since
        delay = self._delay()
      
        if waited < delay or (not stable and waited < delay + SETTLE_EXTRA):
            return 
        self.pending_since = None
        self.prev_lines = lines
        self._mark_seen(lines)
        self.speak(". ".join(l.rstrip(".") for l in new), "area")

    def _mark_seen(self, lines):
        for l in lines:
            n = norm_line(l)
            if len(n) >= 1 and n not in self.seen:
                self.seen.append(n)
        self.seen = self.seen[-SEEN_MAX:]

   
    def preview_ocr(self):
        if self.scanning:
            return
        self.scanning = True
        bbox = self.overlay.bbox
        threading.Thread(target=self._preview_worker, args=(bbox,), daemon=True).start()

    def _preview_worker(self, bbox):
        try:
            img = ImageGrab.grab(bbox=bbox, all_screens=True)
            img = mask_grip(img)
            prepped = prep_image(img)
            words = ocr_word_lines(prepped)
            self.after(0, self._show_preview, prepped, words, None)
        except Exception as e:
            self.after(0, self._show_preview, None, [], e)

    def _show_preview(self, prepped, words, err):
        self.scanning = False
        if err is not None:
            self.log("err", self._ocr_error_text(err))
            return
        win = tk.Toplevel(self)
        win.title("OCR preview")
        thumb = prepped.copy()
        thumb.thumbnail((700, 300))
        photo = ImageTk.PhotoImage(thumb)
        lbl = ttk.Label(win, image=photo)
        lbl.image = photo  # keep a reference
        lbl.pack(padx=8, pady=8)
        txt = tk.Text(win, height=14, width=80, wrap="word", font=("Consolas", 10))
        txt.insert("end", f"WHAT OCR SEES  word(confidence); [brackets] = dropped (below {int(self.filters.min_conf)}):\n"
                   + describe_words(words, self.filters)
                   + "\n\nWHAT WILL BE SPOKEN:\n"
                   + "\n".join(lines_from_words(words, self.filters)))
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True, padx=8, pady=(0, 6))
        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Label(bar, text="Ignore:").pack(side="left")
        ent = ttk.Entry(bar, width=22)
        ent.pack(side="left", padx=6)
        ttk.Button(bar, text="Add to ignore list",
                   command=lambda: self._add_ignore(ent, txt)).pack(side="left")
        ttk.Label(bar, text="(type a word, or select one above first)").pack(side="left", padx=8)
        self._themed(win)

    def _setup_help(self, need_tess, need_cable):
        """One small window listing whatever the app can't find, each with its download link."""
        win = tk.Toplevel(self)
        win.title("Setup needed")
        win.resizable(False, False)
        win.transient(self)

        def section(title, text, url, extra=None):
            box = ttk.LabelFrame(win, text=title)
            box.pack(fill="x", padx=14, pady=(12, 0))
            ttk.Label(box, justify="left", wraplength=440, text=text).pack(anchor="w", padx=10, pady=(8, 6))
            link = tk.Entry(box, font=("Segoe UI", 10), relief="flat", highlightthickness=1)
            link.insert(0, url)
            link.config(state="readonly")
            link.pack(fill="x", padx=10, ipady=3)
            row = ttk.Frame(box)
            row.pack(fill="x", padx=10, pady=8)
            ttk.Button(row, text="Open download page", command=lambda: webbrowser.open(url)).pack(side="left")
            if extra:
                extra(row)

        if need_tess:
            def browse():
                self.pick_tesseract()
                if os.path.isfile(pytesseract.pytesseract.tesseract_cmd):
                    win.destroy()
            section("Tesseract OCR  (screen reader)",
                    "Not found on this computer. Typing and speaking work without it, but Auto-read and "
                    "Preview OCR need it. Download the Windows installer, run it (the default folder is "
                    "fine), then restart this app. Installed somewhere else? Use Browse.",
                    TESS_URL, lambda r: ttk.Button(r, text="Browse...", command=browse).pack(side="left", padx=6))
        if need_cable:
            section("VB-Cable  (virtual microphone)",
                    "Optional. Without it you only hear the voice on your own speakers. To let other apps "
                    "(Discord, games) hear it: download VB-Cable, unzip it, run the setup as administrator, "
                    "and restart the PC. Then pick \"CABLE Input\" as the Device here, and \"CABLE Output\" "
                    "as the microphone in the other app.",
                    CABLE_URL)
        ttk.Button(win, text="Close", command=win.destroy).pack(anchor="e", padx=14, pady=14)
        self._themed(win)

    def pick_tesseract(self):
        p = filedialog.askopenfilename(title="Select tesseract.exe",
                                       filetypes=[("tesseract.exe", "tesseract.exe"), ("All files", "*.*")])
        if p:
            pytesseract.pytesseract.tesseract_cmd = p
            self.cfg["tesseract"] = p
            self.log("sys", f"Tesseract set to {p}")

    
    def _build_quick(self):
        win = tk.Toplevel(self)
        win.title("Quick type")
        win.attributes("-topmost", True)
        win.resizable(True, False)
        win.geometry(self.cfg.get("quick_pos") or "460x84+200+120")
        row = ttk.Frame(win)
        row.pack(fill="x", padx=8, pady=(8, 2))
        self.quick_entry = ttk.Entry(row, font=("Segoe UI", 12))
        self.quick_entry.pack(side="left", fill="x", expand=True, ipady=3)
        ttk.Button(row, text="Send", command=self.quick_send).pack(side="left", padx=(6, 0))
        opts = ttk.Frame(win)
        opts.pack(anchor="w", padx=8)
        ttk.Checkbutton(opts, text="Hide after sending (Esc also hides)",
                        variable=self.quick_hide_var).pack(side="left")
        ttk.Checkbutton(opts, text="Spell check", variable=self.quick_spell_var).pack(side="left", padx=10)
        self.quick_entry.bind("<Return>", lambda e: self.quick_send())
        self.quick_entry.bind("<Shift-Return>", lambda e: self.quick_send(learn=True))
        win.bind("<Escape>", lambda e: self.hide_quick())
        win.protocol("WM_DELETE_WINDOW", self.hide_quick)
        win.withdraw()
        self.quick = win

    def toggle_quick(self):
        if self.quick.state() == "withdrawn":
            self.quick.deiconify()
            self.quick.lift()
            self.quick.attributes("-topmost", True)
            self.quick.after(60, lambda: (force_focus(self.quick), self.quick_entry.focus_set()))
        else:
            self.hide_quick()

    def hide_quick(self):
        self.cfg["quick_pos"] = self.quick.geometry()
        self.quick.withdraw()

  
    def _load_spell(self):
        """Build the spell checker from the package's word list, or from en.json.gz next to the app."""
        try:
            sp = SpellChecker()
        except Exception as e1:
            path = find_dict_file()
            if not path:
                self.spell_err = f"word list not found ({e1})"
                return None
            try:
                sp = SpellChecker(language=None, local_dictionary=path)
            except Exception as e2:
                self.spell_err = f"couldn't read {path}: {e2}"
                return None
        try:
            sp.word_frequency.load_words(KEEP_WORDS)
            sp.word_frequency.load_words(self.user_words)
        except Exception:
            pass
        return sp

    def _fetch_dict(self):
        try:
            self.after(0, self._spell_ready, download_dict(), None)
        except Exception as e:
            self.after(0, self._spell_ready, None, e)

    def _spell_ready(self, path, err):
        if err is None:
            sp = self._load_spell()
            if sp:
                self.spell = sp
                self.log("sys", f"Spell check ready. Word list saved to {path}")
                return
            err = self.spell_err
        self.log("err", f"Couldn't get the spell check word list ({err}). Replacement rules still work. "
                        f"To fix it, download {DICT_URL} and save it next to the app as {DICT_NAME}.")

    def add_word(self, word):
        w = word.strip().lower()
        if not self.spell:
            self.log("err", "The spell check word list isn't loaded (see the message at startup).")
            return False
        if not re.fullmatch(r"[A-Za-z']{2,}", w):
            self.log("sys", f"Can't add \"{word}\" (letters only, 2+ long).")
            return False
        if w in self.user_words:
            return False
        if self.spell.unknown([w]):  
            self.spell.word_frequency.load_words([w])
            self.user_words = sorted(self.user_words + [w])
            self.save_config()
            self.log("sys", f"Added \"{w}\" to the dictionary.")
            if self.dict_win is not None and self.dict_win.winfo_exists():
                self._refresh_dict()
            return True
        self.log("sys", f"\"{w}\" is already a known word.")
        return False

    def add_replacement(self, src, dst):
        s, d = src.strip().lower(), dst.strip()
        if not re.fullmatch(r"[A-Za-z0-9]{2,}", s) or not d:
            self.log("sys", f"Can't add rule \"{src.strip()}\" -> \"{dst.strip()}\". The part BEFORE the = must be "
                            "one short word (letters/numbers, 2+ long), e.g.  fr=for real")
            return False
        self.user_repl[s] = d
        self.save_config()
        self.log("sys", f"Rule added: {s} -> {d}")
        if self.dict_win is not None and self.dict_win.winfo_exists():
            self._refresh_dict()
        return True

    def add_entry(self, token):
        """'word' adds a dictionary word; 'typo=fix' adds a replacement rule."""
        parts = re.split(r"\s*(?:=>|->|=)\s*", token.strip(), maxsplit=1)
        if len(parts) == 2:
            return self.add_replacement(parts[0], parts[1])
        return self.add_word(token)

    def add_entries(self, raw):
        """'a b c' adds dictionary words. 'fr=for real' adds a rule (replacement may have
        spaces); several rules can be separated with ;   e.g.  fr=for real; rn=right now"""
        raw = raw.strip()
        added = False
        if re.search(r"=|->", raw):
            for chunk in raw.split(";"):
                if chunk.strip():
                    added = bool(self.add_entry(chunk)) or added
        else:
            for w in raw.split():
                added = bool(self.add_entry(w)) or added
        return added

    @staticmethod
    def _entry_keys(raw):
        """The lowercase key of each entry in `raw` (the typo of a rule, or the word itself)."""
        raw = raw.strip()
        chunks = raw.split(";") if re.search(r"=|->", raw) else raw.split()
        return {re.split(r"\s*(?:=>|->|=)\s*", c.strip(), maxsplit=1)[0].strip().lower()
                for c in chunks if c.strip()}

    def _remove_item(self, item):
        if " = " in item:
            self.user_repl.pop(item.split(" = ")[0].strip(), None)
            self.save_config()
        else:
            self.remove_word(item)

    def remove_word(self, word):
        w = word.strip().lower()
        if w in self.user_words:
            self.user_words.remove(w)
            try:
                self.spell.word_frequency.remove(w)
            except Exception:
                pass
            self.save_config()
            self.log("sys", f"Removed \"{w}\" from the dictionary.")

    def learn_from_text(self, text):
        """Add every word in `text` that the spell checker doesn't know."""
        for w in dict.fromkeys(re.findall(r"[A-Za-z']{2,}", text)):
            self.add_word(w)

    def _refresh_dict(self):
        self.dict_list.delete(0, "end")
        q = self.dict_filter.get().strip().lower() if getattr(self, "dict_filter", None) else ""
        items = list(self.user_words) + [f"{k} = {self.user_repl[k]}" for k in sorted(self.user_repl)]
        shown = [i for i in items if q in i.lower()]
        for i in shown:
            self.dict_list.insert("end", i)
        if getattr(self, "dict_count", None):
            self.dict_count.set(f"{len(shown)} of {len(items)}" if q else f"{len(items)} entries")

    # ("h", heading) | ("p", paragraph) | ("r", term, description)  -- rows align on a tab stop
    HELP = [
        ("h", "Typing"),
        ("p", "Type in the box at the bottom and press Enter to speak it. Stop silences it."),
        ("p", "Quick type window: a small always-on-top box to pop up over a game with a keybind. "
              "Enter sends, Esc hides."),
        ("h", "Quick type commands"),
        ("r", "Shift+Enter", "send exactly as typed and learn its new words"),
        ("r", "/add word", "a word the spell check should never change"),
        ("r", "/add fr=for real", "a replacement rule; separate several with ;  (rn=right now)"),
        ("h", "Reading a chat"),
        ("r", "Show area", "the blue frame. Drag the bar to move it, pull the edges or orange corner to "
                           "resize. Keep the newest message fully inside, under the bar."),
        ("r", "Auto-read new text", "speaks new messages as they appear at the bottom"),
        ("r", "Delay", "seconds to wait before reading, so a message can post and scroll"),
        ("r", "Skip my own typed messages", "don't read back what you just sent"),
        ("r", "Read area now", "reads everything currently in the box"),
        ("r", "Preview OCR", "shows what the reader sees and what it would say"),
        ("h", "Keybinds"),
        ("p", "Click Set, then press a key or mouse button (hold Shift/Ctrl/Alt first to include it). "
              "Esc cancels."),
        ("r", "Auto-read ON / OFF", "turn auto-read on, off, or toggle it"),
        ("r", "Read NEW message", "bind this to the key you press to send a chat message (like Enter): "
                                  "it waits the Delay, then reads only the new message"),
        ("r", "Stop everything", "auto-read off and silence"),
        ("h", "Text filters"),
        ("r", "Min confidence", "drops words the reader isn't sure about"),
        ("r", "Ignore list", "a plain word matches exactly (case-sensitive); i:word ignores case; "
                             "re:pattern is a regular expression"),
        ("h", "Spell check dictionary"),
        ("p", "Search, edit or remove your words and rules. A rule like d4rk=dark always replaces d4rk "
              "with dark. Common ones (wasnt, dont, im...) are built in. The Spell check box in the "
              "Quick type window turns all of this on and off."),
        ("h", "Other"),
        ("r", "Dark / Light", "switches the colours"),
        ("r", "Debug", "logs what each scan saw"),
        ("r", "Settings", "saved in .tts_mic.json in your user folder"),
    ]

    def open_help(self):
        if self.help_win is not None and self.help_win.winfo_exists():
            self.help_win.lift()
            return
        win = tk.Toplevel(self)
        win.title("Help")
        win.geometry("640x560")
        self.help_win = win
        f = ttk.Frame(win)
        f.pack(fill="both", expand=True, padx=10, pady=10)
        sb = ttk.Scrollbar(f)
        sb.pack(side="right", fill="y")
        txt = tk.Text(f, wrap="word", font=("Segoe UI", 10), relief="flat", highlightthickness=1,
                      yscrollcommand=sb.set, padx=10, pady=6)
        txt.pack(side="left", fill="both", expand=True)
        sb.config(command=txt.yview)
        col = 200  
        txt.tag_configure("h", font=("Segoe UI", 11, "bold"), spacing1=12, spacing3=4)
        txt.tag_configure("p", spacing3=5)
        txt.tag_configure("r", tabs=(col,), lmargin1=0, lmargin2=col, spacing3=5)  
        for item in self.HELP:
            if item[0] == "r":
                txt.insert("end", f"{item[1]}\t{item[2]}\n", "r")
            else:
                txt.insert("end", item[1] + "\n", item[0])
        txt.config(state="disabled")
        self._themed(win)

    def open_dictionary(self):
        if self.dict_win is not None and self.dict_win.winfo_exists():
            self.dict_win.lift()
            return
        win = tk.Toplevel(self)
        win.title("Spell check dictionary")
        self.dict_win = win
        ttk.Label(win, justify="left", foreground=self._theme()["mute"],
                  text="Add a word to never correct it, or a rule like  fr=for real\n"
                       "(separate several with ;).  Quick type: Shift+Enter sends and learns."
                  ).pack(anchor="w", padx=10, pady=(10, 4))
        font = ("Segoe UI", 10)
        row = ttk.Frame(win)
        row.pack(fill="x", padx=10)
        ent = tk.Entry(row, font=font, relief="flat", highlightthickness=1)
        ent.pack(side="left", fill="x", expand=True, ipady=3)
        editing = {"item": None}

        def end_edit():
            editing["item"] = None
            add_btn.config(text="Add")
            cancel_btn.pack_forget()
            ent.delete(0, "end")

        def add():
            raw = ent.get()
            if not raw.strip():
                return
            ok = self.add_entries(raw)
            old = editing["item"]
            if old is not None:
                old_key = old.split(" = ")[0].strip().lower()
                if ok and old_key not in self._entry_keys(raw):
                    self._remove_item(old)  
                if ok or old_key in self._entry_keys(raw):
                    end_edit()
            else:
                ent.delete(0, "end")
            self._refresh_dict()
        ent.bind("<Return>", lambda e: add())
        add_btn = ttk.Button(row, text="Add", width=7, command=add)
        add_btn.pack(side="left", padx=(6, 0))
        cancel_btn = ttk.Button(row, text="Cancel", width=7, command=end_edit)

        srow = ttk.Frame(win)
        srow.pack(fill="x", padx=10, pady=(8, 0))
        ttk.Label(srow, text="Search").pack(side="left", padx=(0, 6))
        self.dict_filter = tk.StringVar()
        self.dict_count = tk.StringVar()
        sent = tk.Entry(srow, font=font, relief="flat", highlightthickness=1, textvariable=self.dict_filter)
        sent.pack(side="left", fill="x", expand=True, ipady=3)
        ttk.Button(srow, text="Clear", width=7, command=lambda: self.dict_filter.set("")) \
            .pack(side="left", padx=(6, 0))
        self.dict_filter.trace_add("write", lambda *a: self._refresh_dict())

        lf = ttk.Frame(win)
        lf.pack(fill="both", expand=True, padx=10, pady=6)
        sb = ttk.Scrollbar(lf)
        sb.pack(side="right", fill="y")
        self.dict_list = tk.Listbox(lf, height=12, selectmode="extended", font=font,
                                    relief="flat", highlightthickness=1, yscrollcommand=sb.set)
        self.dict_list.pack(side="left", fill="both", expand=True)
        sb.config(command=self.dict_list.yview)
        self._refresh_dict()

        def edit():
            sel = self.dict_list.curselection()
            if len(sel) != 1:
                self.log("sys", "Select one entry to edit.")
                return
            item = self.dict_list.get(sel[0])
            editing["item"] = item
            ent.delete(0, "end")
            ent.insert(0, item.replace(" = ", "="))
            ent.focus_set()
            ent.icursor("end")
            add_btn.config(text="Save")
            cancel_btn.pack(side="left", padx=(6, 0))

        def remove():
            for i in reversed(self.dict_list.curselection()):
                self._remove_item(self.dict_list.get(i))
            self._refresh_dict()
        self.dict_list.bind("<Double-Button-1>", lambda e: edit())
        self.dict_list.bind("<Delete>", lambda e: remove())
        bf = ttk.Frame(win)
        bf.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bf, text="Edit selected", command=edit).pack(side="left")
        ttk.Button(bf, text="Remove selected", command=remove).pack(side="left", padx=6)
        ttk.Label(bf, textvariable=self.dict_count, foreground=self._theme()["mute"]).pack(side="left", padx=6)
        ttk.Button(bf, text="Close", command=win.destroy).pack(side="right")
        win.geometry("460x440")
        self._themed(win)

    def quick_send(self, learn=False):
        text = self.quick_entry.get().strip()
        if text.lower().startswith("/add "):
            self.add_entries(text[5:])
            self.quick_entry.delete(0, "end")
            return
        if learn and self.spell:
            self.learn_from_text(text)
        if text and self.quick_spell_var.get() and not learn:
            fixed = autocorrect(text, self.spell, self.user_repl)
            if fixed != text:
                self.log("sys", f"Spell check: \"{text}\" -> \"{fixed}\"")
                text = fixed
        if text and self.speak(text, "you"):
            self.quick_entry.delete(0, "end")
            if self.quick_hide_var.get():
                self.hide_quick()

  
    def _add_ignore(self, entry, text_widget):
        word = entry.get().strip()
        if not word:
            try:
                word = text_widget.selection_get().strip()
            except tk.TclError:
                word = ""
        if not word or "\n" in word:
            self.log("sys", "Type or select a single word/phrase to ignore.")
            return
        if word not in self.filters.ignore:
            self.filters.set_ignore(self.filters.ignore + [word])
            self.save_config()
        entry.delete(0, "end")
        self.log("sys", f"Now ignoring: {word}")

    def open_filters(self):
        if self.flt_win is not None and self.flt_win.winfo_exists():
            self.flt_win.lift()
            return
        win = tk.Toplevel(self)
        win.title("Text filters")
        self.flt_win = win
        f = self.filters
        conf_var = tk.StringVar(value=str(int(f.min_conf)))
        sym_var = tk.BooleanVar(value=f.strip_symbols)

        row = ttk.Frame(win)
        row.pack(fill="x", padx=10, pady=(10, 2))
        ttk.Label(row, text="Min OCR confidence (0-95):").pack(side="left")
        ttk.Spinbox(row, from_=0, to=95, increment=5, width=5, textvariable=conf_var).pack(side="left", padx=6)
        ttk.Label(win, justify="left", wraplength=440, foreground=self._theme()["mute"],
                  text="Words the OCR isn't sure about get dropped. Raise it if junk like \"BE\" is "
                       "being read; lower it if real words go missing. Preview OCR shows each word's score."
                  ).pack(anchor="w", padx=10, pady=(0, 8))
        ttk.Checkbutton(win, variable=sym_var,
                        text="Remove emoji and symbols (fancy unicode letters are always converted to normal text)"
                        ).pack(anchor="w", padx=10)

        ttk.Label(win, text="Words / phrases to ignore (one per line):").pack(anchor="w", padx=10, pady=(10, 2))
        box = tk.Text(win, height=8, width=52, font=("Consolas", 10))
        box.insert("1.0", "\n".join(f.ignore))
        box.pack(fill="both", expand=True, padx=10)
        ttk.Label(win, justify="left", wraplength=440, foreground=self._theme()["mute"],
                  text="Plain text = that exact word, case-sensitive (so BE won't remove \"be\").\n"
                       "i:text = ignore case.   re:pattern = regular expression, "
                       "e.g.  re:\\d{1,2}:\\d{2} ?[AP]M  removes timestamps."
                  ).pack(anchor="w", padx=10, pady=(4, 6))

        status = tk.StringVar()

        def save():
            try:
                mc = min(100.0, max(0.0, float(conf_var.get())))
            except ValueError:
                mc = f.min_conf
            f.min_conf = mc
            f.strip_symbols = sym_var.get()
            f.set_ignore([l.strip("\r") for l in box.get("1.0", "end").splitlines() if l.strip()])
            self.save_config()
            status.set("Saved.")

        def close():
            win.destroy()
            self.flt_win = None

        bf = ttk.Frame(win)
        bf.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bf, text="Save", command=save).pack(side="left")
        ttk.Button(bf, text="Close", command=close).pack(side="left", padx=6)
        ttk.Label(bf, textvariable=status, foreground=self._theme()["area"]).pack(side="left", padx=8)
        win.protocol("WM_DELETE_WINDOW", close)
        self._themed(win)

 
    def open_keybinds(self):
        if not self.hk:
            self.log("err", "Keybinds aren't available (pynput missing or failed to start).")
            return
        if self.kb_win is not None and self.kb_win.winfo_exists():
            self.kb_win.lift()
            return
        win = tk.Toplevel(self)
        win.title("Keybinds")
        win.resizable(False, False)
        self.kb_win = win
        ttk.Label(win, justify="left",
                  text=("Click Set, then press a keyboard key or mouse button.\n"
                        "Hold Shift / Ctrl / Alt first to make it part of the bind (or don't).\n"
                        "Left/Right click need a modifier. Esc cancels.")) \
            .grid(row=0, column=0, columnspan=3, padx=10, pady=(10, 6), sticky="w")
        for i, (action, label) in enumerate(ACTIONS, start=1):
            ttk.Label(win, text=label).grid(row=i, column=0, sticky="w", padx=10, pady=3)
            ttk.Label(win, textvariable=self.bind_vars[action], width=24, anchor="center",
                      relief="sunken").grid(row=i, column=1, padx=6, pady=3)
            bf = ttk.Frame(win)
            bf.grid(row=i, column=2, padx=(0, 10), pady=3)
            ttk.Button(bf, text="Set", width=6, command=lambda a=action: self.begin_capture(a)).pack(side="left")
            ttk.Button(bf, text="Clear", width=6, command=lambda a=action: self.clear_bind(a)).pack(side="left", padx=(4, 0))
        ttk.Label(win, textvariable=self.kb_status, foreground=self._theme()["you"]) \
            .grid(row=len(ACTIONS) + 1, column=0, columnspan=3, padx=10, pady=(6, 10), sticky="w")
        win.protocol("WM_DELETE_WINDOW", self._close_keybinds)
        self._themed(win)

    def _close_keybinds(self):
        if self.hk:
            self.hk.cancel_capture()
        self.kb_status.set("")
        self.kb_win.destroy()
        self.kb_win = None

    def begin_capture(self, action):
        label = dict(ACTIONS)[action]
        self.kb_status.set(f"Press a key or mouse button for \"{label}\"...  (Esc cancels)")
        self.hk.begin_capture(action)

    def clear_bind(self, action):
        self.hk.cancel_capture()
        self.binds.pop(action, None)
        self.bind_vars[action].set("(none)")
        self.hk.set_binds(self.binds)
        self.kb_status.set("Cleared.")
        self.save_config()

    def _finish_capture(self, action, bind):
        for other in [a for a, b in self.binds.items() if b == bind and a != action]:
            self.binds.pop(other)  # same bind can't do two things
            self.bind_vars[other].set("(none)")
        self.binds[action] = bind
        self.bind_vars[action].set(pretty_bind(bind))
        self.hk.set_binds(self.binds)
        self.kb_status.set(f"Saved: {pretty_bind(bind)}")
        self.save_config()

    def _poll_hotkeys(self):
        try:
            while True:
                kind, payload = self.hk_queue.get_nowait()
                if kind == "fire":
                    self._fire(payload)
                elif kind == "captured":
                    self._finish_capture(*payload)
                elif kind == "cancel":
                    self.kb_status.set("Cancelled.")
        except queue.Empty:
            pass
        self.after(40, self._poll_hotkeys)

    def _fire(self, action):
        b = self.binds.get(action)
        try:
            typing = self.focus_get() in (self.entry, self.quick_entry)
        except Exception:
            typing = False
        if b and typing and b["key"].startswith("kb:"):
            name = b["key"][3:]
            if len(name) == 1 and not (set(b["mods"]) & {"ctrl", "alt", "win"}):
                return 
        if action == "toggle_watch":
            self.watch_var.set(not self.watch_var.get())
            self.toggle_watch()
        elif action == "watch_on":
            if not self.watch_var.get():
                self.watch_var.set(True)
                self.toggle_watch()
        elif action == "watch_off":
            if self.watch_var.get():
                self.watch_var.set(False)
                self.toggle_watch()
        elif action == "stop_all":
            if self.watch_var.get():
                self.watch_var.set(False)
                self.toggle_watch()
            self.stop_speech()
        elif action == "read_now":
            self.read_now()
        elif action == "toggle_area":
            self.show_var.set(not self.show_var.get())
            self.toggle_overlay()
        elif action == "stop":
            self.stop_speech()
        elif action == "toggle_quick":
            self.toggle_quick()
        elif action == "focus_chat":
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
            self.after(200, lambda: self.attributes("-topmost", False))
            force_focus(self)
            self.entry.focus_set()

  
    def save_config(self):
        ov = self.overlay
        self.cfg.update({
            "device": self.device_var.get(),
            "voice": self.voice_var.get(),
            "rate": int(self.rate_var.get()),
            "monitor": self.monitor_var.get(),
            "theme": self.theme_name,
            "region": [ov.x, ov.y, ov.w, ov.h],
            "binds": self.binds,
            "delay": self._delay(),
            "quick_hide": self.quick_hide_var.get(),
            "skip_own": self.skip_own_var.get(),
            "quick_spell": self.quick_spell_var.get(),
            "dictionary": self.user_words,
            "replacements": self.user_repl,
            "quick_pos": self.quick.geometry(),
            "min_conf": self.filters.min_conf,
            "strip_symbols": self.filters.strip_symbols,
            "ignore": self.filters.ignore,
        })
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2)
        except Exception:
            pass

    def on_close(self):
        self.save_config()
        self.watching = False
        if self.hk:
            self.hk.stop()
        self.destroy()


if __name__ == "__main__":
    App().mainloop()
