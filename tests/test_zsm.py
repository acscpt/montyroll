# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Tests for the ZSM export: `zsm.py`, `zsmdecode.py` and `zsmcheck.py`.

The pitch tables are checked against anchors from the X16 documentation
(`FMPOKE $28,$4A` is A4; 440 Hz on the PSG is word 591), round trips and
monotonic key codes; the decoder and the checks against hand-built byte
streams; and the emitter's output through the decoder.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from montyroll import model, reduce, smf
from montyroll.export import zsm, zsmcheck, zsmdecode, zsmsim

GOLDEN = Path(__file__).resolve().parent / "data" / "golden.zsm"
LOCAL = Path(__file__).resolve().parent.parent / "resources" / "local"


def sixteenBars() -> model.Song:
    """Sixteen bars of 4/4 in three parts with a tempo change at bar 9.

    A quaver melody on channel 1 whose notes abut (some repeating a pitch),
    crotchet bass notes on beats 1 and 3 on channel 2, and two triads a bar
    on channel 3 with a gap before each; 120 bpm to bar 8 and 100 bpm from
    bar 9, at 480 ticks per quarter.

    Returns:
        model.Song: the song.
    """
    bar = 1920
    track = [smf.Event(0, smf.META, bytearray([4, 2, 24, 8]), smf.META_TIME_SIG),
             smf.Event(0, smf.META, bytearray((500_000).to_bytes(3, "big")), smf.META_TEMPO),
             smf.Event(8 * bar, smf.META, bytearray((600_000).to_bytes(3, "big")),
                       smf.META_TEMPO)]
    steps = (0, 0, 2, 4, 4, 5, 7, 7, 9, 11, 12, 12)

    def note(ch: int, pitch: int, vel: int, start: int, length: int) -> None:
        """Append a note's pair of events."""
        track.append(smf.Event(start, 0x90 | ch, bytearray([pitch, vel])))
        track.append(smf.Event(start + length, 0x80 | ch, bytearray([pitch, 64])))

    for b in range(16):
        for q in range(8):
            note(0, 60 + steps[(b * 8 + q) % len(steps)], 100, b * bar + q * 240, 240)

        for beat in (0, 2):
            note(1, 36 + (0, 7, 5, 7)[b % 4], 90, b * bar + beat * 480, 480)

        for half in (0, 1):
            root = 48 + (0, 5, 7, 5)[b % 4]

            for interval in (0, 4, 7):
                note(2, root + interval, 70, b * bar + half * 960 + 60, 840)

    song = model.Song(smf.MidiFile(0, 480, [track]))
    return song


def x16Plan() -> reduce.Plan:
    """The fixture's plan: melody and bass on the OPM, the chords on the PSG.

    Returns:
        reduce.Plan: five voices over two tiers.
    """
    plan = reduce.Plan([reduce.Pool("melody", [0], 1, tier="opm"),
                        reduce.Pool("bass", [1], 1, tier="opm"),
                        reduce.Pool("chords", [2], 3, tier="psg")],
                       tiers=[reduce.Tier("opm", 8, 0, 300.0, "opm"),
                              reduce.Tier("psg", 16, 4, 0.0, "psg")])
    return plan


def _onsets(decoded: zsmdecode.Decoded) -> set[tuple[int, str, int, int]]:
    """Every note start in a decoded stream, replaying the chips' state.

    An OPM onset is a key-on write, or a KC change while the channel is
    keyed on; a PSG onset is a volume write above zero.

    Args:
        decoded: the stream.

    Returns:
        set[tuple[int, str, int, int]]: (frame, chip, channel, MIDI pitch).
    """
    onsets: set[tuple[int, str, int, int]] = set()
    kc = [0] * 8
    keyed = [False] * 8
    word = [[0, 0] for _ in range(16)]
    level = [0] * 16

    for f in decoded.frames:
        for w in f.items:
            if not isinstance(w, zsmdecode.Write):
                continue

            if w.chip == "opm" and 0x28 <= w.register < 0x30:
                ch = w.register - 0x28
                kc[ch] = w.value

                if keyed[ch]:
                    onsets.add((f.frame, "opm", ch, zsm.kcToMidi(w.value)))
            elif w.chip == "opm" and w.register == 0x08:
                ch = w.value & 0x07
                keyed[ch] = bool(w.value & 0x78)

                if keyed[ch]:
                    onsets.add((f.frame, "opm", ch, zsm.kcToMidi(kc[ch])))
            elif w.chip == "psg":
                ch, reg = divmod(w.register, 4)

                if reg < 2:
                    word[ch][reg] = w.value

                    if level[ch]:
                        pitch = round(zsm.freqwordToMidi(word[ch][0] | (word[ch][1] << 8)))
                        onsets.add((f.frame, "psg", ch, pitch))
                elif reg == 2:
                    if w.value & 0x3F and not level[ch]:
                        pitch = round(zsm.freqwordToMidi(word[ch][0] | (word[ch][1] << 8)))
                        onsets.add((f.frame, "psg", ch, pitch))

                    level[ch] = w.value & 0x3F

    return onsets


