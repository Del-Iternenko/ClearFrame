"""Estimate the real height of a video that a player or browser scaled up to the
window (same idea as the 'getnative' tool used by video encoders).

For a candidate source height h, the on-screen picture should be (almost) exactly
A_h @ S for some h-row image S, where A_h is the vertical bilinear-resize matrix.
Projecting the picture onto the column space of A_h leaves a tiny residual only at
the true height; around it the residual is clearly larger. We look for that dip.

    .venv/Scripts/python live/native_res.py <image>
"""
import sys

import numpy as np
from PIL import Image


def resize_matrix(src, dst):
    """dst x src matrix of bilinear interpolation with pixel-centre alignment."""
    a = np.zeros((dst, src), np.float64)
    scale = src / dst
    for i in range(dst):
        x = (i + 0.5) * scale - 0.5
        x0 = int(np.floor(x))
        t = x - x0
        for k, wgt in ((x0, 1 - t), (x0 + 1, t)):
            a[i, min(max(k, 0), src - 1)] += wgt
    return a


COMMON_WIDTHS = (426, 480, 640, 720, 768, 854, 960, 1024, 1280, 1366, 1440, 1600, 1920)
NEIGHBOURS = (-12, -8, -5, 0, 5, 8, 12)


def residual(u, h, H, norm):
    q, _ = np.linalg.qr(resize_matrix(h, H))
    return np.linalg.norm(u - q @ (q.T @ u)) / norm


def estimate(gray, dip=0.62, columns=256):
    """Native height, or None if the picture looks native at its current size.
    Only plausible source sizes are tested: standard video widths at the picture's
    aspect ratio, each against a few neighbouring heights."""
    H, W = gray.shape
    step = max(1, W // columns)
    u = gray[:, ::step].astype(np.float64)
    u -= u.mean(axis=0)
    norm = np.linalg.norm(u) or 1.0
    cache = {}

    def r(h):
        if h not in cache:
            cache[h] = residual(u, h, H, norm)
        return cache[h]

    best, best_score = None, 1.0
    for cw in COMMON_WIDTHS:
        h = round(cw * H / W)
        if not 0.3 * H <= h <= 0.95 * H:
            continue
        around = [r(h + d) for d in NEIGHBOURS if d and 16 <= h + d < H]
        score = r(h) / np.median(around)
        if score < best_score:
            best, best_score = h, score
    return (best if best_score < dip else None), best_score


if __name__ == "__main__":
    gray = np.asarray(Image.open(sys.argv[1]).convert("L"))
    h, score = estimate(gray)
    print(f"{gray.shape[1]}x{gray.shape[0]}: native height {h} (dip score {score:.2f})")
