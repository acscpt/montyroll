# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Colours, fonts and the compact theme override for the MontyRoll window.

`app.py` reads this module and applies it: the palette of the theme in use
colours the classic tk widgets and the canvases, `COMPACT_TCL` resizes the
Sun Valley widgets when the `sv-ttk` package is installed, and `UI_FONTS`
names the font families to try for the interface text. The module holds
data and colour arithmetic only.
"""

from __future__ import annotations

# Palette keys: window is the toplevel background, panel the frames and
# labels, raised the buttons and cards, field the entries and lists, border
# the frame edges, text and muted the two text colours, accent the
# highlight, and active the tint of the active channel's row.
SUN_VALLEY = {
    "window": "#1c1c1c", "panel": "#1c1c1c", "raised": "#2a2a2a", "field": "#232323",
    "border": "#3a3a3a", "text": "#fafafa", "muted": "#a0a0a0", "accent": "#57c8ff",
    "active": "#203547",
}
CLAM_DARK = {
    "window": "#1d1f25", "panel": "#262931", "raised": "#2f333d", "field": "#20232b",
    "border": "#3a3f4c", "text": "#d9dce3", "muted": "#8a90a0", "accent": "#5b8def",
    "active": "#2c3b5a",
}

# The roll, ruler, keyboard gutter and strip: quieter grid lines than the
# notes, so the notes stand out.
ROLL = {
    "roll": "#15171c", "row": "#1a1c22", "octave": "#2a2d36", "bar": "#353a47",
    "beat": "#1f2229", "ruler": "#20232a", "strip": "#181a20", "sash": "#3a3f4c",
    "white": "#e8e8ec", "black": "#202228", "keyEdge": "#9a9aa2", "keyText": "#444444",
}

UI_FONTS = ("Ubuntu", "Noto Sans", "Cantarell", "DejaVu Sans")   # first one installed wins
UI_FONT_PT = 10          # the interface text size, in points, so it follows Tk scaling
UI_CAPTION_PT = 9        # captions and small labels

# Sun Valley draws each spinbox and combobox arrow in a 34 px slot around a
# 10 px chevron and pads every field generously. These element and layout
# definitions keep the theme's images and give them slots and padding that
# fit the channel rows and the Reduce panel.
COMPACT_TCL = """
ttk::style theme settings sun-valley-dark {
    set up $ttk::theme::sv_dark::I(up)
    set dn $ttk::theme::sv_dark::I(down)
    ttk::style element create Compact.Spinbox.uparrow image $up -width 14 -height 12 -sticky {}
    ttk::style element create Compact.Spinbox.downarrow image $dn -width 14 -height 12 -sticky {}
    ttk::style layout TSpinbox {
        Spinbox.field -side top -sticky we -children {
            Compact.Spinbox.downarrow -side right -sticky ns
            Compact.Spinbox.uparrow -side right -sticky ns
            Spinbox.padding -sticky nswe -children {
                Spinbox.textarea -sticky nsew
            }
        }
    }
    ttk::style element create Compact.Combobox.arrow image $dn -width 20 -sticky {}
    ttk::style layout TCombobox {
        Combobox.field -sticky nsew -children {
            Compact.Combobox.arrow -side right -sticky ns
            Combobox.padding -sticky nsew -children {
                Combobox.textarea -sticky nsew
            }
        }
    }
    ttk::style configure TSpinbox -padding {4 0 0 1}
    ttk::style configure TCombobox -padding {4 0 0 1}
    ttk::style configure TEntry -padding {4 0 2 1}
    ttk::style configure TButton -padding {6 1 6 2}
    ttk::style configure TCheckbutton -padding 1
}
"""

# The Sun Valley named fonts the compact theme sets to the interface font.
SUN_VALLEY_FONTS = ("SunValleyBodyFont", "SunValleyBodyStrongFont", "SunValleyCaptionFont")


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


def pickFont(installed: set[str] | list[str] | tuple[str, ...]) -> str | None:
    """Choose the interface font family from those installed.

    Args:
        installed: the font families the display offers.

    Returns:
        str | None: the first of `UI_FONTS` that is installed, or None when
        none is.
    """
    available = set(installed)
    family = next((name for name in UI_FONTS if name in available), None)
    return family
