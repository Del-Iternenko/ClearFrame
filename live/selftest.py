"""Checks the three output modes against a test video window.

    .venv/Scripts/python live/selftest.py
Opens a test video (bench/out/source.mp4) in mpv, then for each mode starts a session,
inspects the output window and closes everything again.
"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
from windows_capture import WindowsCapture

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_upscale as lu  # noqa: E402

ROOT = lu.ROOT
u = lu.user32
u.WindowFromPoint.restype = ctypes.c_void_p
u.GetAncestor.restype = ctypes.c_void_p


def grab(hwnd, frames=20, timeout=6):
    """Capture a window like OBS does: returns (frames received, mean brightness, changed between frames)."""
    got = []
    cap = WindowsCapture(cursor_capture=False, draw_border=False, window_hwnd=hwnd)
    t0 = time.time()

    @cap.event
    def on_frame_arrived(frame, control):
        got.append(frame.frame_buffer[::8, ::8, :3].astype(np.int16).copy())
        if len(got) >= frames or time.time() - t0 > timeout:
            control.stop()

    @cap.event
    def on_closed():
        pass

    ctl = cap.start_free_threaded()
    end = time.time() + timeout + 2
    while not ctl.is_finished() and time.time() < end:
        time.sleep(0.1)
    if not ctl.is_finished():
        ctl.stop()
    if not got:
        return 0, 0.0, False
    return len(got), float(np.mean(got[-1])), bool(np.abs(got[-1] - got[0]).mean() > 1)


def main():
    src = subprocess.Popen([str(lu.MPV), str(ROOT / "bench" / "out" / "source.mp4"), "--no-config", "--loop-file=inf",
                            "--geometry=1280x720+100+100", "--title=CF-probe-video", "--no-audio", "--really-quiet"])
    time.sleep(2.5)
    probe = lu.find_window("CF-probe-video")[0][0]
    mons = lu.monitors()
    try:
        for name, kw in (("window, over the video", dict(hwnd=probe, overlay=True)),
                         ("whole screen, overlay", dict(monitor=mons[0], overlay=True)),
                         ("window, separate (OBS)", dict(hwnd=probe, overlay=False))):
            s = lu.LiveSession(target=1080, on_status=lambda t: None, **kw)
            s.start()
            time.sleep(5)
            out = s.out
            if out is None:
                print(f"{name:26} -> no output (video area not found)")
                s.close()
                continue
            h = out.window()
            r = lu.RECT()
            u.GetWindowRect(ctypes.c_void_p(h), ctypes.byref(r))
            line = f"{name:26} -> window {r.right - r.left}x{r.bottom - r.top} at ({r.left},{r.top}), excluded={out.host.excluded()}"
            if kw.get("overlay"):
                cx, cy = (r.left + r.right) // 2, (r.top + r.bottom) // 2
                hit = u.GetAncestor(ctypes.c_void_p(u.WindowFromPoint(point(cx, cy))), 2)  # GA_ROOT
                line += f", clicks go to {'the window underneath' if hit != h else 'THE OVERLAY'}"
            if not out.host.excluded():
                n, bright, moving = grab(h)
                line += f", capture: {n} frames, brightness {bright:.0f}, moving={moving}"
            print(line, flush=True)
            s.close()
            time.sleep(1)
    finally:
        src.terminate()


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


u.WindowFromPoint.argtypes = [POINT]


def point(x, y):
    return POINT(x, y)


if __name__ == "__main__":
    main()
