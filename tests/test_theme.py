# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for `theme.py`: the palette, the colour mixing, the font choice and the painter."""

from __future__ import annotations

import base64
import struct
import zlib

import pytest

from montyroll import theme


def _decode(data: str) -> tuple[int, int, list[bytes]]:
    """Read the painter's base64 PNG back into its size and RGBA scanlines.

    Args:
        data: the PNG as `theme.png` returns it.

    Returns:
        tuple[int, int, list[bytes]]: the width, the height, and one row of
        RGBA bytes per scanline.
    """
    raw = base64.b64decode(data)
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8
    chunks: dict[bytes, bytes] = {}

    while pos < len(raw):
        length, = struct.unpack(">I", raw[pos:pos + 4])
        tag = raw[pos + 4:pos + 8]
        body = raw[pos + 8:pos + 8 + length]
        crc, = struct.unpack(">I", raw[pos + 8 + length:pos + 12 + length])
        assert crc == zlib.crc32(tag + body)
        chunks[tag] = body
        pos += 12 + length

    width, height = struct.unpack(">II", chunks[b"IHDR"][:8])
    pixels = zlib.decompress(chunks[b"IDAT"])
    stride = 1 + 4 * width
    rows = [pixels[j * stride + 1:(j + 1) * stride] for j in range(height)]
    decoded = (width, height, rows)
    return decoded


def _alpha(rows: list[bytes], x: int, y: int) -> int:
    """Read one pixel's alpha.

    Args:
        rows: RGBA scanlines from `_decode`.
        x: the column.
        y: the row.

    Returns:
        int: the alpha, 0 to 255.
    """
    alpha = rows[y][4 * x + 3]
    return alpha


class TestPalettes:
    """The palette and the roll colours."""

    def testPaletteKeys(self) -> None:
        """The palette names every colour the window reads from it."""
        assert set(theme.PALETTE) == {"window", "panel", "raised", "field", "border", "text",
                                      "muted", "accent", "active", "lcd", "hover", "ink",
                                      "title"}

    def testColoursAreHex(self) -> None:
        """Every colour is a #rrggbb string."""
        for palette in (theme.PALETTE, theme.ROLL):
            for colour in palette.values():
                assert len(colour) == 7 and colour[0] == "#"
                int(colour[1:], 16)


class TestMix:
    """Mixing one colour towards another."""

    def testEnds(self) -> None:
        """Nothing of the target at 0, all of it at 1."""
        assert theme.mix("#204060", "#ffffff", 0.0) == "#204060"
        assert theme.mix("#204060", "#ffffff", 1.0) == "#ffffff"

    def testHalfway(self) -> None:
        """Half way between black and white is mid grey."""
        assert theme.mix("#000000", "#ffffff", 0.5) == "#808080"


class TestPickFont:
    """The interface font is the first preferred family that is installed."""

    def testFirstPreferenceWins(self) -> None:
        """With several preferred families installed, the earliest one is chosen."""
        assert theme.pickFont({"DejaVu Sans", "Ubuntu", "Courier"}) == "Ubuntu"

    def testFallsThroughTheList(self) -> None:
        """A later preference is chosen when the earlier ones are missing."""
        assert theme.pickFont(["Courier", "DejaVu Sans"]) == "DejaVu Sans"

    def testOtherPreferences(self) -> None:
        """The readout's fixed-width faces are chosen the same way."""
        installed = {"DejaVu Sans Mono", "Ubuntu"}
        assert theme.pickFont(installed, theme.MONO_FONTS) == "DejaVu Sans Mono"

    def testNoneInstalled(self) -> None:
        """None when no preferred family is installed."""
        assert theme.pickFont(("Courier",)) is None


