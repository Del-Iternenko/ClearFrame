"""Trains ClearFrame's own network on the pairs from make_data.py.

Two stages, both kept:
  1. "clean"  - pixel loss only: removes blocks, smear and banding, restores edges; never invents.
  2. "detail" - starts from stage 1, adds a perceptual (VGG19 feature) loss and an adversarial critic
                (U-Net discriminator with spectral norm, as in Real-ESRGAN): the network learns to draw
                plausible texture and fine detail, the way generative upscalers do.
The app blends the two sets of weights (same architecture), which gives a "detail strength" slider
from 0 (clean only) to 1 (full detail).

    .venv/Scripts/python train/train.py --name cf64x4 [--clean-iters 20000] [--detail-iters 30000]

Runs resume from train/runs/<name>/last.pt. Progress: train/runs/<name>/log.txt, eval/ (bench clip
close-ups every --eval-every iterations), clean.pt / detail.pt (weights for the app).
"""
import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from torch.nn.utils import spectral_norm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "engine"))
from archs import ClearFrameNet  # noqa: E402

DATA = ROOT / "train" / "data"
RUNS = ROOT / "train" / "runs"
BENCH = ROOT / "bench" / "out"
FFMPEG = str(ROOT / "vendor" / "ffmpeg" / "bin" / "ffmpeg.exe")


# ------------------------------------------------------------------ data
class Pairs(torch.utils.data.Dataset):
    """Each item: `crops` aligned random crops from one (degraded, clean) frame pair."""

    def __init__(self, names, size=256, crops=4):
        self.names, self.size, self.crops = names, size, crops

    def __len__(self):
        return len(self.names) * 50          # many random crops per pair per "epoch"

    def __getitem__(self, i):
        base = DATA / "pairs" / self.names[i % len(self.names)]
        lq = np.asarray(Image.open(f"{base}_lq.png").convert("RGB"))
        gt = np.asarray(Image.open(f"{base}_gt.png").convert("RGB"))
        h, w = gt.shape[:2]
        s = self.size
        out_lq, out_gt = [], []
        for _ in range(self.crops):
            y, x = random.randrange(0, h - s + 1) // 2 * 2, random.randrange(0, w - s + 1) // 2 * 2
            a, b = lq[y:y + s, x:x + s], gt[y:y + s, x:x + s]
            if random.random() < 0.5:
                a, b = a[:, ::-1], b[:, ::-1]
            out_lq.append(torch.from_numpy(a.copy()).permute(2, 0, 1))
            out_gt.append(torch.from_numpy(b.copy()).permute(2, 0, 1))
        return torch.stack(out_lq), torch.stack(out_gt)


def loader(names, batch_pairs, size, crops, workers):
    return torch.utils.data.DataLoader(Pairs(names, size, crops), batch_size=batch_pairs, shuffle=True,
                                       num_workers=workers, persistent_workers=workers > 0, pin_memory=True,
                                       drop_last=True)


def to_float(x):
    """uint8 B x K x 3 x H x W -> float (B*K) x 3 x H x W on the GPU."""
    return x.flatten(0, 1).cuda(non_blocking=True).float().div_(255)


# ------------------------------------------------------------------ losses
class VGGFeatures(nn.Module):
    """Perceptual loss on VGG19 features (ImageNet weights from torchvision, BSD licence)."""
    LAYERS = {2: 0.1, 7: 0.1, 16: 1.0, 25: 1.0, 34: 1.0}   # conv1_2, conv2_2, conv3_4, conv4_4, conv5_4 (pre-ReLU)

    def __init__(self):
        super().__init__()
        from torchvision.models import VGG19_Weights, vgg19
        self.net = vgg19(weights=VGG19_Weights.IMAGENET1K_V1).features[:35].eval()
        for p in self.net.parameters():
            p.requires_grad_(False)
        self.register_buffer("mean", torch.tensor([0.485, 0.456, 0.406]).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor([0.229, 0.224, 0.225]).view(1, 3, 1, 1))

    def forward(self, x, y):
        x, y = (x - self.mean) / self.std, (y - self.mean) / self.std
        loss = 0.0
        for i, layer in enumerate(self.net):
            x, y = layer(x), layer(y)
            if i in self.LAYERS:
                loss = loss + self.LAYERS[i] * F.l1_loss(x, y)
            if i == max(self.LAYERS):
                break
        return loss


