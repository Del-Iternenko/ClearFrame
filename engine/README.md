# Engine

Compact super-resolution networks and tools to run them with TensorRT on RTX Tensor Cores.

- `archs.py` — Compact (VGG-style, PReLU, pixel shuffle) and SPAN (parameter-free attention) in inference form.
- `speed.py` — exports each network to ONNX (FP16), builds a strongly typed TensorRT engine for this GPU and times it. Speed doesn't depend on weights, so it runs on untrained networks.

```
python -m venv .venv
.venv/Scripts/pip install torch --index-url https://download.pytorch.org/whl/cu130
.venv/Scripts/pip install tensorrt-cu13 onnx onnxscript numpy
cd engine && ../.venv/Scripts/python speed.py --sizes 480p 720p
```

Results on an RTX 4050 Laptop: [docs/speed-results.md](../docs/speed-results.md).
