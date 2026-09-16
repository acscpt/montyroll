# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for `arrange.py`: channel splitting, doubling and echo.

The split cases build a flattened song with known layers, melody over an
accompaniment dyad over a bass, and check that each layer comes out on its
own channel. The figures are small enough to work out by hand.
"""

from __future__ import annotations

import pytest

from montyroll import arrange, model, smf


def _song(*notes: tuple[int, int, int, int, int], division: int = 480,
          extras: tuple[smf.Event, ...] = ()) -> model.Song:
    """Build a one-track song from (channel, pitch, velocity, start, end) tuples.

    Args:
        *notes: the notes.
        division: ticks per quarter.
        extras: events appended after the tempo, such as controllers.

    Returns:
        model.Song: the song, tempo 120 bpm.
    """
    track = [smf.Event(0, smf.META, bytearray((500_000).to_bytes(3, "big")), smf.META_TEMPO)]
    track.extend(extras)

    for ch, pitch, vel, start, end in notes:
        track.append(smf.Event(start, 0x90 | ch, bytearray([pitch, vel])))
        track.append(smf.Event(end, 0x80 | ch, bytearray([pitch, 64])))

    song = model.Song(smf.MidiFile(0, division, [track]))
    return song


def _layers() -> model.Song:
    """A flattened three-layer bar: melody, an accompaniment dyad, a bass.

    Returns:
        model.Song: four beats on channel 0; the melody rests on beat 3.
    """
    notes = []

    for beat in range(4):
        t = beat * 480

        if beat != 2:
            notes.append((0, 76 + beat, 100, t, t + 400))

        notes.append((0, 64, 80, t, t + 200))
        notes.append((0, 60, 80, t, t + 200))
        notes.append((0, 40, 90, t, t + 400))

    song = _song(*notes)
    return song


def _pitches(song: model.Song, channel: int) -> list[int]:
    """The pitches on a channel in start order.

    Args:
        song: the song.
        channel: 0-based channel.

    Returns:
        list[int]: the pitches.
    """
    ordered = sorted((n for n in song.notes if n.channel == channel),
                     key=lambda n: (n.start, -n.pitch))
    pitches = [n.pitch for n in ordered]
    return pitches


class TestAssign:
    """The chord-to-stream assignment."""

    def testNewStreamsFillTopDown(self) -> None:
        """With no history the chord spreads over the streams in order."""
        assert arrange._assign([76, 64, 40], [-1, -1, -1]) == [0, 1, 2]
        assert arrange._assign([64, 60], [-1, -1, -1]) == [0, 1]

    def testNearestStreamWins(self) -> None:
        """Each note goes to the stream whose last pitch is nearest."""
        assert arrange._assign([77, 63, 41], [76, 64, 40]) == [0, 1, 2]

    def testARestingStreamIsSkipped(self) -> None:
        """The accompaniment stays put while the melody stream rests."""
        assert arrange._assign([64, 60], [76, 64, 60]) == [1, 2]

    def testNoCrossing(self) -> None:
        """A chord never crosses: the mapping is non-decreasing."""
        order = arrange._assign([70, 69, 68, 40], [40, 69, 70, 41])
        assert order == sorted(order)

    def testBigChordSharesAStream(self) -> None:
        """A chord bigger than the stream count doubles up on the nearest."""
        order = arrange._assign([76, 66, 64, 40], [76, 65, 40])
        assert order == [0, 1, 1, 2]


class TestSplitChannel:
    """The whole split on the layered bar."""

    def testLayersLandOnTheirChannels(self) -> None:
        """Melody, dyad top, dyad bottom and bass each come out on one channel."""
        song = _layers()
        split = model.Song(arrange.splitChannel(song, 0, 4))
        assert _pitches(split, 0) == [76, 77, 79]
        assert _pitches(split, 1) == [64, 64, 64, 64]
        assert _pitches(split, 2) == [60, 60, 60, 60]
        assert _pitches(split, 3) == [40, 40, 40, 40]
        assert len(split.notes) == len(song.notes)

    def testThreePartsKeepTheDyadTogether(self) -> None:
        """At three parts the dyad shares the middle stream as chord tones."""
        song = _layers()
        split = model.Song(arrange.splitChannel(song, 0, 3))
        assert _pitches(split, 0) == [76, 77, 79]
        assert sorted(_pitches(split, 1)) == [60, 60, 60, 60, 64, 64, 64, 64]
        assert _pitches(split, 2) == [40, 40, 40, 40]

    def testSourceChannelKeepsItsEvents(self) -> None:
        """Stream 0 stays on the source channel with its program and controllers."""
        cc = smf.Event(0, 0xB0, bytearray([10, 20]))
        prog = smf.Event(0, 0xC0, bytearray([56]))
        song = _song((0, 76, 100, 0, 400), (0, 40, 90, 0, 400), extras=(cc, prog))
        split = model.Song(arrange.splitChannel(song, 0, 2))
        assert split.channels[0].program == 56
        assert 10 in split.channels[0].controllers
        assert split.channels[1].program == 0

    def testOtherChannelsPassThrough(self) -> None:
        """A channel not being split keeps its notes, and the drum channel is never taken."""
        song = _song((0, 76, 100, 0, 400), (0, 40, 90, 0, 400), (9, 36, 100, 0, 100),
                     (1, 50, 70, 0, 400))
        split = model.Song(arrange.splitChannel(song, 0, 2))
        assert _pitches(split, 1) == [50]
        assert _pitches(split, 9) == [36]
        assert _pitches(split, 2) == [40]

    def testMetasSurvive(self) -> None:
        """The tempo map and division carry into the split file."""
        song = _layers()
        split = model.Song(arrange.splitChannel(song, 0, 4))
        assert split.tempoMap == song.tempoMap
        assert split.mf.division == song.mf.division

    def testRefusals(self) -> None:
        """Bad part counts, an empty channel and a full file are refused."""
        song = _layers()

        with pytest.raises(ValueError, match="parts"):
            arrange.splitChannel(song, 0, 1)

        with pytest.raises(ValueError, match="no notes"):
            arrange.splitChannel(song, 5, 2)

        crowded = _song(*[(ch, 60, 100, 0, 100) for ch in range(16) if ch != 9])

        with pytest.raises(ValueError, match="free channels"):
            arrange.splitChannel(crowded, 0, 3)


class TestDoubleAndEcho:
    """The two copy helpers."""

    def testDoubleTransposesAndScales(self) -> None:
        """The copy sits an octave up at scaled velocity with its own program."""
        song = _song((0, 60, 100, 0, 400), (0, 120, 100, 480, 880))
        out = model.Song(arrange.double(song, 0, 12, 1, velocity=0.5, program=48))
        assert [(n.pitch, n.velocity) for n in out.notes if n.channel == 1] == [(72, 50)]
        assert out.channels[1].program == 48
        assert _pitches(out, 0) == [60, 120]

    def testEchoDelays(self) -> None:
        """The copy lags by the delay at scaled velocity."""
        song = _song((0, 60, 100, 0, 400))
        out = model.Song(arrange.echo(song, 0, 240, 1, velocity=0.5))
        copies = [n for n in out.notes if n.channel == 1]
        assert [(n.start, n.end, n.velocity) for n in copies] == [(240, 640, 50)]

    def testTargetMustBeFree(self) -> None:
        """A used target and the drum channel are refused."""
        song = _song((0, 60, 100, 0, 400), (1, 62, 100, 0, 400))

        with pytest.raises(ValueError, match="already has notes"):
            arrange.double(song, 0, 12, 1)

        with pytest.raises(ValueError, match="drum"):
            arrange.echo(song, 0, 240, 9)
