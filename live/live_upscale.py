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
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
from windows_capture import Frame, InternalCaptureControl, WindowsCapture

import native_res

ROOT = Path(__file__).resolve().parent.parent
MPV = ROOT / "vendor" / "mpv" / "mpv.exe"
user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi
user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # physical pixels, like the capture


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
    """mpv reading raw BGRA frames from stdin and showing them through RTX VSR.
    place=None: normal window (capture it in OBS); place=(x, y, w, h): borderless,
    click-through window lying exactly over the original video."""

    def __init__(self, w, h, target, max_input, fps, place=None, native_h=None):
        self.w, self.h = w, h
        # players and browsers already upscaled the video to the window: bring it back
        # to its real size (detected, see native_res) so the network sees real pixels;
        # until that is known, just cap the height (never upscale here)
        k = native_h / h if native_h else min(1.0, max_input / h)
        in_h, in_w = int(h * k) // 2 * 2, int(w * k) // 2 * 2
        if place:
            target = place[3]
        scale = max(1.0, min(4.0, target / in_h))
        vf = (f"scale={in_w}:{in_h}:flags=area,format=nv12,"
              f"d3d11vpp=scale={scale:.3f}:scaling-mode=nvidia")
        args = [str(MPV), "--no-config", "--demuxer=rawvideo", f"--demuxer-rawvideo-w={w}", f"--demuxer-rawvideo-h={h}",
                "--demuxer-rawvideo-mp-format=bgra", f"--demuxer-rawvideo-fps={fps}",
                # frames arrive already paced (see LiveSession.pump): show each one at once, buffer nothing
                "--untimed", "--no-cache", "--demuxer-readahead-secs=0", "--demuxer-max-bytes=1MiB",
                "--gpu-api=d3d11", "--d3d11-adapter=NVIDIA", "--vo=gpu-next", f"--vf={vf}",
                "--deband=yes", "--deband-iterations=4", "--deband-threshold=48", "--deband-grain=16",
                "--title=ClearFrame Output", "--force-window=yes", "--keep-open=no", "--osd-level=0",
                "--really-quiet", "--log-file=" + str(ROOT / "live" / "output-mpv.log")]
        if place:
            x, y, pw, ph = place
            args += ["--no-border", "--ontop", "--focus-on=never", "--no-input-default-bindings",
                     f"--geometry={pw}x{ph}+{x}+{y}", "--keepaspect-window=no"]
        else:
            args += ["--geometry=50%", "--input-default-bindings=yes"]
        self.proc = subprocess.Popen(args + ["-"], stdin=subprocess.PIPE, cwd=str(MPV.parent))
        source = f"real {in_w}x{in_h}" if native_h else f"{in_w}x{in_h}"
        self.info = f"on screen {w}x{h} -> {source} -> RTX VSR x{scale:.2f} -> {int(in_w * scale)}x{int(in_h * scale)}"
        if place:
            threading.Thread(target=self._click_through, daemon=True).start()

    def _click_through(self):
        """Let mouse clicks pass to the original player and never take focus."""
        for _ in range(100):
            hwnd = self.window()
            if hwnd:
                ex = user32.GetWindowLongW(ctypes.c_void_p(hwnd), -20)  # GWL_EXSTYLE
                user32.SetWindowLongW(ctypes.c_void_p(hwnd), -20, ex | 0x80000 | 0x20 | 0x08000000)  # LAYERED|TRANSPARENT|NOACTIVATE
                user32.SetLayeredWindowAttributes(ctypes.c_void_p(hwnd), 0, 255, 2)  # fully opaque
                return
            time.sleep(0.05)

    def window(self):
        found = []
        pid = ctypes.c_ulong()

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def cb(hwnd, _):
            user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
            if pid.value == self.proc.pid and user32.IsWindowVisible(hwnd):
                found.append(hwnd)
            return True

        user32.EnumWindows(cb, 0)
        return found[0] if found else None

    def write(self, bgra):
        try:
            self.proc.stdin.write(np.ascontiguousarray(bgra).data)
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def alive(self):
        return self.proc.poll() is None

    def close(self):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.terminate()


class LiveSession:
    """Capture one window, find its video area, upscale it into an Output."""

    def __init__(self, hwnd, target=None, overlay=False, max_input=720, fps=60, on_status=print, source_width=None):
        """source_width: real width of the video if known (854 = 480p, 1280 = 720p...);
        None = detect it automatically (experimental)."""
        self.source_width = source_width
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
        origin = (ctypes.c_long * 2)(0, 0)
        user32.ClientToScreen(ctypes.c_void_p(self.hwnd), origin)
        x, y, w, h = rect
        return origin[0] + x, origin[1] + y, w, h

    def on_frame_arrived(self, frame: Frame, control: InternalCaptureControl):
        if self.stopped.is_set():
            control.stop()
            return
        buf = frame.frame_buffer
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
                if self.source_width:
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
                self.out = Output(w, h, self.target, self.max_input, self.fps, place, native_h)
                self.rect, self.latest, self.native_changed = rect, None, False
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
                self.control.wait()
            except Exception:
                pass  # already finished

    def running(self):
        return not self.stopped.is_set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("window")
    ap.add_argument("--target", type=int, default=None, help="output height (default: screen height)")
    ap.add_argument("--max-input", type=int, default=720, help="downscale captures taller than this before upscaling")
    ap.add_argument("--overlay", action="store_true", help="show the result over the original video")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this many seconds (0 = until closed)")
    args = ap.parse_args()
    wins = find_window(args.window)
    if not wins:
        sys.exit(f"no window with '{args.window}' in the title")
    hwnd, title = wins[0]
    print(f"source window: {title!r}")
    session = LiveSession(hwnd, args.target, args.overlay, args.max_input)
    session.start()
    t0 = time.time()
    try:
        while session.running() and (not args.seconds or time.time() - t0 < args.seconds):
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    session.close()
    print(f"done: {session.frames} frames in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
