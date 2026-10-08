# Benchmark bench

Part 1 of the [roadmap](../docs/roadmap.md): run the same degraded clip through several upscalers and compare them side by side and with metrics.

Work in progress. Planned layout:

- `make-source.*` — download an open-licensed clip and degrade it like a web stream (480p, ~800 kbit/s H.264).
- `run-*.*` — one script per variant (bicubic, RTX VSR, FSR, neural models), all producing 1440p frames of the same clip.
- `compare.*` — side-by-side / split-screen videos, zoomed crops, PSNR/SSIM against the clean original.

Outputs go to `bench/out/` (not committed).
