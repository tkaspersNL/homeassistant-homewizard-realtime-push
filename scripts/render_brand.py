"""Render the integration's brand icons as SVG and PNG.

The light-mode icon is a dark tile with a gradient bolt; the dark-mode icon is a
gradient tile with a dark bolt. Both come from the same geometry below, so the
SVG sources and the PNGs Home Assistant loads never drift apart.

Needs numpy and Pillow (both in the test environment). Run from the repo root:

    python scripts/render_brand.py
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SVG_DIR = ROOT / "assets" / "brand"
PNG_DIR = ROOT / "custom_components" / "homewizard_realtime_push" / "brand"

# Design grid is 170x170 units.
UNITS = 170
TILE_RADIUS = 36
LIME = (0x9B, 0xF5, 0x7E)
AQUA = (0x3B, 0xD0, 0xC9)
TILE_DARK = (0x0B, 0x2A, 0x2E)
INK = (0x04, 0x34, 0x2C)
WAVE_ALPHA = 0.6
BOLT = [(86, 32), (42, 96), (70, 96), (60, 138), (108, 72), (80, 72)]
# Each wave: top end, bottom end (same x) and radius.
WAVES = [((128, 60), (128, 110), 35), ((140, 48), (140, 122), 52)]
WAVE_WIDTH = 7
# Bolt and waves share one diagonal gradient; the tile uses the full diagonal.
MARK_GRADIENT = ((40, 30), (150, 140))
TILE_GRADIENT = ((0, 0), (UNITS, UNITS))
SUPERSAMPLE = 4

Point = tuple[float, float]


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _wave_path(top: Point, bottom: Point, radius: float) -> str:
    return (
        f"M{top[0]:g},{top[1]:g} "
        f"A{radius:g},{radius:g} 0 0 1 {bottom[0]:g},{bottom[1]:g}"
    )


def _gradient_def(gid: str, line: tuple[Point, Point]) -> str:
    (x1, y1), (x2, y2) = line
    return (
        f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" '
        f'x1="{x1:g}" y1="{y1:g}" x2="{x2:g}" y2="{y2:g}">'
        f'<stop offset="0" stop-color="{_hex(LIME)}"/>'
        f'<stop offset="1" stop-color="{_hex(AQUA)}"/></linearGradient>'
    )


def svg(dark_mode: bool) -> str:
    """Return the icon as an SVG document."""
    bolt = " ".join(f"{x:g},{y:g}" for x, y in BOLT)
    if dark_mode:
        defs = _gradient_def("tile", TILE_GRADIENT)
        tile_fill, mark, wave_extra = (
            "url(#tile)",
            _hex(INK),
            f' opacity="{WAVE_ALPHA}"',
        )
    else:
        defs = _gradient_def("mark", MARK_GRADIENT)
        tile_fill, mark, wave_extra = _hex(TILE_DARK), "url(#mark)", ""
    waves = "".join(
        f'<path d="{_wave_path(top, bottom, r)}" fill="none" stroke="{mark}" '
        f'stroke-width="{WAVE_WIDTH}" stroke-linecap="round"{wave_extra}/>'
        for top, bottom, r in WAVES
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {UNITS} {UNITS}">'
        f"<defs>{defs}</defs>"
        f'<rect width="{UNITS}" height="{UNITS}" rx="{TILE_RADIUS}" '
        f'fill="{tile_fill}"/>'
        f'<polygon points="{bolt}" fill="{mark}"/>{waves}</svg>\n'
    )


def _gradient(px: int, scale: float, line: tuple[Point, Point]) -> Image.Image:
    (x1, y1), (x2, y2) = line
    ys, xs = np.mgrid[0:px, 0:px] / scale
    dx, dy = x2 - x1, y2 - y1
    t = np.clip(((xs - x1) * dx + (ys - y1) * dy) / (dx * dx + dy * dy), 0, 1)
    t = t[..., None]
    rgb = np.array(LIME, dtype=float) * (1 - t) + np.array(AQUA, dtype=float) * t
    alpha = np.full((px, px, 1), 255.0)
    return Image.fromarray(np.concatenate([rgb, alpha], axis=2).astype(np.uint8))


def _draw_bolt(
    draw: ImageDraw.ImageDraw, scale: float, fill: int | tuple[int, ...]
) -> None:
    draw.polygon([(x * scale, y * scale) for x, y in BOLT], fill=fill)


def _draw_waves(
    draw: ImageDraw.ImageDraw, scale: float, fill: int | tuple[int, ...]
) -> None:
    for (x, top), (_, bottom), radius in WAVES:
        half = (bottom - top) / 2
        cx, cy = x - math.sqrt(radius**2 - half**2), top + half
        angle = math.degrees(math.atan2(half, x - cx))
        # Pillow draws the stroke inward from the box, so grow it by half a width.
        outer = (radius + WAVE_WIDTH / 2) * scale
        draw.arc(
            (
                cx * scale - outer,
                cy * scale - outer,
                cx * scale + outer,
                cy * scale + outer,
            ),
            -angle,
            angle,
            fill=fill,
            width=round(WAVE_WIDTH * scale),
        )
        cap = WAVE_WIDTH / 2 * scale
        for ey in (top, bottom):
            draw.ellipse(
                (x * scale - cap, ey * scale - cap, x * scale + cap, ey * scale + cap),
                fill=fill,
            )


def png(dark_mode: bool, size: int) -> Image.Image:
    """Return the icon as an RGBA image of the given size."""
    px = size * SUPERSAMPLE
    scale = px / UNITS
    tile = Image.new("L", (px, px), 0)
    ImageDraw.Draw(tile).rounded_rectangle(
        (0, 0, px - 1, px - 1), radius=TILE_RADIUS * scale, fill=255
    )
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    if dark_mode:
        img.paste(_gradient(px, scale, TILE_GRADIENT), (0, 0), tile)
        bolt = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        _draw_bolt(ImageDraw.Draw(bolt), scale, (*INK, 255))
        waves = Image.new("RGBA", (px, px), (0, 0, 0, 0))
        _draw_waves(ImageDraw.Draw(waves), scale, (*INK, round(255 * WAVE_ALPHA)))
        img = Image.alpha_composite(Image.alpha_composite(img, bolt), waves)
    else:
        img.paste(Image.new("RGBA", (px, px), (*TILE_DARK, 255)), (0, 0), tile)
        marks = Image.new("L", (px, px), 0)
        _draw_bolt(ImageDraw.Draw(marks), scale, 255)
        _draw_waves(ImageDraw.Draw(marks), scale, 255)
        img.paste(_gradient(px, scale, MARK_GRADIENT), (0, 0), marks)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    """Write the SVG sources and the PNGs Home Assistant loads."""
    SVG_DIR.mkdir(parents=True, exist_ok=True)
    PNG_DIR.mkdir(parents=True, exist_ok=True)
    for dark_mode, prefix in ((False, ""), (True, "dark_")):
        (SVG_DIR / f"{prefix}icon.svg").write_text(svg(dark_mode))
        png(dark_mode, 256).save(PNG_DIR / f"{prefix}icon.png", optimize=True)
        png(dark_mode, 512).save(PNG_DIR / f"{prefix}icon@2x.png", optimize=True)


if __name__ == "__main__":
    main()