def _header(loop: int = 0, pcm: int = 0, fm: int = 0, psg: int = 0, rate: int = 60,
            reserved: int = 0) -> bytes:
    """Build a ZSM header.

    Args:
        loop: loop offset, 0 for none.
        pcm: PCM block offset, 0 for none.
        fm: OPM channel mask.
        psg: PSG channel mask.
        rate: frames per second.
        reserved: the reserved word.

    Returns:
        bytes: the 16 bytes.
    """
    header = (b"zm" + bytes([1]) + loop.to_bytes(3, "little") + pcm.to_bytes(3, "little")
              + bytes([fm]) + psg.to_bytes(2, "little") + rate.to_bytes(2, "little")
              + reserved.to_bytes(2, "little"))
    return header


# A small stream: a PSG volume and an OPM note-on at frame 0, five frames,
# an EXTCMD and the note-off at frame 5, then a 128-frame gap and the end.
STREAM = bytes([0x02, 0x3F,
                0x42, 0x28, 0x4A, 0x08, 0x78,
                0x85,
                0x40, 0x82, 0xAA, 0xBB,
                0x41, 0x08, 0x00,
                0xFF, 0x81,
                0x80])
GOOD = _header(fm=0b1, psg=0b1) + STREAM


class TestKeyCode:
    """MIDI note to YM2151 key code and back."""

    def testAnchors(self) -> None:
        """A4 is $4A as the X16 BASIC documentation has it; C sits at the top of its block."""
        assert zsm.midiToKc(69) == 0x4A
        assert zsm.midiToKc(72) == 0x4E     # C5 closes block 4
        assert zsm.midiToKc(73) == 0x50     # C#5 opens block 5
        assert zsm.midiToKc(13) == 0x00     # C#0, the lowest
        assert zsm.midiToKc(108) == 0x7E    # C8, the highest

    def testRoundTrip(self) -> None:
        """Every playable note survives the trip to a key code and back."""
        for note in range(13, 109):
            assert zsm.kcToMidi(zsm.midiToKc(note)) == note

    def testMonotonic(self) -> None:
        """Key codes rise strictly with pitch, so no octave boundary is misplaced."""
        kcs = [zsm.midiToKc(n) for n in range(13, 109)]
        assert all(a < b for a, b in zip(kcs, kcs[1:]))

    def testNoInvalidNibbles(self) -> None:
        """The note field never takes one of the four values the chip does not use."""
        for note in range(13, 109):
            assert (zsm.midiToKc(note) & 0x0F) not in (3, 7, 11, 15)

    def testOutOfRangeIsRefused(self) -> None:
        """C0 would need an octave field of -1 and C#8 one of 8: both refused."""
        for note in (12, 109, 0, 127):
            with pytest.raises(ValueError, match="range"):
                zsm.midiToKc(note)

    def testBadKeyCodeIsRefused(self) -> None:
        """A key code with an unused note field, or bit 7 set, is not a note."""
        for kc in (0x43, 0x47, 0x4B, 0x4F, 0x8A):
            with pytest.raises(ValueError, match="not a note"):
                zsm.kcToMidi(kc)


class TestPsgPitch:
    """MIDI note to VERA PSG frequency word and back."""

    def testAnchors(self) -> None:
        """A4 is word 591 and middle C is 351."""
        assert zsm.midiToFreqword(69) == 591
        assert zsm.midiToFreqword(60) == 351
        assert zsm.midiToHz(69) == pytest.approx(440.0)
        assert zsm.hzToMidi(440.0) == pytest.approx(69.0)

    def testRoundTripErrorFollowsTheRegister(self) -> None:
        """The error is at most half a word: a few cents above middle C, more in the bass."""
        for note in range(0, 128):
            word = zsm.midiToFreqword(note)

            if word == 0:
                continue

            # Half a word down is the larger side of the rounding error.
            back = zsm.freqwordToMidi(word)
            halfWord = 1200 * math.log2(word / (word - 0.5))
            cents = abs(back - note) * 100
            assert cents <= halfWord + 1e-6

            if note >= 60:
                assert cents < 3

            if note >= 24:
                assert cents < 20

    def testWordIsClamped(self) -> None:
        """Nothing below zero or above sixteen bits comes out of the conversion."""
        assert zsm.hzToFreqword(-5.0) == 0
        assert zsm.hzToFreqword(1e9) == 0xFFFF
        assert zsm.freqwordToHz(591) == pytest.approx(440.33, abs=0.01)


