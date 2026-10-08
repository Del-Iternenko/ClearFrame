# ClearFrame

**Real-time neural upscaling for movies and TV on NVIDIA RTX.** Turn soft, over-compressed 480p–720p video into a clean 1440p picture while you watch, using the Tensor Cores of your GeForce RTX GPU.

> 🇷🇺 Русская версия ниже.

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

---

## 🇷🇺 ClearFrame

**Нейросетевой апскейл фильмов и сериалов в реальном времени на NVIDIA RTX.** Мыльное, пережатое видео 480p–720p превращается в чистую картинку 1440p прямо во время просмотра — на тензорных ядрах видеокарты GeForce RTX.

> **Статус: ранняя разработка.** Скачивать пока нечего — проект собирается открыто, по шагам. См. [план](docs/roadmap.md).

**Зачем.** Огромная часть видео, которое люди смотрят каждый день, — низкое разрешение и сильное сжатие: квадраты в тенях, полосы, «мыльные» лица. Быстрые нейросетевые апскейлеры в основном обучены на **аниме**, а RTX Video Super Resolution в драйвере универсальный и осторожный. Открытой модели реального времени для **живого видео, испорченного сжатием**, нет — эту дыру и закрывает ClearFrame.

**Как работает (план):** mpv + VapourSynth → модель ONNX, скомпилированная в TensorRT (FP16) под вашу видеокарту → апскейл ×2 на тензорных ядрах → 1440p. Своя компактная модель, обученная на живом видео с реалистичными «стриминговыми» повреждениями. Автопрофиль подбирает модель под разрешение и видеокарту. Расширение для браузера передаёт видео в плеер.

**Требования:** Windows 10/11, NVIDIA GeForce RTX 20/30/40/50, свежий драйвер.

**Лицензия:** код — MIT; сторонние компоненты — под своими лицензиями. Используйте с контентом, который вы вправе смотреть.
