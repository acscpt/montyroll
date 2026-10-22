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
# a raised control under the pointer, ink the text on the accent and on the
# channel colours, title the bar across the top of a panel, and warn a
# figure that needs attention, such as a pool's peak above its voices.
PALETTE = {
    "window": "#282a33", "panel": "#333540", "raised": "#3b3d48", "field": "#22242d",
    "border": "#22242d", "text": "#d4d5dc", "muted": "#878992", "accent": "#57c8ff",
    "active": "#434552", "lcd": "#16181f", "hover": "#4a4c59", "ink": "#1a1b20",
    "title": "#434550", "warn": "#ffb84d",
}

# The roll, ruler, keyboard gutter and strip: a mid grey note grid with
# darker lines, so the pastel notes stand out; muted channels' notes in a
# grey a step lighter than the grid. The strip under the roll, its lane
# switch and its gutter are a near-black display panel, with the budget
# lines and their tags in budget and the demand above them washed in over.
# The area under the pointer is lit towards white in three steps, each
# over the grid and, as a Row tone, over the black-key rows: the bar 5%
# (and on the ruler), the beat 9%, and the slot a note would fill 16%.
ROLL = {
    "roll": "#3b3d48", "row": "#363843", "octave": "#2a2c35", "bar": "#2a2c35",
    "beat": "#353742", "ruler": "#282a33", "strip": "#16181f", "sash": "#22242d",
    "white": "#c4c5cc", "black": "#2a2c35", "keyEdge": "#22242d", "keyText": "#3b3d48",
    "muted": "#4c4e59", "mutedEdge": "#2a2c35",
    "hoverBar": "#454751", "hoverBarRow": "#40424c", "hoverRuler": "#35373f",
    "hoverBeat": "#4d4e58", "hoverBeatRow": "#484a54",
    "hoverCell": "#5a5c65", "hoverCellRow": "#565861",
    "budget": "#ff5a5a", "over": "#ff5050",
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


def box(x0: float, y0: float, x1: float, y1: float, radius: float = 0.0) -> Distance:
    """Describe a rectangle, its corners rounded by radius.

    Args:
        x0: the left edge.
        y0: the top edge.
        x1: the right edge.
        y1: the bottom edge.
        radius: the corner radius.

    Returns:
        Distance: the signed distance from a point to the rectangle's edge.
    """
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    hx, hy = (x1 - x0) / 2 - radius, (y1 - y0) / 2 - radius

    def distance(x: float, y: float) -> float:
        """Measure from a point to the edge; negative inside."""
        qx, qy = abs(x - cx) - hx, abs(y - cy) - hy
        d = math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - radius
        return d

    return distance


def polygon(points: list[tuple[float, float]]) -> Distance:
    """Describe a polygon by its corners, in order.

    Args:
        points: the corners as (x, y).

    Returns:
        Distance: the signed distance from a point to the polygon's edge.
    """
    edges = list(zip(points, points[1:] + points[:1]))

    def distance(x: float, y: float) -> float:
        """Measure from a point to the edge; negative inside."""
        nearest = min(_segmentDistance(x, y, a, b) for a, b in edges)
        inside = False

        # A ray to the right crosses the edges an odd number of times from
        # inside the polygon.
        for (ax, ay), (bx, by) in edges:
            if (ay > y) != (by > y) and x < ax + (y - ay) * (bx - ax) / (by - ay):
                inside = not inside

        d = -nearest if inside else nearest
        return d

    return distance


def arc(cx: float, cy: float, radius: float, width: float,
        start: float = -180.0, end: float = 180.0) -> Distance:
    """Describe a stroke along a circle from one angle to another, with round ends.

    Angles are in degrees from the +x axis, with y downwards, so -90 is the
    top of the circle; the default range is the whole ring.

    Args:
        cx: the centre's x.
        cy: the centre's y.
        radius: the radius of the stroke's centre line.
        width: the stroke's width.
        start: the angle the stroke starts at, from -180.
        end: the angle it ends at, up to 180.

    Returns:
        Distance: the signed distance from a point to the stroke's edge.
    """
    ends = [(cx + radius * math.cos(math.radians(a)), cy + radius * math.sin(math.radians(a)))
            for a in (start, end)]

    def distance(x: float, y: float) -> float:
        """Measure from a point to the edge; negative inside."""
        angle = math.degrees(math.atan2(y - cy, x - cx))

        if start <= angle <= end:
            d = abs(math.hypot(x - cx, y - cy) - radius) - width / 2
        else:
            d = min(math.hypot(x - ex, y - ey) for ex, ey in ends) - width / 2

        return d

    return distance


def union(*shapes: Distance) -> Distance:
    """Describe the shapes together as one.

    Args:
        shapes: the shapes to join.

    Returns:
        Distance: the distance to the nearest of them.
    """
    def distance(x: float, y: float) -> float:
        """Measure from a point to the nearest shape's edge; negative inside any."""
        d = min(shape(x, y) for shape in shapes)
        return d

    return distance


def _segmentDistance(x: float, y: float, a: tuple[float, float],
                     b: tuple[float, float]) -> float:
    """Measure from a point to a line segment.

    Args:
        x: the point's x.
        y: the point's y.
        a: one end of the segment.
        b: the other end.

    Returns:
        float: the distance to the nearest point of the segment.
    """
    (ax, ay), (bx, by) = a, b
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
    d = math.hypot(x - ax - t * dx, y - ay - t * dy)
    return d


def png(width: int, height: int, layers: list[tuple]) -> str:
    """Paint shapes into an RGBA image and encode it as base64 PNG data.

    A pixel takes each layer's colour by the share of it that lies inside
    the shape, which smooths the edges, and the layers are laid over one
    another in order on a clear background. A layer whose colour is None
    erases what is under it instead, which cuts a shape out of the image.

    Args:
        width: the image width in pixels.
        height: the image height in pixels.
        layers: (distance, colour) or (distance, colour, opacity) tuples,
            bottom first; distance takes a pixel centre, and colour is
            "#rrggbb" or None to erase.

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

                if colour is None:
                    a *= 1 - cover
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
    """Paint the switch, the slider knobs and troughs, and the mute and solo icons.

    The switch is a 40 by 20 pill: an outline with a grey knob on the left
    when off, filled with the accent with a dark knob on the right when on,
    and the knob a pixel larger under the pointer. The toolbar knob is a
    grey disc with an accent centre on a thin inset trough. The channel
    rows' knob is dark on a trough of translucent black, so both sit on any
    header colour. The mute and solo icons are 18 pixels square, a speaker
    and headphones: a dark ring and glyph when off, a dark disc with the
    glyph cut out when on, so the header colour shows through it, and a
    faint shadow behind the ring under the pointer. The budget's arrows
    are slim triangles, 40 by 11, in the accent: brighter under the
    pointer, dark on an accent bar when pressed, and dim when the budget
    cannot be edited. The pools table's arrows are smaller, 14 by 8, plain
    or dark on an accent bar when pressed, and muted when disabled. The images are painted once and
    shared by every window.

    Returns:
        dict[str, str]: base64 PNG data by name: switchOff, switchOffHover,
        switchOffDisabled, switchOn, switchOnHover, switchOnDisabled, knob,
        knobHover, trough, rowKnob, rowKnobHover, rowTrough, muteOff,
        muteOffHover, muteOn, muteOnHover and the same four for solo, and
        budgetUp, budgetUpHover, budgetUpPressed, budgetUpDisabled and the
        same four for budgetDown, and poolUp, poolUpPressed, poolUpDisabled
        and the same three for poolDown.
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

    # The icons: each glyph inside a ring, or cut out of a disc.
    glyphs = {
        "mute": union(box(4.8, 7.4, 7.0, 10.6, 0.4),
                      polygon([(6.6, 7.4), (9.6, 5.0), (9.6, 13.0), (6.6, 10.6)]),
                      arc(9.6, 9.0, 2.7, 1.2, -50.0, 50.0)),
        "solo": union(arc(9.0, 9.8, 4.2, 1.3, -180.0, 0.0),
                      box(4.1, 9.4, 6.4, 13.0, 0.8), box(11.6, 9.4, 13.9, 13.0, 0.8)),
    }
    ring = arc(9.0, 9.0, 7.9, 1.3)
    disc = circle(9.0, 9.0, 8.55)

    for name, glyph in glyphs.items():
        images[f"{name}Off"] = png(18, 18, [(ring, p["ink"]), (glyph, p["ink"])])
        images[f"{name}OffHover"] = png(18, 18, [(disc, "#000000", 0.18), (ring, p["ink"]),
                                                 (glyph, p["ink"])])
        images[f"{name}On"] = png(18, 18, [(disc, p["ink"]), (glyph, None)])
        images[f"{name}OnHover"] = png(18, 18, [(disc, "#000000"), (glyph, None)])

    # The budget's arrows: a slim triangle, alone or on a bar.
    arrows = {"budgetUp": polygon([(14.0, 7.5), (20.0, 3.5), (26.0, 7.5)]),
              "budgetDown": polygon([(14.0, 3.5), (20.0, 7.5), (26.0, 3.5)])}
    bar = box(0.5, 0.5, 39.5, 10.5, 2)

    for name, arrow in arrows.items():
        images[name] = png(40, 11, [(arrow, p["accent"])])
        images[f"{name}Hover"] = png(40, 11, [(arrow, mix(p["accent"], "#ffffff", 0.3))])
        images[f"{name}Pressed"] = png(40, 11, [(bar, p["accent"]), (arrow, p["ink"])])
        images[f"{name}Disabled"] = png(40, 11, [(arrow, mix(p["muted"], ROLL["strip"], 0.5))])

    # The pools table's arrows, stacked beside each pool's voices.
    arrows = {"poolUp": polygon([(3.0, 6.0), (7.0, 2.0), (11.0, 6.0)]),
              "poolDown": polygon([(3.0, 2.0), (7.0, 6.0), (11.0, 2.0)])}
    bar = box(0.5, 0.5, 13.5, 7.5, 1.5)

    for name, arrow in arrows.items():
        images[name] = png(14, 8, [(arrow, p["accent"])])
        images[f"{name}Pressed"] = png(14, 8, [(bar, p["accent"]), (arrow, p["ink"])])
        images[f"{name}Disabled"] = png(14, 8, [(arrow, p["muted"])])

    return images
