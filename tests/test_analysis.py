# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Voice-demand tests for `analysis.py`.

The synthetic song's segments are small enough to count by hand, and the
expected figures below were worked out that way from the fixture's
constants: tempo 120 bpm to tick 1920, then 150 bpm, then 100 bpm from
5280, at 480 ticks per quarter, so the hanging note at 5760 lasts 1.25 ms
and the silence before it straddles a tempo change. Two local development files, when present,
pin the figures the voice plan was sized against.
"""

from __future__ import annotations

import pytest

from conftest import ROOT
from montyroll import analysis, model, smf

LOCAL = ROOT / "resources" / "local"


def _localSong(name: str) -> model.Song:
    """Load a git-excluded development file or skip the test.

    Args:
        name: file name under resources/local.

    Returns:
        model.Song: the loaded song.
    """
    path = LOCAL / name

    if not path.exists():
        pytest.skip(f"{name} is not available on this machine")

    song = model.Song.load(str(path))
    return song


class TestSweep:
    """Segments and the four counts on the synthetic song."""

    def testSegmentBoundaries(self, song: model.Song) -> None:
        """Every note start and end is a boundary and nothing else is."""
        demand = analysis.sweep(song)
        assert demand.ticks == [0, 120, 480, 600, 960, 1080, 1440, 1920, 2160, 2400,
                                2640, 2880, 3840, 4320, 5760]
        assert demand.endTick == 5761

    def testRawCounts(self, song: model.Song) -> None:
        """Note-offs apply before note-ons at a shared tick."""
        demand = analysis.sweep(song)
        assert demand.raw == [4, 3, 4, 3, 2, 1, 0, 3, 4, 3, 2, 0, 1, 0, 1]

    def testSameChannelOverlapCountsOnce(self, song: model.Song) -> None:
        """Two overlapping C4s on the piano are one voice, one pitch, one class."""
        demand = analysis.sweep(song)
        i = demand.index(2200)
        assert (demand.raw[i], demand.voices[i], demand.pitches[i], demand.classes[i]) \
            == (4, 3, 3, 3)

    def testOctaveDoublingMergesInClasses(self, song: model.Song) -> None:
        """C4 on the piano and C5 on the flute are two pitches but one class."""
        demand = analysis.sweep(song)
        i = demand.index(0)
        assert (demand.raw[i], demand.pitches[i], demand.classes[i]) == (4, 4, 3)

    def testDrumsNeverMerge(self, song: model.Song) -> None:
        """A drum hit keeps its own pitch and class even against a pitched note."""
        drums = [n for n in song.notes if n.channel == 9]
        kick = drums[0]
        bass = model.Note(0, 2, kick.pitch, 100, kick.start, kick.end)
        demand = analysis.sweep(song, drums + [bass])
        i = demand.index(kick.start)
        assert (demand.raw[i], demand.pitches[i], demand.classes[i]) == (2, 2, 2)

    def testPerChannelCounts(self, song: model.Song) -> None:
        """Each channel's own count runs in parallel with the segments."""
        demand = analysis.sweep(song)
        assert sorted(demand.channels) == [0, 1, 9]
        assert demand.channels[9] == [1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]
        assert demand.channels[0][demand.index(2200)] == 2

    def testSecondsFollowTheTempoMap(self, song: model.Song) -> None:
        """Segment durations are measured through the tempo map, not at one tempo."""
        demand = analysis.sweep(song)
        assert demand.seconds[0] == pytest.approx(0.125)        # 120 ticks at 120 bpm
        assert demand.seconds[demand.index(1920)] == pytest.approx(0.2)   # 240 at 150 bpm
        assert demand.length == pytest.approx(song.tickToSeconds(5761))

    def testSubsetSweep(self, song: model.Song) -> None:
        """Sweeping a subset of notes measures that subset alone."""
        piano = [n for n in song.notes if n.channel == 0]
        demand = analysis.sweep(song, piano)
        assert demand.peak == 2
        assert list(demand.channels) == [0]

    def testEmptySong(self) -> None:
        """No notes gives an empty demand that still answers every query."""
        empty = model.Song(smf.MidiFile(1, 480, [[]]))
        demand = analysis.sweep(empty)
        assert demand.ticks == []
        assert demand.peak == 0
        assert demand.index(100) == -1
        assert demand.window(0, 100) == (0, 0, 0, {})
        assert analysis.channelDemand(demand) == {}


class TestQueries:
    """Looking up the demand at a tick or over a range."""

    def testIndexClampsToTheEnds(self, song: model.Song) -> None:
        """Ticks before the first boundary or past the end land on the end segments."""
        demand = analysis.sweep(song)
        assert demand.index(-50) == 0
        assert demand.index(99_999) == len(demand.ticks) - 1
        assert demand.index(479) == 1
        assert demand.index(480) == 2

    def testWindowTakesTheMaximum(self, song: model.Song) -> None:
        """A range spanning several segments reports the peak of each count."""
        demand = analysis.sweep(song)
        assert demand.window(0, 1000) == (4, 4, 4, {0: 1, 1: 2, 9: 1})
        assert demand.window(1500, 1600) == (0, 0, 0, {})

    def testWindowNarrowerThanASegment(self, song: model.Song) -> None:
        """A pixel-wide window inside one segment still reports that segment."""
        demand = analysis.sweep(song)
        assert demand.window(2200, 2200.5) == (4, 3, 3, {0: 2, 1: 2})


