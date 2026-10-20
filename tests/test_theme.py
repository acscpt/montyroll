# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for `theme.py`: the palettes, the colour mixing and the font choice."""

from __future__ import annotations

from montyroll import theme


class TestPalettes:
    """The two palettes describe the same parts of the window."""

    def testSameKeys(self) -> None:
        """Sun Valley and clam name the same colours, so either can stand in."""
        assert set(theme.SUN_VALLEY) == set(theme.CLAM_DARK)

    def testColoursAreHex(self) -> None:
        """Every colour is a #rrggbb string."""
        for palette in (theme.SUN_VALLEY, theme.CLAM_DARK, theme.ROLL):
            for colour in palette.values():
                assert len(colour) == 7 and colour[0] == "#"
                int(colour[1:], 16)

    def testCompactOverrideTargetsSunValley(self) -> None:
        """The Tcl override changes the Sun Valley dark theme and nothing else."""
        assert "theme settings sun-valley-dark" in theme.COMPACT_TCL


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

    def testNoneInstalled(self) -> None:
        """None when no preferred family is installed."""
        assert theme.pickFont(("Courier",)) is None
