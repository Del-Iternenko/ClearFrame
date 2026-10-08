"""The part of ClearFrame that does the work: starts and stops upscaling sessions,
watches for fullscreen video (auto mode), listens to global hotkeys. The UI and the
tray only call into this and display its state.
"""
import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "live"))
from live_upscale import (fullscreen_window, list_windows, monitor_of_window, monitors,  # noqa: E402
                          user32, window_title)

from settings import SOURCE_WIDTH  # noqa: E402

LIVE_SCRIPT = ROOT / "live" / "live_upscale.py"
# ClearFrame.exe runs sessions as "ClearFrame.exe --session ..."; from source: python live_upscale.py
SESSION_CMD = [sys.executable, "--session"] if getattr(sys, "frozen", False) else [sys.executable, "-u", str(LIVE_SCRIPT)]
LOG = Path(os.environ.get("APPDATA", ".")) / "ClearFrame" / "clearframe.log"
CREATE_NO_WINDOW = 0x08000000
POLL = 0.5
LOST_POLLS = 3

kernel32 = ctypes.windll.kernel32
user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wt.DWORD)]
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_wchar_p, ctypes.POINTER(wt.DWORD)]
kernel32.CloseHandle.argtypes = [ctypes.c_void_p]


def log(msg):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if LOG.exists() and LOG.stat().st_size > 1 << 20:
            LOG.replace(LOG.with_suffix(".old.log"))
        with LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def exe_of(hwnd):
    """File name of the program that owns a window, e.g. 'chrome.exe'."""
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
        return ""
    finally:
        kernel32.CloseHandle(h)


def on_battery():
    class SPS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte), ("BatteryLifePercent", ctypes.c_ubyte),
                    ("SystemStatusFlag", ctypes.c_ubyte), ("BatteryLifeTime", wt.DWORD), ("BatteryFullLifeTime", wt.DWORD)]
    s = SPS()
    return bool(kernel32.GetSystemPowerStatus(ctypes.byref(s))) and s.ACLineStatus == 0


class Session:
    """One upscaling session in its own process (see live_upscale.py main)."""

    def __init__(self, hwnd, monitor, auto, settings, on_status):
        self.hwnd, self.monitor, self.auto = hwnd, monitor, auto
        self.started = time.time()
        self.info = ""
        s = settings
        overlay = True if auto else s["output"] == "overlay"
        engine = "none" if s["engine"] == "none" else "rtx_driver"   # the only engines the live path has today
        args = SESSION_CMD + ["--target", str(s["window_height"]), "--engine", engine,
                "--fps", str(s["max_fps"]), "--deband-strength", str(int(s["deband_strength"])),
                "--deband-grain", str(int(s["deband_grain"]))]
        args += ["--monitor", str(monitor["index"])] if monitor else ["--hwnd", str(hwnd)]
        if overlay:
            args.append("--overlay")
        if not s["deband"]:
            args.append("--no-deband")
        if SOURCE_WIDTH[s["source"]]:
            args += ["--source-width", str(SOURCE_WIDTH[s["source"]])]
        log("run: " + " ".join(args[len(SESSION_CMD):]))
        self.proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace", creationflags=CREATE_NO_WINDOW)
        threading.Thread(target=self._read, args=(on_status,), daemon=True).start()

    def _read(self, on_status):
        for line in self.proc.stdout:
            line = line.rstrip()
            if line.startswith("STATUS: "):
                text = line[8:]
                if "->" in text:
                    self.info = text
                on_status(text)
            elif line:
                log("session: " + line)

    def send(self, cmd):
        try:
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError):
            pass

    def running(self):
        return self.proc.poll() is None

    def stop(self):
        if self.running():
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True,
                           creationflags=CREATE_NO_WINDOW)


