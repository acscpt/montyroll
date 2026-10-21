# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Colours, fonts and painted images for the MontyRoll window.

`app.py` reads this module and applies it: `PALETTE` colours the ttk clam
theme, the classic tk widgets and the canvases, `ROLL` the piano roll and
the strip under it, `paintedImages` gives the switch and the sliders as PNG
data for ttk image elements, and `UI_FONTS` names the font families to try
for the interface text. The module takes colours and shapes and returns
colours and base64 PNG data.
"""

from __future__ import annotations

import base64
import math
import struct
import zlib
from collections.abc import Callable
from functools import cache

# Palette keys: window is the toplevel background and the gaps between
# panes, panel the frames and labels, raised the buttons, field the entries
# and lists, border the etched lines between the parts of the window, text
# and muted the two text colours, accent the "on" colour, active the tint of
# the active channel's row, lcd the panel behind the toolbar readout, hover
# a raised control under the pointer, and ink the text on the accent and on
# the channel colours.
PALETTE = {
    "window": "#282a33", "panel": "#333540", "raised": "#3b3d48", "field": "#22242d",
    "border": "#22242d", "text": "#d4d5dc", "muted": "#878992", "accent": "#57c8ff",
    "active": "#434552", "lcd": "#16181f", "hover": "#4a4c59", "ink": "#1a1b20",
}

# The roll, ruler, keyboard gutter and strip: a mid grey note grid with
# darker lines, so the pastel notes stand out; muted channels' notes in a
# grey a step lighter than the grid. The strip under the roll, its lane
# switch and its gutter are a near-black display panel.
ROLL = {
    "roll": "#3b3d48", "row": "#363843", "octave": "#2a2c35", "bar": "#2a2c35",
    "beat": "#353742", "ruler": "#282a33", "strip": "#16181f", "sash": "#22242d",
    "white": "#c4c5cc", "black": "#2a2c35", "keyEdge": "#22242d", "keyText": "#3b3d48",
    "muted": "#4c4e59", "mutedEdge": "#2a2c35",
}

UI_FONTS = ("Ubuntu", "Noto Sans", "Cantarell", "DejaVu Sans")   # first one installed wins
MONO_FONTS = ("Ubuntu Mono", "Noto Sans Mono", "DejaVu Sans Mono")   # the toolbar readout
UI_FONT_PT = 10          # the interface text size, in points, so it follows Tk scaling
UI_CAPTION_PT = 9        # captions and small labels

Distance = Callable[[float, float], float]   # a shape: negative inside, in pixels


def mix(colour: str, target: str, amount: float) -> str:
    """Mix one colour towards another.

    Args:
        colour: the starting colour, as "#rrggbb".
        target: the colour to mix towards, as "#rrggbb".
        amount: 0.0 keeps `colour`, 1.0 gives `target`.

    Returns:
        str: the mixed colour, as "#rrggbb".
    """
    a = [int(colour[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(target[i:i + 2], 16) for i in (1, 3, 5)]
    mixed = "#%02x%02x%02x" % tuple(round(x + (y - x) * amount) for x, y in zip(a, b))
    return mixed


def pickFont(installed: set[str] | list[str] | tuple[str, ...],
             preferences: tuple[str, ...] = UI_FONTS) -> str | None:
    """Choose a font family from those installed.

    Args:
        installed: the font families the display offers.
        preferences: the families to try, best first.

    Returns:
        str | None: the first of `preferences` that is installed, or None
        when none is.
    """
    available = set(installed)
    family = next((name for name in preferences if name in available), None)
    return family


def capsule(x0: float, x1: float, cy: float, radius: float) -> Distance:
    """Describe a horizontal capsule: a line from x0 to x1 at cy, thickened by radius.

    Args:
        x0: the left end of the centre line.
        x1: the right end of the centre line.
        cy: the height of the centre line.
        radius: half the capsule's height.

    Returns:
        Distance: the signed distance from a point to the capsule's edge.
    """
    def distance(x: float, y: float) -> float:
        """Measure from a point to the edge; negative inside."""
        d = math.hypot(x - min(max(x, x0), x1), y - cy) - radius
        return d

    return distance


def circle(cx: float, cy: float, radius: float) -> Distance:
    """Describe a circle.

    Args:
        cx: the centre's x.
        cy: the centre's y.
        radius: the radius.

    Returns:
        Distance: the signed distance from a point to the circle's edge.
    """
    def distance(x: float, y: float) -> float:
        """Measure from a point to the edge; negative inside."""
        d = math.hypot(x - cx, y - cy) - radius
        return d

    return distance


def png(width: int, height: int, layers: list[tuple]) -> str:
    """Paint shapes into an RGBA image and encode it as base64 PNG data.

    A pixel takes each layer's colour by the share of it that lies inside
    the shape, which smooths the edges, and the layers are laid over one
    another in order on a clear background.

    Args:
        width: the image width in pixels.
        height: the image height in pixels.
        layers: (distance, "#rrggbb") or (distance, "#rrggbb", opacity)
            tuples, bottom first; distance takes a pixel centre.

    Returns:
        str: the PNG, base64 encoded, as `tk.PhotoImage(data=...)` takes it.
    """
    rows = []

    for j in range(height):
        row = bytearray([0])     # each scanline starts with filter type 0

        for i in range(width):
            r = g = b = a = 0.0

            # Lay each shape over what is there, by its coverage of the
            # pixel: half a pixel either side of the edge blends.
            for distance, colour, *opacity in layers:
                cover = min(1.0, max(0.0, 0.5 - distance(i + 0.5, j + 0.5)))
                cover *= opacity[0] if opacity else 1.0

                if cover == 0.0:
                    continue

                cr, cg, cb = (int(colour[k:k + 2], 16) for k in (1, 3, 5))
                out = cover + a * (1 - cover)
                r = (cr * cover + r * a * (1 - cover)) / out
                g = (cg * cover + g * a * (1 - cover)) / out
                b = (cb * cover + b * a * (1 - cover)) / out
                a = out

            row += bytes((round(r), round(g), round(b), round(a * 255)))

        rows.append(bytes(row))

    # An 8-bit RGBA PNG: the header, the compressed scanlines, the end.
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    data = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", header)
            + _chunk(b"IDAT", zlib.compress(b"".join(rows))) + _chunk(b"IEND", b""))
    encoded = base64.b64encode(data).decode("ascii")
    return encoded


def _chunk(tag: bytes, data: bytes) -> bytes:
    """Wrap data as a PNG chunk: its length, the tag, the data and a CRC.

    Args:
        tag: the four-letter chunk type.
        data: the chunk's contents.

    Returns:
        bytes: the chunk as it sits in the file.
    """
    chunk = struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return chunk


@cache
def paintedImages() -> dict[str, str]:
    """Paint the switch, the slider knobs and the slider troughs.

    The switch is a 40 by 20 pill: an outline with a grey knob on the left
    when off, filled with the accent with a dark knob on the right when on,
    and the knob a pixel larger under the pointer. The toolbar knob is a
    grey disc with an accent centre on a thin inset trough. The channel
    rows' knob is dark on a trough of translucent black, so both sit on any
    header colour. The images are painted once and shared by every window.

    Returns:
        dict[str, str]: base64 PNG data by name: switchOff, switchOffHover,
        switchOffDisabled, switchOn, switchOnHover, switchOnDisabled, knob,
        knobHover, trough, rowKnob, rowKnobHover and rowTrough.
    """
    p = PALETTE
    lit = mix(p["accent"], "#ffffff", 0.2)
    dim = mix(p["muted"], p["panel"], 0.5)
    pill = capsule(10, 30, 10, 9.5)
    inner = capsule(10, 30, 10, 8.5)
    images = {
        "switchOff": png(40, 20, [(pill, p["muted"]), (inner, p["field"]),
                                  (circle(10, 10, 5), p["muted"])]),
        "switchOffHover": png(40, 20, [(pill, p["text"]), (inner, p["raised"]),
                                       (circle(10, 10, 6), p["text"])]),
        "switchOffDisabled": png(40, 20, [(pill, dim), (inner, p["panel"]),
                                          (circle(10, 10, 5), dim)]),
        "switchOn": png(40, 20, [(pill, p["accent"]), (circle(30, 10, 5), p["ink"])]),
        "switchOnHover": png(40, 20, [(pill, lit), (circle(30, 10, 6), p["ink"])]),
        "switchOnDisabled": png(40, 20, [(pill, mix(p["accent"], p["panel"], 0.6)),
                                         (circle(30, 10, 5), p["panel"])]),
        "knob": png(16, 16, [(circle(8, 8, 7.5), p["border"]),
                             (circle(8, 8, 6.5), mix(p["raised"], "#ffffff", 0.15)),
                             (circle(8, 8, 3.5), p["accent"])]),
        "knobHover": png(16, 16, [(circle(8, 8, 7.5), p["border"]),
                                  (circle(8, 8, 6.5), mix(p["raised"], "#ffffff", 0.2)),
                                  (circle(8, 8, 4.5), lit)]),
        "trough": png(20, 16, [(capsule(3, 17, 8, 2.5), p["field"])]),
        "rowKnob": png(10, 12, [(circle(5, 6, 4.5), p["ink"])]),
        "rowKnobHover": png(10, 12, [(circle(5, 6, 4.5), "#000000")]),
        "rowTrough": png(16, 12, [(capsule(2, 14, 6, 2), "#000000", 0.35)]),
    }
    return images
