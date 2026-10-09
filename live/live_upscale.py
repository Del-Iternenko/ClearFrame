"""Live upscaling of a video playing in any window.

    window (browser, player...) --WGC capture--> find the moving video area --> crop
      --> mpv: downscale to ~source size, RTX VSR (+deband) --> output window

Output: a normal "ClearFrame Output" window (add it to OBS as Window Capture), or an
overlay lying exactly over the original video (click-through). Audio stays with the
original application.

    .venv/Scripts/python live/live_upscale.py "<part of window title>" [--overlay] [--target 1080]
"""
import argparse
import ctypes
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
from windows_capture import Frame, InternalCaptureControl, WindowsCapture

import native_res
from host_window import HostWindow

ROOT = Path(__file__).resolve().parent.parent
# packaged app (ClearFrame.exe): mpv ships next to the exe; from source: vendor/ (bench.py setup)
MPV = (Path(sys.executable).parent / "mpv" / "mpv.exe" if getattr(sys, "frozen", False)
       else ROOT / "vendor" / "mpv" / "mpv.exe")
user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # physical pixels, like the capture
user32.GetForegroundWindow.restype = ctypes.c_void_p    # handles are pointer-sized
user32.MonitorFromWindow.restype = ctypes.c_void_p


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def client_area(hwnd):
    """Client rectangle relative to what WGC captures (the window's visible frame bounds):
    drops the title bar and borders, whose repaints on focus changes look like motion."""
    frame, client, origin = RECT(), RECT(), (ctypes.c_long * 2)(0, 0)
    dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), 9, ctypes.byref(frame), ctypes.sizeof(frame))  # EXTENDED_FRAME_BOUNDS
    user32.GetClientRect(ctypes.c_void_p(hwnd), ctypes.byref(client))
    user32.ClientToScreen(ctypes.c_void_p(hwnd), origin)
    x, y = origin[0] - frame.left, origin[1] - frame.top
    return max(0, x), max(0, y), client.right, client.bottom


def window_title(hwnd):
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(ctypes.c_void_p(hwnd), buf, 512)
    return buf.value


def list_windows():
    """Visible top-level windows worth capturing: (hwnd, title), largest first."""
    found = []
    rect = RECT()

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER
            return True
        if user32.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW
            return True
        cloaked = ctypes.c_int(0)  # DWMWA_CLOAKED: suspended UWP/background shells (TextInputHost etc.)
        ctypes.windll.dwmapi.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), 14, ctypes.byref(cloaked), 4)
        if cloaked.value:
            return True
        title = window_title(hwnd)
        if not title or title.startswith("ClearFrame"):
            return True
        user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area >= 320 * 200:
            found.append((area, hwnd, title))
        return True

    user32.EnumWindows(cb, 0)
    return [(hwnd, title) for area, hwnd, title in sorted(found, reverse=True)]


def monitors():
    """Displays in the order Windows Graphics Capture numbers them (1-based)."""
    found = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", ctypes.c_ulong)]

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(RECT), ctypes.c_void_p)
    def cb(hmon, hdc, rect, _):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        user32.GetMonitorInfoW(ctypes.c_void_p(hmon), ctypes.byref(info))
        r = info.rcMonitor
        found.append(dict(index=len(found) + 1, handle=hmon, x=r.left, y=r.top, w=r.right - r.left, h=r.bottom - r.top,
                          primary=bool(info.dwFlags & 1)))
        return True

    user32.EnumDisplayMonitors(None, None, cb, 0)
    return found


SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}  # desktop, taskbar


def window_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(ctypes.c_void_p(hwnd), buf, 256)
    return buf.value