VK = {**{chr(c): c for c in range(0x41, 0x5B)}, **{str(d): 0x30 + d for d in range(10)},
      **{f"F{n}": 0x6F + n for n in range(1, 13)}, "Space": 0x20, "Home": 0x24, "End": 0x23, "Insert": 0x2D,
      "Delete": 0x2E, "PageUp": 0x21, "PageDown": 0x22}
MODS = {"Alt": 0x1, "Ctrl": 0x2, "Shift": 0x4, "Win": 0x8}
HOTKEY_ACTIONS = ["hotkey_window", "hotkey_screen", "hotkey_auto", "hotkey_compare"]


def parse_hotkey(text):
    parts = text.split("+")
    mods = 0
    for p in parts[:-1]:
        mods |= MODS.get(p, 0)
    return mods, VK.get(parts[-1])


class Controller:
    def __init__(self, settings, on_change=None):
        self.settings = settings
        self.on_change = on_change or (lambda: None)
        self.session = None
        self.ended = None
        self.lost = 0
        self.comparing = False
        self.status = "idle"
        self.status_text = ""
        self.hotkey_errors = []
        self.lock = threading.Lock()
        self.quitting = threading.Event()
        self.hotkey_thread_id = None
        settings.listeners.append(self._setting_changed)

    # ---------------- state for the UI
    def state(self):
        s = self.session
        return {
            "status": self.status,
            "text": self.status_text,
            "running": bool(s and s.running()),
            "auto": bool(s and s.auto),
            "target": (f"screen {s.monitor['index']}" if s and s.monitor else window_title(s.hwnd)[:80] if s else ""),
            "info": s.info if s else "",
            "since": s.started if s else None,
            "comparing": self.comparing,
            "on_battery": on_battery(),
            "hotkey_errors": self.hotkey_errors,
        }

    def _notify(self, status=None, text=None):
        if status:
            self.status = status
        if text is not None:
            self.status_text = text
        self.on_change()

    # ---------------- sessions
    def start(self, hwnd=None, monitor=None, auto=False):
        if self.settings["pause_on_battery"] and on_battery():
            self._notify("paused_battery", "")
            return
        with self.lock:
            if self.session:
                self.session.stop()
            self.comparing = False
            self.session = Session(hwnd, monitor, auto, self.settings, self._session_status)
        self._notify("auto" if auto else "running", "")

    def stop(self, by_user=False):
        with self.lock:
            if self.session:
                if by_user and self.session.auto:
                    self.ended = self.session.hwnd   # don't restart until it leaves fullscreen
                self.session.stop()
            self.session = None
            self.comparing = False
        self._notify("idle", "")

    def restart(self):
        s = self.session
        if s:
            self.start(hwnd=s.hwnd, monitor=s.monitor, auto=s.auto)

    def _session_status(self, text):
        log("status: " + text)
        self._notify(text=text)

    def start_window(self, hwnd):
        self.start(hwnd=int(hwnd))

    def start_screen(self, index):
        m = next((m for m in monitors() if m["index"] == int(index)), None)
        if m:
            self.start(monitor=m)

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

    def toggle_compare(self):
        s = self.session
        if not s:
            return
        self.comparing = not self.comparing
        s.send("hide" if self.comparing else "show")
        self._notify()

    def toggle_auto(self):
        self.settings.set("auto_fullscreen", not self.settings["auto_fullscreen"])

    def windows(self):
        return [{"hwnd": h, "title": t, "exe": exe_of(h)} for h, t in list_windows()[:30]]

    def screens(self):
        return [{"index": m["index"], "w": m["w"], "h": m["h"], "primary": m["primary"]} for m in monitors()]

    def _setting_changed(self, key, value):
        if key in ("hotkey_window", "hotkey_screen", "hotkey_auto", "hotkey_compare", "*"):
            self.reload_hotkeys()
        if key == "auto_fullscreen" and not value and self.session and self.session.auto:
            self.stop()
        elif key in ("output", "window_height", "engine", "source", "deband", "deband_strength", "deband_grain",
                     "max_fps", "*"):
            self.restart()   # pipeline settings apply to a running session at once
        self.on_change()

    # ---------------- auto mode
    def _app_allowed(self, hwnd):
        mode = self.settings["auto_apps_mode"]
        if mode == "all":
            return True
        apps = {a.lower() for a in self.settings["auto_apps"]}
        exe = exe_of(hwnd).lower()
        return exe in apps if mode == "only" else exe not in apps

    def watch_loop(self):
        candidate, since = None, 0.0
        while not self.quitting.wait(POLL):
            try:
                candidate, since = self._watch_step(candidate, since)
            except Exception:
                log("watcher error:\n" + traceback.format_exc())

    def _watch_step(self, candidate, since):
        session = self.session
        if session is not None and not session.running():
            log("session ended by itself")
            self.stop()
            return None, 0.0
        if self.settings["pause_on_battery"] and session is not None and on_battery():
            self.stop()
            self._notify("paused_battery")
            return None, 0.0
        if session is not None and not session.auto:
            return candidate, since
        hwnd = fullscreen_window() if self.settings["auto_fullscreen"] else None
        if hwnd and not self._app_allowed(hwnd):
            hwnd = None
        if session is not None:
            self.lost = 0 if hwnd == session.hwnd else self.lost + 1
            if self.lost >= LOST_POLLS:
                self.lost = 0
                self.stop()
            return candidate, since
        if hwnd != self.ended:
            self.ended = None
        if hwnd is None or hwnd == self.ended:
            return None, 0.0
        if hwnd != candidate:
            return hwnd, time.time()
        if time.time() - since >= self.settings["auto_delay"]:
            log(f"auto start on {hwnd} ({exe_of(hwnd)})")
            self.start(hwnd=hwnd, auto=True)
            return None, 0.0
        return candidate, since

    # ---------------- hotkeys
    def hotkey_loop(self):
        self.hotkey_thread_id = kernel32.GetCurrentThreadId()
        self._register()
        msg = wt.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == 0x0312:  # WM_HOTKEY
                action = HOTKEY_ACTIONS[msg.wParam - 1] if 1 <= msg.wParam <= len(HOTKEY_ACTIONS) else None
                if action == "hotkey_window":
                    self.toggle_foreground()
                elif action == "hotkey_screen":
                    self.toggle_foreground(screen=True)
                elif action == "hotkey_auto":
                    self.toggle_auto()
                elif action == "hotkey_compare":
                    self.toggle_compare()
            elif msg.message == 0x0400 + 1:   # our "reload hotkeys" message
                self._register()
        for i in range(len(HOTKEY_ACTIONS)):
            user32.UnregisterHotKey(None, i + 1)

    def _register(self):
        """Runs on the hotkey thread (hotkeys belong to the thread that registers them)."""
        errors = []
        for i, action in enumerate(HOTKEY_ACTIONS):
            user32.UnregisterHotKey(None, i + 1)
            mods, vk = parse_hotkey(self.settings[action])
            if vk is None or not user32.RegisterHotKey(None, i + 1, mods | 0x4000, vk):  # MOD_NOREPEAT
                errors.append(action)
        self.hotkey_errors = errors
        if errors:
            log("hotkeys not registered: " + ", ".join(errors))

    def reload_hotkeys(self):
        if self.hotkey_thread_id:
            user32.PostThreadMessageW(self.hotkey_thread_id, 0x0400 + 1, 0, 0)

    # ---------------- lifecycle
    def run_background(self):
        threading.Thread(target=self.watch_loop, daemon=True).start()
        threading.Thread(target=self.hotkey_loop, daemon=True).start()

    def shutdown(self):
        self.quitting.set()
        self.stop()
        if self.hotkey_thread_id:
            user32.PostThreadMessageW(self.hotkey_thread_id, 0x0012, 0, 0)  # WM_QUIT
