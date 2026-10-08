# Prior art and how it uses RTX Tensor Cores

Notes from the initial survey (October 2026). Numbers are the projects' own claims unless stated otherwise.

## Ways to run a neural network on Tensor Cores

| Route | Vendor | Notes |
|---|---|---|
| **TensorRT** | NVIDIA only | Fastest. ONNX model → engine built and benchmarked for the exact GPU (first run takes minutes, then cached). FP16/INT8 run on Tensor Cores. |
| ONNX Runtime + TensorRT / CUDA EP | NVIDIA | Easier to embed, usually slower than raw TensorRT. |
| DirectML | Any DX12 GPU | Works on AMD/Intel too; slower. Out of scope for v1 (RTX only). |
| **NVIDIA RTX Video SDK** | NVIDIA | Official way to put RTX Video Super Resolution / artifact reduction / SDR→HDR into your own app. Closed SDK, NVIDIA license — possible optional backend, not the core. |
| Driver RTX VSR via D3D11 video processor | NVIDIA | What mpv exposes as `--vf=d3d11vpp=scaling-mode=nvidia`. Free to use, but a black box with no tuning. Our baseline to beat. |

Plain GLSL/compute shaders (Anime4K, FSR, CuNNy) run on regular shader units, not Tensor Cores.

## Projects

- **[vs-mlrt](https://github.com/AmusementClub/vs-mlrt)** — VapourSynth ML runtimes (TensorRT `vstrt`, ONNX Runtime, OpenVINO, ncnn). Ships wrappers for waifu2x, DPIR, Real-ESRGAN, Real-CUGAN, RIFE, SCUNet. The engine most real-time setups are built on.
- **[mpv-upscale-2x_animejanai](https://github.com/the-database/mpv-upscale-2x_animejanai)** — mpv build that runs Real-ESRGAN *Compact* ONNX models in real time via TensorRT (or DirectML). Three profiles (Compact / UltraCompact / SuperUltraCompact). Recommended GPUs for 1080p→4K: RTX 4090 (quality), RTX 3080+ (balanced), RTX 3060+ (performance). TensorRT engine is built once per model on first play. **Anime only**; authors note weak results on SD sources.
- **[VideoJaNai](https://github.com/the-database/VideoJaNai)** — Windows GUI for offline TensorRT upscaling + RIFE.
- **[Anime4K](https://github.com/bloc97/Anime4K)** — tiny CNNs as mpv GLSL shaders (S/M/L/VL/UL sizes, each doubling compute). Line-art oriented.
- **CuNNy** — small realtime CNN upscaler exported to mpv shaders; some variants use dp4a.
- **[SPAN](https://github.com/hongyuanyu/SPAN)** — Swift Parameter-free Attention Network, an efficient SR architecture. A 2026 benchmark ([arXiv 2602.11339](https://arxiv.org/pdf/2602.11339)) reports ~96 FPS for 2× with TensorRT FP16 on an RTX A6000.
- **[4x-ClearRealityV1](https://openmodeldb.info/models/4x-ClearRealityV1)** — SPAN model for realistic footage (faces, hair, nature); needs fairly clean input, artifacts on grain/bokeh. License listed as CC-BY-NC-SA-4.0 on OpenModelDB (non-commercial) — not shippable by default.
- **[SPAN-ncnn-vulkan](https://github.com/tntwise/SPAN-ncnn-vulkan)** — portable SPAN inference, offline frame-by-frame video.

## Takeaways for ClearFrame

1. **Gap:** fast open models are anime-centric. Live-action + *streaming compression* (low-bitrate H.264, blocking in shadows, banding) is under-served. That is our model's job.
2. **Engine:** don't reinvent inference — mpv + VapourSynth + vs-mlrt/TensorRT is proven for real time.
3. **Budget:** an RTX 4050 Laptop is far below an RTX 3060 desktop, so we need a compact 2× model and to upscale 480/720p → 1440p, not 1080p → 4K.
4. **Licensing:** only redistribute permissively licensed models; our own model solves this.
5. **Baseline to beat:** driver RTX VSR (`d3d11vpp scaling-mode=nvidia`). First real-world feedback from our tester: on heavily compressed web video it looked barely better than plain scaling.