def fullscreen_window():
    """The foreground window if it covers its whole monitor (a fullscreen video), else None."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd or not user32.IsWindowVisible(ctypes.c_void_p(hwnd)) or user32.IsIconic(ctypes.c_void_p(hwnd)):
        return None
    if window_class(hwnd) in SHELL_CLASSES or window_title(hwnd).startswith("ClearFrame"):
        return None
    m = monitor_of_window(hwnd)
    rect = RECT()
    user32.GetWindowRect(ctypes.c_void_p(hwnd), ctypes.byref(rect))
    if m and rect.left <= m["x"] and rect.top <= m["y"] and rect.right >= m["x"] + m["w"] and rect.bottom >= m["y"] + m["h"]:
        return hwnd
    return None


def monitor_of_window(hwnd):
    hmon = user32.MonitorFromWindow(ctypes.c_void_p(hwnd), 2)  # MONITOR_DEFAULTTONEAREST
    return next((m for m in monitors() if m["handle"] == hmon), None)


def find_window(part):
    return [(h, t) for h, t in list_windows() if part.lower() in t.lower()]


class VideoArea:
    """Bounding box of the pixels that change over time = where the video is.
    Static UI (page, buttons, title bar) and black bars stay outside. The box only
    grows (quiet scenes would otherwise shrink it); a new window size starts over."""

    def __init__(self, settle_frames=24, step=4):
        self.prev = None
        self.count = None     # per pixel: in how many frames it changed
        self.total = 0
        self.frames = 0
        self.settle = settle_frames
        self.step = step
        self.rect = None  # x, y, w, h in captured pixels

    def update(self, bgra):
        s = self.step
        small = bgra[::s, ::s, 1].astype(np.int16)  # green channel at 1/4 size is plenty
        if self.prev is None or self.prev.shape != small.shape:
            self.prev, self.count, self.total, self.frames, self.rect = small, np.zeros(small.shape, np.int32), 0, 0, None
            return None
        self.count += np.abs(small - self.prev) > 6
        self.prev = small
        self.total += 1
        self.frames += 1
        # first decision after ~2 s of watching, so the output doesn't open and then reopen bigger
        if self.frames >= self.settle and (self.rect is not None or self.total >= 2 * self.settle):
            # video pixels change again and again; a one-off repaint (hover, focus) doesn't count
            # (controls that pop up for a few seconds don't either: at least 1 frame in 8)
            box = self._box(self.count >= max(4, self.total // 8), bgra.shape)
            box = box and self._to_edges(box, bgra)
            if box and (self.rect is None or self._grows(box)):
                self.rect = box
            self.frames = 0
        return self.rect

    def _grows(self, box):
        # restart the output only for a clearly bigger area, not for a few pixels
        x, y, w, h = self.rect
        return box[2] * box[3] > w * h * 1.05

    @staticmethod
    def _to_edges(box, bgra, black=24):
        """Motion misses dark, still parts of the picture: grow the box outwards until a
        black bar (every pixel near black) or the window edge."""
        x, y, w, h = box
        H, W = bgra.shape[:2]
        x0, y0, x1, y1 = x, y, x + w, y + h
        lum = lambda a: a[..., :3].max()
        while y0 > 0 and lum(bgra[y0 - 1, x0:x1]) > black:
            y0 -= 1
        while y1 < H and lum(bgra[y1, x0:x1]) > black:
            y1 += 1
        while x0 > 0 and lum(bgra[y0:y1, x0 - 1]) > black:
            x0 -= 1
        while x1 < W and lum(bgra[y0:y1, x1]) > black:
            x1 += 1
        return x0, y0, x1 - x0, y1 - y0   # exact: the native-size detector needs every row

    def _box(self, mask, shape):
        s = self.step
        rows, cols = np.flatnonzero(mask.sum(1) > 2), np.flatnonzero(mask.sum(0) > 2)
        if len(rows) < 8 or len(cols) < 8:
            return None
        h, w = shape[:2]
        y0, y1 = rows[0] * s, min(h, (rows[-1] + 1) * s)
        x0, x1 = cols[0] * s, min(w, (cols[-1] + 1) * s)
        return x0, y0, (x1 - x0) // 4 * 4, (y1 - y0) // 4 * 4


class Output:
    """mpv reading raw BGRA frames from stdin and showing them through RTX VSR, drawn
    into a window that ClearFrame owns (see host_window):
    place=(x, y, w, h): borderless click-through overlay exactly over the original;
    geometry=(x, y, w, h): borderless window filling that area (e.g. another monitor);
    neither: a normal window sized for the target height (capture it in OBS).
    exclude: hide the window from screen capture (when it lies on a captured screen)."""

    def __init__(self, w, h, target, max_input, fps, place=None, native_h=None, exclude=False, geometry=None,
                 tuning=None):
        """tuning: engine ('neural', 'nvvfx', 'rtx_driver' or 'none'), quality, artifact_reduction (nvvfx),
        deband (bool), deband_strength, deband_grain."""
        t = {"engine": "rtx_driver", "deband": True, "deband_strength": 48, "deband_grain": 16, **(tuning or {})}
        self.w, self.h = w, h
        self.place, self.exclude, self.label = place, exclude, None
        # players and browsers already upscaled the video to the window: bring it back
        # to its real size (detected, see native_res) so the network sees real pixels;
        # until that is known, just cap the height (never upscale here)
        k = native_h / h if native_h else min(1.0, max_input / h)
        in_h, in_w = int(h * k) // 2 * 2, int(w * k) // 2 * 2
        if place:
            target = place[3]
        elif geometry:
            target = geometry[3]
        scale = max(1.0, min(4.0, target / in_h))
        out_w, out_h = int(in_w * scale), int(in_h * scale)
        if place:
            self.host = HostWindow("ClearFrame Output", *place, overlay=True, exclude_from_capture=exclude)
        elif geometry:
            self.host = HostWindow("ClearFrame Output", *geometry, popup=True, exclude_from_capture=exclude)
        else:
            # client area at the output size, shrunk to fit the screen if needed
            fit = min(1.0, 0.9 * user32.GetSystemMetrics(0) / out_w, 0.9 * user32.GetSystemMetrics(1) / out_h)
            self.host = HostWindow("ClearFrame Output", 100, 100, int(out_w * fit), int(out_h * fit),
                                   exclude_from_capture=exclude)
        self.stage = None
        fmt, feed_w, feed_h = "bgra", w, h
        if t["engine"] in ("neural", "nvvfx"):
            # the network runs here, on the GPU (gpu_stage); mpv only fits its output to the window
            from gpu_stage import GpuStage
            self.stage = GpuStage(t["engine"], in_w, in_h, out_w, out_h, t)
            fmt, feed_w, feed_h = "rgb24", self.stage.out_w, self.stage.out_h
            vf = ""
        elif t["engine"] == "none":   # plain scaling, for comparison or non-RTX GPUs
            vf = f"scale={in_w}:{in_h}:flags=area"
        else:
            vf = (f"scale={in_w}:{in_h}:flags=area,format=nv12,"
                  f"d3d11vpp=scale={scale:.3f}:scaling-mode=nvidia")
        deband = ([f"--deband=yes", "--deband-iterations=4", f"--deband-threshold={int(t['deband_strength'])}",
                   f"--deband-grain={int(t['deband_grain'])}"] if t["deband"] else ["--deband=no"])
        args = [str(MPV), "--no-config", "--demuxer=rawvideo", f"--demuxer-rawvideo-w={feed_w}",
                f"--demuxer-rawvideo-h={feed_h}", f"--demuxer-rawvideo-mp-format={fmt}", f"--demuxer-rawvideo-fps={fps}",
                # frames arrive already paced (see LiveSession.pump): show each one at once, buffer nothing
                "--untimed", "--no-cache", "--demuxer-readahead-secs=0", "--demuxer-max-bytes=1MiB",
                "--gpu-api=d3d11", "--d3d11-adapter=NVIDIA", "--vo=gpu-next", f"--vf={vf}",
                "--scale=ewa_lanczossharp", "--dscale=mitchell",
                *deband,
                f"--wid={self.host.hwnd}", "--no-input-default-bindings", "--input-cursor=no", "--cursor-autohide=no",
                "--keep-open=no", "--osd-level=0", "--really-quiet",
                "--log-file=" + str(ROOT / "live" / "output-mpv.log")]
        self.proc = subprocess.Popen(args + ["-"], stdin=subprocess.PIPE, cwd=str(MPV.parent))
        source = f"real {in_w}x{in_h}" if native_h else f"{in_w}x{in_h}"
        if self.stage:
            self.engine_label = self.stage.label
            self.info = f"on screen {w}x{h} -> {source} -> {self.stage.label} -> {self.stage.out_w}x{self.stage.out_h}"
        else:
            name = "no upscaling" if t["engine"] == "none" else f"RTX VSR x{scale:.2f}"
            self.engine_label = "Bicubic" if t["engine"] == "none" else "NVIDIA RTX VSR (driver)"
            self.info = f"on screen {w}x{h} -> {source} -> {name} -> {out_w}x{out_h}"

    def window(self):
        return self.host.hwnd

    def write(self, bgra):
        try:
            if self.stage:
                self.stage.submit(bgra)
                if self.stage.result is not None:
                    self.proc.stdin.write(self.stage.result)
                return True
            self.proc.stdin.write(np.ascontiguousarray(bgra).data)
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def alive(self):
        """mpv runs and the user hasn't closed the window."""
        return self.proc.poll() is None and not self.host.closed.is_set()

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.terminate()
        if self.stage:
            self.stage.close()
        if self.label:
            self.label.close()
        self.host.close()

    def set_split(self, on, words=("WITH", "WITHOUT"), rtl=False, accent=(118, 185, 0)):
        """Split comparison (overlay only): upscaled on the left, the real original on the right."""
        if not self.place:
            return
        from split_overlay import LabelWindow, clip_left, draw_overlay
        x, y, w, h = map(int, self.place)
        if on:
            clip_left(self.host.hwnd, w // 2, h)
            if self.label is None:
                self.label = LabelWindow(x, y, w, h, exclude_from_capture=self.exclude)
            self.label.show(draw_overlay(w, h, w // 2, f"{words[0]}: {self.engine_label}", words[1], accent, rtl))
        else:
            clip_left(self.host.hwnd, None, h)
            if self.label:
                self.label.close()
                self.label = None


class LiveSession:
    """Capture one window (and find its video area) or a whole screen, upscale it into an Output."""

    def __init__(self, hwnd=None, target=None, overlay=False, max_input=720, fps=60, on_status=print,
                 source_width=None, monitor=None, tuning=None, split=None):
        """source_width: real width of the video if known (854 = 480p, 1280 = 720p...);
        None = detect it automatically (experimental). monitor: a monitors() entry to
        capture the whole screen instead of one window."""
        self.source_width = source_width
        self.monitor = monitor
        self.tuning = tuning
        self.split = split   # None, or {"words": (with, without), "rtl": bool, "accent": (r, g, b)} when on
        self.split_style = split
        self.hwnd, self.overlay, self.max_input, self.fps = hwnd, overlay, max_input, fps
        self.target = target or user32.GetSystemMetrics(1)
        self.on_status = on_status
        self.area = VideoArea()
        self.out, self.rect, self.client, self.frame_size = None, None, None, None
        self.frames, self.control = 0, None
        self.latest = None    # newest cropped frame, written out by pump() at a steady rate
        self.native = {}      # video area -> detected real height (None = shown at native size)
        self.native_changed = False
        self.lock = threading.Lock()
        self.stopped = threading.Event()

    def start(self):
        if self.monitor:
            cap = WindowsCapture(cursor_capture=True, draw_border=False, monitor_index=self.monitor["index"])
        else:
            cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=self.hwnd)
        cap.event(self.on_frame_arrived)
        cap.event(self.on_closed)
        self.on_status("looking for the video (play it)...")
        self.control = cap.start_free_threaded()
        threading.Thread(target=self.pump, daemon=True).start()

    def pump(self):
        """Feed the output at a constant rate. Captured frames arrive in uneven bursts;
        passing them on as they come made playback speed up and slow down. A frame that
        hasn't changed yet is simply shown again, like a browser does."""
        period = 1.0 / self.fps
        next_t = time.perf_counter()
        while not self.stopped.is_set():
            with self.lock:
                out, frame = self.out, self.latest
            if out is not None and frame is not None:
                if not out.write(frame):
                    if self.out is out:   # not just a restart for a new video area
                        self.stop()
                        return
                self.frames += 1
            next_t += period
            delay = next_t - time.perf_counter()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.perf_counter()  # fell behind: don't try to catch up in a burst

    def placement(self, rect):
        """Screen rectangle of the video area (for the overlay)."""
        if self.monitor:
            m = self.monitor
            return m["x"], m["y"], m["w"], m["h"]
        origin = (ctypes.c_long * 2)(0, 0)
        user32.ClientToScreen(ctypes.c_void_p(self.hwnd), origin)
        x, y, w, h = rect
        return origin[0] + x, origin[1] + y, w, h

    def on_frame_arrived(self, frame: Frame, control: InternalCaptureControl):
        if self.stopped.is_set():
            control.stop()
            return
        buf = frame.frame_buffer
        if self.monitor:
            rect = (0, 0, buf.shape[1], buf.shape[0])   # everything on the screen
        else:
            if self.frame_size != buf.shape[:2]:
                self.frame_size, self.client = buf.shape[:2], client_area(self.hwnd)
            cx, cy, cw, ch = self.client
            buf = buf[cy:cy + ch, cx:cx + cw]
            rect = self.area.update(buf)
        if rect is None:
            return  # still looking for the video area
        with self.lock:
            if self.out is not None and not self.out.alive():
                self.stop()  # the output window was closed
                return
            x, y, w, h = rect
            crop = np.ascontiguousarray(buf[y:y + h, x:x + w])  # copy: the capture buffer is reused
            if rect not in self.native:
                if self.monitor:
                    # a screen is drawn at its own resolution; only an explicit choice lowers it
                    self.native[rect] = round(h * self.source_width / w) if self.source_width and self.source_width < w else None
                elif self.source_width:
                    # chosen by the user: the picture's real width, scaled to this area's height
                    self.native[rect] = round(h * self.source_width / w) if self.source_width < w else None
                else:
                    self.native[rect] = "pending"
                    threading.Thread(target=self.detect_native, args=(rect, crop), daemon=True).start()
            if self.out is None or rect != self.rect or self.native_changed:
                if self.out is not None:
                    self.out.close()
                place = self.placement(rect) if self.overlay else None
                native_h = self.native[rect] if isinstance(self.native[rect], int) else None
                exclude, geometry = False, None
                if self.monitor:
                    if self.overlay:
                        exclude = True   # lies on the captured screen: must not capture itself
                    else:
                        others = [m for m in monitors() if m["handle"] != self.monitor["handle"]]
                        if others:       # fill another monitor, visible to OBS
                            o = others[0]
                            geometry = (o["x"], o["y"], o["w"], o["h"])
                        else:            # only one screen: hide it from capture (OBS can't see it then)
                            exclude = True
                max_input = 10 ** 6 if self.monitor else self.max_input
                self.out = Output(w, h, self.target, max_input, self.fps, place, native_h, exclude, geometry, self.tuning)
                self.rect, self.latest, self.native_changed = rect, None, False
                if self.split:
                    self.out.set_split(True, **self.split)
                self.on_status(self.out.info)
            self.latest = crop

    def detect_native(self, rect, crop):
        """Find the video's real height in the background, then restart the output with it."""
        gray = crop[..., 1]  # green ~ luma
        h, score = native_res.estimate(gray)
        with self.lock:
            self.native[rect] = h
            if h and rect == self.rect:
                self.native_changed = True
        self.on_status(f"real video height: {h}" if h else "video shown at its real size")

    def on_closed(self):
        self.stopped.set()

    def stop(self):
        self.stopped.set()
        if self.out is not None:
            self.out.close()
            self.out = None

    def close(self):
        """Stop and wait for the capture thread (call from the owning thread, not a callback)."""
        self.stop()
        if self.control is not None:
            try:
                self.control.stop()
                # wait() can block forever when the captured window is already gone
                for _ in range(40):
                    if self.control.is_finished():
                        break
                    time.sleep(0.05)
            except Exception:
                pass  # already finished

    def running(self):
        return not self.stopped.is_set()

    def set_split(self, on):
        """Switch the split comparison of the running output on or off."""
        with self.lock:
            self.split = self.split_style if on else None
            if self.out is not None:
                self.out.set_split(on, **(self.split_style or {}))

    def set_visible(self, visible):
        """Hide the output for a moment to see the original (compare)."""
        out = self.out
        if out is not None and out.host.hwnd:
            user32.ShowWindow(ctypes.c_void_p(out.host.hwnd), 8 if visible else 0)  # SW_SHOWNA / SW_HIDE
            if out.label is not None:
                user32.ShowWindow(ctypes.c_void_p(out.label.hwnd), 8 if visible else 0)


def main(argv=None):
    """Runs one session. The tray starts this as a child process (one per session), so a
    capture that hangs inside Windows can never freeze the tray: it just kills the process.
    Status lines go to stdout as 'STATUS: ...'."""
    ap = argparse.ArgumentParser()
    ap.add_argument("window", nargs="?", help="part of the window title")
    ap.add_argument("--hwnd", type=int, help="window handle (instead of a title)")
    ap.add_argument("--monitor", type=int, help="capture this whole screen (1-based) instead of a window")
    ap.add_argument("--target", type=int, default=None, help="output height (default: screen height)")
    ap.add_argument("--max-input", type=int, default=720, help="downscale captures taller than this before upscaling")
    ap.add_argument("--source-width", type=int, default=None, help="real video width if known (854 = 480p...)")
    ap.add_argument("--overlay", action="store_true", help="show the result over the original video")
    ap.add_argument("--engine", default="rtx_driver", choices=["neural", "nvvfx", "rtx_driver", "none"])
    ap.add_argument("--split", action="store_true", help="compare: upscaled left, original right (overlay)")
    ap.add_argument("--split-words", default="WITH|WITHOUT", help="labels of the two sides")
    ap.add_argument("--rtl", action="store_true", help="labels in a right-to-left language")
    ap.add_argument("--accent", default="#76b900")
    ap.add_argument("--quality", default="high", choices=["low", "medium", "high", "ultra"], help="nvvfx")
    ap.add_argument("--artifact-reduction", default="strong", choices=["off", "light", "strong"], help="nvvfx")
    ap.add_argument("--fps", type=int, default=60, help="output pacing")
    ap.add_argument("--no-deband", action="store_true")
    ap.add_argument("--deband-strength", type=int, default=48)
    ap.add_argument("--deband-grain", type=int, default=16)
    ap.add_argument("--seconds", type=float, default=0, help="stop after this many seconds (0 = until closed)")
    args = ap.parse_args(argv)

    def status(text):
        print("STATUS: " + text, flush=True)

    monitor = None
    hwnd = args.hwnd
    if args.monitor:
        monitor = next((m for m in monitors() if m["index"] == args.monitor), None)
        if monitor is None:
            sys.exit(f"no screen {args.monitor}")
    elif hwnd is None:
        wins = find_window(args.window or "")
        if not args.window or not wins:
            sys.exit(f"no window with '{args.window}' in the title")
        hwnd = wins[0][0]
    if args.engine == "neural":
        status("loading the neural network (the first time it is prepared for your GPU, about a minute)...")
        import gpu_stage
        gpu_stage.prepare()
    tuning = {"engine": args.engine, "deband": not args.no_deband,
              "deband_strength": args.deband_strength, "deband_grain": args.deband_grain,
              "quality": args.quality, "artifact_reduction": args.artifact_reduction}
    a = args.accent.lstrip("#")
    style = {"words": tuple(args.split_words.split("|", 1)), "rtl": args.rtl,
             "accent": tuple(int(a[i:i + 2], 16) for i in (0, 2, 4))}
    session = LiveSession(hwnd, args.target, args.overlay, args.max_input, args.fps, on_status=status,
                          source_width=args.source_width, monitor=monitor, tuning=tuning, split=style)
    session.split = style if args.split else None
    session.start()

    def commands():
        """Lines from the parent on stdin: 'hide' / 'show' (compare), 'stop'."""
        for line in sys.stdin:
            cmd = line.strip()
            if cmd in ("hide", "show"):
                session.set_visible(cmd == "show")
            elif cmd in ("split on", "split off"):
                session.set_split(cmd == "split on")
            elif cmd == "stop":
                session.stop()
                break

    threading.Thread(target=commands, daemon=True).start()
    t0 = time.time()
    try:
        while session.running() and (not args.seconds or time.time() - t0 < args.seconds):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    session.stop()
    secs = time.time() - t0
    gpu = sys.modules.get("gpu_stage")
    net = (f", network: {gpu.stats['frames']} new frames ({gpu.stats['frames'] / secs:.1f}/s), "
           f"{gpu.stats['ms']:.1f} ms each") if gpu and gpu.stats["frames"] else ""
    status(f"ended: {session.frames} frames in {secs:.1f}s{net}")
    os._exit(0)   # don't wait for capture threads that may never finish


if __name__ == "__main__":
    main()
