# Roadmap

Each part ends with something you can see or measure. We don't move on until it works.

## Part 1 — Benchmark bench `bench/`
Goal: an honest, repeatable way to compare upscalers.
- [ ] Test clips: open-licensed live-action footage (e.g. Blender *Tears of Steel*, CC-BY) degraded like web streams: 480p/720p, ~0.7–1.5 Mbit/s H.264, banding in dark scenes.
- [ ] Variants: bicubic (what a browser does), driver RTX VSR, FSR 1, existing neural models.
- [ ] Output: side-by-side video, split-screen video, zoomed crops, metrics (PSNR/SSIM vs the clean original, plus LPIPS when available).
- [ ] Known issue: mpv encode mode (`--o`) has no D3D11 device, so `d3d11vpp` (RTX VSR) can't render to a file that way — capture frames from a real VO instead.

## Part 2 — Engine
- [ ] mpv + VapourSynth + vs-mlrt (TensorRT) running an existing compact model.
- [ ] FPS table on the reference GPU (RTX 4050 Laptop 6 GB): 480p→1440p and 720p→1440p, FP16.
- [ ] Engine cache, first-run engine build UX.

## Part 3 — Own live-action model
- [ ] Degradation pipeline mimicking streaming: downscale, low-bitrate H.264/HEVC re-encode, chroma subsampling, banding, light blur/noise.
- [ ] Training set from permissively licensed footage.
- [ ] Train compact 2× (SPAN / Compact) — locally or on a rented GPU.
- [ ] Export ONNX → TensorRT; real-time check on the reference GPU; compare against RTX VSR on the bench.

## Part 4 — Product
- [ ] One-click Windows installer, auto profile (model size × scale from source resolution and GPU).
- [ ] Hotkeys: A/B compare, profile switch; on-screen info.
- [ ] Optional browser extension: send the current video to the ClearFrame player.

## Part 5 — Release
- [ ] GitHub releases with player build and model files, checksums.
- [ ] Docs in English and Russian.
