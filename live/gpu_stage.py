"""ClearFrame's network on live frames, on the GPU (used by live_upscale.Output).

Captured BGRA frame (numpy, the video exactly as it is shown on screen) -> CUDA -> the network
(engine/neural.py, TensorRT FP16) at the same size -> RGB24 bytes for mpv. The frame is never scaled
down first: the network restores what is on screen, whatever the stream's real resolution was.
The work runs in a worker thread on the newest frame only, so a frame that hasn't changed is never
processed twice.
"""
import sys
import threading
import time
from pathlib import Path

import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import neural  # noqa: E402

_cache = {}   # loaded networks by strength, shared by every Output of this session process
stats = {"frames": 0, "ms": 0.0}   # frames through the network in this process, mean time per frame


def prepare(strength):
    """Load the network now (the engine for this strength is built on first use, about a minute)."""
    if strength not in _cache:
        _cache[strength] = neural.Restorer(strength)
    return _cache[strength]


class GpuStage:
    def __init__(self, w, h, strength):
        # the network needs even sizes and has a size limit: fit the frame into it (rarely needed)
        k = min(1.0, neural.MAX_W / w, neural.MAX_H / h)
        self.in_w, self.in_h = int(w * k) // 2 * 2, int(h * k) // 2 * 2
        self.out_w, self.out_h = self.in_w, self.in_h
        self.net = prepare(strength)
        self.net.prepare(self.in_w, self.in_h)
        self.label = f"ClearFrame Neural · {round(strength * 100)}% detail"
        self.ms = 0.0
        self.pending, self.last_in, self.result = None, None, None
        self.wake = threading.Event()
        self.stopped = False
        threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, bgra):
        """Called at the output rate; only a frame that is new gets processed."""
        if bgra is not self.last_in:
            self.last_in = bgra
            self.pending = bgra
            self.wake.set()

    def _worker(self):
        torch.cuda.set_device(0)
        while not self.stopped:
            self.wake.wait(0.5)
            self.wake.clear()
            frame, self.pending = self.pending, None
            if frame is None or self.stopped:
                continue
            t0 = time.perf_counter()
            with torch.inference_mode():
                x = torch.from_numpy(frame).cuda(non_blocking=True)          # H x W x 4, BGRA
                x = x[..., [2, 1, 0]].permute(2, 0, 1).float().div_(255)     # 3 x H x W, RGB
                if x.shape[1:] != (self.in_h, self.in_w):
                    x = F.interpolate(x[None], size=(self.in_h, self.in_w), mode="bilinear",
                                      antialias=True, align_corners=False)[0]
                y = self.net(x.contiguous())
                y = y.clamp_(0, 1).mul_(255).round_().to(torch.uint8).permute(1, 2, 0).contiguous()
                self.result = y.cpu().numpy().tobytes()
            ms = (time.perf_counter() - t0) * 1000
            self.ms = ms if not self.ms else self.ms * 0.9 + ms * 0.1
            stats["ms"] = (stats["ms"] * stats["frames"] + ms) / (stats["frames"] + 1)
            stats["frames"] += 1

    def close(self):
        self.stopped = True
        self.wake.set()
