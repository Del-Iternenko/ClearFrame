"""ClearFrame benchmark bench.

Runs the same web-style degraded clip through several upscalers and compares the
results against the clean original: PSNR/SSIM numbers, a split-screen video and
zoomed crops.

    python bench/bench.py all          # everything below, in order
    python bench/bench.py setup        # download pinned ffmpeg + mpv into vendor/
    python bench/bench.py source       # clean reference + degraded source clip
    python bench/bench.py bicubic      # plain bicubic upscale (what a browser does)
    python bench/bench.py rtx-vsr      # NVIDIA RTX Video Super Resolution (driver, via mpv)
    python bench/bench.py compare      # metrics, split-screen video, crops -> bench/out/results.md

Windows only for now (RTX VSR goes through D3D11). Outputs go to bench/out/.
Test footage: "Tears of Steel" (CC BY 3.0) (c) Blender Foundation | mango.blender.org
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
OUT = ROOT / "bench" / "out"

# Pinned tools: (url, sha256, folder inside vendor/)
FFMPEG = (
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-09-27-13-04/"
    "ffmpeg-N-126905-gb87602a63a-win64-gpl.zip",
    "7915e450f56f9abaeb7c3120086cea6b893d53340a1bc930977e93d31efd50ba",
    "ffmpeg",
)
MPV = (
    "https://github.com/shinchiro/mpv-winbuild-cmake/releases/download/20261008/"
    "mpv-x86_64-v3-20261008-git-36bf3d5290.7z",
    "653fbe02e256b979045876df2a5c91b81a3121d141679165ccac1deea50a8292",
    "mpv",
)

# Clip: 20 s of live action with faces, hair, dark interiors.
SOURCE_URL = "https://download.blender.org/demo/movies/ToS/tears_of_steel_1080p.mov.zip"  # ~580 MB, cached
CACHE = ROOT / "bench" / "cache"
CLIP_START, CLIP_SECONDS = 98, 20
REF_W, REF_H = 1920, 800          # the 1080p release is 1920x800 (2.4:1)
SCALE = 2                         # upscale factor under test
SRC_W, SRC_H = REF_W // SCALE, REF_H // SCALE
SRC_BITRATE = "700k"              # typical of a low-quality web stream at this size

REFERENCE = OUT / "reference.mkv"     # clean, lossless, REF_W x REF_H
SOURCE = OUT / "source.mp4"           # degraded, SRC_W x SRC_H
VARIANTS = {                          # name -> lossless output at REF_W x REF_H
    "bicubic": OUT / "bicubic.mkv",
    "rtx-vsr": OUT / "rtx-vsr.mkv",
    "vfx-high": OUT / "vfx-high.mkv",
    "vfx-ultra": OUT / "vfx-ultra.mkv",
    "liveaction-span": OUT / "liveaction-span.mkv",
}
LABELS = {"bicubic": "Bicubic (browser)", "rtx-vsr": "NVIDIA RTX VSR",
          "vfx-high": "NVIDIA VFX SuperRes High", "vfx-ultra": "NVIDIA VFX SuperRes Ultra",
          "liveaction-span": "2xLiveActionV1 SPAN"}


# ---------------------------------------------------------------- helpers
def log(msg):
    print(f"[bench] {msg}", flush=True)


def run(cmd, **kw):
    log(" ".join(str(c) for c in cmd)[:300])
    return subprocess.run([str(c) for c in cmd], check=True, **kw)


def tool(name):
    paths = {"ffmpeg": VENDOR / "ffmpeg" / "bin" / "ffmpeg.exe",
             "ffprobe": VENDOR / "ffmpeg" / "bin" / "ffprobe.exe",
             "mpv": VENDOR / "mpv" / "mpv.com"}
    p = paths[name]
    if not p.exists():
        sys.exit(f"{p} missing - run: python bench/bench.py setup")
    return p


def open_url(url):
    # some hosts reject Python's default User-Agent
    req = urllib.request.Request(url, headers={"User-Agent": "ClearFrame-bench/0.1 (+https://github.com/Del-Iternenko/ClearFrame)"})
    return urllib.request.urlopen(req)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url, expected_sha, dest):
    if not dest.exists():
        log(f"download {url}")
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open_url(url) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.rename(dest)
    actual = sha256(dest)
    if actual != expected_sha:
        dest.unlink()
        sys.exit(f"checksum mismatch for {dest.name}: {actual}")


def frame_count(video):
    out = subprocess.run([str(tool("ffprobe")), "-v", "error", "-select_streams", "v:0", "-count_frames",
                          "-show_entries", "stream=nb_read_frames", "-of", "json", str(video)],
                         check=True, capture_output=True, text=True).stdout
    return int(json.loads(out)["streams"][0]["nb_read_frames"])


def lossless(dst):
    """ffmpeg output args for a lossless intermediate."""
    return ["-c:v", "ffv1", "-level", "3", "-pix_fmt", "yuv420p", "-an", str(dst)]


# ---------------------------------------------------------------- steps
def setup():
    VENDOR.mkdir(exist_ok=True)
    for url, sha, folder in (FFMPEG, MPV):
        target = VENDOR / folder
        if target.exists():
            log(f"{folder} already in vendor/")
            continue
        archive = VENDOR / url.rsplit("/", 1)[1]
        fetch(url, sha, archive)
        tmp = VENDOR / (folder + ".tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as z:
                z.extractall(tmp)
            inner = [p for p in tmp.iterdir() if p.is_dir()]
            (inner[0] if len(inner) == 1 else tmp).rename(target)
            shutil.rmtree(tmp, ignore_errors=True)
        else:  # .7z: Windows 10/11 bsdtar can read it
            run([Path(os.environ["SystemRoot"]) / "System32" / "tar.exe", "-xf", archive, "-C", tmp])
            tmp.rename(target)
        archive.unlink()
        log(f"{folder} ready")


def master_file():
    """The clean 1080p film, downloaded once into bench/cache/."""
    CACHE.mkdir(parents=True, exist_ok=True)
    movs = list(CACHE.rglob("*.mov"))
    if movs:
        return movs[0]
    archive = CACHE / SOURCE_URL.rsplit("/", 1)[1]
    if not archive.exists():
        log(f"download {SOURCE_URL} (one time)")
        tmp = archive.with_suffix(".part")
        with open_url(SOURCE_URL) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f, 1 << 20)
        tmp.rename(archive)
    with zipfile.ZipFile(archive) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".mov"))
        z.extract(name, CACHE)
    archive.unlink()
    return next(CACHE.rglob("*.mov"))


def source():
    OUT.mkdir(parents=True, exist_ok=True)
    ff = tool("ffmpeg")
    if not REFERENCE.exists():
        movie = master_file()
        run([ff, "-hide_banner", "-y", "-ss", CLIP_START, "-t", CLIP_SECONDS, "-i", movie,
             "-vf", f"scale={REF_W}:{REF_H}:flags=lanczos,setsar=1", *lossless(REFERENCE)])
    if not SOURCE.exists():
        # what a low-quality web stream looks like: half resolution, starved H.264
        run([ff, "-hide_banner", "-y", "-i", REFERENCE,
             "-vf", f"scale={SRC_W}:{SRC_H}:flags=area,setsar=1",
             "-c:v", "libx264", "-preset", "medium", "-b:v", SRC_BITRATE,
             "-maxrate", SRC_BITRATE, "-bufsize", "1000k", "-pix_fmt", "yuv420p", "-an", SOURCE])
    log(f"reference {REFERENCE.name}: {frame_count(REFERENCE)} frames; source {SOURCE.name}: {frame_count(SOURCE)} frames")


def bicubic():
    run([tool("ffmpeg"), "-hide_banner", "-y", "-i", SOURCE,
         "-vf", f"scale={REF_W}:{REF_H}:flags=bicubic,setsar=1", *lossless(VARIANTS["bicubic"])])


def rtx_vsr():
    """RTX VSR only exists inside a real D3D11 video output, not in mpv's encode mode,
    so mpv plays the clip slowly and saves every filtered frame (after VSR) as PNG."""
    frames = OUT / "frames-rtx-vsr"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir(parents=True)
    script = OUT / "save-frames.lua"
    script.write_text("mp.register_event('file-loaded', function() mp.commandv('screenshot', 'video+each-frame') end)\n")
    log_file = OUT / "rtx-vsr.log"
    run([tool("mpv"), SOURCE, "--no-config", "--gpu-api=d3d11", "--d3d11-adapter=NVIDIA", "--vo=gpu-next", "--hwdec=d3d11va",
         f"--vf=format=nv12,d3d11vpp=scale={SCALE}:scaling-mode=nvidia",
         "--speed=0.2", "--framedrop=no", "--no-audio", "--geometry=640x360", "--keep-open=no", "--idle=no",
         f"--script={script}", f"--screenshot-directory={frames}",
         "--screenshot-template=%{estimated-frame-number}", "--screenshot-format=png", "--screenshot-png-compression=2",
         f"--log-file={log_file}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if "NVIDIA RTX Super Resolution enabled" not in log_file.read_text(errors="ignore"):
        sys.exit("RTX VSR did not start (needs a GeForce RTX GPU and mpv running on it) - see " + str(log_file))
    files = sorted(frames.glob("*.png"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0))
    expected = frame_count(SOURCE)
    log(f"captured {len(files)} of {expected} frames")
    if len(files) != expected:
        sys.exit("frame capture incomplete - try a lower --speed")
    # renumber 0..n-1 so ffmpeg reads them as one sequence
    for i, f in enumerate(files):
        f.rename(frames / f"f{i:05d}.png")
    run([tool("ffmpeg"), "-hide_banner", "-y", "-framerate", "24", "-i", frames / "f%05d.png",
         "-vf", f"scale={REF_W}:{REF_H}:flags=bicubic,setsar=1", *lossless(VARIANTS["rtx-vsr"])])
    shutil.rmtree(frames)


def vfx():
    """NVIDIA Video Effects SDK VideoSuperRes (pip install nvidia-vfx, user-installed, proprietary):
    the source decoded to RGB, upscaled on the GPU frame by frame, encoded losslessly."""
    import numpy as np
    import torch
    from nvvfx import VideoSuperRes
    for name, quality in (("vfx-high", "HIGH"), ("vfx-ultra", "ULTRA")):
        log(f"{name}: VideoSuperRes {quality} {SRC_W}x{SRC_H} -> {REF_W}x{REF_H}")
        dec = subprocess.Popen([str(tool("ffmpeg")), "-v", "error", "-i", str(SOURCE), "-f", "rawvideo",
                                "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
        enc = subprocess.Popen([str(tool("ffmpeg")), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                "-s", f"{REF_W}x{REF_H}", "-framerate", "24", "-i", "-",
                                *lossless(VARIANTS[name])], stdin=subprocess.PIPE)
        sr = VideoSuperRes(quality=VideoSuperRes.QualityLevel[quality])
        sr.output_width, sr.output_height = REF_W, REF_H
        sr.load()
        size, frames = SRC_W * SRC_H * 3, 0
        while (buf := dec.stdout.read(size)) and len(buf) == size:
            x = torch.from_numpy(np.frombuffer(bytearray(buf), np.uint8).reshape(SRC_H, SRC_W, 3)).cuda()
            x = x.permute(2, 0, 1).float().div_(255).contiguous()
            y = torch.from_dlpack(sr.run(x).image).clone()
            enc.stdin.write(y.clamp_(0, 1).mul_(255).round_().byte().permute(1, 2, 0).contiguous().cpu().numpy().tobytes())
            frames += 1
        sr.close()
        enc.stdin.close()
        enc.wait()
        dec.wait()
        log(f"{name}: {frames} frames")


def upscale_frames(name, fn):
    """Decode SOURCE to RGB, run fn(float CUDA tensor 3xHxW in [0,1]) -> 3xREF_HxREF_W, encode losslessly."""
    import numpy as np
    import torch
    dec = subprocess.Popen([str(tool("ffmpeg")), "-v", "error", "-i", str(SOURCE), "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen([str(tool("ffmpeg")), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{REF_W}x{REF_H}", "-framerate", "24", "-i", "-", *lossless(VARIANTS[name])],
                           stdin=subprocess.PIPE)
    size, frames = SRC_W * SRC_H * 3, 0
    with torch.inference_mode():
        while (buf := dec.stdout.read(size)) and len(buf) == size:
            x = torch.from_numpy(np.frombuffer(bytearray(buf), np.uint8).reshape(SRC_H, SRC_W, 3)).cuda()
            y = fn(x.permute(2, 0, 1).float().div_(255).contiguous())
            enc.stdin.write(y.clamp(0, 1).mul_(255).round_().byte().permute(1, 2, 0).contiguous().cpu().numpy().tobytes())
            frames += 1
    enc.stdin.close()
    enc.wait()
    dec.wait()
    log(f"{name}: {frames} frames")


def liveaction_span():
    """2xLiveActionV1_SPAN by jcj83429 (CC-BY-NC-SA-4.0): SPAN trained on H.264/VP9/H.265-compressed
    live action. Weights: vendor/models/2xLiveActionV1_SPAN.pth (not in git). Loaded with spandrel."""
    import spandrel
    import torch
    net = spandrel.ModelLoader().load_from_file(str(VENDOR / "models" / "2xLiveActionV1_SPAN.pth")).model
    net = net.cuda().half().eval()
    upscale_frames("liveaction-span", lambda x: net(x.half()[None])[0].float())


def metrics(variant):
    """PSNR and SSIM of a variant against the clean reference (whole clip)."""
    res = subprocess.run([str(tool("ffmpeg")), "-hide_banner", "-i", str(variant), "-i", str(REFERENCE),
                          "-lavfi", "[0:v][1:v]psnr;[0:v][1:v]ssim", "-f", "null", "-"],
                         capture_output=True, text=True, errors="ignore")
    psnr = re.search(r"PSNR .*?average:([\d.]+|inf)", res.stderr)
    ssim = re.search(r"SSIM .*?All:([\d.]+)", res.stderr)
    return (float(psnr.group(1)) if psnr else None, float(ssim.group(1)) if ssim else None)


ZOOM_W, ZOOM_H, ZOOM = 480, 270, 2   # region size in reference pixels, magnification


def pick_regions(count=2):
    """The most detailed ZOOM_W x ZOOM_H areas of the clip (edges, text, texture),
    measured on the clean reference, so every zoom video shows the same places."""
    k = 4  # analyse at 1/4 size
    w, h, rw, rh = REF_W // k, REF_H // k, ZOOM_W // k, ZOOM_H // k
    energy = [[0.0] * w for _ in range(h)]
    for t in (2, 6, 10, 14, 18):
        raw = subprocess.run([str(tool("ffmpeg")), "-v", "error", "-ss", str(t), "-i", str(REFERENCE), "-frames:v", "1",
                              "-vf", f"scale={w}:{h}:flags=area,format=gray", "-f", "rawvideo", "-"],
                             check=True, capture_output=True).stdout
        for y in range(1, h):
            row, up = raw[y * w:(y + 1) * w], raw[(y - 1) * w:y * w]
            for x in range(1, w):
                energy[y][x] += abs(row[x] - row[x - 1]) + abs(row[x] - up[x])
    # summed-area table -> energy of every window in O(1)
    sat = [[0.0] * (w + 1) for _ in range(h + 1)]
    for y in range(h):
        acc = 0.0
        for x in range(w):
            acc += energy[y][x]
            sat[y + 1][x + 1] = sat[y][x + 1] + acc
    scored = []
    for y in range(0, h - rh + 1, 2):
        for x in range(0, w - rw + 1, 2):
            s = sat[y + rh][x + rw] - sat[y][x + rw] - sat[y + rh][x] + sat[y][x]
            scored.append((s, x, y))
    scored.sort(reverse=True)
    picked = []
    for s, x, y in scored:
        if all(abs(x - px) >= rw or abs(y - py) >= rh for px, py in picked):
            picked.append((x, y))
            if len(picked) == count:
                break
    return [(x * k, y * k) for x, y in picked]


def zoom_videos(ready, label):
    """Same regions, same moments, every variant:
    zoom/<variant>.mp4            - the regions of one variant (flip between files to compare)
    zoom/bicubic-vs-<variant>.mp4 - unprocessed (bicubic) left, processed right
    zoom/regions.png              - where the regions are in the frame"""
    ff = tool("ffmpeg")
    zdir = OUT / "zoom"
    zdir.mkdir(exist_ok=True)
    regions = pick_regions()
    log(f"zoom regions (x, y): {regions}")
    small = lambda text: label(text).replace("fontsize=40", "fontsize=28").replace("x=24:y=24", "x=14:y=14")
    sources = {"original": (REFERENCE, "Original"), "source": (SOURCE, "Source (pixels as delivered)"),
               **{n: (p, LABELS[n]) for n, p in ready.items()}}
    encode = ["-c:v", "libx264", "-crf", "12", "-preset", "slow", "-pix_fmt", "yuv420p"]

    def panels(index, name, text):
        pre = f"[{index}:v]" + (f"scale={REF_W}:{REF_H}:flags=neighbor," if sources[name][0] == SOURCE else "")
        chains = [f"{pre}split={len(regions)}" + "".join(f"[{name}{i}in]" for i in range(len(regions)))]
        for i, (x, y) in enumerate(regions):
            chains.append(f"[{name}{i}in]crop={ZOOM_W}:{ZOOM_H}:{x}:{y},"
                          f"scale={ZOOM_W * ZOOM}:{ZOOM_H * ZOOM}:flags=neighbor,{small(text)}[{name}{i}]")
        return chains

    for name, (path, text) in sources.items():
        rows = "".join(f"[{name}{i}]" for i in range(len(regions)))
        run([ff, "-hide_banner", "-y", "-i", path, "-filter_complex",
             ";".join(panels(0, name, text)) + f";{rows}vstack=inputs={len(regions)}[v]",
             "-map", "[v]", *encode, zdir / f"{name}.mp4"])
    for name in ready:
        if name == "bicubic":
            continue
        chains = panels(0, "bicubic", LABELS["bicubic"]) + panels(1, name, LABELS[name])
        chains += [f"[bicubic{i}][{name}{i}]hstack[row{i}]" for i in range(len(regions))]
        rows = "".join(f"[row{i}]" for i in range(len(regions)))
        run([ff, "-hide_banner", "-y", "-i", ready["bicubic"], "-i", ready[name], "-filter_complex",
             ";".join(chains) + f";{rows}vstack=inputs={len(regions)}[v]", "-map", "[v]", *encode,
             zdir / f"bicubic-vs-{name}.mp4"])
    boxes = ",".join(f"drawbox=x={x}:y={y}:w={ZOOM_W}:h={ZOOM_H}:color=0x76b900:t=6" for x, y in regions)
    run([ff, "-hide_banner", "-y", "-ss", "10", "-i", REFERENCE, "-vf", boxes, "-frames:v", "1", zdir / "regions.png"])
    return regions


def compare():
    ff = tool("ffmpeg")
    ready = {k: v for k, v in VARIANTS.items() if v.exists()}
    if len(ready) < 2:
        sys.exit("need at least two variants - run bicubic and rtx-vsr first")
    font = "C\\:/Windows/Fonts/arialbd.ttf"
    label = lambda text: (f"drawtext=fontfile='{font}':text='{text}':x=24:y=24:fontsize=40:"
                          "fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=12")

    rows = []
    for name, path in ready.items():
        p, s = metrics(path)
        rows.append((name, p, s))
        log(f"{name}: PSNR {p} dB, SSIM {s}")

    a, b = "bicubic", "rtx-vsr"
    half = REF_W // 2
    # split screen: left half bicubic | right half RTX VSR, same frame
    run([ff, "-hide_banner", "-y", "-i", ready[a], "-i", ready[b], "-filter_complex",
         f"[0:v]crop={half}:{REF_H}:0:0,{label(LABELS[a])}[l];"
         f"[1:v]crop={half}:{REF_H}:{half}:0,{label(LABELS[b])}[r];"
         f"[l][r]hstack,drawbox=x={half - 1}:y=0:w=2:h={REF_H}:color=white@0.8:t=fill[v]",
         "-map", "[v]", "-c:v", "libx264", "-crf", "12", "-preset", "slow", "-pix_fmt", "yuv420p", OUT / "split-bicubic-vs-rtx-vsr.mp4"])
    # each variant as its own watchable file
    for name, path in ready.items():
        run([ff, "-hide_banner", "-y", "-i", path, "-vf", label(LABELS[name]),
             "-c:v", "libx264", "-crf", "12", "-preset", "slow", "-pix_fmt", "yuv420p", OUT / f"{name}.mp4"])
    # 2x zoomed crops of the centre at three moments: reference | source | variants
    for t in (3, 10, 17):
        inputs = [REFERENCE, SOURCE, *ready.values()]
        names = ["Original", "Source", *[LABELS[n] for n in ready]]
        args, chains = [], []
        for i, (p, n) in enumerate(zip(inputs, names)):
            args += ["-ss", str(t), "-i", p]
            pre = f"scale={REF_W}:{REF_H}:flags=neighbor," if p == SOURCE else ""
            chains.append(f"[{i}:v]{pre}crop=480:400:{REF_W // 2 - 240}:{REF_H // 2 - 200},"
                          f"scale=960:800:flags=neighbor,{label(n)}[c{i}]")
        stack = "".join(f"[c{i}]" for i in range(len(inputs)))
        run([ff, "-hide_banner", "-y", *args, "-filter_complex",
             ";".join(chains) + f";{stack}hstack=inputs={len(inputs)}[v]",
             "-map", "[v]", "-frames:v", "1", OUT / f"crop-{t:02d}s.png"])
    regions = zoom_videos(ready, label)

    lines = ["# Bench results", "",
             f"Clip: Tears of Steel {CLIP_START}s+{CLIP_SECONDS}s, reference {REF_W}x{REF_H}, "
             f"source {SRC_W}x{SRC_H} H.264 {SRC_BITRATE}bit/s, upscale x{SCALE}.", "",
             "| Variant | PSNR (dB) | SSIM |", "|---|---|---|"]
    lines += [f"| {LABELS[n]} | {p:.2f} | {s:.4f} |" for n, p, s in rows]
    lines += ["", "Higher is closer to the clean original. Metrics don't capture everything - watch the videos.", "",
              f"Zoom videos (`zoom/`): the same {len(regions)} most detailed {ZOOM_W}x{ZOOM_H} regions "
              f"{regions}, magnified {ZOOM}x, in every variant - `zoom/<variant>.mp4` to flip between, "
              "`zoom/bicubic-vs-<variant>.mp4` side by side, `zoom/regions.png` shows where they are."]
    (OUT / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"done -> {OUT / 'results.md'}")


STEPS = {"setup": setup, "source": source, "bicubic": bicubic, "rtx-vsr": rtx_vsr, "vfx": vfx, "liveaction-span": liveaction_span, "compare": compare}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=[*STEPS, "all"])
    step = ap.parse_args().step
    for name in (STEPS if step == "all" else [step]):
        STEPS[name]()
