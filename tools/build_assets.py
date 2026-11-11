#!/usr/bin/env python3
"""Generates NovaDeck's graphics: wallpaper, app icons, splash images.

Only needed when you change the design; the generated files are committed.
Requires numpy and Pillow:  python3 tools/build_assets.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
ICONS_JSON = ROOT / "novadeck" / "web" / "icons.json"
WEB_IMG = ROOT / "novadeck" / "web" / "img"
ASSET_ICONS = ROOT / "assets" / "icons"
SPLASH = ROOT / "theme" / "look-and-feel" / "com.technova.novadeck.desktop" / "contents"
INK = "#F5F7FA"

# ---------------------------------------------------------------- icons

TILE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="128" height="128" viewBox="0 0 128 128">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#2c2e34"/>
      <stop offset="0.55" stop-color="#121316"/>
      <stop offset="1" stop-color="#060607"/>
    </linearGradient>
    <radialGradient id="sheen" cx="0.28" cy="0.18" r="0.85">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.22"/>
      <stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="rim" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.65"/>
      <stop offset="0.45" stop-color="#ffffff" stop-opacity="0.08"/>
      <stop offset="1" stop-color="#ffffff" stop-opacity="0.28"/>
    </linearGradient>
  </defs>
  <rect x="6" y="6" width="116" height="116" rx="30" fill="url(#bg)"/>
  <rect x="6" y="6" width="116" height="116" rx="30" fill="url(#sheen)"/>
  <rect x="6.75" y="6.75" width="114.5" height="114.5" rx="29.25" fill="none" stroke="url(#rim)" stroke-width="1.5"/>
  <g transform="translate(28 28) scale(3)" fill="none" stroke="{ink}" stroke-linecap="round" stroke-linejoin="round">
    <g stroke-width="3.4" stroke-opacity="0.13">{glyph}</g>
    <g stroke-width="1.55">{glyph}</g>
  </g>
</svg>
"""

LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24">
  <g fill="none" stroke="{ink}" stroke-linecap="round" stroke-linejoin="round">
    <g stroke-width="2.6" stroke-opacity="0.16">{glyph}</g>
    <g stroke-width="1.3">{glyph}</g>
  </g>
