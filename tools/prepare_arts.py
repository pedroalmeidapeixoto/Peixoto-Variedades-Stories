"""Turn finished product arts of any size into Story-ready 1080x1920 JPGs.

Instagram covers about 250 px at the top and bottom of a Story with its own interface, so the
content of each art must sit inside that area. Two layouts achieve that without a flat frame:

* **bleed** (arts that are already about 9:16): the art is scaled down a little and its empty
  edge margins are *stretched* back out to the Story's edges, so the wave decoration simply
  continues to the border. Only margins with nothing in them are stretched; the logo, text and
  product are never distorted.
* **card** (3:4, 2:3, 4:5, ...): the art becomes a rounded card with a soft shadow over a blurred,
  tinted copy of itself, so the background carries the colours of the product.

    python tools/prepare_arts.py SRC_DIR OUT_DIR [--manifest manifest.json]

The optional manifest is an ordered list of {"file": "<source file name>", "name": "<slug>"}, plus
optional "layout" ("bleed" or "card") and "tint" ("cream" or "#rrggbb", the card background
colour) to override the automatic choices. Output files are named `NN-<slug>.jpg`, so sorting by
name gives the posting order. Needs Pillow and numpy (`pip install pillow numpy`).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps

WIDTH, HEIGHT = 1080, 1920
STORY_RATIO = WIDTH / HEIGHT

BLEED_SCALE = 0.90  # the art is shrunk to 90% and the margins stretched back to the edges
BLEED_TOP = 100  # room above the art: puts the logo about 170 px from the top
BLEED_SIDE_BAND = 0.16  # share of the width over which the side decoration is stretched
MIN_BAND = 6  # never stretch less than this many pixels of margin
SOFTEN_SIGMAS = (7, 26)  # horizontal blurs for the stretched rows only (near, far): hide streaks

CARD_BOX = (1000, 1420)  # the card must fit this safe box (width, height)
CARD_TOP = 250
CARD_RADIUS = 34


def smooth(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def axis_map(
    out_len: int,
    art_len: int,
    pad_lead: float,
    pad_trail: float,
    band_lead: float,
    band_trail: float,
) -> np.ndarray:
    """For every output pixel along one axis, the source coordinate it is taken from.

    The art is placed with `pad_lead` / `pad_trail` of extra room on each side. That room is
    absorbed by stretching only the first `band_lead` and last `band_trail` pixels of the art,
    with a smooth ease so no kink shows where the stretching starts.
    """
    u = np.linspace(0, art_len, 8001)
    extra = pad_lead * smooth(u / band_lead) + pad_trail * smooth(
        (u - (art_len - band_trail)) / band_trail
    )
    return np.interp(np.arange(out_len) + 0.5, u + extra, u)


def sample(a: np.ndarray, coords: np.ndarray, axis: int) -> np.ndarray:
    """Linear resampling of `a` along `axis` at the given source coordinates."""
    c = np.clip(coords - 0.5, 0, a.shape[axis] - 1.001)
    i0 = np.floor(c).astype(int)
    f = c - i0
    if axis == 1:
        return a[:, i0] * (1 - f)[None, :, None] + a[:, i0 + 1] * f[None, :, None]
    return a[i0] * (1 - f)[:, None, None] + a[i0 + 1] * f[:, None, None]


def content_rows(a: np.ndarray) -> tuple[int, int]:
    """First and last row with anything in the middle of the art (logo at the top, footer)."""
    h, w, _ = a.shape
    mid = a[:, int(w * 0.3) : int(w * 0.7)].astype(float)
    top_ref = np.median(mid[:6].reshape(-1, 3), axis=0)
    bottom_ref = np.median(mid[-6:].reshape(-1, 3), axis=0)
    busy_top = (np.abs(mid - top_ref).max(axis=2) > 14).sum(axis=1) > 1
    busy_bottom = (np.abs(mid - bottom_ref).max(axis=2) > 14).sum(axis=1) > 1
    return int(np.argmax(busy_top)), int(h - 1 - np.argmax(busy_bottom[::-1]))


def soften_rows(a: np.ndarray, start: int, stop: int, ramp: np.ndarray, sigma: float) -> None:
    """Blur rows [start, stop) sideways, fading in with `ramp` (0 = untouched, 1 = fully blurred).

    Stretching a margin 15x turns every pixel-wide detail into a vertical streak; a gentle
    horizontal blur turns the streaks into the soft colour wash they should have been.
    """
    radius = int(sigma * 3)
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    kernel /= kernel.sum()
    block = a[start:stop]
    padded = np.pad(block, ((0, 0), (radius, radius), (0, 0)), mode="edge")
    blurred = sum(kernel[i] * padded[:, i : i + block.shape[1]] for i in range(len(kernel)))
    w = ramp[:, None, None]
    a[start:stop] = block * (1 - w) + blurred * w


def bleed(art: Image.Image) -> Image.Image:
    aw, ah = round(WIDTH * BLEED_SCALE), round(HEIGHT * BLEED_SCALE)
    small = np.asarray(art.convert("RGB").resize((aw, ah), Image.LANCZOS), dtype=float)
    first, last = content_rows(small)
    pad_x = (WIDTH - aw) / 2
    pad_bottom = HEIGHT - ah - BLEED_TOP
    band_x = BLEED_SIDE_BAND * aw
    cols = axis_map(WIDTH, aw, pad_x, pad_x, band_x, band_x)
    rows = axis_map(
        HEIGHT,
        ah,
        BLEED_TOP,
        pad_bottom,
        max(MIN_BAND, first - 4),
        max(MIN_BAND, ah - last - 4),
    )
    out = sample(sample(small, cols, 1), rows, 0)
    # fade the softening in from the art's own edge (untouched) to the Story's edge (full): a
    # light blur first, then a wide one that only reaches the outermost part of the margin
    top_end = BLEED_TOP + 4
    bottom_start = HEIGHT - pad_bottom - 4
    up = np.arange(top_end)[::-1] / top_end  # 1 at the Story's top edge, 0 at the art's edge
    down = np.arange(HEIGHT - bottom_start) / (HEIGHT - bottom_start)
    for sigma, start in zip(SOFTEN_SIGMAS, (0.0, 0.45), strict=True):
        ramp_up = smooth((up - start) / (1 - start))
        ramp_down = smooth((down - start) / (1 - start))
        soften_rows(out, 0, top_end, ramp_up, sigma)
        soften_rows(out, bottom_start, HEIGHT, ramp_down, sigma)
    img = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    return img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=45, threshold=2))


def edge_color(img: Image.Image) -> tuple[int, int, int]:
    """Dominant colour along the left and right edges, ignoring plain paper."""
    a = np.asarray(img.convert("RGB").resize((200, 200)), dtype=int)
    ring = np.concatenate([a[:, :10].reshape(-1, 3), a[:, -10:].reshape(-1, 3)])
    creamy = (ring.min(axis=1) > 170) & (ring.max(axis=1) - ring.min(axis=1) < 60)
    pool = ring[~creamy] if (~creamy).mean() >= 0.10 else ring
    bins = (pool // 32) @ np.array([64, 8, 1])
    values, counts = np.unique(bins, return_counts=True)
    r, g, b = np.median(pool[bins == values[counts.argmax()]], axis=0)
    return int(r), int(g), int(b)


def cream_color(img: Image.Image) -> tuple[int, int, int]:
    """Median of the light, unsaturated pixels along the edges (the art's own paper colour)."""
    a = np.asarray(img.convert("RGB").resize((200, 200)), dtype=int)
    ring = np.concatenate([a[:, :10].reshape(-1, 3), a[:, -10:].reshape(-1, 3)])
    light = ring[(ring.min(axis=1) > 170) & (ring.max(axis=1) - ring.min(axis=1) < 60)]
    r, g, b = np.median(light if len(light) else ring, axis=0)
    return int(r), int(g), int(b)


def parse_tint(value: str | None, art: Image.Image) -> tuple[int, int, int]:
    if value is None:
        return edge_color(art)
    if value == "cream":
        return cream_color(art)
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def card(art: Image.Image, tint: str | None = None) -> Image.Image:
    art = art.convert("RGB")
    cover = ImageOps.fit(art, (WIDTH // 4, HEIGHT // 4), Image.LANCZOS).resize(
        (WIDTH, HEIGHT), Image.BICUBIC
    )
    bg = cover.filter(ImageFilter.GaussianBlur(80))
    bg = ImageEnhance.Color(bg).enhance(1.5)
    bg = Image.blend(bg, Image.new("RGB", (WIDTH, HEIGHT), parse_tint(tint, art)), 0.4)
    yy, xx = np.mgrid[0:HEIGHT, 0:WIDTH]
    dist = np.sqrt(((xx - WIDTH / 2) / (WIDTH / 2)) ** 2 + ((yy - HEIGHT / 2) / (HEIGHT / 2)) ** 2)
    vignette = np.clip(1.0 - 0.34 * np.clip(dist - 0.30, 0, 1), 0.55, 1)[..., None]
    bg = Image.fromarray(
        np.clip(np.asarray(bg, dtype=float) * vignette * 0.92, 0, 255).astype(np.uint8)
    )

    w, h = art.size
    scale = min(CARD_BOX[0] / w, CARD_BOX[1] / h)
    size = (round(w * scale), round(h * scale))
    face = art.resize(size, Image.LANCZOS)
    if scale > 1:
        face = face.filter(ImageFilter.UnsharpMask(radius=1.0, percent=40, threshold=2))
    x, y = (WIDTH - size[0]) // 2, CARD_TOP + (CARD_BOX[1] - size[1]) // 2

    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size[0] - 1, size[1] - 1), CARD_RADIUS, fill=255)
    shadow = Image.new("L", (WIDTH, HEIGHT), 0)
    shadow.paste(Image.new("L", size, 150), (x, y + 14), mask)
    bg.paste(
        Image.new("RGB", (WIDTH, HEIGHT), (0, 0, 0)),
        (0, 0),
        shadow.filter(ImageFilter.GaussianBlur(26)),
    )
    bg.paste(face, (x, y), mask)
    return bg


def compose(src: Image.Image, layout: str | None = None, tint: str | None = None) -> Image.Image:
    w, h = src.size
    if layout is None:
        layout = "bleed" if abs(w / h - STORY_RATIO) < 0.03 else "card"
    return bleed(src) if layout == "bleed" else card(src, tint)


def main() -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("src", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    if args.manifest:
        entries = json.loads(args.manifest.read_text(encoding="utf-8"))
    else:
        entries = [{"file": p.name, "name": p.stem} for p in sorted(args.src.iterdir())]

    args.out.mkdir(parents=True, exist_ok=True)
    digits = max(2, len(str(len(entries))))
    for number, entry in enumerate(entries, start=1):
        target = args.out / f"{number:0{digits}d}-{entry['name']}.jpg"
        art = Image.open(args.src / entry["file"])
        compose(art, entry.get("layout"), entry.get("tint")).save(
            target, "JPEG", quality=93, subsampling=0, optimize=True
        )
        print(target.name)


if __name__ == "__main__":
    main()
