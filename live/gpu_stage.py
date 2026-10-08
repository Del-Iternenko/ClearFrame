"""Neural upscaling of live frames on the GPU (used by live_upscale.Output).

Captured BGRA frame (numpy) -> CUDA -> scaled to the network's input size -> network:
    "neural": ClearFrame's default network (engine/neural.py, TensorRT FP16), 2x;
              inputs that don't fit it go to NVIDIA VFX when installed, else are scaled down to fit
    "nvvfx":  NVIDIA Video Effects VideoSuperRes (pip install nvidia-vfx; user-installed, not bundled)
-> RGB24 bytes for mpv. The heavy work runs in a worker thread on the newest frame only, so
frames that haven't changed are never processed twice.
"""
import sys
import threading
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "engine"))
import neural  # noqa: E402

VFX_QUALITY = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "ultra": "ULTRA"}
_cache = {}   # loaded networks, shared by every Output of this session process
stats = {"frames": 0, "ms": 0.0}   # frames through the network in this process, mean time per frame


def vfx_available():
    try:
        import nvvfx  # noqa: F401
        return True
    except Exception:
        return False


def _span():
    if "span" not in _cache:
        _cache["span"] = neural.NeuralUpscaler("liveaction-span")
    return _cache["span"]


def prepare():
    """Load the default network now (weights download + engine build on first use)."""
    _span()


class GpuStage:
    def __init__(self, engine, in_w, in_h, target_w, target_h, tuning):
        """in_w x in_h: the video's real size; target: the size it is shown at."""
        quality = VFX_QUALITY.get(tuning.get("quality", "high"), "HIGH")
        # artifact reduction off -> NVIDIA's high-bitrate modes (keep everything), on -> standard modes
        if tuning.get("artifact_reduction", "strong") == "off":
            quality = "HIGHBITRATE_" + quality
        fits = in_w <= neural.MAX_W and in_h <= neural.MAX_H
        if engine == "neural" and not fits and vfx_available():
            engine = "nvvfx"
        if engine == "neural" and not fits:
            k = min(neural.MAX_W / in_w, neural.MAX_H / in_h)
            in_w, in_h = int(in_w * k) // 2 * 2, int(in_h * k) // 2 * 2
        self.engine, self.in_w, self.in_h = engine, in_w, in_h
        if engine == "neural":
            self.net = _span()
            self.net.prepare(in_w, in_h)
            self.out_w, self.out_h = in_w * 2, in_h * 2
            self.label = "ClearFrame Neural x2"
        else:
            from nvvfx import VideoSuperRes
            # VFX goes straight to the shown size (up to 4x)
            k = max(1.0, min(4.0, target_h / in_h))
            self.out_w, self.out_h = int(in_w * k) // 2 * 2, int(in_h * k) // 2 * 2
            self.vfx = VideoSuperRes(quality=VideoSuperRes.QualityLevel[quality])
            self.vfx.output_width, self.vfx.output_height = self.out_w, self.out_h
            self.vfx.load()
            self.label = f"NVIDIA VFX {quality.title()} x{k:.2f}"
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
                x = x.contiguous()
                if self.engine == "neural":
                    y = self.net(x)
                else:
                    y = torch.from_dlpack(self.vfx.run(x).image).clone()
                y = y.clamp_(0, 1).mul_(255).round_().to(torch.uint8).permute(1, 2, 0).contiguous()
                self.result = y.cpu().numpy().tobytes()
            ms = (time.perf_counter() - t0) * 1000
            self.ms = ms if not self.ms else self.ms * 0.9 + ms * 0.1
            stats["ms"] = (stats["ms"] * stats["frames"] + ms) / (stats["frames"] + 1)
            stats["frames"] += 1

    def close(self):
        self.stopped = True
        self.wake.set()
        vfx = getattr(self, "vfx", None)
        if vfx is not None:
            time.sleep(0.1)
            try:
                vfx.close()
            except Exception:
                pass