class UNetDiscriminator(nn.Module):
    """U-Net discriminator with spectral normalisation (Wang et al., Real-ESRGAN 2021): a real/fake
    score per pixel, so it judges local texture everywhere, not just the whole crop."""

    def __init__(self, ch=64):
        super().__init__()
        sn = spectral_norm
        self.c0 = nn.Conv2d(3, ch, 3, 1, 1)
        self.c1 = sn(nn.Conv2d(ch, ch * 2, 4, 2, 1, bias=False))
        self.c2 = sn(nn.Conv2d(ch * 2, ch * 4, 4, 2, 1, bias=False))
        self.c3 = sn(nn.Conv2d(ch * 4, ch * 8, 4, 2, 1, bias=False))
        self.c4 = sn(nn.Conv2d(ch * 8, ch * 4, 3, 1, 1, bias=False))
        self.c5 = sn(nn.Conv2d(ch * 4, ch * 2, 3, 1, 1, bias=False))
        self.c6 = sn(nn.Conv2d(ch * 2, ch, 3, 1, 1, bias=False))
        self.c7 = sn(nn.Conv2d(ch, ch, 3, 1, 1, bias=False))
        self.c8 = sn(nn.Conv2d(ch, ch, 3, 1, 1, bias=False))
        self.c9 = nn.Conv2d(ch, 1, 3, 1, 1)

    def forward(self, x):
        act = lambda t: F.leaky_relu(t, 0.2)
        up = lambda t: F.interpolate(t, scale_factor=2, mode="bilinear", align_corners=False)
        x0 = act(self.c0(x))
        x1 = act(self.c1(x0))
        x2 = act(self.c2(x1))
        x3 = act(self.c3(x2))
        x4 = act(self.c4(up(x3))) + x2
        x5 = act(self.c5(up(x4))) + x1
        x6 = act(self.c6(up(x5))) + x0
        return self.c9(act(self.c8(act(self.c7(x6)))))


