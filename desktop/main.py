"""ClearFrame desktop app: settings window (HTML UI in WebView2 via pywebview) + tray.

    .venv/Scripts/pythonw desktop/main.py [--minimized]
"""
import ctypes
import json
import locale
import sys
import threading
import webbrowser
import winreg
from pathlib import Path

import pystray
import webview
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from controller import LOG, Controller, log  # noqa: E402
from settings import LANGUAGES, Settings  # noqa: E402

APP_NAME = "ClearFrame"
VERSION = "0.3.0"
REPO = "https://github.com/Del-Iternenko/ClearFrame"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
# tray notifications in the UI language (on, off)
NOTIFY = {"en": ("Upscaling on", "Upscaling off"), "ru": ("Улучшение включено", "Улучшение выключено"),
          "zh": ("增强已开启", "增强已关闭"), "hi": ("अपस्केलिंग चालू", "अपस्केलिंग बंद"), "es": ("Escalado activado", "Escalado desactivado"),
          "ar": ("التحسين قيد التشغيل", "التحسين متوقف"), "fr": ("Amélioration activée", "Amélioration désactivée"),
          "bn": ("আপস্কেলিং চালু", "আপস্কেলিং বন্ধ")}
UI = HERE / "ui" / "index.html"


def system_language():
    """Windows UI language -> one of ours (English if we don't have it)."""
    try:
        lcid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        code = (locale.windows_locale.get(lcid) or "en").split("_")[0]
    except Exception:
        code = "en"
    return code if code in LANGUAGES else "en"


def tray_image(active, accent):
    rgb = tuple(int(accent[i:i + 2], 16) for i in (1, 3, 5))
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=16, fill=rgb if active else (52, 58, 72))
    ink = (10, 12, 16) if active else (225, 229, 238)
    d.rounded_rectangle((14, 19, 50, 45), radius=5, outline=ink, width=5)
    d.polygon([(28, 25), (28, 39), (39, 32)], fill=ink)
    return img


class Api:
    """Everything the UI may call (window.pywebview.api.*). Returns plain JSON-able data."""

    def __init__(self, app):
        self._app = app

    # data
    def bootstrap(self):
        s = self._app.settings
        return {"settings": s.all(), "schema": s.schema(), "state": self._app.controller.state(),
                "system_language": system_language(), "version": VERSION, "repo": REPO,
                "languages": LANGUAGES, "log_path": str(LOG)}

    def state(self):
        return self._app.controller.state()

    def windows(self):
        return self._app.controller.windows()

    def screens(self):
        return self._app.controller.screens()

    # actions
    def set_setting(self, key, value):
        try:
            self._app.settings.set(key, value)
            if key == "start_with_windows":
                self._app.set_autostart(value)
            if key in ("accent",):
                self._app.update_tray()
            return {"ok": True}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def reset_settings(self):
        self._app.settings.reset()
        return self._app.settings.all()

    def start_window(self, hwnd):
        self._app.controller.start_window(hwnd)

    def start_screen(self, index):
        self._app.controller.start_screen(index)

    def stop(self):
        self._app.controller.stop(by_user=True)

    def toggle_compare(self):
        self._app.controller.toggle_compare()

    def open_log(self):
        LOG.parent.mkdir(parents=True, exist_ok=True)
        if not LOG.exists():
            LOG.write_text("", encoding="utf-8")
        ctypes.windll.shell32.ShellExecuteW(None, "open", str(LOG.parent), None, None, 1)

    def open_repo(self):
        webbrowser.open(REPO)

    # window chrome (frameless window)
    def minimize(self):
        self._app.window.minimize()

    def toggle_maximize(self):
        w = self._app.window
        if getattr(self._app, "maximized", False):
            w.restore()
        else:
            w.maximize()
        self._app.maximized = not getattr(self._app, "maximized", False)

    def close(self):
        self._app.on_close_button()


