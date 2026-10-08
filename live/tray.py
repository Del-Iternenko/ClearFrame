"""ClearFrame tray app: upscale any video that is already playing.

Right-click the tray icon, pick the window with the video (or press Ctrl+Alt+U on it),
and ClearFrame captures it, finds the video area and shows it through RTX Video Super
Resolution - either over the original video (also fullscreen) or in a separate window
for OBS. Ctrl+Alt+U again (or "Stop") turns it off.

    pythonw live/tray.py     (or double-click ClearFrame-Live.bat)
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import threading
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_upscale import LiveSession, list_windows, user32, window_title  # noqa: E402

SETTINGS = Path(os.environ.get("APPDATA", ".")) / "ClearFrame" / "live.json"
DEFAULTS = {"output": "overlay", "target": 1440, "source": "auto"}
SOURCES = {"auto": ("Auto (experimental)", None), "360p": ("360p", 640), "480p": ("480p", 854),
           "720p": ("720p", 1280), "1080p": ("1080p", 1920)}
HOTKEY_ID, MOD_CTRL_ALT, VK_U = 1, 0x0002 | 0x0001, 0x55


def icon_image(active):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(118, 185, 0) if active else (70, 75, 90))
    d.rectangle((14, 20, 50, 44), outline=(0, 0, 0) if active else (220, 224, 235), width=5)
    d.polygon([(28, 26), (28, 38), (39, 32)], fill=(0, 0, 0) if active else (220, 224, 235))
    return img


class TrayApp:
    def __init__(self):
        self.settings = {**DEFAULTS, **self.load()}
        self.session = None
        self.status = "Idle"
        self.lock = threading.Lock()
        self.icon = pystray.Icon("ClearFrame", icon_image(False), "ClearFrame - idle", self.menu())

    # ---------------- settings
    def load(self):
        try:
            return json.loads(SETTINGS.read_text())
        except (OSError, ValueError):
            return {}

    def save(self):
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps(self.settings, indent=2))
        except OSError:
            pass

    def setter(self, key, value):
        def apply(icon, item):
            self.settings[key] = value
            self.save()
            if self.session:   # restart with the new setting
                self.start(self.session.hwnd)
        return apply

    def is_set(self, key, value):
        return lambda item: self.settings[key] == value

    # ---------------- menu
    def window_items(self):
        items = []
        for hwnd, title in list_windows()[:15]:
            label = title if len(title) <= 60 else title[:57] + "..."
            items.append(pystray.MenuItem(label, (lambda h: lambda icon, item: self.start(h))(hwnd)))
        return items or [pystray.MenuItem("(no windows)", None, enabled=False)]

    def menu(self):
        M, I = pystray.Menu, pystray.MenuItem
        return M(
            I(lambda item: self.status, None, enabled=False),
            M.SEPARATOR,
            I("Upscale window", M(lambda: self.window_items())),
            I("Upscale active window   Ctrl+Alt+U", lambda icon, item: self.toggle_foreground()),
            I("Stop", lambda icon, item: self.stop(), enabled=lambda item: self.session is not None),
            M.SEPARATOR,
            I("Show", M(
                I("Over the original video (also fullscreen)", self.setter("output", "overlay"), checked=self.is_set("output", "overlay"), radio=True),
                I("In a separate window (for OBS)", self.setter("output", "window"), checked=self.is_set("output", "window"), radio=True),
            )),
            I("Source quality", M(*[
                I(label, self.setter("source", key), checked=self.is_set("source", key), radio=True)
                for key, (label, _) in SOURCES.items()])),
            I("Output height (separate window)", M(*[
                I(f"{t}p", self.setter("target", t), checked=self.is_set("target", t), radio=True) for t in (1080, 1440, 2160)])),
            M.SEPARATOR,
            I("Quit", lambda icon, item: self.quit()),
        )

    # ---------------- sessions
    def start(self, hwnd):
        with self.lock:
            if self.session:
                self.session.close()
            s = self.settings
            self.session = LiveSession(hwnd, target=s["target"], overlay=s["output"] == "overlay",
                                       source_width=SOURCES[s["source"]][1], on_status=self.on_status)
            self.session.start()
        self.on_status(f"Upscaling: {window_title(hwnd)[:60]}")
        self.icon.icon = icon_image(True)

    def stop(self):
        with self.lock:
            if self.session:
                self.session.close()
            self.session = None
        self.on_status("Idle")
        self.icon.icon = icon_image(False)

    def toggle_foreground(self):
        if self.session:
            self.stop()
            return
        hwnd = user32.GetForegroundWindow()
        if hwnd and not window_title(hwnd).startswith("ClearFrame"):
            self.start(hwnd)

    def on_status(self, text):
        self.status = text if len(text) <= 100 else text[:97] + "..."
        self.icon.title = ("ClearFrame - " + self.status)[:127]
        self.icon.update_menu()
        if self.session and not self.session.running():
            # capture ended (window closed, output closed)
            self.session = None
            self.icon.icon = icon_image(False)

    # ---------------- global hotkey
    def hotkey_loop(self):
        if not user32.RegisterHotKey(None, HOTKEY_ID, MOD_CTRL_ALT | 0x4000, VK_U):  # MOD_NOREPEAT
            self.on_status("Ctrl+Alt+U is taken by another program")
            return
        self.hotkey_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and msg.wParam == HOTKEY_ID:  # WM_HOTKEY
                self.toggle_foreground()
        user32.UnregisterHotKey(None, HOTKEY_ID)

    def quit(self):
        self.stop()
        if getattr(self, "hotkey_thread", None):
            user32.PostThreadMessageW(self.hotkey_thread, 0x0012, 0, 0)  # WM_QUIT
        self.icon.stop()

    def run(self):
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        self.icon.run(setup=lambda icon: (setattr(icon, "visible", True),
                                          icon.notify("Right-click the icon or press Ctrl+Alt+U on a video window.", "ClearFrame is running")))


if __name__ == "__main__":
    TrayApp().run()
