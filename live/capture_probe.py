"""Probe Windows Graphics Capture of one window: frame rate, size, one saved frame.

    .venv/Scripts/python live/capture_probe.py "<part of window title>" [seconds]
"""
import ctypes
import sys
import time

import numpy as np
from PIL import Image
from windows_capture import Frame, InternalCaptureControl, WindowsCapture

user32 = ctypes.windll.user32


def find_window(part):
    found = []
    buf = ctypes.create_unicode_buffer(512)

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            user32.GetWindowTextW(hwnd, buf, 512)
            if part.lower() in buf.value.lower():
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found


def main():
    part, seconds = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 4
    wins = find_window(part)
    if not wins:
        sys.exit(f"no window with '{part}' in the title")
    hwnd, title = wins[0]
    print(f"capturing: {title!r} hwnd={hwnd}")
    stats = {"n": 0, "first": None, "last": None, "size": None, "saved": False, "gaps": []}

    cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd)

    @cap.event
    def on_frame_arrived(frame: Frame, control: InternalCaptureControl):
        now = time.perf_counter()
        if stats["last"] is not None:
            stats["gaps"].append((now - stats["last"]) * 1000)
        stats["first"] = stats["first"] or now
        stats["last"] = now
        stats["n"] += 1
        stats["size"] = (frame.width, frame.height)
        if not stats["saved"] and stats["n"] == 30:
            Image.fromarray(np.ascontiguousarray(frame.frame_buffer[:, :, 2::-1])).save("capture-probe.png")
            stats["saved"] = True
        if now - stats["first"] > seconds:
            control.stop()

    @cap.event
    def on_closed():
        pass

    cap.start()
    dur = (stats["last"] - stats["first"]) if stats["n"] > 1 else 0
    print(f"frames {stats['n']} in {dur:.2f}s -> {stats['n'] / dur if dur else 0:.1f} fps, size {stats['size']}")
    g = sorted(stats["gaps"])
    if g:
        pct = lambda q: g[min(len(g) - 1, int(q * len(g)))]
        print(f"frame interval ms: median {pct(0.5):.1f}, p95 {pct(0.95):.1f}, max {g[-1]:.1f}, gaps >100 ms: {sum(x > 100 for x in g)}")


if __name__ == "__main__":
    main()
