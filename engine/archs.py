"""Compact super-resolution architectures used by ClearFrame.

Written from the published descriptions; speed depends only on the graph, so these
are also used to benchmark untrained networks before any training happens.

- Compact: plain VGG-style stack with per-channel PReLU and a pixel-shuffle head,
  plus a nearest-neighbour skip of the input (the "SRVGGNetCompact" design from
  Real-ESRGAN, Wang et al. 2021). Variants differ only in width/depth.
- SPAN: Swift Parameter-free Attention Network (Wan et al., CVPR-W 2024), in its
  inference form (the re-parameterised 3-branch convs collapsed into plain 3x3 convs).
"""
import torch
import torch.nn as nn
import torch.nn.functional as F


class Compact(nn.Module):
    def __init__(self, scale=2, features=64, convs=16, channels=3):
        super().__init__()
        self.scale = scale
        layers = [nn.Conv2d(channels, features, 3, padding=1), nn.PReLU(features)]
        for _ in range(convs):
            layers += [nn.Conv2d(features, features, 3, padding=1), nn.PReLU(features)]
        layers += [nn.Conv2d(features, channels * scale * scale, 3, padding=1), nn.PixelShuffle(scale)]
        self.body = nn.Sequential(*layers)

    def forward(self, x):
        return self.body(x) + F.interpolate(x, scale_factor=self.scale, mode="nearest")


class SPAB(nn.Module):
    """Swift parameter-free attention block: attention = sigmoid(features) - 0.5."""

    def __init__(self, features):
        super().__init__()
        self.c1 = nn.Conv2d(features, features, 3, padding=1)
        self.c2 = nn.Conv2d(features, features, 3, padding=1)
        self.c3 = nn.Conv2d(features, features, 3, padding=1)

    def forward(self, x):
        out1 = self.c1(x)
        out3 = self.c3(F.silu(self.c2(F.silu(out1))))
        attention = torch.sigmoid(out3) - 0.5
        return (out3 + x) * attention, out1


class SPAN(nn.Module):
    def __init__(self, scale=2, features=48, blocks=6, channels=3):
        super().__init__()
        assert blocks >= 2
        self.head = nn.Conv2d(channels, features, 3, padding=1)
        self.blocks = nn.ModuleList(SPAB(features) for _ in range(blocks))
        self.fuse = nn.Conv2d(features * 4, features, 1)
        self.tail = nn.Conv2d(features, features, 3, padding=1)
        self.up = nn.Sequential(nn.Conv2d(features, channels * scale * scale, 3, padding=1), nn.PixelShuffle(scale))

    def forward(self, x):
        head = self.head(x)
        out, _ = self.blocks[0](head)
        first = out
        for block in self.blocks[1:-1]:
            out, _ = block(out)
        out, last_inner = self.blocks[-1](out)
        out = self.tail(out)
        # features from the head, the end, the first block and inside the last block
        out = self.fuse(torch.cat([head, out, first, last_inner], dim=1))
        return self.up(out)


class ClearFrameNet(nn.Module):
    """ClearFrame's own network: restoration (scale 1) or restoration + upscale (scale 2) of streamed
    live action. The frame is pixel-unshuffled 2x first, so the body runs on a quarter of the pixels
    (what makes 1080p real-time on a laptop GPU); SPAN-style attention blocks in between; the result
    is added to the (resized) input, so the network only learns the correction."""

    def __init__(self, scale=1, features=48, blocks=4, channels=3):
        super().__init__()
        self.scale = scale
        c = channels * 4                      # after pixel_unshuffle(2)
        self.head = nn.Conv2d(c, features, 3, padding=1)
        self.blocks = nn.ModuleList(SPAB(features) for _ in range(blocks))
        self.fuse = nn.Conv2d(features * 2, features, 1)
        self.tail = nn.Conv2d(features, channels * (2 * scale) ** 2, 3, padding=1)
        self.shuffle = nn.PixelShuffle(2 * scale)

    def forward(self, x):
        head = self.head(F.pixel_unshuffle(x, 2))
        out = head
        for block in self.blocks:
            out, _ = block(out)
        out = self.tail(self.fuse(torch.cat([head, out], dim=1)))
        base = x if self.scale == 1 else F.interpolate(x, scale_factor=self.scale, mode="bilinear", align_corners=False)
        return base + self.shuffle(out)


# name -> constructor (scale is passed in)
MODELS = {
    "compact-24x8": lambda s: Compact(s, 24, 8),     # "super-ultra-compact"
    "compact-48x8": lambda s: Compact(s, 48, 8),
    "compact-64x8": lambda s: Compact(s, 64, 8),     # "ultra-compact"
    "compact-64x16": lambda s: Compact(s, 64, 16),   # Real-ESRGAN AnimeVideo v3 size
    "compact-64x32": lambda s: Compact(s, 64, 32),   # general-x4v3 size
    "span-32x6": lambda s: SPAN(s, 32, 6),
    "span-48x6": lambda s: SPAN(s, 48, 6),            # paper default
    "cf-32x4": lambda s: ClearFrameNet(s, 32, 4),
    "cf-48x4": lambda s: ClearFrameNet(s, 48, 4),
    "cf-48x6": lambda s: ClearFrameNet(s, 48, 6),
    "cf-64x4": lambda s: ClearFrameNet(s, 64, 4),
    "cf-64x6": lambda s: ClearFrameNet(s, 64, 6),
}


def count_params(model):
    return sum(p.numel() for p in model.parameters())