class TestPainter:
    """Shapes painted into PNG data."""

    def testSizeAndCrc(self) -> None:
        """The PNG has the size asked for, and every chunk's CRC checks."""
        width, height, rows = _decode(theme.png(12, 7, []))
        assert (width, height, len(rows)) == (12, 7, 7)

    def testCircleIsSolidInsideAndClearOutside(self) -> None:
        """A circle covers its centre fully, its corners not at all, and blends its edge."""
        _, _, rows = _decode(theme.png(16, 16, [(theme.circle(8, 7.5, 6.25), "#ffffff")]))
        assert _alpha(rows, 8, 8) == 255
        assert _alpha(rows, 0, 0) == 0
        assert _alpha(rows, 14, 7) == round(0.25 * 255)   # the centre 6.5 px out

    def testColourIsKept(self) -> None:
        """A solid pixel takes the layer's colour exactly."""
        _, _, rows = _decode(theme.png(4, 4, [(theme.capsule(0, 4, 2, 4), "#57c8ff")]))
        assert rows[2][4:8] == bytes((0x57, 0xC8, 0xFF, 255))

    def testOpacity(self) -> None:
        """A layer's opacity scales the alpha of what it covers."""
        _, _, rows = _decode(theme.png(4, 4, [(theme.capsule(0, 4, 2, 4), "#000000", 0.35)]))
        assert _alpha(rows, 2, 2) == round(0.35 * 255)

    def testLaterLayersCover(self) -> None:
        """A later layer lies over an earlier one."""
        layers = [(theme.circle(8, 8, 7), "#ff0000"), (theme.circle(8, 8, 3), "#0000ff")]
        _, _, rows = _decode(theme.png(16, 16, layers))
        assert rows[7][28:32] == bytes((0, 0, 255, 255))
        assert rows[7][8:12] == bytes((255, 0, 0, 255))

    def testEraseCutsAShapeOut(self) -> None:
        """A layer with no colour clears what is under it, leaving the rest."""
        layers = [(theme.circle(8, 8, 8), "#000000"), (theme.circle(8, 8, 3), None)]
        _, _, rows = _decode(theme.png(16, 16, layers))
        assert _alpha(rows, 8, 8) == 0
        assert _alpha(rows, 2, 8) == 255

    def testPaintedImages(self) -> None:
        """The switch is 40 by 20 in each state, and the images are painted once."""
        images = theme.paintedImages()

        for name in ("switchOff", "switchOffHover", "switchOffDisabled",
                     "switchOn", "switchOnHover", "switchOnDisabled"):
            width, height, _ = _decode(images[name])
            assert (width, height) == (40, 20)

        for name in ("muteOff", "muteOffHover", "muteOn", "muteOnHover",
                     "soloOff", "soloOffHover", "soloOn", "soloOnHover"):
            width, height, _ = _decode(images[name])
            assert (width, height) == (18, 18)

        assert theme.paintedImages() is images


class TestShapes:
    """The distance functions the painter draws with: negative inside, positive outside."""

    def testBox(self) -> None:
        """A box is inside at its centre and outside beyond its edge, by the distance."""
        shape = theme.box(2, 2, 8, 6)
        assert shape(5, 4) < 0
        assert shape(10, 4) == pytest.approx(2.0)

    def testRoundedCorner(self) -> None:
        """A rounded box no longer covers its corner."""
        assert theme.box(0, 0, 10, 10)(0.5, 0.5) < 0
        assert theme.box(0, 0, 10, 10, 3)(0.5, 0.5) > 0

    def testPolygon(self) -> None:
        """A triangle holds a point inside it and measures to its nearest edge outside."""
        shape = theme.polygon([(0, 0), (10, 0), (0, 10)])
        assert shape(2, 2) < 0
        assert shape(-3, 5) == pytest.approx(3.0)
        assert shape(8, 8) > 0

    def testArc(self) -> None:
        """An arc is the stroke on its circle within its angles, with round ends."""
        top = theme.arc(0, 0, 5, 1, -180.0, 0.0)
        assert top(0, -5) < 0              # the top of the circle
        assert top(0, 5) > 0               # the bottom, outside the angles
        assert top(5, 1) == pytest.approx(0.5)   # a pixel below the right end

    def testUnion(self) -> None:
        """Two shapes joined cover what either covers."""
        shape = theme.union(theme.circle(0, 0, 2), theme.circle(10, 0, 2))
        assert shape(0, 0) < 0 and shape(10, 0) < 0 and shape(5, 0) > 0

