# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Parser and writer tests for `smf.py`.

The writer always emits a full status byte and orders events within a tick
(meta and controllers, then note-offs, then note-ons), so a round trip is
compared event for event rather than byte for byte. Running status and the
error paths are exercised with hand-built byte strings.
"""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from conftest import DIVISION, TRACK_NAMES, buildSong, buildSongFile, eventTuples
from montyroll import smf


def _sortedTuples(mf: smf.MidiFile) -> list[list[tuple[int, int, bytes, int]]]:
    """Flatten a file to tuples sorted the way the writer orders them.

    Args:
        mf: the file.

    Returns:
        list[list[tuple[int, int, bytes, int]]]: one sorted list per track.
    """
    flat = [sorted((e.tick, e.status, bytes(e.data), e.metaType) for e in track)
            for track in mf.tracks]
    return flat


class TestVlq:
    """Variable-length quantity encoding and decoding."""

    @pytest.mark.parametrize("value, encoded", [
        (0, b"\x00"),
        (0x7F, b"\x7F"),
        (0x80, b"\x81\x00"),
        (0x2000, b"\xC0\x00"),
        (0x3FFF, b"\xFF\x7F"),
        (0x4000, b"\x81\x80\x00"),
        (0x0FFFFFFF, b"\xFF\xFF\xFF\x7F"),
    ])
    def testEncodeMatchesSpecExamples(self, value: int, encoded: bytes) -> None:
        """The SMF specification's worked examples encode as listed."""
        assert smf._encodeVlq(value) == encoded

    @pytest.mark.parametrize("value", [0, 1, 127, 128, 8191, 8192, 123_456, 0x0FFFFFFF])
    def testDecodeInvertsEncode(self, value: int) -> None:
        """Decoding an encoded value gives the value back and the offset past it."""
        encoded = smf._encodeVlq(value)
        decoded, pos = smf._readVlq(b"\xAA" + encoded + b"\xBB", 1)
        assert decoded == value
        assert pos == 1 + len(encoded)


class TestEvent:
    """The `Event` helpers that classify a status byte."""

    def testChannelFromVoiceStatus(self) -> None:
        """Voice events report their low nibble as the channel."""
        assert smf.Event(0, 0x93, bytearray([60, 100])).channel == 3
        assert smf.Event(0, 0xCF, bytearray([1])).channel == 15

    def testMetaAndSysexHaveNoChannel(self) -> None:
        """Meta and sysex events report channel -1."""
        assert smf.Event(0, smf.META, bytearray(), 0x51).channel == -1
        assert smf.Event(0, smf.SYSEX, bytearray()).channel == -1

    def testNoteOnRequiresNonZeroVelocity(self) -> None:
        """A note-on with velocity zero counts as a note-off, as the spec allows."""
        loud = smf.Event(0, 0x90, bytearray([60, 1]))
        silent = smf.Event(0, 0x90, bytearray([60, 0]))
        off = smf.Event(0, 0x80, bytearray([60, 64]))
        assert loud.isNoteOn and not loud.isNoteOff
        assert silent.isNoteOff and not silent.isNoteOn
        assert off.isNoteOff and not off.isNoteOn

    @pytest.mark.parametrize("status, kind", [
        (0x80, "note_off"), (0x91, "note_on"), (0xA2, "poly_pressure"),
        (0xB3, "control"), (0xC4, "program"), (0xD5, "channel_pressure"),
        (0xE6, "pitch_bend"),
    ])
    def testKindNamesVoiceEvents(self, status: int, kind: str) -> None:
        """Each voice status maps to its kind name regardless of channel."""
        assert smf.Event(0, status, bytearray([0, 0])).kind() == kind

    def testKindNamesMetaAndSysex(self) -> None:
        """Meta and sysex have their own kind names."""
        assert smf.Event(0, smf.META, bytearray(), 0x03).kind() == "meta"
        assert smf.Event(0, smf.SYSEX, bytearray()).kind() == "sysex"
        assert smf.Event(0, smf.SYSEX_CONT, bytearray()).kind() == "sysex"


