"""Checks the tray's auto mode (the tray must be running): a test video goes fullscreen,
windowed, fullscreen again, then closes; prints how many ClearFrame outputs exist.

    .venv/Scripts/python live/autotest.py [monitor_number]
"""
import ctypes
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
import clearframe_app as ca  # noqa: E402
import live_upscale as lu  # noqa: E402

u = lu.user32


def outputs():
    ps = "@(Get-CimInstance Win32_Process -Filter \"Name='mpv.exe'\" | Where-Object { $_.CommandLine -like '*--wid=*' }).Count"
    return subprocess.run(["powershell", "-NoProfile", "-c", ps], capture_output=True, text=True).stdout.strip()


def wait_outputs(want, limit=12):
    t0 = time.time()
    while time.time() - t0 < limit:
        if outputs() == want:
            return f"{want} after {time.time() - t0:.1f}s"
        time.sleep(0.5)
    return f"still {outputs()} after {limit}s"


def main():
    screen = int(sys.argv[1]) if len(sys.argv) > 1 else 1   # mpv numbers screens from 0
    m = ca.Mpv()
    m.pipe = r"\\.\pipe\cf-autotest"
    m.proc = subprocess.Popen([str(lu.MPV), str(lu.ROOT / "bench" / "out" / "source.mp4"), "--no-config", "--loop-file=inf",
                               "--fs", f"--fs-screen={screen}", f"--input-ipc-server={m.pipe}", "--title=CF-probe-video",
                               "--no-audio", "--really-quiet"])
    try:
        for _ in range(100):
            h = ca.kernel32.CreateFileW(m.pipe, 0xC0000000, 0, None, 3, 0, None)
            if h and h != ca.INVALID_HANDLE:
                m.handle = h
                break
            time.sleep(0.05)
        time.sleep(2)
        hwnd = lu.find_window("CF-probe-video")[0][0]

        def front():
            u.keybd_event(0x12, 0, 0, 0)
            u.keybd_event(0x12, 0, 2, 0)
            u.SetForegroundWindow(ctypes.c_void_p(hwnd))
            time.sleep(0.3)

        state = lambda: f"(active+fullscreen: {lu.fullscreen_window() == hwnd})"
        front()
        print("1 fullscreen video ", state(), "-> outputs", wait_outputs("1"), flush=True)
        m.command("set_property", "fullscreen", False)
        print("2 left fullscreen  ", state(), "-> outputs", wait_outputs("0"), flush=True)
        m.command("set_property", "fullscreen", True)
        time.sleep(1)
        front()
        print("3 fullscreen again ", state(), "-> outputs", wait_outputs("1"), flush=True)
    finally:
        m.proc.terminate()
    print("4 video closed                       -> outputs", wait_outputs("0"), flush=True)


if __name__ == "__main__":
    main()
