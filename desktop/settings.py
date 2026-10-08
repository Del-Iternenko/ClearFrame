"""ClearFrame settings: one schema for defaults, allowed values and validation.

Stored in %APPDATA%/ClearFrame/settings.json. The UI reads the schema to know what a
setting may be, so a value outside it can never be saved.
"""
import json
import os
import threading
from pathlib import Path

PATH = Path(os.environ.get("APPDATA", ".")) / "ClearFrame" / "settings.json"

LANGUAGES = ["en", "zh", "hi", "es", "ar", "fr", "bn", "ru"]

# key -> (default, allowed) ; allowed is a list of choices, a (min, max) range, bool or "list"
SCHEMA = {
    # appearance
    "language": ("auto", ["auto"] + LANGUAGES),
    "theme": ("dark", ["dark", "light", "system"]),
    "accent": ("#76b900", ["#76b900", "#3b82f6", "#a855f7", "#ec4899", "#f97316", "#14b8a6", "#eab308"]),
    "ui_scale": (100, [90, 100, 110, 125]),
    "reduce_motion": (False, bool),
    # system
    "start_with_windows": (False, bool),
    "start_minimized": (False, bool),
    "close_to_tray": (True, bool),
    "notifications": (True, bool),
    # mode / capture / output
    "auto_fullscreen": (True, bool),
    "auto_delay": (1.0, (0.5, 5.0)),
    "auto_apps_mode": ("video", ["video", "all", "only", "except"]),   # video: browsers + players (+ auto_apps)
    "auto_apps": ([], "list"),
    "output": ("overlay", ["overlay", "window"]),
    "window_height": (1440, [1080, 1440, 2160]),
    # upscaling
    "engine": ("clearframe", ["clearframe", "nvvfx", "rtx_driver", "none"]),
    "quality": ("high", ["low", "medium", "high", "ultra"]),
    "artifact_reduction": ("strong", ["off", "light", "strong"]),
    "source": ("auto", ["auto", "360p", "480p", "720p", "1080p"]),
    "deband": (True, bool),
    "deband_strength": (48, (16, 128)),
    "deband_grain": (16, (0, 48)),
    # performance
    "max_fps": (60, [30, 60, 120]),
    "skip_duplicates": (True, bool),
    "pause_on_battery": (False, bool),
    # hotkeys
    "hotkey_window": ("Ctrl+Alt+U", "hotkey"),
    "hotkey_screen": ("Ctrl+Alt+S", "hotkey"),
    "hotkey_auto": ("Ctrl+Alt+A", "hotkey"),
    "hotkey_compare": ("Ctrl+Alt+C", "hotkey"),
}

SOURCE_WIDTH = {"auto": None, "360p": 640, "480p": 854, "720p": 1280, "1080p": 1920}
HOTKEY_MODS = {"Ctrl", "Alt", "Shift", "Win"}


def valid(key, value):
    default, allowed = SCHEMA[key]
    if allowed is bool:
        return isinstance(value, bool)
    if allowed == "list":
        return isinstance(value, list) and all(isinstance(v, str) and 0 < len(v) < 260 for v in value) and len(value) < 200
    if allowed == "hotkey":
        parts = value.split("+") if isinstance(value, str) else []
        return len(parts) >= 2 and set(parts[:-1]) <= HOTKEY_MODS and len(parts[-1]) >= 1
    if isinstance(allowed, tuple):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and allowed[0] <= value <= allowed[1]
    return value in allowed


class Settings:
    def __init__(self, path=PATH):
        self.path = path
        self.lock = threading.Lock()
        self.values = {k: d for k, (d, _) in SCHEMA.items()}
        self.listeners = []
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in stored.items():
                if k in SCHEMA and valid(k, v):
                    self.values[k] = v
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self.values[key]

    def all(self):
        with self.lock:
            return dict(self.values)

    def set(self, key, value):
        if key not in SCHEMA or not valid(key, value):
            raise ValueError(f"invalid setting {key}={value!r}")
        with self.lock:
            changed = self.values.get(key) != value
            self.values[key] = value
            self._save()
        if changed:
            for fn in self.listeners:
                fn(key, value)
        return changed

    def reset(self):
        with self.lock:
            self.values = {k: d for k, (d, _) in SCHEMA.items()}
            self._save()
        for fn in self.listeners:
            fn("*", None)

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.values, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    @staticmethod
    def schema():
        out = {}
        for k, (d, allowed) in SCHEMA.items():
            out[k] = {"default": d, "kind": "bool" if allowed is bool else "range" if isinstance(allowed, tuple)
                      else allowed if isinstance(allowed, str) else "choice",
                      "options": list(allowed) if isinstance(allowed, (list, tuple)) else None}
        return out
