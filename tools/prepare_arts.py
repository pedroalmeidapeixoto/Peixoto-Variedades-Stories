"""Turn finished product arts of any size into Story-ready 1080x1920 JPGs.

Instagram covers the top (~250 px) and bottom (~250 px) of a Story with its own interface, so
each art is scaled to sit inside that safe area and surrounded by a flat frame in the art's own
edge colour (the wave decoration blends into it, so the art reads as a card).

    python tools/prepare_arts.py SRC_DIR OUT_DIR [--manifest manifest.json]

The optional manifest is an ordered list of {"file": "<source file name>", "name": "<slug>"},
plus an optional "frame" ("cream" or "#rrggbb") to override the automatic frame colour.
Output files are named `NN-<slug>.jpg`, so sorting by name gives the posting order. Files not
in the manifest are skipped. Needs Pillow and numpy (`pip install pillow numpy`).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

WIDTH, HEIGHT = 1080, 1920
STORY_RATIO = WIDTH / HEIGHT
# Layout proven on the first published Story: 86% scale, logo ~170 px from the top.
TALL_SCALE, TALL_TOP = 0.86, 118
# Other shapes are fitted inside the safe box instead.
SAFE_BOX = (1000, 1420)  # width, height
SAFE_TOP = 250


def frame_color(img: Image.Image) -> tuple[int, int, int]:
    """Dominant non-cream colour along the art's outer edge (cream when there is none)."""
    a = np.asarray(img.convert("RGB"), dtype=int)
    edge = np.concatenate(
        [
            a[:, :8].reshape(-1, 3),
            a[:, -8:].reshape(-1, 3),
            a[:8].reshape(-1, 3),
            a[-8:].reshape(-1, 3),
        ]
    )
    spread = edge.max(axis=1) - edge.min(axis=1)
    creamy = (edge.min(axis=1) > 170) & (spread < 60)
    pool = edge[~creamy] if (~creamy).mean() >= 0.10 else edge
    bins = (pool // 32) @ np.array([64, 8, 1])
    values, counts = np.unique(bins, return_counts=True)
    chosen = pool[bins == values[counts.argmax()]]
    r, g, b = np.median(chosen, axis=0)
    return int(r), int(g), int(b)


def cream_color(img: Image.Image) -> tuple[int, int, int]:
    """Median of the light, unsaturated pixels along the edge (the art's own paper colour)."""
    a = np.asarray(img.convert("RGB"), dtype=int)
    edge = np.concatenate([a[:, :8].reshape(-1, 3), a[:, -8:].reshape(-1, 3)])
    light = edge[(edge.min(axis=1) > 170) & (edge.max(axis=1) - edge.min(axis=1) < 60)]
    r, g, b = np.median(light if len(light) else edge, axis=0)
    return int(r), int(g), int(b)


def parse_frame(value: str | None, art: Image.Image) -> tuple[int, int, int]:
    """`None` = automatic, "cream" = the art's paper colour, "#rrggbb" = that colour."""
    if value is None:
        return frame_color(art)
    if value == "cream":
        return cream_color(art)
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def compose(src: Image.Image, frame: str | None = None) -> Image.Image:
    art = src.convert("RGB")
    w, h = art.size
    if abs(w / h - STORY_RATIO) < 0.03:
        size = (round(WIDTH * TALL_SCALE), round(HEIGHT * TALL_SCALE))
        top = TALL_TOP
    else:
        scale = min(SAFE_BOX[0] / w, SAFE_BOX[1] / h)
        size = (round(w * scale), round(h * scale))
        top = SAFE_TOP + (SAFE_BOX[1] - size[1]) // 2
    resized = art.resize(size, Image.LANCZOS)
    if size[0] > w:  # upscaled: recover a little crispness
        resized = resized.filter(ImageFilter.UnsharpMask(radius=1.0, percent=40, threshold=2))
    canvas = Image.new("RGB", (WIDTH, HEIGHT), parse_frame(frame, art))
    canvas.paste(resized, ((WIDTH - size[0]) // 2, top))
    return canvas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("src", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()

    if args.manifest:
        entries = json.loads(args.manifest.read_text(encoding="utf-8"))
    else:
        entries = [{"file": p.name, "name": p.stem} for p in sorted(args.src.iterdir())]

    args.out.mkdir(parents=True, exist_ok=True)
    width = len(str(len(entries)))
    for number, entry in enumerate(entries, start=1):
        target = args.out / f"{number:0{max(width, 2)}d}-{entry['name']}.jpg"
        compose(Image.open(args.src / entry["file"]), entry.get("frame")).save(
            target, "JPEG", quality=92, subsampling=0, optimize=True
        )
        print(target.name)


if __name__ == "__main__":
    main()