class App:
    def __init__(self, minimized):
        self.settings = Settings()
        self.controller = Controller(self.settings, on_change=self.push_state)
        self.start_hidden = minimized or self.settings["start_minimized"]
        self.window = None
        self.tray = None
        self.quitting = False
        self.ui_ready = False
        self.was_running = False

    # -------- UI push
    def push_state(self):
        running = self.controller.session is not None
        if running != self.was_running:
            self.was_running = running
            if self.settings["notifications"] and self.tray:
                lang = self.settings["language"]
                lang = system_language() if lang == "auto" else lang
                on, off = NOTIFY.get(lang, NOTIFY["en"])
                try:
                    self.tray.notify(self.controller.state()["target"] or " ", on if running else off)
                except Exception:
                    pass
        if self.window is not None and self.ui_ready:
            try:
                payload = json.dumps({"state": self.controller.state(), "settings": self.settings.all()})
                self.window.evaluate_js(f"window.cfPush && window.cfPush({payload})")
            except Exception:
                pass
        self.update_tray()

    # -------- window
    def show(self):
        if self.window:
            self.window.show()
            self.window.restore()

    def on_close_button(self):
        if self.settings["close_to_tray"]:
            self.window.hide()
        else:
            self.quit()

    def on_closing(self):
        if self.quitting:
            return True
        if self.settings["close_to_tray"]:
            self.window.hide()
            return False   # keep running in the tray
        self.quit()
        return True

    # -------- tray
    def tray_menu(self):
        M, I = pystray.Menu, pystray.MenuItem
        c = self.controller
        return M(
            I("Open ClearFrame", lambda: self.show(), default=True),
            M.SEPARATOR,
            I("Auto: fullscreen video", lambda: c.toggle_auto(), checked=lambda i: self.settings["auto_fullscreen"]),
            I("Upscale active window", lambda: c.toggle_foreground()),
            I("Upscale its whole screen", lambda: c.toggle_foreground(screen=True)),
            I("Compare with original", lambda: c.toggle_compare(), enabled=lambda i: c.session is not None),
            I("Stop", lambda: c.stop(by_user=True), enabled=lambda i: c.session is not None),
            M.SEPARATOR,
            I("Quit", lambda: self.quit()),
        )

    def update_tray(self):
        if self.tray:
            active = self.controller.session is not None
            self.tray.icon = tray_image(active, self.settings["accent"])
            self.tray.title = "ClearFrame - " + (self.controller.status_text or self.controller.status)[:100]
            try:
                self.tray.update_menu()
            except Exception:
                pass

    # -------- autostart
    def set_autostart(self, enabled):
        if getattr(sys, "frozen", False):
            cmd = f'"{sys.executable}" --minimized'
        else:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            cmd = f'"{pythonw}" "{Path(__file__).resolve()}" --minimized'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except FileNotFoundError:
                    pass

    # -------- lifecycle
    def quit(self):
        self.quitting = True
        self.controller.shutdown()
        if self.tray:
            self.tray.stop()
        if self.window:
            self.window.destroy()

    def _on_loaded(self):
        self.ui_ready = True
        self.push_state()

    def run(self):
        self.window = webview.create_window(
            APP_NAME, url=str(UI), js_api=Api(self), width=1120, height=760, min_size=(880, 600),
            frameless=True, easy_drag=False, background_color="#0b0e13", hidden=self.start_hidden)
        self.window.events.closing += self.on_closing
        self.window.events.loaded += self._on_loaded
        self.tray = pystray.Icon(APP_NAME, tray_image(False, self.settings["accent"]), APP_NAME, self.tray_menu())
        self.tray.run_detached()
        self.controller.run_background()
        log(f"ClearFrame {VERSION} started")
        webview.start(gui="edgechromium", private_mode=False,
                      storage_path=str(LOG.parent / "webview"), debug="--debug" in sys.argv)


def already_running():
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    already_running.mutex = k32.CreateMutexW(None, False, r"Local\ClearFrameApp")
    if k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS: show the running one instead
        hwnd = ctypes.windll.user32.FindWindowW(None, APP_NAME)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        return True
    return False


if __name__ == "__main__":
    if sys.argv[1:2] == ["--session"]:   # ClearFrame.exe starting one upscaling session (see controller.Session)
        from live_upscale import main as session_main
        session_main(sys.argv[2:])
    elif not already_running():
        App("--minimized" in sys.argv).run()