class TestFigures:
    """The derived summaries."""

    def testStats(self, song: model.Song) -> None:
        """Peak, duration-weighted mean and threshold fractions over sounding time."""
        demand = analysis.sweep(song)
        st = analysis.stats(demand.raw, demand.seconds, (1, 3))
        silent = 0.5 + 0.8 + (0.8 + 0.6)     # the last gap straddles the tempo change
        assert st.peak == 4
        assert st.sounding == pytest.approx(demand.length - silent)
        assert st.mean == pytest.approx(6.67625 / st.sounding, rel=1e-4)
        assert st.above[3] == pytest.approx((0.125 + 0.125 + 0.2) / st.sounding, rel=1e-4)
        assert 0 < st.above[1] < 1

    def testChannelDemand(self, song: model.Song) -> None:
        """Per-channel peak, mean while sounding and share of the piece."""
        figures = analysis.channelDemand(analysis.sweep(song))
        assert figures[0].peak == 2
        assert figures[0].sounding == pytest.approx(2.5)
        assert figures[0].fraction == pytest.approx(2.5 / 5.40125, rel=1e-4)
        assert figures[1].sounding == pytest.approx(1.0 + 0.8 + 0.6 / 480, rel=1e-4)
        assert figures[9].peak == 1
        assert figures[9].mean == pytest.approx(1.0)

    def testDoubling(self, song: model.Song) -> None:
        """The note-seconds split: same-channel 0.2 s, octaves 0.5 s, no unisons."""
        d = analysis.doubling(analysis.sweep(song))
        assert d.total == pytest.approx(6.67625, rel=1e-6)
        assert d.sameChannel == pytest.approx(0.2)
        assert d.unison == pytest.approx(0.0)
        assert d.octave == pytest.approx(0.5)
        assert d.distinct == pytest.approx(d.total - 0.7, rel=1e-4)

    def testUnisonAcrossChannels(self, song: model.Song) -> None:
        """The same pitch on two channels at once is counted as unison time."""
        c4 = next(n for n in song.notes if n.pitch == 60)
        twin = model.Note(2, 2, 60, 90, c4.start, c4.end)
        d = analysis.doubling(analysis.sweep(song, [c4, twin]))
        assert d.unison == pytest.approx(0.5)
        assert d.distinct == pytest.approx(0.5)

    def testCoActivity(self, song: model.Song) -> None:
        """Seconds each pair of channels sounds together; every pair is present."""
        together = analysis.coActivity(analysis.sweep(song))
        assert sorted(together) == [(0, 1), (0, 9), (1, 9)]
        assert together[(0, 1)] == pytest.approx(1.6)
        assert together[(0, 9)] == pytest.approx(0.375)
        assert together[(1, 9)] == pytest.approx(0.25)


class TestLocalFiles:
    """The figures the voice plan was sized against, when the files are present."""

    def testJsbdDemand(self) -> None:
        """JSBD-1: peak 32 as written, 15 unique pitches, 7 pitch classes."""
        demand = analysis.sweep(_localSong("JSBD-1.mid"))
        raw = analysis.stats(demand.raw, demand.seconds)
        pitches = analysis.stats(demand.pitches, demand.seconds)
        classes = analysis.stats(demand.classes, demand.seconds)
        assert (raw.peak, pitches.peak, classes.peak) == (32, 15, 7)
        assert raw.mean == pytest.approx(12.75, abs=0.01)
        assert pitches.mean == pytest.approx(6.78, abs=0.01)
        assert raw.above[16] == pytest.approx(0.256, abs=0.002)
        assert pitches.above[12] == pytest.approx(0.010, abs=0.002)
        d = analysis.doubling(demand)
        assert d.unison / d.total == pytest.approx(0.368, abs=0.002)
        assert d.octave / d.total == pytest.approx(0.274, abs=0.002)

    def testDanubeDemand(self) -> None:
        """The retempo'd Danube: half of all note-time is cross-channel unison."""
        demand = analysis.sweep(_localSong("blue-danube-1-retempo.mid"))
        raw = analysis.stats(demand.raw, demand.seconds)
        pitches = analysis.stats(demand.pitches, demand.seconds)
        assert (raw.peak, pitches.peak) == (32, 15)
        assert raw.mean == pytest.approx(12.21, abs=0.01)
        assert pitches.above[8] == pytest.approx(0.186, abs=0.002)
        d = analysis.doubling(demand)
        assert d.unison / d.total == pytest.approx(0.470, abs=0.002)
        assert d.sameChannel / d.total < 0.002


def testSweepIsFast() -> None:
    """A sweep of a large file is cheap enough to run lazily from the UI."""
    import time

    song = _localSong("JSBD-1.mid")
    started = time.perf_counter()
    analysis.sweep(song)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0