class TestRoundTrip:
    """Writing and re-reading the synthetic song."""

    def testHeaderSurvives(self, songPath: Path) -> None:
        """Format, division and track count come back as written."""
        mf = smf.parse(str(songPath))
        assert mf.format == 1
        assert mf.division == DIVISION
        assert len(mf.tracks) == len(TRACK_NAMES)

    def testEventsSurviveAsSets(self, songPath: Path) -> None:
        """Every event survives with the same tick, status, data and meta type."""
        original = buildSong()
        reread = smf.parse(str(songPath))
        assert _sortedTuples(reread) == _sortedTuples(original)

    def testSecondWriteIsByteIdentical(self, songPath: Path, tmp_path: Path) -> None:
        """Once a file has been through the writer, writing it again is a no-op."""
        first = songPath.read_bytes()
        again = tmp_path / "again.mid"
        smf.write(smf.parse(str(songPath)), str(again))
        assert again.read_bytes() == first

    def testEndOfTrackIsNotAnEvent(self, songPath: Path) -> None:
        """The end-of-track meta is consumed on read and regenerated on write."""
        mf = smf.parse(str(songPath))

        for track in mf.tracks:
            assert not any(e.status == smf.META and e.metaType == smf.META_END_OF_TRACK
                           for e in track)

    def testWriterOrdersWithinATick(self, tmp_path: Path) -> None:
        """At one tick the writer puts controllers first, then note-offs, then note-ons."""
        on = smf.Event(0, 0x90, bytearray([60, 100]))
        off = smf.Event(0, 0x80, bytearray([60, 64]))
        cc = smf.Event(0, 0xB0, bytearray([7, 100]))
        path = tmp_path / "order.mid"
        smf.write(smf.MidiFile(0, DIVISION, [[on, off, cc]]), str(path))
        statuses = [e.status for e in smf.parse(str(path)).tracks[0]]
        assert statuses == [0xB0, 0x80, 0x90]

    def testWriterKeepsSourceOrderForEqualPriority(self, tmp_path: Path) -> None:
        """Events of the same priority at the same tick keep their original order."""
        first = smf.Event(0, 0x90, bytearray([60, 100]))
        second = smf.Event(0, 0x90, bytearray([64, 100]))
        path = tmp_path / "stable.mid"
        smf.write(smf.MidiFile(0, DIVISION, [[first, second]]), str(path))
        pitches = [e.data[0] for e in smf.parse(str(path)).tracks[0]]
        assert pitches == [60, 64]

    def testDeltasAreAbsoluteOnRead(self, songPath: Path) -> None:
        """Ticks are absolute in memory: the drum track lands at 0, 480 and 960."""
        mf = smf.parse(str(songPath))
        drums = [e.tick for e in mf.tracks[4] if e.isNoteOn]
        assert drums == [0, 480, 960]


def _trackChunk(body: bytes) -> bytes:
    """Wrap a track body in an MTrk chunk.

    Args:
        body: the event bytes, without an end-of-track.

    Returns:
        bytes: the chunk with an end-of-track appended.
    """
    full = body + b"\x00\xFF\x2F\x00"
    chunk = b"MTrk" + struct.pack(">I", len(full)) + full
    return chunk


def _header(fmt: int, ntracks: int, division: int) -> bytes:
    """Build an MThd chunk.

    Args:
        fmt: SMF format.
        ntracks: track count.
        division: time division word.

    Returns:
        bytes: the header chunk.
    """
    header = b"MThd" + struct.pack(">IHHH", 6, fmt, ntracks, division)
    return header