class TestDecode:
    """The strict decoder on hand-built streams."""

    def testGoodStream(self) -> None:
        """Header fields, frames of writes, opaque EXTCMDs, offsets, delay runs and length."""
        d = zsmdecode.decode(GOOD)
        assert d.header == zsmdecode.Header(0, 0, 0b1, 0b1, 60, 0)
        assert [f.frame for f in d.frames] == [0, 5]
        assert d.frames[0].items == [zsmdecode.Write("psg", 2, 0x3F),
                                     zsmdecode.Write("opm", 0x28, 0x4A),
                                     zsmdecode.Write("opm", 0x08, 0x78)]
        assert d.frames[1].items == [zsmdecode.ExtCmd(2, b"\xaa\xbb"),
                                     zsmdecode.Write("opm", 0x08, 0x00)]
        assert d.length == 133
        assert d.offsets == {16, 18, 23, 24, 28, 31, 32, 33}
        assert d.delayRuns == [[(23, 5)], [(31, 127), (32, 1)]]
        assert d.trailing == 0
        assert zsmcheck.check(d, 133) == []

    def testHeaderIsChecked(self) -> None:
        """A short file, a wrong magic or another version is refused with the offset."""
        with pytest.raises(ValueError, match="offset 0"):
            zsmdecode.decode(GOOD[:10])

        with pytest.raises(ValueError, match="magic"):
            zsmdecode.decode(b"ZM" + GOOD[2:])

        with pytest.raises(ValueError, match="version 2"):
            zsmdecode.decode(GOOD[:2] + bytes([2]) + GOOD[3:])

    def testTruncatedStreamsAreRefused(self) -> None:
        """A stream that ends inside a command, or without its end marker, is refused."""
        cases = {
            "no end marker": GOOD[:-1],
            "PSG write": _header() + bytes([0x02]),
            "YM2151 command": _header() + bytes([0x42, 0x28, 0x4A, 0x08]),
            "EXTCMD": _header() + bytes([0x40, 0x83, 0xAA]),
            "EXTCMD count": _header() + bytes([0x40]),
        }

        for what, data in cases.items():
            with pytest.raises(ValueError, match="runs past the end"):
                zsmdecode.decode(data)

    def testBytesAfterTheEnd(self) -> None:
        """Trailing bytes are counted, and are a problem only without a PCM block."""
        d = zsmdecode.decode(GOOD + b"\x00\x00\x00")
        assert d.trailing == 3
        assert any("follow the end marker" in p for p in zsmcheck.check(d))
        withPcm = zsmdecode.decode(_header(pcm=len(GOOD), fm=0b1, psg=0b1) + STREAM + b"\x00")
        assert zsmcheck.check(withPcm) == []


class TestChecks:
    """The structural invariants, one problem at a time."""

    def testMasksBothWays(self) -> None:
        """A used channel missing from a mask, and a claimed channel never written."""
        lacking = zsmdecode.decode(_header(fm=0, psg=0b1) + STREAM)
        assert [p for p in zsmcheck.check(lacking) if "FM mask lacks" in p and "0" in p]
        claiming = zsmdecode.decode(_header(fm=0b1, psg=0b11) + STREAM)
        assert [p for p in zsmcheck.check(claiming) if "PSG mask claims" in p and "1" in p]

    def testLoopOffsetOnABoundary(self) -> None:
        """A loop into the middle of a command is caught; onto a command it is fine."""
        good = zsmdecode.decode(_header(loop=23, fm=0b1, psg=0b1) + STREAM)
        assert zsmcheck.check(good) == []
        bad = zsmdecode.decode(_header(loop=19, fm=0b1, psg=0b1) + STREAM)
        assert any("loop offset 19" in p for p in zsmcheck.check(bad))

    def testDelaysSplitTooFinely(self) -> None:
        """Two delays that one byte could carry are a problem; two that could not are fine."""
        split = zsmdecode.decode(_header() + bytes([0x85, 0x85, 0x80]))
        assert any("2 adjacent delays for a gap of 10" in p for p in zsmcheck.check(split))
        forLoop = zsmdecode.decode(_header(loop=17) + bytes([0x85, 0x85, 0x80]))
        assert zsmcheck.check(forLoop) == []
        needed = zsmdecode.decode(_header() + bytes([0xFF, 0xFF, 0x80]))
        assert zsmcheck.check(needed) == []
        assert needed.length == 254

    def testUnexpectedRegisterAndBadKeyCode(self) -> None:
        """A write to TEST and a KC with an unused note field are named, with the frame."""
        stream = bytes([0x42, 0x01, 0x00, 0x2B, 0x43, 0x80])
        d = zsmdecode.decode(_header(fm=0b1000) + stream)
        problems = zsmcheck.check(d)
        assert any("register $01" in p for p in problems)
        assert any("KC $43 on channel 3" in p and "not a note" in p for p in problems)

    def testLengthRateAndReserved(self) -> None:
        """The expected frame count, the 60 Hz rate and the reserved word are enforced."""
        d = zsmdecode.decode(GOOD)
        assert any("133 frames long, expected 100" in p for p in zsmcheck.check(d, 100))
        odd = zsmdecode.decode(_header(fm=0b1, psg=0b1, rate=50, reserved=7) + STREAM)
        problems = zsmcheck.check(odd)
        assert any("frame rate is 50" in p for p in problems)
        assert any("reserved field is 7" in p for p in problems)


