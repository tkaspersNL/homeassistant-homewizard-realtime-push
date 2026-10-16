"""Tests for the brand images Home Assistant loads from the integration folder."""

from __future__ import annotations

from pathlib import Path
import struct

import pytest

BRAND = (
    Path(__file__).parent.parent
    / "custom_components"
    / "homewizard_realtime_push"
    / "brand"
)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
RGBA = 6


@pytest.mark.parametrize(
    ("name", "size"),
    [
        ("icon.png", 256),
        ("icon@2x.png", 512),
        ("dark_icon.png", 256),
        ("dark_icon@2x.png", 512),
    ],
)
def test_brand_icon_is_square_rgba_png(name: str, size: int) -> None:
    header = (BRAND / name).read_bytes()[:26]

    assert header[:8] == PNG_SIGNATURE
    assert header[12:16] == b"IHDR"
    width, height, _, color_type = struct.unpack(">IIBB", header[16:26])
    # Transparent corners need an alpha channel.
    assert (width, height, color_type) == (size, size, RGBA)
