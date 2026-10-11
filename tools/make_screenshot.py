#!/usr/bin/env python3
"""Produce assets/screenshot.png from a raw terminal capture.

The capture was taken on a desktop with a translucent terminal, so the
wallpaper shows through and the dim ANSI-grey text is hard to read on a
README page. This flattens the image onto a solid background:

  1. a luminance ramp keeps only pixels bright enough to be text
  2. a median-filtered local-detail mask keeps text strokes but rejects
     smooth scenery, which the first step alone would let through
  3. the surviving pixels are composited onto a near-black background and
     brightened, preserving hue so the amber logo and green accent stay

Usage:
    python3 tools/make_screenshot.py <capture.png> [output.png]

Defaults are the capture and output used for the committed asset.
"""

from __future__ import annotations

import pathlib
import sys

from PIL import Image, ImageChops, ImageFilter

DEFAULT_SRC = pathlib.Path.home() / "Pictures/Screenshots/20261011_035119.png"
DEFAULT_OUT = pathlib.Path(__file__).resolve().parent.parent / "assets/screenshot.png"

BG = (13, 13, 20)        # near-black, faintly blue
LO, HI = 52, 92          # luminance ramp: below is background, above is text
DETAIL_DIV = 62          # higher kills more scenery; text detail is far larger
BOOST = 1.55             # lift the dim grey lines into readable range
CROP = (62, 22, 1412, 694)


def build(src: pathlib.Path, dst: pathlib.Path) -> None:
    img = Image.open(src).convert("RGB")
    lum = img.convert("L")

    keep = lum.point(lambda v: max(0, min(255, int((v - LO) / (HI - LO) * 255))))

    # median blur estimates the local background: text is removed, scenery stays
    est = img.filter(ImageFilter.MedianFilter(size=31)).convert("L")
    detail = ImageChops.subtract(lum, est)
    detail = detail.point(lambda v: max(0, min(255, int(v / DETAIL_DIV * 255))))

    alpha = ImageChops.multiply(keep, detail)
    text = Image.composite(img, Image.new("RGB", img.size, (0, 0, 0)), alpha)
    text = text.point(lambda v: min(255, int(v * BOOST)))
    flat = ImageChops.add(text, Image.new("RGB", img.size, BG))

    dst.parent.mkdir(parents=True, exist_ok=True)
    flat.crop(CROP).save(dst, optimize=True)
    print(f"wrote {dst} ({flat.crop(CROP).size[0]}x{flat.crop(CROP).size[1]})")


def main() -> int:
    src = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SRC
    dst = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUT
    if not src.exists():
        print(f"make_screenshot: capture not found: {src}", file=sys.stderr)
        return 1
    build(src, dst)
    return 0


if __name__ == "__main__":
    sys.exit(main())