class TestEmit:
    """The stream writer, checked through the independent decoder."""

    def testDelaysSplitAndBatchesFill(self) -> None:
        """A 300-frame gap is three delay bytes; 70 OPM writes on one frame are two commands."""
        many = [("opm", 0x60 + (i % 32), i) for i in range(70)]
        writes = {0: many, 300: [("psg", 2, 0x3F)]}
        stream, loop = zsm.emitStream(writes)
        assert loop == 0
        assert stream[0] == 0x40 | 63
        assert stream[1 + 2 * 63] == 0x40 | 7
        tail = stream[1 + 2 * 63 + 1 + 2 * 7:]
        assert list(tail) == [0xFF, 0xFF, 0x80 | 46, 0x02, 0x3F, 0x80]
        d = zsmdecode.decode(zsm.emitHeader(*zsm.channelMasks(writes)) + stream)
        assert d.length == 300
        assert [f for _, f in d.delayRuns[0]] == [127, 127, 46]
        assert zsmcheck.check(d, 300) == []
        assert [w.value for w in d.frames[0].items] == list(range(70))

    def testLoopLandsOnABoundary(self) -> None:
        """The loop offset points at the loop frame's first command, even in a gap."""
        writes = {0: [("opm", 0x20, 0xC7)], 50: [("opm", 0x08, 0x78)]}
        data = zsm.build(writes, loopFrame=10)
        d = zsmdecode.decode(data)
        assert d.header.loopOffset in d.offsets
        assert zsmcheck.check(d) == []

        # The loop offset sits after the delay to frame 10 and before the
        # delay on to frame 50, so a player jumping there waits 40 frames.
        assert data[d.header.loopOffset] == 0x80 | 40
        assert d.length == 50

    def testMasksFollowTheWrites(self) -> None:
        """The header claims exactly the channels written, both chips."""
        writes = {0: [("opm", 0x23, 0xC7), ("opm", 0x08, 0x7B), ("psg", 4 * 5 + 2, 0xFF),
                      ("psg", 4 * 15, 0x10)]}
        assert zsm.channelMasks(writes) == (0b1000, (1 << 5) | (1 << 15))
        d = zsmdecode.decode(zsm.build(writes))
        assert zsmcheck.check(d) == []
        assert d.header.fmMask == 0b1000

    def testPatchWrites(self) -> None:
        """A patch lands on its channel's registers and operator slots."""
        patch = zsm.Patch(feedback=3, algorithm=4, pms=2, ams=1,
                          operators=[zsm.Operator(dt1=1, mul=2, tl=20, ks=1, ar=31, amsEnable=True,
                                                  d1r=5, dt2=1, d2r=3, d1l=4, rr=7)] * 4)
        writes = dict(zsm.patchWrites(2, patch, left=True, right=False))
        assert writes[0x22] == 0x40 | (3 << 3) | 4
        assert writes[0x3A] == (2 << 4) | 1
        assert writes[0x42] == (1 << 4) | 2 and writes[0x62] == 20
        assert writes[0x82] == (1 << 6) | 31 and writes[0xA2] == 0x80 | 5
        assert writes[0xC2] == (1 << 6) | 3 and writes[0xE2] == (4 << 4) | 7
        assert 0x40 + 3 * 8 + 2 in writes and len(writes) == 26

    def testNoteWrites(self) -> None:
        """Note on is KC then key-on with all operators; PSG is frequency then volume."""
        assert zsm.opmNoteOn(3, 69) == [(0x2B, 0x4A), (0x08, 0x7B)]
        assert zsm.opmNoteOff(3) == [(0x08, 0x03)]
        assert zsm.psgNoteOn(2, 69) == [(8, 591 & 0xFF), (9, 591 >> 8), (10, 0xC0 | 63)]
        assert zsm.psgNoteOff(2) == [(10, 0)]
        assert zsm.psgVoice(1, zsm.PSG_TRIANGLE, 0) == [(7, 2 << 6)]

    def testChromaticScales(self) -> None:
        """Both scales decode clean and give back every note in order at the right frames."""
        opm = zsmdecode.decode(zsm.chromaticScale("opm", 36, 84, 20))
        assert zsmcheck.check(opm, 49 * 20 - 2) == []     # ends on the last note-off
        kcs = [(f.frame, w.value) for f in opm.frames for w in f.items
               if w.chip == "opm" and w.register == 0x28]
        assert [zsm.kcToMidi(kc) for _, kc in kcs] == list(range(36, 85))
        assert [frame for frame, _ in kcs] == [20 * i for i in range(49)]
        assert opm.header.fmMask == 0b1 and opm.header.psgMask == 0

        psg = zsmdecode.decode(zsm.chromaticScale("psg", 36, 84, 20))
        assert zsmcheck.check(psg, 49 * 20 - 2) == []
        words = {}

        for f in psg.frames:
            for w in f.items:
                if w.chip == "psg" and w.register in (0, 1):
                    words.setdefault(f.frame, [0, 0])[w.register] = w.value

        played = [lo | (hi << 8) for lo, hi in (words[20 * i] for i in range(49))]
        assert played == [zsm.midiToFreqword(n) for n in range(36, 85)]
        assert psg.header.psgMask == 0b1 and psg.header.fmMask == 0


