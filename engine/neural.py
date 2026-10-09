"""ClearFrame's own network at run time, as an FP16 TensorRT engine.

    net = Restorer(strength=0.5)        # 0 = clean only ... 1 = full detail
    y = net(x)                          # x: 3xHxW float CUDA tensor in [0, 1] (the frame as shown) -> same size

Two weight sets come out of training (train/train.py): "clean" (removes blocks, smear, banding) and
"detail" (also draws plausible texture). Same architecture, so they are blended weight by weight;
`strength` picks the mix. Each mix gets its own engine, built once (about a minute) and cached.

Weights are looked up in %LOCALAPPDATA%\\ClearFrame\\models (clearframe-clean.pt, clearframe-detail.pt),
then in the newest training run (train/runs/<name>/clean.pt, detail.pt).

    .venv/Scripts/python engine/neural.py [--strength 0.5] [--sizes 1920x1080 1920x800]   # build + time
"""
import argparse
import hashlib
import io
import os
import sys
import time
from pathlib import Path

import tensorrt as trt
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from archs import ClearFrameNet  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.environ.get("LOCALAPPDATA", ".")) / "ClearFrame" / "models"
RUNS = ROOT / "train" / "runs"
MAX_W, MAX_H = 2560, 1440          # one engine covers every frame size up to this
OPT_W, OPT_H = 1920, 1080
LOGGER = trt.Logger(trt.Logger.WARNING)


def weight_files():
    """(clean.pt, detail.pt or None) - installed model first, else the newest training run."""
    clean, detail = HOME / "clearframe-clean.pt", HOME / "clearframe-detail.pt"
    if clean.exists():
        return clean, detail if detail.exists() else None
    runs = sorted((p for p in RUNS.glob("*/clean.pt")), key=lambda p: p.stat().st_mtime, reverse=True) \
        if RUNS.exists() else []
    if runs:
        d = runs[0].with_name("detail.pt")
        return runs[0], d if d.exists() else None
    return None, None


def available():
    return weight_files()[0] is not None


def blended(strength):
    """ClearFrameNet with clean/detail weights mixed: (1 - strength) * clean + strength * detail."""
    clean_path, detail_path = weight_files()
    if clean_path is None:
        raise FileNotFoundError("no ClearFrame model yet (train one with train/train.py)")
    ck = torch.load(clean_path, map_location="cpu", weights_only=True)
    state = ck["net"]
    if detail_path is not None and strength > 0:
        d = torch.load(detail_path, map_location="cpu", weights_only=True)["net"]
        state = {k: (1 - strength) * v + strength * d[k] if v.is_floating_point() else v for k, v in state.items()}
    net = ClearFrameNet(1, ck["config"]["features"], ck["config"]["blocks"])
    net.load_state_dict(state)
    return net


def _engine(strength):
    net = blended(strength).cuda().half().eval()
    buf = io.BytesIO()
    torch.save(net.state_dict(), buf)
    gpu = torch.cuda.get_device_name().replace(" ", "")
    tag = hashlib.sha1(buf.getvalue() + f"{gpu}|{trt.__version__}|{MAX_W}x{MAX_H}".encode()).hexdigest()[:12]
    path = HOME / "engines" / f"clearframe-{tag}.engine"
    if path.exists():
        return path.read_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    onnx_path = path.with_suffix(".onnx")
    x = torch.rand(1, 3, 64, 64, device="cuda", dtype=torch.half)
    with torch.no_grad():
        torch.onnx.export(net, (x,), str(onnx_path), input_names=["input"], output_names=["output"], opset_version=17,
                          dynamic_axes={"input": {2: "h", 3: "w"}, "output": {2: "h", 3: "w"}}, dynamo=False)
    builder = trt.Builder(LOGGER)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED))
    parser = trt.OnnxParser(network, LOGGER)
    if not parser.parse(onnx_path.read_bytes()):
        raise RuntimeError("; ".join(str(parser.get_error(i)) for i in range(parser.num_errors)))
    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 1 << 30)
    profile = builder.create_optimization_profile()
    profile.set_shape("input", (1, 3, 64, 64), (1, 3, OPT_H, OPT_W), (1, 3, MAX_H, MAX_W))
    config.add_optimization_profile(profile)
    data = builder.build_serialized_network(network, config)
    onnx_path.unlink(missing_ok=True)
    if data is None:
        raise RuntimeError("TensorRT engine build failed")
    path.write_bytes(bytes(data))
    return bytes(data)


class Restorer:
    """Callable: 3xHxW float CUDA tensor in [0, 1] -> restored 3xHxW (H, W even, at most MAX_W x MAX_H)."""

    def __init__(self, strength=0.5):
        self.strength = strength
        self.stream = torch.cuda.Stream()
        self.runtime = trt.Runtime(LOGGER)
        self.engine = self.runtime.deserialize_cuda_engine(_engine(strength))
        self.ctx = self.engine.create_execution_context()
        self.size = None

    def prepare(self, w, h):
        if self.size == (w, h):
            return
        if w > MAX_W or h > MAX_H or w % 2 or h % 2:
            raise ValueError(f"frame {w}x{h}: needs even sizes up to {MAX_W}x{MAX_H}")
        self.ctx.set_input_shape("input", (1, 3, h, w))
        self.inp = torch.empty(1, 3, h, w, device="cuda", dtype=torch.half)
        self.out = torch.empty(1, 3, h, w, device="cuda", dtype=torch.half)
        self.ctx.set_tensor_address("input", self.inp.data_ptr())
        self.ctx.set_tensor_address("output", self.out.data_ptr())
        self.size = (w, h)

    def __call__(self, x):
        _, h, w = x.shape
        self.prepare(w, h)
        cur = torch.cuda.current_stream()
        self.stream.wait_stream(cur)
        with torch.cuda.stream(self.stream):
            self.inp[0].copy_(x)
            self.ctx.execute_async_v3(self.stream.cuda_stream)
            y = self.out[0].float()
        cur.wait_stream(self.stream)
        return y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strength", type=float, default=0.5)
    ap.add_argument("--sizes", nargs="*", default=["1920x1080", "1920x800", "1280x720"])
    args = ap.parse_args()
    print("weights:", *weight_files())
    t0 = time.time()
    net = Restorer(args.strength)
    print(f"engine ready in {time.time() - t0:.0f}s")
    for size in args.sizes:
        w, h = map(int, size.split("x"))
        x = torch.rand(3, h, w, device="cuda")
        for _ in range(10):
            net(x)
        torch.cuda.synchronize()
        t1 = time.perf_counter()
        for _ in range(50):
            net(x)
        torch.cuda.synchronize()
        ms = (time.perf_counter() - t1) / 50 * 1000
        print(f"{w}x{h}: {ms:.1f} ms/frame ({1000 / ms:.0f} fps)")


if __name__ == "__main__":
    main()