# ------------------------------------------------------------------ eval on the bench clip (never trained on)
def eval_frames():
    """Bench test frames: the 960x400 stream stretched to 1920x800 like a browser does, and the clean frame."""
    cache = RUNS / "eval_frames.npz"
    if cache.exists():
        d = np.load(cache)
        return d["lq"], d["gt"]
    import subprocess
    lqs, gts = [], []
    for t in (3, 10, 17):
        frame = lambda src, vf: np.frombuffer(subprocess.run(
            [FFMPEG, "-v", "error", "-ss", str(t), "-i", str(src), "-frames:v", "1", "-vf", vf, "-f", "rawvideo",
             "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout, np.uint8).reshape(800, 1920, 3)
        lqs.append(frame(BENCH / "source.mp4", "scale=1920:800:flags=bicubic"))
        gts.append(frame(BENCH / "reference.mkv", "null"))
    lq, gt = np.stack(lqs), np.stack(gts)
    RUNS.mkdir(parents=True, exist_ok=True)
    np.savez(cache, lq=lq, gt=gt)
    return lq, gt


@torch.no_grad()
def evaluate(net, out_png):
    lq, gt = eval_frames()
    net.eval()
    x = torch.from_numpy(lq).cuda().permute(0, 3, 1, 2).float() / 255
    with torch.autocast("cuda", torch.bfloat16):
        y = torch.cat([net(x[i:i + 1]) for i in range(len(x))]).float().clamp(0, 1)
    net.train()
    g = torch.from_numpy(gt).cuda().permute(0, 3, 1, 2).float() / 255
    psnr_in = (-10 * torch.log10(F.mse_loss(x, g))).item()
    psnr_out = (-10 * torch.log10(F.mse_loss(y, g))).item()
    # close-up of the most detailed part of each frame: input | output | clean
    out = (y.permute(0, 2, 3, 1) * 255).round().byte().cpu().numpy()
    tiles = []
    for i in range(len(lq)):
        y0, x0 = 300, 820
        crop = lambda a: Image.fromarray(a[i][y0:y0 + 200, x0:x0 + 300]).resize((600, 400), Image.NEAREST)
        row = Image.new("RGB", (1812, 400), "white")
        for j, a in enumerate((lq, out, gt)):
            row.paste(crop(a), (j * 606, 0))
        tiles.append(row)
    grid = Image.new("RGB", (1812, 406 * len(tiles)), "white")
    for i, t in enumerate(tiles):
        grid.paste(t, (0, i * 406))
    grid.save(out_png)
    return psnr_in, psnr_out


# ------------------------------------------------------------------ training
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="cf64x4")
    ap.add_argument("--features", type=int, default=64)
    ap.add_argument("--blocks", type=int, default=4)
    ap.add_argument("--clean-iters", type=int, default=20000)
    ap.add_argument("--detail-iters", type=int, default=30000)
    ap.add_argument("--crop", type=int, default=256)
    ap.add_argument("--batch-pairs", type=int, default=2, help="frame pairs per batch (x crops each)")
    ap.add_argument("--crops", type=int, default=4)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--eval-every", type=int, default=2000)
    ap.add_argument("--gan-weight", type=float, default=0.1)
    ap.add_argument("--perceptual-weight", type=float, default=1.0)
    args = ap.parse_args()

    run = RUNS / args.name
    (run / "eval").mkdir(parents=True, exist_ok=True)
    logf = open(run / "log.txt", "a", encoding="utf-8")

    def log(text):
        line = time.strftime("%H:%M:%S ") + text
        print(line, flush=True)
        logf.write(line + "\n")
        logf.flush()

    meta = json.loads((DATA / "pairs.json").read_text())
    names = [n for m in meta for n in m["pairs"]]
    log(f"{len(names)} training pairs, {torch.cuda.get_device_name()}")
    torch.backends.cudnn.benchmark = True
    torch.backends.cuda.matmul.allow_tf32 = True
    torch.backends.cudnn.allow_tf32 = True

    net = ClearFrameNet(1, args.features, args.blocks).cuda()
    opt = torch.optim.AdamW(net.parameters(), lr=2e-4, betas=(0.9, 0.99), weight_decay=0)
    disc = vgg = opt_d = None
    step, stage = 0, "clean"
    total = args.clean_iters + args.detail_iters
    last = run / "last.pt"
    if last.exists():
        ck = torch.load(last, map_location="cuda", weights_only=True)
        net.load_state_dict(ck["net"])
        opt.load_state_dict(ck["opt"])
        step, stage = ck["step"], ck["stage"]
        log(f"resumed at step {step} ({stage})")

    def start_detail():
        nonlocal disc, vgg, opt_d, opt
        disc = UNetDiscriminator().cuda()
        vgg = VGGFeatures().cuda()
        opt_d = torch.optim.AdamW(disc.parameters(), lr=1e-4, betas=(0.9, 0.99), weight_decay=0)
        opt = torch.optim.AdamW(net.parameters(), lr=1e-4, betas=(0.9, 0.99), weight_decay=0)
        if last.exists():
            ck = torch.load(last, map_location="cuda", weights_only=True)
            if "disc" in ck:
                disc.load_state_dict(ck["disc"])
                opt_d.load_state_dict(ck["opt_d"])
                opt.load_state_dict(ck["opt"])

    if stage == "detail":
        start_detail()

    def save():
        ck = {"net": net.state_dict(), "opt": opt.state_dict(), "step": step, "stage": stage,
              "config": {"features": args.features, "blocks": args.blocks}}
        if disc is not None:
            ck.update(disc=disc.state_dict(), opt_d=opt_d.state_dict())
        torch.save(ck, run / "last.tmp")
        (run / "last.tmp").replace(last)

    def export(kind):
        torch.save({"net": net.state_dict(), "config": {"features": args.features, "blocks": args.blocks},
                    "step": step}, run / f"{kind}.pt")

    data = loader(names, args.batch_pairs, args.crop, args.crops, args.workers)
    it = iter(data)
    t0, seen = time.time(), 0
    avg = {}
    while step < total:
        try:
            lq_u8, gt_u8 = next(it)
        except StopIteration:
            it = iter(data)
            continue
        lq, gt = to_float(lq_u8), to_float(gt_u8)
        if stage == "clean":
            lr = 2e-4 * 0.5 * (1 + math.cos(math.pi * step / args.clean_iters))
            for g in opt.param_groups:
                g["lr"] = max(lr, 1e-6)
            with torch.autocast("cuda", torch.bfloat16):
                out = net(lq)
            loss = torch.sqrt((out.float() - gt) ** 2 + 1e-6).mean()   # Charbonnier
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            parts = {"pix": loss.item()}
        else:
            # generator: stay faithful (pixel + perceptual) and fool the critic
            for p in disc.parameters():
                p.requires_grad_(False)
            with torch.autocast("cuda", torch.bfloat16):
                out = net(lq)
                pix = F.l1_loss(out.float(), gt)
                per = vgg(out.float(), gt)
                adv = F.binary_cross_entropy_with_logits(disc(out).float(), torch.ones_like(out[:, :1]).float())
            g_loss = pix + args.perceptual_weight * per + args.gan_weight * adv
            opt.zero_grad(set_to_none=True)
            g_loss.backward()
            opt.step()
            # critic: tell real frames from generated ones
            for p in disc.parameters():
                p.requires_grad_(True)
            with torch.autocast("cuda", torch.bfloat16):
                real = disc(gt)
                fake = disc(out.detach())
                d_loss = (F.binary_cross_entropy_with_logits(real.float(), torch.ones_like(real).float()) +
                          F.binary_cross_entropy_with_logits(fake.float(), torch.zeros_like(fake).float()))
            opt_d.zero_grad(set_to_none=True)
            d_loss.backward()
            opt_d.step()
            parts = {"pix": pix.item(), "per": per.item(), "adv": adv.item(), "d": d_loss.item()}
        for k, v in parts.items():
            avg[k] = v if k not in avg else avg[k] * 0.98 + v * 0.02
        step += 1
        seen += lq.shape[0]
        if step % 100 == 0:
            el = time.time() - t0
            rate = step_rate = 100 / el
            left = (total - step) / max(rate, 1e-6)
            log(f"{stage:6} step {step}/{total}  " + "  ".join(f"{k} {v:.4f}" for k, v in avg.items()) +
                f"  {step_rate:.1f} it/s  ~{left / 3600:.1f} h left")
            t0 = time.time()
        if step % args.eval_every == 0 or step == args.clean_iters or step == total:
            p_in, p_out = evaluate(net, run / "eval" / f"{stage}-{step:06d}.png")
            log(f"eval step {step}: bench PSNR stretched input {p_in:.2f} dB -> network {p_out:.2f} dB")
            save()
            export(stage)
        if stage == "clean" and step >= args.clean_iters:
            export("clean")
            stage = "detail"
            save()
            start_detail()
            log("stage 2: detail (perceptual + adversarial)")
            avg = {}
    export(stage)
    save()
    log("finished")


if __name__ == "__main__":
    main()
