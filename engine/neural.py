"""Neural upscaler at run time: a super-resolution network as an FP16 TensorRT engine.

    up = NeuralUpscaler("liveaction-span")       # downloads the weights once
    y = up(x)                                    # x: 3xHxW float CUDA tensor in [0, 1] -> 3x2Hx2W

Weights are fetched from the model author on first use (they are not ours to bundle) into
%LOCALAPPDATA%\\ClearFrame\\models. Each input size gets its own engine (fixed shapes run
fastest), built once in about a minute and cached next to the weights.

    .venv/Scripts/python engine/neural.py --sizes 960x400 1280x720     # build + time
"""
import argparse
import hashlib
import os
import time
import urllib.request
from pathlib import Path

import tensorrt as trt
import torch

MODELS = {
    # 2xLiveActionV1_SPAN by jcj83429, CC-BY-NC-SA-4.0: SPAN (48 ch) trained on live action with
    # JPEG / MPEG-4 / H.264 / VP9 / H.265 compression, chroma subsampling, rescaling blur, halos.
    "liveaction-span": {
        "url": "https://raw.githubusercontent.com/jcj83429/upscaling/f73a3a02874360ec6ced18f8bdd8e43b5d7bba57/"
               "2xLiveActionV1_SPAN/2xLiveActionV1_SPAN_490000.pth",
        "file": "2xLiveActionV1_SPAN.pth",
        "scale": 2,
        "credit": "2xLiveActionV1_SPAN by jcj83429 (CC-BY-NC-SA-4.0)",
    },
}
# one engine covers every input size up to this (a live video area can have any size); bigger
# inputs are scaled down to fit before the network
MAX_W, MAX_H = 1024, 576
OPT_W, OPT_H = 960, 540
HOME = Path(os.environ.get("LOCALAPPDATA", ".")) / "ClearFrame" / "models"
LOGGER = trt.Logger(trt.Logger.WARNING)


def weights(name):
    """Path of the model weights, downloaded from the author on first use."""
    spec = MODELS[name]
    path = HOME / spec["file"]
    if not path.exists():
        HOME.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        urllib.request.urlretrieve(spec["url"], tmp)
        tmp.replace(path)
    return path


def _onnx(name):
    """FP16 ONNX with dynamic height/width, exported from the PyTorch weights (via spandrel)."""
    path = HOME / f"{name}.fp16.onnx"
    if not path.exists():
        import spandrel
        net = spandrel.ModelLoader().load_from_file(str(weights(name))).model.cuda().half().eval()
        x = torch.rand(1, 3, 64, 64, device="cuda", dtype=torch.half)
        tmp = path.with_suffix(".part")
        torch.onnx.export(net, (x,), str(tmp), input_names=["input"], output_names=["output"], opset_version=17,
                          dynamic_axes={"input": {2: "h", 3: "w"}, "output": {2: "h2", 3: "w2"}}, dynamo=False)
        tmp.replace(path)
    return path


def _engine(name):
    """Serialized TensorRT engine for inputs up to MAX_W x MAX_H (cached per GPU and TensorRT version)."""
    gpu = torch.cuda.get_device_name().replace(" ", "")
    tag = hashlib.sha1(f"{gpu}|{trt.__version__}".encode()).hexdigest()[:8]
    path = HOME / "engines" / f"{name}-{MAX_W}x{MAX_H}-{tag}.engine"
    if path.exists():
        return path.read_bytes()
    onnx_path = _onnx(name)
    builder = trt.Builder(LOGGER)
    # strongly typed: precision comes from the ONNX graph (FP16) -> Tensor Cores
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
    if data is None:
        raise RuntimeError("TensorRT engine build failed")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))
    return bytes(data)


class NeuralUpscaler:
    """Callable: 3xHxW float CUDA tensor in [0, 1] -> 3x(sH)x(sW) float CUDA tensor."""

    def __init__(self, name="liveaction-span"):
        self.name = name
        self.scale = MODELS[name]["scale"]
        self.stream = torch.cuda.Stream()
        self.runtime = trt.Runtime(LOGGER)
        self.engine = self.runtime.deserialize_cuda_engine(_engine(name))   # ~1 min to build the first time
        self.ctx = self.engine.create_execution_context()
        self.size = None

    def prepare(self, w, h):
        """Buffers for a w x h input (at most MAX_W x MAX_H)."""
        if self.size == (w, h):
            return
        if w > MAX_W or h > MAX_H:
            raise ValueError(f"input {w}x{h} is larger than {MAX_W}x{MAX_H}")
        self.ctx.set_input_shape("input", (1, 3, h, w))
        self.inp = torch.empty(1, 3, h, w, device="cuda", dtype=torch.half)
        self.out = torch.empty(1, 3, h * self.scale, w * self.scale, device="cuda", dtype=torch.half)
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
    ap.add_argument("--model", default="liveaction-span", choices=list(MODELS))
    ap.add_argument("--sizes", nargs="*", default=["960x400", "854x480", "1024x576", "640x360"])
    args = ap.parse_args()
    up = NeuralUpscaler(args.model)
    for size in args.sizes:
        w, h = map(int, size.split("x"))
        t0 = time.time()
        up.prepare(w, h)
        x = torch.rand(3, h, w, device="cuda")
        for _ in range(10):
            up(x)
        torch.cuda.synchronize()
        t1 = time.perf_counter()
        for _ in range(50):
            up(x)
        torch.cuda.synchronize()
        ms = (time.perf_counter() - t1) / 50 * 1000
        print(f"{args.model} {w}x{h} -> {w * up.scale}x{h * up.scale}: {ms:.1f} ms/frame "
              f"({1000 / ms:.0f} fps), engine ready in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