class TestParserEdgeCases:
    """Hand-built byte strings for paths the writer never produces."""

    def testRunningStatusIsExpanded(self, tmp_path: Path) -> None:
        """A data byte with no status byte reuses the previous status."""
        body = (b"\x00\x90\x3C\x64"      # note on C4
                b"\x60\x3C\x00"          # running status: C4 off (vel 0)
                b"\x00\x40\x64")         # running status: E4 on
        path = tmp_path / "running.mid"
        path.write_bytes(_header(0, 1, DIVISION) + _trackChunk(body))
        events = smf.parse(str(path)).tracks[0]
        assert [(e.tick, e.status, bytes(e.data)) for e in events] == [
            (0, 0x90, b"\x3C\x64"), (96, 0x90, b"\x3C\x00"), (96, 0x90, b"\x40\x64")]

    def testMetaResetsRunningStatus(self, tmp_path: Path) -> None:
        """A data byte after a meta event with no fresh status is an error."""
        body = (b"\x00\x90\x3C\x64"
                b"\x00\xFF\x06\x02hi"     # marker meta
                b"\x00\x3C\x00")          # no status: invalid after a meta
        path = tmp_path / "reset.mid"
        path.write_bytes(_header(0, 1, DIVISION) + _trackChunk(body))

        with pytest.raises(ValueError, match="running status"):
            smf.parse(str(path))

    def testMissingHeaderIsRejected(self, tmp_path: Path) -> None:
        """A file that does not start with MThd is rejected with its path."""
        path = tmp_path / "bad.mid"
        path.write_bytes(b"RIFF" + bytes(20))

        with pytest.raises(ValueError, match=str(path)):
            smf.parse(str(path))

    def testSmpteDivisionIsRejected(self, tmp_path: Path) -> None:
        """SMPTE time division (high bit set) is refused rather than misread."""
        path = tmp_path / "smpte.mid"
        path.write_bytes(_header(0, 1, 0xE728) + _trackChunk(b""))

        with pytest.raises(ValueError, match="SMPTE"):
            smf.parse(str(path))

    def testAlienChunksAreSkipped(self, tmp_path: Path) -> None:
        """An unknown chunk between tracks is stepped over, as the spec requires."""
        alien = b"XFIH" + struct.pack(">I", 4) + b"\xDE\xAD\xBE\xEF"
        track = _trackChunk(b"\x00\x90\x3C\x64\x00\x80\x3C\x40")
        path = tmp_path / "alien.mid"
        path.write_bytes(_header(1, 2, DIVISION) + track + alien + track)
        mf = smf.parse(str(path))
        assert len(mf.tracks) == 2
        assert all(len(t) == 2 for t in mf.tracks)

    def testLongHeaderIsHonoured(self, tmp_path: Path) -> None:
        """A header longer than six bytes is skipped by its declared length."""
        header = b"MThd" + struct.pack(">IHHH", 8, 0, 1, DIVISION) + b"\x00\x00"
        path = tmp_path / "long.mid"
        path.write_bytes(header + _trackChunk(b"\x00\x90\x3C\x64"))
        mf = smf.parse(str(path))
        assert mf.division == DIVISION
        assert len(mf.tracks[0]) == 1

    def testSysexAndContinuationAreKept(self, tmp_path: Path) -> None:
        """F0 and F7 sysex events keep their payloads and round-trip."""
        body = b"\x00\xF0\x03\x01\x02\xF7" b"\x00\xF7\x02\x03\x04"
        path = tmp_path / "sysex.mid"
        path.write_bytes(_header(0, 1, DIVISION) + _trackChunk(body))
        events = smf.parse(str(path)).tracks[0]
        assert [(e.status, bytes(e.data)) for e in events] == [
            (smf.SYSEX, b"\x01\x02\xF7"), (smf.SYSEX_CONT, b"\x03\x04")]
        out = tmp_path / "sysex2.mid"
        smf.write(smf.parse(str(path)), str(out))
        assert eventTuples(smf.parse(str(out))) == eventTuples(smf.parse(str(path)))


def testBuildSongFileIsParseable(tmp_path: Path) -> None:
    """The fixture file itself parses to the expected number of tracks."""
    path = buildSongFile(tmp_path / "fixture.mid")
    assert len(smf.parse(str(path)).tracks) == len(TRACK_NAMES)