</svg>
"""


def write_icons(icons: dict[str, str]) -> None:
    ASSET_ICONS.mkdir(parents=True, exist_ok=True)
    WEB_IMG.mkdir(parents=True, exist_ok=True)
    for old in ASSET_ICONS.glob("novadeck*.svg"):
        old.unlink()
    for name, glyph in icons.items():
        glyph = glyph.replace("currentColor", INK)
        target = "novadeck.svg" if name == "nova" else f"novadeck-{name}.svg"
        (ASSET_ICONS / target).write_text(TILE_SVG.format(ink=INK, glyph=glyph), encoding="utf-8")
    (WEB_IMG / "novadeck.svg").write_text((ASSET_ICONS / "novadeck.svg").read_text(), encoding="utf-8")
    splash_images = SPLASH / "splash" / "images"
    splash_images.mkdir(parents=True, exist_ok=True)
    (splash_images / "logo.svg").write_text(LOGO_SVG.format(size=256, ink=INK, glyph=icons["nova"]), encoding="utf-8")
    print(f"icons: {len(icons)} tile icons")


# ---------------------------------------------------------------- image helpers

def box_blur_1d(a: np.ndarray, radius: int, axis: int) -> np.ndarray:
    if radius < 1:
        return a
    pad = [(0, 0)] * a.ndim
    pad[axis] = (radius + 1, radius)
    p = np.pad(a, pad, mode="edge")
    c = np.cumsum(p, axis=axis, dtype=np.float64)
    hi = np.take(c, range(2 * radius + 1, c.shape[axis]), axis=axis)
    lo = np.take(c, range(0, c.shape[axis] - 2 * radius - 1), axis=axis)
    return ((hi - lo) / (2 * radius + 1)).astype(np.float32)


def blur(a: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian-ish blur (3 box passes); downsamples first for big radii."""
    if sigma <= 0.3:
        return a
    factor = max(1, int(sigma // 4))
    h, w = a.shape[:2]
    work = a
    if factor > 1:
        hh, ww = h // factor * factor, w // factor * factor
        work = a[:hh, :ww].reshape(hh // factor, factor, ww // factor, factor, *a.shape[2:]).mean(axis=(1, 3))
    s = sigma / factor
    radius = max(1, int(round(math.sqrt(12 * s * s / 3 + 1) - 1) // 2))
    for _ in range(3):
        work = box_blur_1d(work, radius, 0)
        work = box_blur_1d(work, radius, 1)
    if factor > 1:
        chans = work if work.ndim == 3 else work[..., None]
        up = [np.asarray(Image.fromarray(chans[..., i].astype(np.float32), "F").resize((w, h), Image.BILINEAR))
              for i in range(chans.shape[2])]
        work = np.stack(up, axis=-1)
        if a.ndim == 2:
            work = work[..., 0]
    return work.astype(np.float32)


def rounded_rect_sdf(xx, yy, cx, cy, w, h, r, angle_deg):
    t = math.radians(angle_deg)
    x, y = xx - cx, yy - cy
    xr = x * math.cos(t) + y * math.sin(t)
    yr = -x * math.sin(t) + y * math.cos(t)
    qx = np.abs(xr) - (w / 2 - r)
    qy = np.abs(yr) - (h / 2 - r)
    outside = np.sqrt(np.maximum(qx, 0) ** 2 + np.maximum(qy, 0) ** 2)
    inside = np.minimum(np.maximum(qx, qy), 0)
    return outside + inside - r, xr, yr


def gauss(xx, yy, cx, cy, radius):
    return np.exp(-(((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * radius**2))).astype(np.float32)


# ---------------------------------------------------------------- wallpaper

def wallpaper(width=2560, height=1600, seed=7) -> Image.Image:
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    nx, ny = xx / width, yy / height

    # deep near-black base with a whisper of cool grey
    base = 0.018 + 0.022 * (1 - ny) + 0.012 * (1 - nx)
    img = np.stack([base * 0.97, base * 0.985, base * 1.03], axis=-1)

    def add_light(cx, cy, radius, amount, tint=(1, 1, 1)):
        g = gauss(xx, yy, cx * width, cy * height, radius * width) * amount
        for i in range(3):
            img[..., i] += g * tint[i]

    add_light(0.80, 0.12, 0.30, 0.20, (0.96, 0.98, 1.0))
    add_light(0.10, 0.92, 0.26, 0.09)
    add_light(0.48, 0.58, 0.14, 0.05)

    # glowing neon ring
    cx, cy, rad = 0.705 * width, 0.40 * height, 0.255 * height
    dist = np.abs(np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) - rad)
    ring = np.clip(1.6 - dist, 0, 1)
    glow = blur(ring, 10) * 2.2 + blur(ring, 40) * 3.5
    for i in range(3):
        img[..., i] += ring * 0.9 + glow * 0.55

    # matte spheres for depth
    def sphere(scx, scy, r, light=0.34):
        d = np.sqrt((xx - scx) ** 2 + (yy - scy) ** 2)
        m = np.clip(r + 0.5 - d, 0, 1)
        lx, ly = scx - r * 0.45, scy - r * 0.55
        shade = np.clip(1 - np.sqrt((xx - lx) ** 2 + (yy - ly) ** 2) / (r * 1.9), 0, 1) ** 1.6
        rim = np.clip(1 - np.abs(d - r) / 3.0, 0, 1) * 0.18
        col = 0.025 + light * shade + rim
        for i in range(3):
            img[..., i] = img[..., i] * (1 - m) + col * m

    sphere(0.815 * width, 0.66 * height, 0.135 * height)
    sphere(0.385 * width, 0.225 * height, 0.06 * height, 0.26)
    sphere(0.18 * width, 0.58 * height, 0.035 * height, 0.2)

    # frosted glass panels (back to front)
    panels = [
        (0.86, 0.26, 0.17, 0.20, 0.028, 12),
        (0.665, 0.535, 0.30, 0.34, 0.034, -7),
        (0.33, 0.73, 0.15, 0.15, 0.024, -15),
    ]
    for pcx, pcy, pw, ph, pr, angle in panels:
        sdf, xr, yr = rounded_rect_sdf(xx, yy, pcx * width, pcy * height, pw * width, ph * width, pr * width, angle)
        mask = np.clip(0.5 - sdf, 0, 1)
        shadow = blur(np.roll(mask, 34, axis=0), 38)
        img *= (1 - 0.55 * shadow)[..., None]
        frosted = blur(img, 26)
        # top-left lighter frost, fading to bottom-right
        u = (xr / (pw * width) + yr / (ph * width)) * 0.5 + 0.5
        frost = 0.055 - 0.035 * np.clip(u, 0, 1)
        glass = frosted * 0.9 + frost[..., None]
        img = img * (1 - mask[..., None]) + glass * mask[..., None]
        edge = np.clip(1.2 - np.abs(sdf + 0.6), 0, 1)
        edge_light = (0.62 - 0.5 * np.clip(u, 0, 1)) * edge
        img += edge_light[..., None]

    # vignette & grain
    vig = 1 - 0.42 * np.clip(((nx - 0.55) ** 2 + (ny - 0.45) ** 2) * 1.6, 0, 1)
    img *= vig[..., None]
    img += rng.normal(0, 0.009, size=(height, width, 1)).astype(np.float32)
    img = np.clip(img, 0, 1) ** (1 / 1.12)
    return Image.fromarray((img * 255 + 0.5).astype(np.uint8), "RGB")


# ---------------------------------------------------------------- splash images

def glow_png(size=512) -> Image.Image:
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    c = (size - 1) / 2
    d = np.sqrt((xx - c) ** 2 + (yy - c) ** 2) / c
    alpha = np.clip(1 - d, 0, 1) ** 2.4 * 0.55
    rgba = np.zeros((size, size, 4), np.uint8)
    rgba[..., :3] = 255
    rgba[..., 3] = (alpha * 255).astype(np.uint8)
    return Image.fromarray(rgba, "RGBA")


def star_points(cx, cy, scale):
    """Samples the 'nova' four-point star outline (24-unit path) as a polygon."""
    segs = [
        ((12, 2.5), (12.55, 7.8), (16.2, 11.45), (21.5, 12)),
        ((21.5, 12), (16.2, 12.55), (12.55, 16.2), (12, 21.5)),
        ((12, 21.5), (11.45, 16.2), (7.8, 12.55), (2.5, 12)),
        ((2.5, 12), (7.8, 11.45), (11.45, 7.8), (12, 2.5)),
    ]
    pts = []
    for p0, p1, p2, p3 in segs:
        for i in range(24):
            t = i / 24
            x = (1 - t) ** 3 * p0[0] + 3 * (1 - t) ** 2 * t * p1[0] + 3 * (1 - t) * t * t * p2[0] + t**3 * p3[0]
            y = (1 - t) ** 3 * p0[1] + 3 * (1 - t) ** 2 * t * p1[1] + 3 * (1 - t) * t * t * p2[1] + t**3 * p3[1]
            pts.append((cx + (x - 12) * scale, cy + (y - 12) * scale))
    return pts


def splash_preview(width=1280, height=800) -> Image.Image:
    img = Image.new("RGB", (width, height), (5, 5, 6))
    glow = glow_png(700)
    img.paste(glow, (width // 2 - 350, height // 2 - 400), glow)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    pts = star_points(width / 2, height / 2 - 70, 5.2)
    draw.line(pts + [pts[0]], fill=(245, 247, 250, 255), width=5, joint="curve")
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    text = "N O V A   D E C K"
    tw = draw.textlength(text, font=font)
    draw.text(((width - tw) / 2, height / 2 + 30), text, fill=(245, 247, 250, 255), font=font)
    draw.rounded_rectangle((width / 2 - 110, height / 2 + 100, width / 2 + 110, height / 2 + 102), 1, fill=(255, 255, 255, 40))
    draw.rounded_rectangle((width / 2 - 110, height / 2 + 100, width / 2 + 30, height / 2 + 102), 1, fill=(245, 247, 250, 255))
    img.paste(layer, (0, 0), layer)
    return img


def main() -> None:
    icons = json.loads(ICONS_JSON.read_text(encoding="utf-8"))
    write_icons(icons)

    WEB_IMG.mkdir(parents=True, exist_ok=True)
    wp = wallpaper()
    wp.save(WEB_IMG / "wallpaper.jpg", quality=90, optimize=True, progressive=True, subsampling=0)
    print(f"wallpaper: {(WEB_IMG / 'wallpaper.jpg').stat().st_size // 1024} KB")

    (SPLASH / "splash" / "images").mkdir(parents=True, exist_ok=True)
    glow_png().save(SPLASH / "splash" / "images" / "glow.png", optimize=True)
    (SPLASH / "previews").mkdir(parents=True, exist_ok=True)
    splash_preview().save(SPLASH / "previews" / "splash.png", optimize=True)
    wp.resize((640, 400), Image.LANCZOS).save(SPLASH / "previews" / "preview.png", optimize=True)
    print("splash: ok")


if __name__ == "__main__":
    main()
