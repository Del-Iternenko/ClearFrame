# Training ClearFrame's own network

ClearFrame restores streamed live action **as it is shown on screen**: the browser has already stretched
a 144p…1080p stream to the window, and the network turns that into a clean picture of the same size —
no blocks, no smear, no banded dark gradients, crisp edges — and, with the *detail* weights, draws
plausible fine texture the way generative upscalers do.

## Data

Clean footage, all under licences that allow training and publishing the resulting weights:

| Film | Source | Licence |
|---|---|---|
| *Chimera* (2014), live action, DCI 4K 23.98p | [Netflix Open Content](https://opencontent.netflix.com) | CC BY 4.0 |
| *Meridian* (2016), live action, UHD 59.94p | [Netflix Open Content](https://opencontent.netflix.com) | CC BY 4.0 |
| *Tears of Steel* (2012), live action, 1080p | [Blender Foundation](https://mango.blender.org) | CC BY 3.0 |

The Netflix masters are HDR (PQ, P3) without colour tags; `make_data.py` tags them and tone-maps to SDR
BT.709. *Tears of Steel* 90–125 s is left out: it holds the bench clip (`bench/`), the test set.

Put the files in `train/data/clean/` (Chimera_DCI4k2398p_HDR_P3PQ.mp4, Meridian_UHD4k5994_HDR_P3PQ.mp4
from the Netflix bucket) and run `python bench/bench.py setup` once for ffmpeg and the Tears of Steel master.

## Degradations

Each 2 s segment is degraded the way streaming does it, then stretched back like a browser:

- resolution ladder: 144p, 240p, 360p, 480p, 540p, 720p, or kept at 1080p (a starved 1080p stream);
- real codecs at low quality: H.264 (crf 22–40, sometimes adaptive quantisation off → banding in the
  dark), H.265 (crf 26–42), MPEG-4 Part 2 (old DivX/Xvid rips);
- film grain added before encoding in 30% of segments (grain that the encoder turned into mush);
- random down/up-scaling filters (bicubic, bilinear, area, lanczos).

```
train/make_data.bat            # or: python train/make_data.py --segments 470   (~30 min, CPU)
```

## Network and training

`engine/archs.py` → `ClearFrameNet(scale=1, features=64, blocks=4)`: pixel-unshuffle ×2 (the body runs on a
quarter of the pixels), four SPAN-style attention blocks, pixel-shuffle back, added to the input.
465k parameters, **20 ms per 1080p frame** on an RTX 4050 Laptop (TensorRT FP16).

1. **clean** — Charbonnier pixel loss, 20k iterations (~30 min).
2. **detail** — from *clean*: L1 + VGG19 perceptual loss + U-Net discriminator with spectral norm
   (Real-ESRGAN style adversarial training), 15k iterations (~2.5 h).

The app blends the two weight sets (same architecture) for its *Detail* setting: 0% = clean, 100% = detail.

```
train/train.bat                # or: python train/train.py --name cf64x4   (resumes from last.pt)
```

Progress goes to `train/runs/<name>/log.txt`; `eval/` gets close-ups of the bench clip (stretched input |
network | original) every 2000 iterations. The app picks up `train/runs/<name>/clean.pt` and `detail.pt`.
