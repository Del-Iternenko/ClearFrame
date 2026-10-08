# Inference speed - NVIDIA GeForce RTX 4050 Laptop GPU

TensorRT 11.3.0.99, FP16 strongly typed; PyTorch 2.14.1+cu130 FP16 eager for reference. Network time only (no decode, color conversion or display). Real-time needs < 41.7 ms at 24 fps, < 33.3 ms at 30 fps.

| Model | Params | Input | Output | TensorRT ms | TensorRT fps | PyTorch ms | 24 fps? |
|---|---|---|---|---|---|---|---|
| compact-24x8 | 45k | 854x480 | 1708x960 | 5.06 | 197.7 | 10.73 | yes |
| compact-24x8 | 45k | 1280x720 | 2560x1440 | 11.25 | 88.9 | 26.75 | yes |
| compact-48x8 | 173k | 854x480 | 1708x960 | 11.47 | 87.2 | 27.95 | yes |
| compact-48x8 | 173k | 1280x720 | 2560x1440 | 26.67 | 37.5 | 61.94 | yes |
| compact-64x8 | 305k | 854x480 | 1708x960 | 13.86 | 72.1 | 34.79 | yes |
| compact-64x8 | 305k | 1280x720 | 2560x1440 | 33.07 | 30.2 | 79.24 | yes |
| compact-64x16 | 601k | 854x480 | 1708x960 | 26.61 | 37.6 | 67.38 | yes |
| compact-64x16 | 601k | 1280x720 | 2560x1440 | 62.39 | 16.0 | 153.28 | no |
| compact-64x32 | 1193k | 854x480 | 1708x960 | 52.92 | 18.9 | 134.47 | no |
| compact-64x32 | 1193k | 1280x720 | 2560x1440 | 121.18 | 8.3 | 303.08 | no |
| span-32x6 | 184k | 854x480 | 1708x960 | 10.65 | 93.9 | 40.68 | yes |
| span-32x6 | 184k | 1280x720 | 2560x1440 | 23.8 | 42.0 | 96.43 | yes |
| span-48x6 | 411k | 854x480 | 1708x960 | 24.96 | 40.1 | 76.92 | yes |
| span-48x6 | 411k | 1280x720 | 2560x1440 | 57.83 | 17.3 | 173.02 | no |

'yes' = under 80% of the 24 fps frame budget, leaving room for decoding and display.

## Reading the table

Budget at 24 fps is 41.7 ms per frame, and decoding, colour conversion and display need part of it, so the network should stay around 30 ms or below.

- **480p → 960p (x2):** everything up to Compact 64x16 (the size of Real-ESRGAN AnimeVideo v3, 26.6 ms) and SPAN 48x6 (25.0 ms) fits on an RTX 4050 Laptop.
- **720p → 1440p (x2):** SPAN 32x6 (23.8 ms) and Compact 48x8 (26.7 ms) fit; Compact 64x8 (33.1 ms) is borderline.
- The large Compact 64x32 (Real-ESRGAN general-x4v3 size) is not real-time here at any input size.
- TensorRT is 2.3–4x faster than PyTorch eager for the same FP16 network.

So a model for this GPU class should have roughly 150–400k parameters. SPAN 32x6 / 48x6 or Compact 48x8 / 64x16 are the training candidates; faster GPUs can run larger profiles.
