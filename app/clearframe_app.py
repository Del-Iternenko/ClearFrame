"""ClearFrame control panel.

A small always-on-top window that plays a video in mpv and switches upscaling
on and off while it plays, so the difference can be judged on the spot.

    pythonw app/clearframe_app.py        (or double-click ClearFrame.bat)

Needs mpv in vendor/mpv (run `python bench/bench.py setup` once). Standard library only.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parent.parent
MPV = ROOT / "vendor" / "mpv" / "mpv.exe"
SETTINGS = Path(os.environ.get("APPDATA", ROOT)) / "ClearFrame" / "settings.json"

MODES = {
    "rtx": "RTX Video Super Resolution (Tensor Cores)",
    "sharp": "Sharp scaler (EWA Lanczos, shaders)",
    "model": "ClearFrame neural model (coming soon)",
}
TARGETS = (1080, 1440, 2160)
DEFAULTS = {"enabled": True, "mode": "rtx", "target": 1440, "deband": True, "deband_strength": 48}

# ---------------------------------------------------------------- mpv over its JSON IPC pipe
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.CreateFileW.restype = wt.HANDLE
kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
kernel32.WriteFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.ReadFile.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.PeekNamedPipe.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
INVALID_HANDLE = wt.HANDLE(-1).value


class Mpv:
    """One pipe handle, never blocking: writes are small, reads only what PeekNamedPipe reports."""

    def __init__(self):
        self.proc = None
        self.handle = None
        self.buffer = b""
        self.pipe = rf"\\.\pipe\clearframe-{os.getpid()}"

    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        args = [str(MPV), "--idle=yes", "--force-window=yes", "--keep-open=yes", f"--input-ipc-server={self.pipe}",
                "--gpu-api=d3d11", "--d3d11-adapter=NVIDIA", "--vo=gpu-next", "--hwdec=d3d11va",
                "--title=ClearFrame - ${media-title}", "--geometry=60%", "--osd-font-size=26",
                "--dither-depth=auto", "--correct-downscaling=yes", "--dscale=mitchell"]
        self.proc = subprocess.Popen(args, cwd=str(MPV.parent))
        for _ in range(100):
            h = kernel32.CreateFileW(self.pipe, 0xC0000000, 0, None, 3, 0, None)  # GENERIC_READ|WRITE, OPEN_EXISTING
            if h and h != INVALID_HANDLE:
                self.handle = h
                return
            time.sleep(0.05)
        raise RuntimeError("mpv did not open its control pipe")

    def command(self, *args):
        if not self.handle:
            return
        data = (json.dumps({"command": list(args)}) + "\n").encode()
        written = wt.DWORD()
        if not kernel32.WriteFile(self.handle, data, len(data), ctypes.byref(written), None):
            self.close()

    def poll(self):
        """Messages that already arrived (property changes, events)."""
        if not self.handle:
            return []
        avail = wt.DWORD()
        if not kernel32.PeekNamedPipe(self.handle, None, 0, None, ctypes.byref(avail), None):
            self.close()
            return []
        if avail.value:
            buf = ctypes.create_string_buffer(avail.value)
            read = wt.DWORD()
            kernel32.ReadFile(self.handle, buf, avail.value, ctypes.byref(read), None)
            self.buffer += buf.raw[:read.value]
        *lines, self.buffer = self.buffer.split(b"\n")
        out = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
        self.handle = None

    def quit(self):
        self.command("quit")
        self.close()


# ---------------------------------------------------------------- UI
class App:
    def __init__(self, root):
        self.root = root
        self.mpv = Mpv()
        self.src = None       # (w, h) of the decoded video
        self.out = None       # (w, h) after filters
        self.comparing = False
        self.settings = {**DEFAULTS, **self.load()}

        root.title("ClearFrame")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        root.configure(bg="#14161d")
        self.style()

        pad = {"padx": 14, "pady": 6}
        top = ttk.Frame(root, style="Card.TFrame")
        top.pack(fill="x", **pad)
        ttk.Label(top, text="ClearFrame", style="Title.TLabel").pack(side="left")
        self.power = tk.Button(top, width=14, font=("Segoe UI", 11, "bold"), relief="flat", bd=0,
                               cursor="hand2", command=self.toggle)
        self.power.pack(side="right")

        src = ttk.Frame(root, style="Card.TFrame")
        src.pack(fill="x", **pad)
        ttk.Button(src, text="Open file...", command=self.open_file).pack(side="left")
        self.url = ttk.Entry(src, width=34)
        self.url.pack(side="left", padx=8)
        self.url.insert(0, "or paste a video URL / .m3u8")
        self.url.bind("<FocusIn>", lambda e: self.url.select_range(0, "end"))
        self.url.bind("<Return>", lambda e: self.open_url())
        ttk.Button(src, text="Play", command=self.open_url).pack(side="left")

        box = ttk.LabelFrame(root, text="Upscaler", style="Card.TLabelframe")
        box.pack(fill="x", **pad)
        self.mode = tk.StringVar(value=self.settings["mode"])
        for key, text in MODES.items():
            rb = ttk.Radiobutton(box, text=text, value=key, variable=self.mode, command=self.changed)
            rb.pack(anchor="w", padx=8, pady=1)
            if key == "model":
                rb.state(["disabled"])
        row = ttk.Frame(box, style="Card.TFrame")
        row.pack(fill="x", padx=8, pady=(6, 8))
        ttk.Label(row, text="Target").pack(side="left")
        self.target = tk.IntVar(value=self.settings["target"])
        for t in TARGETS:
            ttk.Radiobutton(row, text=f"{t}p", value=t, variable=self.target, command=self.changed).pack(side="left", padx=6)

        clean = ttk.LabelFrame(root, text="Clean-up", style="Card.TLabelframe")
        clean.pack(fill="x", **pad)
        self.deband = tk.BooleanVar(value=self.settings["deband"])
        ttk.Checkbutton(clean, text="Remove banding and blocks in dark scenes (deband)", variable=self.deband,
                        command=self.changed).pack(anchor="w", padx=8, pady=(4, 0))
        srow = ttk.Frame(clean, style="Card.TFrame")
        srow.pack(fill="x", padx=8, pady=(2, 8))
        ttk.Label(srow, text="Strength").pack(side="left")
        self.strength = tk.IntVar(value=self.settings["deband_strength"])
        ttk.Scale(srow, from_=16, to=128, variable=self.strength, command=lambda v: self.changed()).pack(side="left", fill="x", expand=True, padx=8)

        cmp_row = ttk.Frame(root, style="Card.TFrame")
        cmp_row.pack(fill="x", **pad)
        self.compare_btn = ttk.Button(cmp_row, text="Hold to compare (shows original)")
        self.compare_btn.pack(fill="x")
        self.compare_btn.bind("<ButtonPress-1>", lambda e: self.compare(True))
        self.compare_btn.bind("<ButtonRelease-1>", lambda e: self.compare(False))

        self.status = ttk.Label(root, text="Open a video to start.", style="Status.TLabel", anchor="w", justify="left")
        self.status.pack(fill="x", padx=14, pady=(4, 12))

        root.bind("<u>", lambda e: self.toggle())
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.paint_power()
        root.after(100, self.tick)

    # ---------------- look
    def style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        bg, card, fg, muted, accent = "#14161d", "#14161d", "#e8e9ee", "#8a8fa3", "#76b900"
        s.configure(".", background=card, foreground=fg, fieldbackground="#1c1f29", font=("Segoe UI", 10))
        s.configure("Card.TFrame", background=card)
        s.configure("Card.TLabelframe", background=card, bordercolor="#262a36")
        s.configure("Card.TLabelframe.Label", background=card, foreground=muted)
        s.configure("Title.TLabel", font=("Segoe UI", 16, "bold"), background=bg)
        s.configure("Status.TLabel", foreground=muted, background=bg, font=("Consolas", 9))
        s.configure("TButton", background="#262a36", foreground=fg, borderwidth=0, padding=6)
        s.map("TButton", background=[("active", "#323746")])
        s.configure("TRadiobutton", background=card, foreground=fg)
        s.configure("TCheckbutton", background=card, foreground=fg)
        s.map("TRadiobutton", background=[("active", card)], foreground=[("disabled", "#555a6a")])
        s.map("TCheckbutton", background=[("active", card)])
        s.configure("Horizontal.TScale", background=card, troughcolor="#262a36")
        self.accent = accent

    def paint_power(self):
        on = self.settings["enabled"]
        self.power.configure(text="UPSCALER ON" if on else "UPSCALER OFF",
                             bg=self.accent if on else "#3a3f4f", fg="#000" if on else "#cfd3e0",
                             activebackground=self.accent if on else "#4a5063")

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

    def changed(self):
        self.settings.update(mode=self.mode.get(), target=self.target.get(), deband=self.deband.get(),
                             deband_strength=int(self.strength.get()))
        self.save()
        self.apply()

    def toggle(self):
        self.settings["enabled"] = not self.settings["enabled"]
        self.save()
        self.paint_power()
        self.apply(announce=True)

    def compare(self, pressed):
        self.comparing = pressed
        self.apply(announce=True)

    # ---------------- player
    def ensure_mpv(self):
        if not MPV.exists():
            messagebox.showerror("ClearFrame", f"mpv not found:\n{MPV}\n\nRun once:  python bench/bench.py setup")
            return False
        if not self.mpv.running() or not self.mpv.handle:
            self.mpv.start()
            for prop in ("video-params", "video-out-params", "hwdec-current", "estimated-vf-fps"):
                self.mpv.command("observe_property", 1, prop)
        return True

    def open_file(self):
        path = filedialog.askopenfilename(title="Open video",
                                          filetypes=[("Video", "*.mp4 *.mkv *.webm *.mov *.avi *.ts *.m4v"), ("All files", "*.*")])
        if path:
            self.play(path)

    def open_url(self):
        url = self.url.get().strip()
        if url.lower().startswith(("http://", "https://")):
            self.play(url)

    def play(self, target):
        if self.ensure_mpv():
            self.src = self.out = None
            self.mpv.command("loadfile", target)
            self.apply()

    def apply(self, announce=False):
        """Push the current settings into the running player."""
        if not self.mpv.handle:
            return
        active = self.settings["enabled"] and not self.comparing
        mode = self.settings["mode"]
        vf = ""
        if active and mode == "rtx" and self.src:
            scale = max(1.0, min(4.0, self.settings["target"] / self.src[1]))
            vf = f"format=nv12,d3d11vpp=scale={scale:.3f}:scaling-mode=nvidia"
        self.mpv.command("set_property", "vf", vf)
        # "off" behaves like a browser: plain bilinear scaling to the window
        self.mpv.command("set_property", "scale", "ewa_lanczossharp" if active and mode == "sharp" else "bilinear")
        self.mpv.command("set_property", "deband", "yes" if self.settings["deband"] and active else "no")
        self.mpv.command("set_property", "deband-threshold", int(self.settings["deband_strength"]))
        if announce:
            text = "ORIGINAL (compare)" if self.comparing else ("Upscaler ON - " + MODES[mode] if active else "Upscaler OFF")
            self.mpv.command("show-text", text, 1500)

    def tick(self):
        for msg in self.mpv.poll():
            if msg.get("event") != "property-change":
                continue
            name, data = msg.get("name"), msg.get("data")
            if name == "video-params" and data:
                first = self.src is None
                self.src = (data.get("w"), data.get("h"))
                if first:
                    self.apply()
            elif name == "video-out-params" and data:
                self.out = (data.get("w"), data.get("h"))
            elif name == "hwdec-current":
                self.hwdec = data
        if not self.mpv.running() and self.mpv.handle:
            self.mpv.close()
        self.update_status()
        self.root.after(150, self.tick)

    def update_status(self):
        if not self.mpv.handle:
            self.status.configure(text="Open a video to start.")
            return
        if not self.src:
            self.status.configure(text="Loading...")
            return
        sw, sh = self.src
        line = f"Source  {sw}x{sh}"
        if self.out:
            ow, oh = self.out
            line += f"   ->   {ow}x{oh}"
            if self.settings["enabled"] and not self.comparing and self.settings["mode"] == "rtx":
                line += "   RTX VSR active" if oh > sh else "   RTX VSR not applied (needs an RTX GPU)"
        line += f"\nDecoder {getattr(self, 'hwdec', None) or 'software'}   |   U = on/off"
        self.status.configure(text=line)

    def close(self):
        if self.mpv.running():
            self.mpv.quit()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
