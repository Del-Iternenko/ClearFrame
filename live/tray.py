"""ClearFrame tray app: upscale any video that is already playing.

Auto (on by default): a video that goes fullscreen - browser, any player - is upscaled
over the original by itself, and leaving fullscreen turns it off again.

Manual, like screen sharing in Discord: pick a whole screen or one window from the tray
menu. A window: ClearFrame finds the video area in it. A screen: everything visible on
it is upscaled. The result is shown through RTX Video Super Resolution over the original
or in a separate window for OBS.

Hotkeys: Ctrl+Alt+U - the active window, Ctrl+Alt+S - the whole screen it is on.
The same hotkey again (or "Stop") turns it off.

    pythonw live/tray.py     (or double-click ClearFrame-Live.bat)
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_upscale import (fullscreen_window, list_windows, monitor_of_window, monitors,  # noqa: E402
                          user32, window_title)

LIVE_SCRIPT = Path(__file__).resolve().parent / "live_upscale.py"
CREATE_NO_WINDOW = 0x08000000

SETTINGS = Path(os.environ.get("APPDATA", ".")) / "ClearFrame" / "live.json"
LOG = SETTINGS.with_name("live.log")
DEFAULTS = {"auto_fullscreen": True, "output": "overlay", "target": 1440, "source": "auto"}
SOURCES = {"auto": ("Auto (experimental)", None), "360p": ("360p", 640), "480p": ("480p", 854),
           "720p": ("720p", 1280), "1080p": ("1080p", 1920)}
MOD_CTRL_ALT = 0x0002 | 0x0001
HOTKEYS = {1: 0x55, 2: 0x53}   # id -> virtual key: U = active window, S = its whole screen
POLL = 0.5                     # seconds between fullscreen checks
SETTLE_POLLS = 2               # a window must stay fullscreen this long before auto starts
LOST_POLLS = 3                 # ...and be gone this long before auto stops (focus blips, notifications)


def log(msg):
    """Diagnostics for 'why didn't it start': %APPDATA%/ClearFrame/live.log (kept short)."""
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > 512 * 1024:
            LOG.replace(LOG.with_suffix(".old.log"))
        with LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def icon_image(active):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(118, 185, 0) if active else (70, 75, 90))
    d.rectangle((14, 20, 50, 44), outline=(0, 0, 0) if active else (220, 224, 235), width=5)
    d.polygon([(28, 26), (28, 38), (39, 32)], fill=(0, 0, 0) if active else (220, 224, 235))
    return img


class SessionProcess:
    """One upscaling session in its own process (live_upscale.py). Window capture can hang
    inside Windows when the captured window goes away; in a child process that can never
    freeze the tray, and stopping is simply ending the process tree (with its mpv)."""

    def __init__(self, hwnd, monitor, overlay, target, source_width, on_status):
        self.hwnd, self.monitor = hwnd, monitor
        args = [sys.executable, "-u", str(LIVE_SCRIPT), "--target", str(target)]
        args += ["--monitor", str(monitor["index"])] if monitor else ["--hwnd", str(hwnd)]
        if overlay:
            args.append("--overlay")
        if source_width:
            args += ["--source-width", str(source_width)]
        log("run: " + " ".join(args[2:]))
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=self._read, args=(on_status,), daemon=True).start()

    def _read(self, on_status):
        for line in self.proc.stdout:
            line = line.rstrip()
            if line.startswith("STATUS: "):
                on_status(line[8:])
            elif line:
                log("session: " + line)

    def running(self):
        return self.proc.poll() is None

    def stop(self):
        if self.running():
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)


