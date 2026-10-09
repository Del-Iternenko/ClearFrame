"""Builds training pairs for ClearFrame's own network: (what a bad stream looks like on screen, clean frame).

Clean footage (CC-BY, see train/README.md) is cut into short segments. Each segment is degraded the way
streaming degrades video: downscaled to a random rung of the ladder (144p ... 720p, or kept at 1080p),
sometimes given film grain, encoded with a real codec at low quality (H.264 / H.265 / MPEG-4: blocks,
smear, banding in dark gradients), decoded and stretched back to screen size like a browser does.
Frames are taken at the same positions from the clean and the degraded segment.

    .venv/Scripts/python train/make_data.py [--segments 470] [--workers 3]

Output: train/data/pairs/<segment>_<frame>_lq.png (input) and _gt.png (target), plus pairs.json.
"""
import argparse
import json
import random
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FFMPEG = str(ROOT / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe")
DATA = ROOT / "train" / "data"
PAIRS = DATA / "pairs"

# Netflix Open Content masters are HDR (PQ, P3) H.264 without colour tags: tag them, tone-map to SDR BT.709
TONEMAP = ("setparams=color_primaries=smpte432:color_trc=smpte2084:colorspace=bt709:range=tv,"
           "zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=hable:desat=0,"
           "zscale=t=bt709:m=bt709:r=tv,format=yuv420p,")
SOURCES = {
    # name: (file, duration s, needs tone mapping, share of segments, excluded range)
    "chimera": (DATA / "clean" / "Chimera_DCI4k2398p_HDR_P3PQ.mp4", 372, True, 0.28, None),
    "meridian": (DATA / "clean" / "Meridian_UHD4k5994_HDR_P3PQ.mp4", 718, True, 0.40, None),
    # the bench clip (98 s + 20 s) is the test set: never train on it
    "tears": (ROOT / "bench" / "cache" / "tears_of_steel_1080p.mov", 734, False, 0.32, (90, 125)),
}
SEG_SECONDS = 2.0
FRAMES = (5, 17, 29, 41)                # frames taken from each segment (not only the first, intra frame)
LADDER = [(144, 1), (240, 2), (360, 3), (480, 3), (540, 2), (720, 3), (1080, 2)]   # (height, weight)


def say(text):
    """Progress to the console window and to train/data/make_data.log."""
    print(text, flush=True)
    with open(DATA / "make_data.log", "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + text + "\n")


def run(args):
    res = subprocess.run(args, capture_output=True, text=True, errors="replace")
    if res.returncode:
        raise RuntimeError(res.stderr[-1500:])


def pick_degradation(rng):
    height = rng.choices([h for h, _ in LADDER], [w for _, w in LADDER])[0]
    codec = rng.choices(["h264", "hevc", "mpeg4"], [6, 3, 1])[0]
    # lower rungs are usually less starved (bitrate ladders), the top rung gets the harshest crf
    if codec == "h264":
        q = rng.randint(26, 40) if height >= 720 else rng.randint(22, 36)
    elif codec == "hevc":
        q = rng.randint(30, 42) if height >= 720 else rng.randint(26, 38)
    else:
        q = rng.randint(5, 14)          # mpeg4 qscale: old DivX/Xvid rips
    return {
        "height": height, "codec": codec, "q": q,
        "down": rng.choice(["bicubic", "bilinear", "area", "lanczos"]),
        "up": rng.choice(["bicubic", "bilinear", "lanczos"]),
        "grain": rng.randint(4, 14) if rng.random() < 0.3 else 0,
        "preset": rng.choice(["veryfast", "fast", "medium"]),
        "aq_off": rng.random() < 0.3,   # adaptive quantisation off: more banding in dark gradients
    }


def make_segment(job):
    seg_id, src, start, rng_seed = job
    rng = random.Random(rng_seed)
    path, _, tonemap, _, _ = SOURCES[src]
    d = pick_degradation(rng)
    tmp = Path(tempfile.mkdtemp(prefix="cf-seg-"))
    try:
        clean = tmp / "clean.mkv"
        # clean 1080p-class master of the segment (4K sources downscaled with lanczos: very clean)
        run([FFMPEG, "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", str(SEG_SECONDS), "-i", str(path),
             "-vf", (TONEMAP if tonemap else "") + "scale=1920:-2:flags=lanczos,crop=1920:trunc(ih/16)*16",
             "-an", "-c:v", "ffv1", "-pix_fmt", "yuv420p", str(clean)])
        # degraded stream
        enc = tmp / ("deg.mkv" if d["codec"] != "mpeg4" else "deg.avi")
        vf = f"scale=-2:{d['height']}:flags={d['down']}"
        if d["grain"]:
            vf += f",noise=alls={d['grain']}:allf=t"
        if d["codec"] == "h264":
            codec = ["-c:v", "libx264", "-crf", str(d["q"]), "-preset", d["preset"]]
            if d["aq_off"]:
                codec += ["-aq-mode", "0"]
        elif d["codec"] == "hevc":
            codec = ["-c:v", "libx265", "-crf", str(d["q"]), "-preset", d["preset"], "-x265-params", "log-level=error"]
        else:
            codec = ["-c:v", "mpeg4", "-q:v", str(d["q"])]
        run([FFMPEG, "-v", "error", "-y", "-i", str(clean), "-vf", vf, *codec, "-pix_fmt", "yuv420p", str(enc)])
        select = "select='" + "+".join(f"eq(n\\,{n})" for n in FRAMES) + "'"
        out = []
        for kind, src_file, extra in (("gt", clean, ""), ("lq", enc, None)):
            if extra is None:   # stretch back to the clean frame size, like the browser does
                w, h = probe_size(clean)
                extra = f"scale={w}:{h}:flags={d['up']},"
            pattern = tmp / f"{kind}_%d.png"
            run([FFMPEG, "-v", "error", "-y", "-i", str(src_file), "-vf", extra + select, "-fps_mode", "passthrough",
                 "-pix_fmt", "rgb24", str(pattern)])
            out.append(sorted(tmp.glob(f"{kind}_*.png"), key=lambda p: int(p.stem.split("_")[1])))
        gts, lqs = out
        if len(gts) != len(FRAMES) or len(lqs) != len(FRAMES):
            raise RuntimeError(f"got {len(gts)} clean / {len(lqs)} degraded frames")
        names = []
        for i, (g, l) in enumerate(zip(gts, lqs)):
            if flat(g):
                continue        # black frames, fades, title cards teach nothing (dark frames with detail stay)
            base = f"{seg_id:04d}_{i}"
            shutil.move(str(g), PAIRS / f"{base}_gt.png")
            shutil.move(str(l), PAIRS / f"{base}_lq.png")
            names.append(base)
        return {"segment": seg_id, "source": src, "start": round(start, 2), **d, "pairs": names}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def flat(png):
    """True for (nearly) uniform frames: luma spread under ~2% of the range."""
    from PIL import Image, ImageStat
    return ImageStat.Stat(Image.open(png).convert("L").reduce(4)).stddev[0] < 5


def probe_size(video):
    out = subprocess.run([FFMPEG.replace("ffmpeg.exe", "ffprobe.exe"), "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True).stdout.strip()
    w, h = map(int, out.split(","))
    return w, h


def plan(total, seed):
    rng = random.Random(seed)
    jobs = []
    for src, (path, dur, _, share, excl) in SOURCES.items():
        if not path.exists():
            sys.exit(f"missing {path}")
        n = round(total * share)
        # spread segments evenly over the film, with jitter, skipping the excluded range
        starts = []
        for i in range(n * 3):
            if len(starts) == n:
                break
            t = (i / (n * 3)) * (dur - SEG_SECONDS - 1) + rng.uniform(0, 1)
            if excl and excl[0] - SEG_SECONDS <= t <= excl[1]:
                continue
            if i % 3 == 0 or rng.random() < 0.1:
                starts.append(t)
        jobs += [(src, t) for t in starts]
    rng.shuffle(jobs)
    return [(i, src, t, rng.randrange(1 << 30)) for i, (src, t) in enumerate(jobs)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--segments", type=int, default=470)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    PAIRS.mkdir(parents=True, exist_ok=True)
    meta_path = DATA / "pairs.json"
    done = {m["segment"]: m for m in json.loads(meta_path.read_text())} if meta_path.exists() else {}
    jobs = [j for j in plan(args.segments, args.seed) if j[0] not in done]
    say(f"{len(done)} segments already built, {len(jobs)} to go, {args.workers} workers")
    t0, finished, failed = time.time(), 0, 0
    with ThreadPoolExecutor(args.workers) as pool:
        for job, fut in [(j, pool.submit(make_segment, j)) for j in jobs]:
            try:
                m = fut.result()
                done[m["segment"]] = m
                finished += 1
            except Exception as e:
                failed += 1
                say(f"segment {job[0]} ({job[1]} @ {job[2]:.1f}s) failed: {e}")
            if (finished + failed) % 5 == 0 or finished + failed == len(jobs):
                meta_path.write_text(json.dumps(sorted(done.values(), key=lambda m: m["segment"]), indent=1))
                el = time.time() - t0
                left = el / max(1, finished + failed) * (len(jobs) - finished - failed)
                say(f"[{finished + failed}/{len(jobs)}] {len(done) * len(FRAMES)} pairs, "
                      f"{el / 60:.1f} min elapsed, ~{left / 60:.0f} min left")
    meta_path.write_text(json.dumps(sorted(done.values(), key=lambda m: m["segment"]), indent=1))
    say(f"done: {len(done) * len(FRAMES)} pairs in {PAIRS}")


if __name__ == "__main__":
    main()
