# Prior art: 36 projects and what ClearFrame takes from them

Survey of October 2026 (GitHub search over 20 queries, 142 candidates, 36 read in detail). Stars are approximate at survey time. Claims are the projects' own unless noted.

## Real-time upscaling of windows / screens (closest to ClearFrame Live)

| Project | ★ | How it works | Take-away for ClearFrame |
|---|---|---|---|
| [Blinue/Magpie](https://github.com/Blinue/Magpie) | 15.4k | C++/HLSL. Frame sources: Graphics Capture, Desktop Duplication, DWM shared surface, GDI. Copies the capture **texture on the GPU** (`CopySubresourceRegion`), runs effects as compute shaders, presents through a **flip-discard swapchain** with tearing/VRR and a frame-latency waitable object. Detects unchanged frames on the GPU and skips work. Own cursor drawing; scaling window is `WS_EX_LAYERED \| NOACTIVATE \| NOREDIRECTIONBITMAP`. | Keep frames on the GPU end to end; skip unchanged frames; low-latency flip presentation. |
| [picki1/AnimeEnhancer](https://github.com/picki1/AnimeEnhancer) | 13 | C#/.NET. WGC → D3D11 → Anime4K CNN passes (FP16 intermediates) → flip-model overlay. **Auto mode detects fullscreen windows by geometry**; moving the pointer briefly reveals the original so controls stay usable; split-view compare; optional RIFE; a capturable "LS source" window for chaining; SHA256SUMS for unsigned releases. Warns that <720p sources lack detail. | Same design as our auto mode — validates it. Ideas: pointer-reveal, split view, timing from capture timestamps, release hashes. |
| [L65536/RealTimeSuperResolutionScreenUpscaler](https://github.com/L65536/RealTimeSuperResolutionScreenUpscalerforLinux) | 23 | ~300 lines of Python: WGC (Windows) / NvFBC (Linux) capture, Magpie's CuNNy HLSL run through **compushady**, present straight to a swapchain. | A GPU-only path is possible from Python with compushady. |
| [baronsmv/linux-rt-upscaler](https://github.com/baronsmv/linux-rt-upscaler) | 71 | X11 window upscaler with CuNNy; **tile-based processing of only the regions that changed**. | Tile skipping for the whole-screen mode. |
| [perseval-BLR/NeuralScreen](https://github.com/perseval-BLR/NeuralScreen) | 1.0k | Desktop/window capture → NGX "neural rendering" (leaked runtime, GPU-arch spoofing) → overlay; NVOFA optical flow for motion; scene-cut resets. | Optical flow + scene cuts are useful; the runtime is not usable. |
| [Likely7/Veyra-NRVideo](https://github.com/Likely7/Veyra-NRVideo) | 506 | Player + capture-card/streaming enhancer with several "NR" runtimes, DLSS/XeSS/FSR, frame generation, bounded async SDK submission. | Bounded async work keeps presentation moving; runtimes of unclear origin — avoid. |
| [Merserk/dlss5-visual-enhancer](https://github.com/Merserk/dlss5-visual-enhancer) | 1.3k | Python app: NGX neural rendering, DLSS SR with optical-flow quality, RTX VSR, live playback. Restrictive license. | DLSS SR on video needs motion vectors from optical flow. |
| [hadoooooouken/qimgv-plus](https://github.com/hadoooooouken/qimgv-plus) | 27 | Image viewer that upscales **only the visible viewport crop** in real time (ncnn). | Process only what is shown. |

## NVIDIA RTX Video / Video Effects SDK users

| Project | ★ | How it works | Take-away |
|---|---|---|---|
| [Bemjo/OBS-RTX-SuperResolution](https://github.com/Bemjo/OBS-RTX-SuperResolution) | 113 | OBS filter on the NVIDIA Video Effects SDK: **Artifact Reduction pre-pass + Super Resolution** (or plain Upscale). Documents NVIDIA's advice: compressed video → ArtifactReduction, then SuperRes. Input limits 160x90…1080p for AR. | For web video, artifact reduction *before* super resolution — the driver VSR path we use skips this. An OBS-side option for streamers. |
| [glarsson/fast-rtxvsr](https://github.com/glarsson/fast-rtxvsr) | 20 | Python: NVDEC → `nvvfx.VideoSuperRes` (quality LOW…ULTRA) on **torch CUDA tensors** → NVENC, all on the GPU; pitfalls documented (stream sync before encode, BT.709 maths on GPU). ~80–95 fps model core on an RTX 5060 Ti. | `nvidia-vfx` (NVIDIA wheel, proprietary licence, user-installed) gives RTX VSR with quality levels directly on GPU tensors — no mpv round trip. |
| [DrC0ns0le/RTXVideoProcessor](https://github.com/DrC0ns0le/RTXVideoProcessor) | 36 | C++ CLI on the RTX Video SDK: VSR + TrueHDR, FFmpeg-compatible, GPU path on FFmpeg's CUDA context, 2–5x real time on an RTX 4070. | Reference for a native RTX Video SDK path. |
| [whmc76/ComfyUI-NVIDIA-RTX-VSR-Pro](https://github.com/whmc76/ComfyUI-NVIDIA-RTX-VSR-Pro), [Deno2026/comfyui-deno-custom-nodes](https://github.com/Deno2026/comfyui-deno-custom-nodes), [Haoming02/sd-forge-nvidia-vfx](https://github.com/Haoming02/sd-forge-nvidia-vfx) | 45 / 189 / 28 | Wrap `nvidia-vfx` VSR for image/video workflows. | Confirms `nvvfx` as the common way to call RTX VSR from Python. |

## Players and pipelines with neural upscaling

| Project | ★ | How it works | Take-away |
|---|---|---|---|
| [the-database/mpv-AnimeJaNai](https://github.com/the-database/mpv-AnimeJaNai) | 767 | mpv + VapourSynth + TensorRT running Real-ESRGAN **Compact** ONNX models; engine built on first play; three profiles (RTX 3060 / 3080 / 4090). | Profiles by GPU class; first-run engine build UX. |
| [the-database/VideoJaNai](https://github.com/the-database/VideoJaNai) | 281 | Offline GUI, same TensorRT + VapourSynth stack. | — |
| [AmusementClub/vs-mlrt](https://github.com/AmusementClub/vs-mlrt) | 482 | VapourSynth ML runtimes (TensorRT, ORT, OpenVINO, ncnn) with ready model wrappers. | Engine for a file/stream player path. |
| [styler00dollar/VSGAN-tensorrt-docker](https://github.com/styler00dollar/VSGAN-tensorrt-docker) | 360 | VapourSynth + TensorRT for SR and interpolation, many archs via spandrel, model-based **shot boundary detection**. | Scene-cut detection for temporal models. |
| [Kristijan1001/MPVonCrack](https://github.com/Kristijan1001/MPVonCrack) | 13 | Portable mpv with bundled Python, 56 models, vs-mlrt TensorRT, RIFE; splits the CUDA runtime into separate download parts. | Packaging: ship the CUDA runtime as an optional add-on. |
| [luoxue03/mpv-lazy-2026-custom](https://github.com/luoxue03/mpv-lazy-2026-custom) | 70 | mpv build with RIFE/TensorRT, VapourSynth, Anime4K. | — |
| [mafiosnik777/enhancr](https://github.com/mafiosnik777/enhancr) | 812 | Electron GUI, TensorRT/ncnn, custom ONNX/pth models auto-converted, **scene detection**, realtime player. | Auto-convert user models. |
| [TNTwise/REAL-Video-Enhancer](https://github.com/TNTwise/REAL-Video-Enhancer) | 2.3k | Upscale/interpolate/decompress/denoise; TensorRT (8 GB VRAM) or ncnn (4 GB). | VRAM budgets matter on 6 GB laptops. |
| [k4yt3x/video2x](https://github.com/k4yt3x/video2x) | 22k | C++ framework, Real-ESRGAN/Real-CUGAN/Anime4K/RIFE via ncnn+Vulkan. | — |
| [AaronFeng753/Waifu2x-Extension-GUI](https://github.com/AaronFeng753/Waifu2x-Extension-GUI) | 17k | Multi-engine offline GUI. | — |
| [Kiteretsu77/FAST_Anime_VSR](https://github.com/Kiteretsu77/FAST_Anime_VSR) | 74 | TensorRT, **frame division + redundancy skipping** (like a codec), decode at lower fps; real time on 480p with Real-CUGAN on a 3060 Ti. Author's **VCISR** trains on video-compression degradations. | Skip redundant work; VCISR-style compression degradations for Part 3. |

## Shaders and browser

| Project | ★ | How it works | Take-away |
|---|---|---|---|
| [bloc97/Anime4K](https://github.com/bloc97/Anime4K) | 21k | GLSL CNNs for mpv, restore + upscale passes, sizes S…UL. | Restore pass *before* upscale. |
| [TianZerL/Anime4KCPP](https://github.com/TianZerL/Anime4KCPP) | 2.0k | C++ (CPU/OpenCL/CUDA) Anime4K-style upscaler. | — |
| [chenmozhijin/NijiLucid](https://github.com/chenmozhijin/NijiLucid) | 238 | **WebGPU browser extension** with several SR models; runs a GPU benchmark (1080p→4K at 24 fps) to choose settings; fixed targets 720p…4K. | Auto-benchmark to pick a profile. |

## Models, training, research (offline unless noted)

| Project | ★ | Notes |
|---|---|---|
| [xinntao/Real-ESRGAN](https://github.com/xinntao/Real-ESRGAN) | 37k | Degradation model and SRVGGNetCompact (our `Compact`). BSD-3. |
| [OpenImagingLab/FlashVSR](https://github.com/OpenImagingLab/FlashVSR) | 1.9k | One-step diffusion streaming VSR — "real-time" on large GPUs, far beyond a 6 GB laptop. |
| [yjsunnn/DLoRAL](https://github.com/yjsunnn/DLoRAL) | 357 | One-step diffusion VSR, alternating temporal-consistency / detail training. |
| [Thmen/EGVSR](https://github.com/Thmen/EGVSR) | 954 | Faster TecoGAN via sub-pixel convolution; temporal coherence metrics. |
| [skycrapers/TecoGAN-PyTorch](https://github.com/skycrapers/TecoGAN-PyTorch) | 246 | Temporally coherent GAN VSR (flow-warped previous output). |
| [victorca25/traiNNer](https://github.com/victorca25/traiNNer) | 310 | Training framework for SR/restoration with custom datasets. |
| [sczhou/Upscale-A-Video](https://github.com/sczhou/Upscale-A-Video), [NJU-PCALab/STAR](https://github.com/NJU-PCALab/STAR) | 1.5k / 1.5k | Diffusion VSR, offline only. |

Also seen earlier: DLSS5-Swapper, DLSS5-Feeder, DLSS5oneclick, DLSS-5-MANAGER (game injectors built on the same leaked runtime — out of scope).

## What this changes in ClearFrame (priority order)

1. **GPU-only Live pipeline.** Today: capture → CPU buffer → pipe → mpv (CPU downscale + NV12) → GPU. Every serious tool (Magpie, AnimeEnhancer, fast-rtxvsr, L65536) keeps frames on the GPU. Plan: WGC texture → CUDA tensor → `nvvfx` (ArtifactReduction + VideoSuperRes) → swapchain in our host window.
2. **Don't process duplicates.** 24 fps video captured at 60 Hz is re-upscaled 2.5x per real frame today. Upscale only new frames; pace presentation from capture timestamps (AnimeEnhancer) instead of a fixed 60 fps pump.
3. **Artifact reduction before super resolution** for compressed web video (NVIDIA's own recommendation), plus VSR quality levels.
4. **UX:** pointer-reveal and split-view compare (AnimeEnhancer), GPU auto-benchmark to choose a profile (NijiLucid, AnimeJaNai), first-run engine build progress.
5. **Whole-screen mode:** process only changed tiles (linux-rt-upscaler, Magpie's duplicate detection).
6. **Own model (Part 3):** VCISR-style video-compression degradations, Compact/SPAN size from our speed table, temporal input with optical flow and scene-cut resets (NeuralScreen, VSGAN, TecoGAN) as a second stage.
7. **Release:** portable zip + SHA256SUMS, CUDA runtime as an optional add-on (MPVonCrack, AnimeEnhancer); `nvidia-vfx` installed by the user from NVIDIA, never bundled.
