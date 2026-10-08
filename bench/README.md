# Benchmark bench

Part 1 of the [roadmap](../docs/roadmap.md): run the same degraded clip through several upscalers and compare them with the clean original.

```
python bench/bench.py all
```

Steps (each can be run alone): `setup` (pinned ffmpeg + mpv into `vendor/`, checksum-verified), `source` (clean 20 s reference + web-style degraded copy), `bicubic`, `rtx-vsr`, `compare` (PSNR/SSIM, split-screen video, zoomed crops, `results.md`).

Requirements: Windows 10/11, Python 3.9+, an NVIDIA GeForce RTX GPU for the `rtx-vsr` step. The first `source` run downloads the 1080p *Tears of Steel* release (~580 MB) into `bench/cache/`.

Outputs go to `bench/out/` (not committed). Published results: [docs/bench-results.md](../docs/bench-results.md).

Footage: "Tears of Steel" (CC BY 3.0) (c) Blender Foundation | mango.blender.org
