# ClearFrame

**Real-time neural upscaling for movies and TV on NVIDIA RTX.** Turn soft, over-compressed 480p–720p video into a clean 1440p picture while you watch, using the Tensor Cores of your GeForce RTX GPU.

> **Status: early development.** Nothing to download yet — the project is being built in the open, step by step. See the [roadmap](docs/roadmap.md).

## Why

A huge amount of video people watch every day is low resolution and heavily compressed: blocky shadows, banding, mushy faces. Existing fast neural upscalers are mostly trained for **anime**. Driver-level RTX Video Super Resolution is generic and conservative. There is no good open, *real-time* model for **live-action video damaged by streaming compression** — that is the gap ClearFrame targets.

## How it works (planned)

```
video ──► decode (NVDEC) ──► deblock / deband ──► neural 2× upscale ──► 1440p ──► screen
                                                    TensorRT on RTX Tensor Cores
```

- **Player:** [mpv](https://mpv.io) with a VapourSynth filter graph.
- **Inference:** ONNX models compiled to TensorRT engines (FP16) for the user's exact GPU, via [vs-mlrt](https://github.com/AmusementClub/vs-mlrt).
- **Models:** compact super-resolution networks (SPAN / Real-ESRGAN Compact family), trained by this project on live-action footage with realistic streaming degradations (low-bitrate H.264/HEVC, blocking, banding, blur).
- **Auto profile:** picks the scale factor and model size from the source resolution and the GPU, so playback stays real-time.
- **Browser hand-off (optional):** a browser extension sends the video you're watching to the ClearFrame player.

## ClearFrame Live — upscale any video that is already playing

A tray app for viewers, streamers and film critics: YouTube in a browser, a movie in any player, a streaming site — ClearFrame captures that window (Windows Graphics Capture), finds the video area by itself, and shows it through RTX Video Super Resolution:

- **over the original video** (also fullscreen) — click-through, nothing else changes;
- **or in a separate window** — add it to OBS as *Window Capture* for streaming.

Press **Ctrl+Alt+U** on a window with video to turn it on or off, or pick a window from the tray menu. *Source quality* (Auto / 360p / 480p / 720p / 1080p) tells ClearFrame the real resolution of what you watch, so the network works on real pixels rather than on the browser's stretched copy.

```
ClearFrame-Live.bat          # needs .venv (see engine/README.md) and: python bench/bench.py setup
```

Limits: DRM-protected services (Netflix and similar) give a black picture to every capture tool, OBS included. Audio stays with the original app.

## Try the file player (early preview)

A control panel that plays a video and switches upscaling on and off while it plays:

```
python bench/bench.py setup      # once: downloads pinned mpv + ffmpeg into vendor/
ClearFrame.bat                   # or: pythonw app/clearframe_app.py
```

Open a file or paste a URL, then toggle **UPSCALER ON/OFF** (or press `U`), pick a mode (RTX Video Super Resolution or a sharp shader scaler), a target (1080p / 1440p / 2160p) and deband strength, and hold **compare** to see the original. The status line shows source and output resolution, so you can see whether upscaling is really applied. The ClearFrame neural model will appear as a mode once it's trained.

## Requirements (target)

- Windows 10/11 64-bit
- NVIDIA GeForce RTX 20/30/40/50 series, recent driver
- The first run of a model builds a TensorRT engine for your GPU (a few minutes, once)

## Roadmap

1. **Benchmark bench** — the same degraded clip through bicubic, RTX VSR, FSR and neural models; side-by-side videos + metrics.
2. **Engine** — mpv + VapourSynth + TensorRT with existing open models; measure FPS on real GPUs (starting with an RTX 4050 Laptop).
3. **Own model** — train a compact 2× live-action model on streaming-style degradations; compare against RTX VSR on the bench.
4. **Product** — one-click installer, auto profiles, browser hand-off.
5. **Release** — signed builds and model files as GitHub releases.

Details: [docs/roadmap.md](docs/roadmap.md) · prior art: [docs/research.md](docs/research.md)

## License

ClearFrame's own code: [MIT](LICENSE). Bundled third-party components (mpv, VapourSynth, TensorRT runtime, models) keep their own licenses; only models with licenses that allow redistribution are shipped by default.

ClearFrame is a general video-enhancement tool. Use it with content you have the right to watch.