class TestExport:
    """A reduction to register writes, through the decoder."""

    def _song(self, *notes: tuple[int, int, int, int, int]) -> model.Song:
        """A one-track song at 120 bpm from (channel, pitch, velocity, start, end)."""
        track = [smf.Event(0, smf.META, bytearray((500_000).to_bytes(3, "big")), smf.META_TEMPO)]

        for ch, pitch, vel, start, end in notes:
            track.append(smf.Event(start, 0x90 | ch, bytearray([pitch, vel])))
            track.append(smf.Event(end, 0x80 | ch, bytearray([pitch, 64])))

        song = model.Song(smf.MidiFile(0, 480, [track]))
        return song

    def _opmPlan(self, voices: int = 1) -> reduce.Plan:
        """One pool of channel 1 on an OPM tier."""
        plan = reduce.Plan([reduce.Pool("one", [0], voices, tier="opm")],
                           tiers=[reduce.Tier("opm", 8, chip="opm")])
        return plan

    def testFramesFollowTheTempoMap(self) -> None:
        """A tick's frame is its rounded absolute position, so a tempo change does not drift."""
        song = sixteenBars()
        assert zsm.frameOf(song, 0) == 0
        assert zsm.frameOf(song, 1920) == 120          # a bar at 120 bpm is 2 s
        assert zsm.frameOf(song, 8 * 1920) == 960
        assert zsm.frameOf(song, 9 * 1920) == 960 + 144    # a bar at 100 bpm is 2.4 s

    def testVoicesLandOnTheirChips(self) -> None:
        """Setup at frame 0 reaches OPM channels 0 and 1 and PSG channels 0 to 2."""
        song = sixteenBars()
        plan = x16Plan()
        writes = zsm.exportWrites(song, reduce.reduce(song, plan), plan)
        opm = {r & 0x07 for chip, r, _ in writes[0] if chip == "opm" and r >= 0x20}
        psg = {r >> 2 for chip, r, _ in writes[0] if chip == "psg"}
        assert opm == {0, 1} and psg == {0, 1, 2}

        # The setup comes before the first key-on in the frame.
        registers = [r for chip, r, _ in writes[0] if chip == "opm"]
        assert registers.index(0x08) > registers.index(0xE0 + 8 * 3 + 1)

    def testSlideAndRetrigger(self) -> None:
        """Abutting notes at another pitch slide; at the same pitch they release and restart."""
        song = self._song((0, 60, 100, 0, 480), (0, 64, 100, 480, 960), (0, 64, 100, 960, 1440))
        plan = self._opmPlan()
        writes = zsm.exportWrites(song, reduce.reduce(song, plan), plan)
        keyed = lambda f: [(r, v) for _, r, v in writes[f] if not 0x60 <= r < 0x80]  # noqa: E731
        assert keyed(30) == [(0x28, zsm.midiToKc(64))]
        assert keyed(60) == [(0x08, 0x00), (0x28, zsm.midiToKc(64)), (0x08, 0x78)]
        assert keyed(90) == [(0x08, 0x00)]

    def testOffsComeBeforeOnsAndShortNotesLastAFrame(self) -> None:
        """Offs precede ons in a frame, a slide replaces an off, and a tiny note gets a frame."""
        song = self._song((0, 60, 100, 0, 480), (0, 64, 100, 0, 480), (0, 67, 100, 480, 482),
                          (0, 62, 100, 960, 1440))
        plan = self._opmPlan(2)
        r = reduce.reduce(song, plan)
        writes = zsm.exportWrites(song, r, plan)
        assert sorted(writes) == [0, 30, 31, 60, 90]

        # Voice 0 slides from 60 to 67 while voice 1 is released.
        keyed = lambda f: [(r, v) for _, r, v in writes[f] if not 0x60 <= r < 0x80]  # noqa: E731
        assert keyed(30) == [(0x28, zsm.midiToKc(67)), (0x08, 0x01)]
        assert keyed(31) == [(0x08, 0x00)]

    def testPsgVolumeFollowsVelocity(self) -> None:
        """Full velocity is 63, half is 32, the quietest still sounds."""
        song = self._song((0, 60, 127, 0, 100), (0, 62, 64, 200, 300), (0, 64, 1, 400, 500))
        plan = reduce.Plan([reduce.Pool("one", [0], 1, tier="psg")],
                           tiers=[reduce.Tier("psg", 16, chip="psg")])
        writes = zsm.exportWrites(song, reduce.reduce(song, plan), plan,
                                  psgSounds={0: zsm.PsgSound()})
        volumes = [v & 0x3F for items in writes.values() for chip, r, v in items
                   if chip == "psg" and r == 2 and v & 0x3F]
        assert volumes == [63, 32, 1]

    def testRangeAndLoop(self) -> None:
        """Ticks outside the range are left out, the start is frame 0, the loop is a boundary."""
        song = sixteenBars()
        plan = x16Plan()
        r = reduce.reduce(song, plan)
        data = zsm.export(song, r, plan, start=4 * 1920, end=8 * 1920, loop=6 * 1920)
        d = zsmdecode.decode(data)
        assert zsmcheck.check(d) == []
        assert d.length == 4 * 120
        assert d.header.loopOffset in d.offsets
        assert d.frames[0].frame == 0
        assert max(f.frame for f in d.frames) <= 4 * 120     # the last release, at the range's end

    def testNoChipIsRefused(self) -> None:
        """A pool without a tier, or a tier without a chip, cannot be exported."""
        song = self._song((0, 60, 100, 0, 480))
        plain = reduce.Plan.single(song, 1)

        with pytest.raises(ValueError, match="no tier"):
            zsm.exportWrites(song, reduce.reduce(song, plain), plain)

        odd = reduce.Plan([reduce.Pool("one", [0], 1, tier="sid")],
                          tiers=[reduce.Tier("sid", 3, chip="sid")])

        with pytest.raises(ValueError, match="not opm or psg"):
            zsm.exportWrites(song, reduce.reduce(song, odd), odd)

    def testGoldenFile(self) -> None:
        """The sixteen-bar fixture exports byte for byte as the checked-in file."""
        song = sixteenBars()
        plan = x16Plan()
        data = zsm.export(song, reduce.reduce(song, plan), plan)
        assert data == GOLDEN.read_bytes()

    def testGoldenRoundTrip(self) -> None:
        """Every placed note starts in the stream on its channel and pitch, within half a frame."""
        song = sixteenBars()
        plan = x16Plan()
        r = reduce.reduce(song, plan)
        d = zsmdecode.decode(zsm.export(song, r, plan))
        assert zsmcheck.check(d) == []
        onsets = _onsets(d)
        expected = set()
        worst = 0.0

        for p in r.placed:
            frame = zsm.frameOf(song, p.start)
            chip = "opm" if p.voice < 2 else "psg"
            channel = p.voice if chip == "opm" else p.voice - 2
            expected.add((frame, chip, channel, p.pitch))
            worst = max(worst, abs(frame * 1000 / 60 - song.tickToSeconds(p.start) * 1000))

        assert onsets == expected
        assert worst <= 1000 / 60 / 2 + 1e-6
        assert len(r.placed) + len(r.dropped) == 128 + 32 + 96

    def testDanubeFullArrangement(self) -> None:
        """JSBD-1, when present: the whole piece on 8 OPM and 12 PSG voices, clean and in time."""
        path = LOCAL / "JSBD-1.mid"

        if not path.exists():
            pytest.skip("JSBD-1.mid is not available on this machine")

        song = model.Song.load(str(path))
        chans = sorted({n.channel for n in song.notes if n.channel != 9})
        core = [c for c in chans if c <= 7 or c == 15]
        rest = [c for c in chans if c not in core]
        plan = reduce.Plan([reduce.Pool("core", core, 8, dedupOctave=True, tier="opm"),
                            reduce.Pool("rest", rest, 12, dedupOctave=True, tier="psg")],
                           tiers=[reduce.Tier("opm", 8, 0, 300.0, "opm"),
                                  reduce.Tier("psg", 16, 4, 0.0, "psg")])
        r = reduce.reduce(song, plan)
        d = zsmdecode.decode(zsm.export(song, r, plan))
        assert zsmcheck.check(d) == []
        assert d.length == zsm.frameOf(song, max(p.end for p in r.placed))

        # Every onset on its channel at its pitch, the worst timing error
        # under half a frame over the whole five minutes.
        onsets = _onsets(d)
        worst = 0.0

        for p in r.placed:
            frame = zsm.frameOf(song, p.start)
            chip = "opm" if p.voice < 8 else "psg"
            channel = p.voice if chip == "opm" else p.voice - 8
            assert (frame, chip, channel, p.pitch) in onsets
            worst = max(worst, abs(frame * 1000 / 60 - song.tickToSeconds(p.start) * 1000))

        assert worst <= 1000 / 60 / 2 + 1e-6


