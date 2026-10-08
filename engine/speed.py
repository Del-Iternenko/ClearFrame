"""Measure how fast each architecture runs on this GPU with TensorRT (FP16, Tensor Cores).

Speed does not depend on trained weights, so random weights are fine. For every
model and input size: export ONNX (FP16) -> build a strongly typed TensorRT engine
-> time inference with CUDA events. PyTorch FP16 eager is timed as a reference.

    .venv/Scripts/python engine/speed.py                      # all models, default sizes
    .venv/Scripts/python engine/speed.py --models span-48x6 --sizes 1280x720

Results: engine/out/speed.md (+ speed.json). Engines are cached in engine/out/engines/.
"""
import argparse
import json
import time
from pathlib import Path

import tensorrt as trt
import torch

from archs import MODELS, count_params

OUT = Path(__file__).resolve().parent / "out"
ENGINES = OUT / "engines"
SIZES = {"480p": (854, 480), "bench": (960, 400), "720p": (1280, 720)}
LOGGER = trt.Logger(trt.Logger.WARNING)


def export_onnx(model, w, h, path):
    x = torch.rand(1, 3, h, w, device="cuda", dtype=torch.half)
    torch.onnx.export(model, (x,), str(path), input_names=["input"], output_names=["output"],
                      opset_version=17, dynamo=False)


def build_engine(onnx_path, engine_path):
    if engine_path.exists():
        return engine_path.read_bytes()
    builder = trt.Builder(LOGGER)
    # strongly typed: precision comes from the ONNX graph (FP16), runs on Tensor Cores
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.STRONGLY_TYPED))
    parser = trt.OnnxParser(network, LOGGER)
    if not parser.parse(onnx_path.read_bytes()):
        raise RuntimeError("; ".join(str(parser.get_error(i)) for i in range(parser.num_errors)))
    config = builder.create_builder_config()
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, 1 << 30)
    data = builder.build_serialized_network(network, config)
    if data is None:
        raise RuntimeError("engine build failed")
    engine_path.write_bytes(bytes(data))
    return bytes(data)


def time_cuda(fn, warmup=10, iters=60):
    for _ in range(warmup):
        fn()
    torch.cuda.synchronize()
    start, end = torch.cuda.Event(enable_timing=True), torch.cuda.Event(enable_timing=True)
    start.record()
    for _ in range(iters):
        fn()
    end.record()
    torch.cuda.synchronize()
    return start.elapsed_time(end) / iters  # ms per frame


def bench_trt(engine_bytes, w, h, scale):
    runtime = trt.Runtime(LOGGER)
    engine = runtime.deserialize_cuda_engine(engine_bytes)
    ctx = engine.create_execution_context()
    x = torch.rand(1, 3, h, w, device="cuda", dtype=torch.half)
    y = torch.empty(1, 3, h * scale, w * scale, device="cuda", dtype=torch.half)
    ctx.set_tensor_address("input", x.data_ptr())
    ctx.set_tensor_address("output", y.data_ptr())
    stream = torch.cuda.current_stream().cuda_stream
    return time_cuda(lambda: ctx.execute_async_v3(stream))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="*", default=list(MODELS))
    ap.add_argument("--sizes", nargs="*", default=list(SIZES))
    ap.add_argument("--scale", type=int, default=2)
    args = ap.parse_args()
    ENGINES.mkdir(parents=True, exist_ok=True)
    gpu = torch.cuda.get_device_name(0)
    torch.backends.cudnn.benchmark = True
    rows = []
    for name in args.models:
        model = MODELS[name](args.scale).cuda().half().eval()
        params = count_params(model)
        for size in args.sizes:
            w, h = SIZES[size] if size in SIZES else map(int, size.split("x"))
            tag = f"{name}_x{args.scale}_{w}x{h}"
            onnx_path, engine_path = ENGINES / f"{tag}.onnx", ENGINES / f"{tag}.plan"
            t0 = time.time()
            with torch.no_grad():
                if not onnx_path.exists():
                    export_onnx(model, w, h, onnx_path)
                engine = build_engine(onnx_path, engine_path)
                build_s = time.time() - t0
                x = torch.rand(1, 3, h, w, device="cuda", dtype=torch.half)
                eager_ms = time_cuda(lambda: model(x), warmup=5, iters=20)
            trt_ms = bench_trt(engine, w, h, args.scale)
            row = dict(model=name, params=params, input=f"{w}x{h}", output=f"{w * args.scale}x{h * args.scale}",
                       trt_ms=round(trt_ms, 2), trt_fps=round(1000 / trt_ms, 1), torch_ms=round(eager_ms, 2),
                       build_s=round(build_s, 1))
            rows.append(row)
            print(f"{name:15} {w}x{h} -> x{args.scale}: TensorRT {trt_ms:6.2f} ms ({1000 / trt_ms:6.1f} fps) | "
                  f"PyTorch {eager_ms:6.2f} ms | {params / 1e3:.0f}k params", flush=True)
            torch.cuda.empty_cache()

    (OUT / "speed.json").write_text(json.dumps(dict(gpu=gpu, trt=trt.__version__, torch=torch.__version__, rows=rows), indent=2))
    lines = [f"# Inference speed - {gpu}", "",
             f"TensorRT {trt.__version__}, FP16 strongly typed; PyTorch {torch.__version__} FP16 eager for reference. "
             "Network time only (no decode, color conversion or display). Real-time needs < 41.7 ms at 24 fps, < 33.3 ms at 30 fps.", "",
             "| Model | Params | Input | Output | TensorRT ms | TensorRT fps | PyTorch ms | 24 fps? |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        ok = "yes" if r["trt_ms"] < 1000 / 24 * 0.8 else ("tight" if r["trt_ms"] < 1000 / 24 else "no")
        lines.append(f"| {r['model']} | {r['params'] / 1e3:.0f}k | {r['input']} | {r['output']} | {r['trt_ms']} | {r['trt_fps']} | {r['torch_ms']} | {ok} |")
    lines += ["", "'yes' = under 80% of the 24 fps frame budget, leaving room for decoding and display."]
    (OUT / "speed.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nsaved {OUT / 'speed.md'}")


if __name__ == "__main__":
    main()
