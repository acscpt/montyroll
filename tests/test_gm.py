# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""General MIDI table tests for `gm.py`.

The tables are data; the tests pin their sizes and a handful of well-known
entries so a stray edit (a dropped instrument shifting every name after it)
is caught immediately.
"""

from __future__ import annotations

import pytest

from montyroll import gm


class TestTables:
    """Sizes and spot checks of the name tables."""

    def testProgramTableHas128Entries(self) -> None:
        """GM defines exactly 128 melodic programs."""
        assert len(gm.GM_PROGRAMS) == 128
        assert len(set(gm.GM_PROGRAMS)) == 128

    def testFamilyTableHas16Entries(self) -> None:
        """Sixteen families of eight programs each."""
        assert len(gm.GM_FAMILIES) == 16

    @pytest.mark.parametrize("program, name", [
        (0, "Acoustic Grand Piano"), (24, "Acoustic Guitar (nylon)"),
        (40, "Violin"), (73, "Flute"), (127, "Gunshot"),
    ])
    def testWellKnownPrograms(self, program: int, name: str) -> None:
        """Landmarks in the program table sit at their GM numbers."""
        assert gm.GM_PROGRAMS[program] == name

    def testDrumMapCoversTheGmRange(self) -> None:
        """Every pitch from 35 to 81 has a drum name."""
        assert all(p in gm.GM_DRUMS for p in range(35, 82))
        assert gm.GM_DRUMS[36] == "Bass Drum 1"
        assert gm.GM_DRUMS[42] == "Closed Hi-Hat"

    def testControllerNames(self) -> None:
        """The controllers the UI decodes have names."""
        assert gm.CONTROLLERS[7] == "Volume"
        assert gm.CONTROLLERS[10] == "Pan"
        assert gm.CONTROLLERS[64] == "Sustain Pedal"


class TestHelpers:
    """The lookup functions."""

    @pytest.mark.parametrize("pitch, name", [
        (0, "C-1"), (21, "A0"), (60, "C4"), (61, "C#4"), (69, "A4"), (127, "G9"),
    ])
    def testNoteName(self, pitch: int, name: str) -> None:
        """Middle C is C4 and octaves roll over at C."""
        assert gm.noteName(pitch) == name

    def testProgramNameMasksToSevenBits(self) -> None:
        """A program number over 127 wraps rather than raising."""
        assert gm.programName(0) == "Acoustic Grand Piano"
        assert gm.programName(128) == "Acoustic Grand Piano"
        assert gm.programName(0x80 | 73) == "Flute"

    @pytest.mark.parametrize("program, family", [
        (0, "Piano"), (7, "Piano"), (8, "Chromatic Percussion"),
        (73, "Pipe"), (120, "Sound Effects"), (127, "Sound Effects"),
    ])
    def testFamilyName(self, program: int, family: str) -> None:
        """Families are blocks of eight programs."""
        assert gm.familyName(program) == family

    def testDrumNameFallsBackToNoteName(self) -> None:
        """Pitches outside the GM drum map get the ordinary note name."""
        assert gm.drumName(38) == "Acoustic Snare"
        assert gm.drumName(100) == gm.noteName(100)
        assert gm.drumName(0) == "C-1"