class TestSimulate:
    """The chip-state replay and its checks."""

    def _opmSetup(self, channel: int = 0) -> bytes:
        """A patch for one OPM channel as one YM2151 command."""
        pairs = zsm.patchWrites(channel, zsm.DEFAULT_PATCH)
        data = bytes([0x40 | len(pairs)]) + b"".join(bytes(p) for p in pairs)
        return data

    def testNotesAndPeaks(self) -> None:
        """Key-ons and key-offs become notes with a pitch; a KC change while keyed splits one."""
        stream = (self._opmSetup(0)
                  + bytes([0x42, 0x28, 0x4A, 0x08, 0x78,     # A4 on
                           0x8A,                             # 10 frames
                           0x41, 0x28, 0x4E,                 # slide to C5
                           0x85,
                           0x41, 0x08, 0x00,                 # off
                           0x80]))
        sim = zsmsim.simulate(zsmdecode.decode(_header(fm=1) + stream))
        assert [(n.frame, n.chip, n.channel, n.pitch, n.end) for n in sim.notes] == [
            (0, "opm", 0, 69, 10), (10, "opm", 0, 72, 15)]
        assert sim.peakOpm == 1 and sim.peakPsg == 0
        assert sim.problems == []

    def testPsgNotesAndReserve(self) -> None:
        """A PSG note is a volume above zero at its word's pitch; a reserved channel is flagged."""
        on = zsm.psgNoteOn(0, 69) + zsm.psgNoteOn(15, 60)
        off = zsm.psgNoteOff(0) + zsm.psgNoteOff(15)
        stream = (b"".join(bytes(p) for p in on) + bytes([0x85])
                  + b"".join(bytes(p) for p in off) + bytes([0x80]))
        sim = zsmsim.simulate(zsmdecode.decode(_header(psg=0x8001) + stream), psgVoices=12)
        pitches = sorted(round(n.pitch) for n in sim.notes)
        assert pitches == [60, 69]
        assert sim.peakPsg == 2
        assert any("channel 15 sounds but is reserved" in p for p in sim.problems)

    def testKeyHandlingProblems(self) -> None:
        """A double key-on, a stray key-off, a key-on before the patch, and a note left sounding."""
        stream = bytes([0x41, 0x08, 0x78,        # key-on with no patch
                        0x41, 0x08, 0x78,        # again while on
                        0x41, 0x08, 0x01,        # key-off channel 1, which is off
                        0x80])
        sim = zsmsim.simulate(zsmdecode.decode(_header() + stream))
        texts = " | ".join(sim.problems)
        assert "keyed on with 0 of 26 patch registers" in texts
        assert "key-on to OPM channel 0 while keyed on" in texts
        assert "key-off to OPM channel 1 while off" in texts
        assert "OPM channel 0 is still keyed on" in texts

    def testShortGapsCountAgainstTheRelease(self) -> None:
        """A key-on sooner after a key-off than the release is counted, not condemned."""
        stream = (self._opmSetup(0)
                  + bytes([0x42, 0x28, 0x4A, 0x08, 0x78, 0x85, 0x41, 0x08, 0x00,
                           0x83, 0x41, 0x08, 0x78, 0x85, 0x41, 0x08, 0x00, 0x80]))
        d = zsmdecode.decode(_header(fm=1) + stream)
        assert zsmsim.simulate(d, release={"opm": 5}).shortGaps == 1
        assert zsmsim.simulate(d, release={"opm": 3}).shortGaps == 0
        assert zsmsim.simulate(d).problems == []

    def testGoldenSimulatesClean(self) -> None:
        """The fixture replays with no problems, within its tiers, and plays every placed note."""
        song = sixteenBars()
        plan = x16Plan()
        r = reduce.reduce(song, plan)
        sim = zsmsim.simulate(zsmdecode.decode(zsm.export(song, r, plan)), psgVoices=12,
                              release={"opm": 18})
        assert sim.problems == []
        assert sim.peakOpm <= 2 and sim.peakPsg <= 3
        assert len(sim.notes) == len(r.placed)
        assert all(n.end > n.frame for n in sim.notes)