class TrayApp:
    def __init__(self):
        self.settings = {**DEFAULTS, **self.load()}
        self.session = None
        self.auto = False          # the running session was started by the fullscreen watcher
        self.ended = None          # fullscreen window whose auto session the user stopped
        self.lost = 0              # polls in a row the auto session's window wasn't fullscreen
        self.current = {}
        self.lock = threading.Lock()
        self.quitting = threading.Event()
        self.status = self.idle_text()
        self.icon = pystray.Icon("ClearFrame", icon_image(False), "ClearFrame - " + self.status, self.menu())

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
                self.start(**self.current)
        return apply

    def is_set(self, key, value):
        return lambda item: self.settings[key] == value

    def toggle_auto(self, icon=None, item=None):
        self.settings["auto_fullscreen"] = not self.settings["auto_fullscreen"]
        self.save()
        if not self.settings["auto_fullscreen"] and self.auto:
            self.stop()
        self.on_status(self.status if self.session else self.idle_text())

    def idle_text(self):
        return "Waiting for a fullscreen video" if self.settings["auto_fullscreen"] else "Idle"

    # ---------------- menu
    def window_items(self):
        items = []
        for hwnd, title in list_windows()[:15]:
            label = title if len(title) <= 60 else title[:57] + "..."
            items.append(pystray.MenuItem(label, (lambda h: lambda icon, item: self.start(hwnd=h))(hwnd)))
        return items or [pystray.MenuItem("(no windows)", None, enabled=False)]

    def screen_items(self):
        items = []
        for m in monitors():
            label = f"Screen {m['index']}  {m['w']}x{m['h']}" + ("  (main)" if m["primary"] else "")
            items.append(pystray.MenuItem(label, (lambda mon: lambda icon, item: self.start(monitor=mon))(m)))
        return items

    def menu(self):
        M, I = pystray.Menu, pystray.MenuItem
        return M(
            I(lambda item: self.status, None, enabled=False),
            M.SEPARATOR,
            I("Auto: upscale fullscreen video", self.toggle_auto, checked=lambda item: self.settings["auto_fullscreen"]),
            M.SEPARATOR,
            I("Entire screen", M(lambda: self.screen_items())),
            I("Window", M(lambda: self.window_items())),
            I("Active window   Ctrl+Alt+U", lambda icon, item: self.toggle_foreground()),
            I("Its whole screen   Ctrl+Alt+S", lambda icon, item: self.toggle_foreground(screen=True)),
            I("Stop", lambda icon, item: self.stop(by_user=True), enabled=lambda item: self.session is not None),
            M.SEPARATOR,
            I("Show (screen / window)", M(
                I("Over the original", self.setter("output", "overlay"), checked=self.is_set("output", "overlay"), radio=True),
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
    def start(self, hwnd=None, monitor=None, auto=False):
        with self.lock:
            if self.session:
                self.session.stop()
            s = self.settings
            overlay = True if auto else s["output"] == "overlay"   # fullscreen auto mode always lies over the video
            self.current = dict(hwnd=hwnd, monitor=monitor, auto=auto)
            self.auto = auto
            self.session = SessionProcess(hwnd, monitor, overlay, s["target"], SOURCES[s["source"]][1], self.on_status)
        what = f"screen {monitor['index']}" if monitor else window_title(hwnd)[:60]
        self.on_status(("Auto (fullscreen): " if auto else "Upscaling: ") + what)
        self.icon.icon = icon_image(True)

    def stop(self, by_user=False):
        with self.lock:
            if by_user and self.auto and self.session:
                self.ended = self.session.hwnd   # don't restart until it leaves fullscreen
            if self.session:
                self.session.stop()
            self.session, self.auto = None, False
        self.on_status(self.idle_text())
        self.icon.icon = icon_image(False)

    def toggle_foreground(self, screen=False):
        if self.session:
            self.stop(by_user=True)
            return
        hwnd = user32.GetForegroundWindow()
        if not hwnd or window_title(hwnd).startswith("ClearFrame"):
            return
        if screen:
            self.start(monitor=monitor_of_window(hwnd))
        else:
            self.start(hwnd=hwnd)

    def on_status(self, text):
        log("status: " + text)
        self.status = text if len(text) <= 100 else text[:97] + "..."
        self.icon.title = ("ClearFrame - " + self.status)[:127]
        self.icon.update_menu()

    # ---------------- fullscreen watcher
    def watch_loop(self):
        """Auto mode: start over a window that goes fullscreen, stop when it leaves
        fullscreen. Sessions the user started by hand are left alone."""
        candidate, seen, last = None, 0, object()
        while not self.quitting.wait(POLL):
            try:
                candidate, seen, last = self.watch_step(candidate, seen, last)
            except Exception:
                log("watcher error:\n" + traceback.format_exc())   # keep watching

    def watch_step(self, candidate, seen, last):
        """One poll of the watcher; returns the updated (candidate, seen, last)."""
        session = self.session
        if session is not None and not session.running():
            log("session ended by itself")
            self.stop()   # capture or output ended (window closed...)
            return candidate, seen, last
        if session is not None and not self.auto:
            return candidate, seen, last      # manual session: not ours to manage
        hwnd = fullscreen_window() if self.settings["auto_fullscreen"] else None
        if hwnd != last:
            log(f"fullscreen window: {hwnd} {window_title(hwnd)[:50]!r}" if hwnd else "fullscreen window: none")
            last = hwnd
        if session is not None:
            self.lost = 0 if hwnd == session.hwnd else self.lost + 1
            if self.lost >= LOST_POLLS:
                self.lost = 0
                self.stop()
            return candidate, seen, last
        if hwnd != self.ended:
            self.ended = None
        if hwnd is None or hwnd == self.ended:
            return None, 0, last
        seen = seen + 1 if hwnd == candidate else 1
        candidate = hwnd
        if seen >= SETTLE_POLLS:   # skip the moment of switching to fullscreen
            log(f"auto start on {hwnd}")
            self.start(hwnd=hwnd, auto=True)
            return None, 0, last
        return candidate, seen, last

    # ---------------- global hotkey
    def hotkey_loop(self):
        for hid, vk in HOTKEYS.items():
            if not user32.RegisterHotKey(None, hid, MOD_CTRL_ALT | 0x4000, vk):  # MOD_NOREPEAT
                self.on_status(f"Ctrl+Alt+{chr(vk)} is taken by another program")
        self.hotkey_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312 and msg.wParam in HOTKEYS:  # WM_HOTKEY
                self.toggle_foreground(screen=msg.wParam == 2)
        for hid in HOTKEYS:
            user32.UnregisterHotKey(None, hid)

    def quit(self):
        self.quitting.set()
        self.stop()
        if getattr(self, "hotkey_thread", None):
            user32.PostThreadMessageW(self.hotkey_thread, 0x0012, 0, 0)  # WM_QUIT
        self.icon.stop()

    def run(self):
        threading.Thread(target=self.hotkey_loop, daemon=True).start()
        threading.Thread(target=self.watch_loop, daemon=True).start()
        self.icon.run(setup=lambda icon: (setattr(icon, "visible", True), icon.notify(
            "Fullscreen videos are upscaled automatically. Right-click the icon to pick a screen or a window; "
            "Ctrl+Alt+U = active window, Ctrl+Alt+S = whole screen.", "ClearFrame is running")))


def already_running():
    """One ClearFrame tray per user session: a second copy would fight over windows."""
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    already_running.mutex = kernel32.CreateMutexW(None, False, r"Local\ClearFrameLiveTray")  # held until exit
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


if __name__ == "__main__":
    if already_running():
        log("another ClearFrame tray is already running - exiting")
        sys.exit(0)
    TrayApp().run()