class TestExpression:
    """Loudness, pan, PSG sounds and decay in the export."""

    def _song(self, *notes: tuple[int, int, int, int, int],
              controls: tuple[tuple[int, int, int], ...] = ()) -> model.Song:
        """A song at 120 bpm from notes and (channel, controller, value) triples at tick 0."""
        track = [smf.Event(0, smf.META, bytearray((500_000).to_bytes(3, "big")), smf.META_TEMPO)]

        for ch, cc, value in controls:
            track.append(smf.Event(0, 0xB0 | ch, bytearray([cc, value])))

        for ch, pitch, vel, start, end in notes:
            track.append(smf.Event(start, 0x90 | ch, bytearray([pitch, vel])))
            track.append(smf.Event(end, 0x80 | ch, bytearray([pitch, 64])))

        song = model.Song(smf.MidiFile(0, 480, [track]))
        return song

    def _plan(self, chip: str, channels: list[int]) -> reduce.Plan:
        """One pool of the channels, four voices, on a tier of the chip."""
        plan = reduce.Plan([reduce.Pool("one", channels, 4, tier=chip)],
                           tiers=[reduce.Tier(chip, 16, chip=chip)])
        return plan

    def testCarriersByAlgorithm(self) -> None:
        """Algorithm 7 has every slot as a carrier, 4 the two C slots, 0 to 3 only C2."""
        assert zsm.Patch(algorithm=7).carriers == (0, 1, 2, 3)
        assert zsm.Patch(algorithm=4).carriers == (2, 3)
        assert zsm.Patch(algorithm=0).carriers == (3,)

    def testVelocityAndVolumeSetTheCarriers(self) -> None:
        """Full velocity plays at the patch's TL; half velocity and CC7 each add attenuation."""
        song = self._song((0, 60, 127, 100, 200), (0, 62, 64, 300, 400), (1, 64, 127, 500, 600),
                          controls=((1, 7, 64),))
        plan = self._plan("opm", [0, 1])
        writes = zsm.exportWrites(song, reduce.reduce(song, plan), plan)
        levels = [v for f in sorted(writes) for chip, r, v in writes[f]
                  if chip == "opm" and r == 0x60 and f > 0]
        base = zsm.DEFAULT_PATCH.operators[0].tl

        # The first note is at the patch's level, which the setup already
        # wrote; the second and third are half as loud, and only one write
        # is needed between them.
        assert levels == [base + round((1 - 64 / 127) * zsm.LEVEL_RANGE)]

    def testLevelsAreWrittenOnlyWhenTheyChange(self) -> None:
        """Notes at one velocity after another write no level; a change writes the carrier."""
        song = self._song((0, 60, 100, 100, 200), (0, 62, 100, 300, 400), (0, 64, 50, 500, 600),
                          (0, 65, 50, 700, 800))
        plan = self._plan("opm", [0])
        writes = zsm.exportWrites(song, reduce.reduce(song, plan), plan)
        levels = [(f, r) for f in sorted(writes) for chip, r, v in writes[f]
                  if chip == "opm" and 0x60 <= r < 0x80 and f > 0]
        assert levels == [(6, 0x60), (31, 0x60)]

    def testPanFromTheController(self) -> None:
        """CC10 hard left and right place the notes; a voice is rewritten only on a change."""
        song = self._song((0, 60, 100, 100, 200), (0, 62, 100, 300, 400), (1, 64, 100, 500, 600),
                          controls=((0, 10, 0), (1, 10, 127)))
        assert zsm.channelPans(song) == {0: (True, False), 1: (False, True)}
        plan = self._plan("opm", [0, 1])
        writes = zsm.exportWrites(song, reduce.reduce(song, plan), plan)
        panWrites = [(f, v >> 6) for f in sorted(writes) for chip, r, v in writes[f]
                     if chip == "opm" and r == 0x20 and f > 0]
        assert panWrites == [(6, 0b01), (31, 0b10)]    # left at the first note, right at the third
        psg = self._plan("psg", [0, 1])
        flat = {0: zsm.PsgSound(), 1: zsm.PsgSound()}
        pw = zsm.exportWrites(song, reduce.reduce(song, psg), psg, psgSounds=flat)
        pans = [v >> 6 for f in sorted(pw) for chip, r, v in pw[f]
                if chip == "psg" and r % 4 == 2 and v & 0x3F]
        assert pans == [0b01, 0b01, 0b10]

    def testPsgSoundsByProgram(self) -> None:
        """Programs pick a waveform and, for plucked parts, a decay."""
        assert zsm.psgSoundFor(46) == zsm.PsgSound(zsm.PSG_PULSE, 8, 40)     # harp
        assert zsm.psgSoundFor(43) == zsm.PsgSound(zsm.PSG_TRIANGLE, 32, 0)  # contrabass
        assert zsm.psgSoundFor(60).wave == zsm.PSG_SAW                        # French horn
        assert zsm.psgSoundFor(73).wave == zsm.PSG_TRIANGLE                   # flute
        assert zsm.psgSoundFor(90) == zsm.PsgSound()

    def testDecayStepsDownToSilence(self) -> None:
        """A plucked note steps its volume down every other frame to zero within the decay."""
        song = self._song((0, 60, 127, 0, 960))
        plan = self._plan("psg", [0])
        sounds = {0: zsm.PsgSound(zsm.PSG_PULSE, 8, 10)}
        writes = zsm.exportWrites(song, reduce.reduce(song, plan), plan, psgSounds=sounds)
        levels = [(f, v & 0x3F) for f in sorted(writes) for chip, r, v in writes[f]
                  if chip == "psg" and r == 2]
        assert levels == [(0, 63), (2, 50), (4, 38), (6, 25), (8, 13), (60, 0)]
        wave = [v for chip, r, v in writes[0] if chip == "psg" and r == 3]
        assert wave == [(zsm.PSG_PULSE << 6) | 32, (zsm.PSG_PULSE << 6) | 8]

    def testMasterLevelBringsBothChipsDown(self) -> None:
        """Half amplitude is eight TL steps on every carrier and half the PSG volume."""
        assert zsm.masterSteps(1.0) == 0 and zsm.masterSteps(0.5) == 8
        assert zsm.masterSteps(1.5) == 0 and zsm.masterSteps(0.0) == 0
        song = self._song((0, 60, 127, 100, 200))
        opm = self._plan("opm", [0])
        full = zsm.exportWrites(song, reduce.reduce(song, opm), opm)
        half = zsm.exportWrites(song, reduce.reduce(song, opm), opm, master=0.5)
        assert [r for _, r, _ in full[6]] == [0x28, 0x08]              # no level write at full
        assert [(r, v) for _, r, v in half[6]][0] == (0x60, zsm.DEFAULT_PATCH.operators[0].tl + 8)
        psg = self._plan("psg", [0])
        writes = zsm.exportWrites(song, reduce.reduce(song, psg), psg, master=0.5,
                                  psgSounds={0: zsm.PsgSound()})
        assert [v & 0x3F for _, r, v in writes[6] if r == 2] == [32]

    def testSlideKeepsTheLevel(self) -> None:
        """An OPM slide carries the new note's level writes but no key cycle."""
        song = self._song((0, 60, 127, 0, 480), (0, 64, 64, 480, 960))
        plan = self._plan("opm", [0])
        writes = zsm.exportWrites(song, reduce.reduce(song, plan), plan)
        regs = [r for _, r, _ in writes[30]]
        assert 0x08 not in regs
        assert 0x28 in regs and regs.count(0x60) == 1
