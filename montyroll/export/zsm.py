# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""ZSM export for the Commander X16: the YM2151 and the VERA PSG.

The module is an export target (see the package docstring): it holds the
two chips' pitch arithmetic, the patch and note register writes, the ZSM
header and stream writer, and the `export`, `verify` and `describe` the
application calls. It works in frames, one sixtieth of a second, the rate
the X16 player runs at; a MIDI tick is converted through the song's tempo
map.

The YM2151's key code (KC) is not a linear note number: its low nibble is a
note field where the four values with both low bits set are unused, and
the twelve valid values run C# to C, so C sits at the top of the block and
its octave field is one less than its octave number. The PSG takes a 16-bit
frequency word of about 0.745 Hz a step, which is 2 to 3 cents of error
above middle C and 10 to 20 in the bass octave.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .. import model, reduce
from . import zsmcheck, zsmdecode, zsmsim
from .chip import Chip

NAME = "Commander X16 (ZSM)"
SUFFIX = ".zsm"
BANK = 8192               # bytes per RAM bank the player reads from

# YM2151 key code: the note field for each semitone above C, C# to C in the
# nibble order the chip wants, with 3, 7, 11 and 15 never used.
OPM_NOTE_NIBBLE = (14, 0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13)
OPM_INVALID_NIBBLES = (3, 7, 11, 15)
KC_LOWEST = 13           # C#0, key code $00
KC_HIGHEST = 108         # C8, key code $7E

# VERA PSG: the frequency word steps at 25 MHz / 2^25.
PSG_HZ_PER_UNIT = 25_000_000 / (1 << 25)
PSG_MAX_WORD = 0xFFFF


# --------------------------------------------------------------- YM2151
def foldToOpmRange(note: int) -> int:
    """Move a note by octaves until the YM2151 can play it.

    Args:
        note: MIDI note number.

    Returns:
        int: the note, raised or lowered by whole octaves into 13 to 108.
    """
    folded = note

    while folded < KC_LOWEST:
        folded += 12

    while folded > KC_HIGHEST:
        folded -= 12

    return folded


def midiToKc(note: int) -> int:
    """Turn a MIDI note number into a YM2151 key code.

    The octave field is the note's octave number for C# through B and one
    less for C, which closes the block below rather than opening its own.
    A4 (69) is `$4A`, C5 (72) is `$4E` and C#5 (73) is `$50`.

    Args:
        note: MIDI note number, 13 (C#0) to 108 (C8).

    Returns:
        int: the key code, octave in bits 6 to 4 and note in bits 3 to 0.

    Raises:
        ValueError: the note is outside what the chip can play.
    """
    if not KC_LOWEST <= note <= KC_HIGHEST:
        raise ValueError(f"MIDI note {note} is outside the YM2151's range "
                         f"({KC_LOWEST} to {KC_HIGHEST})")

    semitone = note % 12
    octave = note // 12 - 1

    if semitone == 0:
        octave -= 1

    kc = (octave << 4) | OPM_NOTE_NIBBLE[semitone]
    return kc


def kcToMidi(kc: int) -> int:
    """Turn a YM2151 key code back into a MIDI note number.

    Args:
        kc: the key code, bit 7 clear.

    Returns:
        int: the MIDI note number.

    Raises:
        ValueError: the note field is one of the four values the chip
            does not use, or bit 7 is set.
    """
    nibble = kc & 0x0F
    octave = kc >> 4

    if nibble in OPM_INVALID_NIBBLES or octave > 7:
        raise ValueError(f"key code ${kc:02X} is not a note")

    semitone = OPM_NOTE_NIBBLE.index(nibble)
    note = (octave + 1) * 12 + semitone

    if semitone == 0:
        note += 12

    return note


# ------------------------------------------------------------------ PSG
def midiToHz(note: float) -> float:
    """Frequency of a MIDI note in equal temperament at A4 = 440 Hz.

    Args:
        note: MIDI note number, fractional for a bent pitch.

    Returns:
        float: hertz.
    """
    hz = 440.0 * 2 ** ((note - 69) / 12)
    return hz


def hzToMidi(hz: float) -> float:
    """MIDI note number of a frequency, fractional between semitones.

    Args:
        hz: hertz, above zero.

    Returns:
        float: the note number.
    """
    note = 69 + 12 * math.log2(hz / 440.0)
    return note


def hzToFreqword(hz: float) -> int:
    """Turn a frequency into the VERA PSG's 16-bit frequency word.

    Args:
        hz: hertz.

    Returns:
        int: the nearest word, 0 to 65535.
    """
    word = max(0, min(PSG_MAX_WORD, round(hz / PSG_HZ_PER_UNIT)))
    return word


def freqwordToHz(word: int) -> float:
    """The frequency a VERA PSG frequency word plays.

    Args:
        word: 0 to 65535.

    Returns:
        float: hertz.
    """
    hz = word * PSG_HZ_PER_UNIT
    return hz


def midiToFreqword(note: float) -> int:
    """Turn a MIDI note into the VERA PSG frequency word nearest its pitch.

    A4 (69) is 591 and middle C (60) is 351.

    Args:
        note: MIDI note number, fractional for a bent pitch.

    Returns:
        int: the word, 0 to 65535.
    """
    word = hzToFreqword(midiToHz(note))
    return word


def freqwordToMidi(word: int) -> float:
    """The MIDI note number, fractional, that a PSG frequency word plays.

    Args:
        word: 1 to 65535.

    Returns:
        float: the note number.
    """
    note = hzToMidi(freqwordToHz(word))
    return note


# -------------------------------------------------------------- patches
@dataclass
class Operator:
    """One of a YM2151 channel's four operators, as its registers hold it."""

    dt1: int = 0            # detune 1, 0 to 7
    mul: int = 1            # frequency multiple, 0 to 15
    tl: int = 0             # total level, 0 loud to 127 silent
    ks: int = 0             # key scaling, 0 to 3
    ar: int = 31            # attack rate, 0 to 31
    amsEnable: bool = False
    d1r: int = 0            # first decay rate, 0 to 31
    dt2: int = 0            # detune 2, 0 to 3
    d2r: int = 0            # second decay rate, 0 to 31
    d1l: int = 0            # first decay level, 0 to 15
    rr: int = 15            # release rate, 0 to 15


@dataclass
class Patch:
    """A YM2151 voice: the channel registers and four operators.

    `patchWrites` turns one into the register writes for a channel.
    """

    feedback: int = 0       # 0 to 7
    algorithm: int = 7      # 0 to 7; 7 has all four operators as carriers
    pms: int = 0            # pitch modulation sensitivity, 0 to 7
    ams: int = 0            # amplitude modulation sensitivity, 0 to 3
    operators: list[Operator] = field(default_factory=lambda: [Operator() for _ in range(4)])

    @property
    def carriers(self) -> tuple[int, ...]:
        """The operator slots the algorithm sends to the output."""
        slots = CARRIERS[self.algorithm]
        return slots


# Carrier operator slots per algorithm, in the register order M1, M2, C1, C2.
CARRIERS = {0: (3,), 1: (3,), 2: (3,), 3: (3,), 4: (2, 3), 5: (1, 2, 3), 6: (1, 2, 3),
            7: (0, 1, 2, 3)}
LEVEL_RANGE = 24          # TL steps of 0.75 dB between full and zero velocity or volume


# A plain tone for the test scales: one carrier at a modest level, the
# other three silent, quick attack and release.
SILENT = Operator(tl=127)
DEFAULT_PATCH = Patch(operators=[Operator(tl=24, ar=31, d1r=6, d1l=2, rr=10),
                                 SILENT, SILENT, SILENT])
KEY_ON_ALL = 0x78         # all four operators in a key-on write


def _op(mul: int, tl: int, ar: int, d1r: int, d1l: int, d2r: int, rr: int,
        dt1: int = 0, ks: int = 0) -> Operator:
    """An operator from the values a patch sheet lists.

    Args:
        mul: frequency multiple.
        tl: total level.
        ar: attack rate.
        d1r: first decay rate.
        d1l: first decay level.
        d2r: second decay rate.
        rr: release rate.
        dt1: detune.
        ks: key scaling.

    Returns:
        Operator: the operator.
    """
    operator = Operator(dt1=dt1, mul=mul, tl=tl, ks=ks, ar=ar, d1r=d1r, d2r=d2r, d1l=d1l, rr=rr)
    return operator


# The patch library: operators in the register order M1, M2, C1, C2, so
# algorithm 4 pairs M1 with C1 and M2 with C2, detuned against each other
# where a part wants width.
PATCHES: dict[str, Patch] = {
    "default": DEFAULT_PATCH,
    "strings": Patch(feedback=6, algorithm=4, operators=[
        _op(1, 40, 18, 6, 2, 2, 7), _op(2, 48, 17, 6, 2, 2, 7, dt1=3),
        _op(1, 24, 14, 4, 1, 1, 7), _op(1, 24, 13, 4, 1, 1, 7, dt1=5)]),
    "flute": Patch(feedback=0, algorithm=4, operators=[
        _op(2, 55, 26, 10, 3, 0, 8), _op(1, 127, 31, 0, 0, 0, 15),
        _op(1, 20, 24, 6, 1, 1, 8), _op(1, 36, 24, 6, 1, 1, 8, dt1=4)]),
    "reed": Patch(feedback=5, algorithm=4, operators=[
        _op(3, 46, 31, 8, 2, 1, 8), _op(1, 60, 31, 5, 2, 1, 8),
        _op(1, 22, 28, 6, 1, 1, 8), _op(2, 40, 28, 6, 1, 1, 8)]),
    "brass": Patch(feedback=6, algorithm=4, operators=[
        _op(1, 32, 24, 10, 2, 3, 8), _op(1, 38, 24, 10, 2, 3, 8, dt1=2),
        _op(1, 20, 20, 8, 1, 2, 8), _op(1, 22, 20, 8, 1, 2, 8, dt1=6)]),
    "bass": Patch(feedback=5, algorithm=4, operators=[
        _op(1, 30, 31, 12, 3, 5, 10), _op(1, 127, 31, 0, 0, 0, 15),
        _op(1, 18, 31, 10, 2, 6, 10), _op(1, 127, 31, 0, 0, 0, 15)]),
    "piano": Patch(feedback=3, algorithm=4, operators=[
        _op(3, 42, 31, 14, 4, 6, 9), _op(1, 52, 31, 12, 4, 6, 9),
        _op(1, 18, 31, 10, 2, 7, 9), _op(2, 30, 31, 10, 2, 7, 9, dt1=2)]),
}


def patchFor(program: int) -> str:
    """The library patch for a General MIDI program, by family.

    Args:
        program: GM program number, 0 to 127.

    Returns:
        str: a key of `PATCHES`.
    """
    if program < 32 or program in (45, 46):           # keyboards, guitars, pizzicato, harp
        name = "piano"
    elif program < 40 or program == 47:               # basses, timpani
        name = "bass"
    elif program < 56 or 80 <= program < 96:          # strings, ensembles, synth leads and pads
        name = "strings"
    elif program < 64:                                # brass
        name = "brass"
    elif program < 72:                                # reeds
        name = "reed"
    elif program < 80:                                # pipes
        name = "flute"
    else:
        name = "default"

    return name


def patchWrites(channel: int, patch: Patch, left: bool = True,
                right: bool = True) -> list[tuple[int, int]]:
    """The YM2151 register writes that load a patch onto a channel.

    Args:
        channel: OPM channel, 0 to 7.
        patch: the voice.
        left: play on the left.
        right: play on the right.

    Returns:
        list[tuple[int, int]]: (register, value) pairs in register order.
    """
    rl = (0x80 if right else 0) | (0x40 if left else 0)
    writes = [(0x20 + channel, rl | (patch.feedback << 3) | patch.algorithm),
              (0x38 + channel, (patch.pms << 4) | patch.ams)]

    # Operator registers are indexed base + op * 8 + channel.
    for op, o in enumerate(patch.operators):
        slot = op * 8 + channel
        writes += [(0x40 + slot, (o.dt1 << 4) | o.mul),
                   (0x60 + slot, o.tl),
                   (0x80 + slot, (o.ks << 6) | o.ar),
                   (0xA0 + slot, (0x80 if o.amsEnable else 0) | o.d1r),
                   (0xC0 + slot, (o.dt2 << 6) | o.d2r),
                   (0xE0 + slot, (o.d1l << 4) | o.rr)]

    return writes


def opmNoteOn(channel: int, note: int) -> list[tuple[int, int]]:
    """The writes that start a note on an OPM channel: KC, then key on.

    Args:
        channel: OPM channel, 0 to 7.
        note: MIDI note number.

    Returns:
        list[tuple[int, int]]: (register, value) pairs.
    """
    writes = [(0x28 + channel, midiToKc(note)), (0x08, KEY_ON_ALL | channel)]
    return writes


def masterSteps(master: float) -> int:
    """TL steps that bring the whole export down to a master level.

    Args:
        master: an amplitude factor, 1.0 for full level.

    Returns:
        int: steps of 0.75 dB, 0 at full level or above.
    """
    steps = round(-20 * math.log10(master) / 0.75) if 0 < master < 1 else 0
    return steps


def opmLevelWrites(channel: int, patch: Patch, loudness: float,
                   extra: int = 0) -> list[tuple[int, int]]:
    """The TL writes that set a channel's carriers for a note's loudness.

    Args:
        channel: OPM channel, 0 to 7.
        patch: the voice, for its carriers and their levels.
        loudness: 0.0 to 1.0, velocity and channel volume combined.
        extra: further TL steps taken off every carrier, for a master level.

    Returns:
        list[tuple[int, int]]: one (register, value) pair per carrier.
    """
    attenuation = round((1.0 - loudness) * LEVEL_RANGE) + extra
    writes = [(0x60 + slot * 8 + channel, min(127, patch.operators[slot].tl + attenuation))
              for slot in patch.carriers]
    return writes


def opmPanWrite(channel: int, patch: Patch, left: bool, right: bool) -> tuple[int, int]:
    """The RL, FB and CONNECT write that places a channel left, right or both.

    Args:
        channel: OPM channel, 0 to 7.
        patch: the voice, for its feedback and algorithm.
        left: play on the left.
        right: play on the right.

    Returns:
        tuple[int, int]: the (register, value) pair.
    """
    rl = (0x80 if right else 0) | (0x40 if left else 0)
    write = (0x20 + channel, rl | (patch.feedback << 3) | patch.algorithm)
    return write


def opmNoteOff(channel: int) -> list[tuple[int, int]]:
    """The write that releases an OPM channel.

    Args:
        channel: OPM channel, 0 to 7.

    Returns:
        list[tuple[int, int]]: one (register, value) pair.
    """
    writes = [(0x08, channel)]
    return writes


PSG_PULSE, PSG_SAW, PSG_TRIANGLE, PSG_NOISE = 0, 1, 2, 3
PSG_CHANNELS = 16


@dataclass
class PsgSound:
    """What a PSG channel plays for a part: a waveform and, for a plucked
    part, a decay in frames after which the note is silent."""

    wave: int = PSG_PULSE
    width: int = 32         # pulse width, 0 to 63; 32 is square
    decay: int = 0          # frames from the note's start to silence; 0 holds the note


def psgSoundFor(program: int) -> PsgSound:
    """A PSG sound for a General MIDI program, by family.

    Args:
        program: GM program number, 0 to 127.

    Returns:
        PsgSound: the waveform, width and decay.
    """
    if program == 45:                                # pizzicato strings
        sound = PsgSound(PSG_PULSE, 8, 20)
    elif program == 46:                              # orchestral harp
        sound = PsgSound(PSG_PULSE, 8, 40)
    elif program == 47:                              # timpani
        sound = PsgSound(PSG_TRIANGLE, 32, 25)
    elif program < 8:                                # piano
        sound = PsgSound(PSG_PULSE, 16, 45)
    elif program < 16 or 24 <= program < 32:         # chromatic percussion, guitar
        sound = PsgSound(PSG_PULSE, 16, 40)
    elif 32 <= program < 40 or program == 43:        # bass, contrabass
        sound = PsgSound(PSG_TRIANGLE, 32, 0)
    elif 40 <= program < 56 or 56 <= program < 64:   # strings, ensembles, brass
        sound = PsgSound(PSG_SAW, 32, 0)
    elif 64 <= program < 72:                         # reeds
        sound = PsgSound(PSG_PULSE, 16, 0)
    elif 72 <= program < 80:                         # pipes
        sound = PsgSound(PSG_TRIANGLE, 32, 0)
    else:
        sound = PsgSound()

    return sound


# Sounds a pool can be given by name: a patch for an OPM pool, a waveform
# for a PSG pool.
PSG_SOUNDS: dict[str, PsgSound] = {
    "square": PsgSound(PSG_PULSE, 32),
    "pulse": PsgSound(PSG_PULSE, 16),
    "saw": PsgSound(PSG_SAW, 32),
    "triangle": PsgSound(PSG_TRIANGLE, 32),
}
OPM_SOUNDS = tuple(name for name in PATCHES if name != "default")


def psgVoice(channel: int, wave: int = PSG_PULSE, width: int = 32) -> list[tuple[int, int]]:
    """The write that sets a PSG channel's waveform.

    Args:
        channel: PSG channel, 0 to 15.
        wave: `PSG_PULSE`, `PSG_SAW`, `PSG_TRIANGLE` or `PSG_NOISE`.
        width: pulse width, 0 to 63; 32 is square.

    Returns:
        list[tuple[int, int]]: one (register offset, value) pair.
    """
    writes = [(channel * 4 + 3, (wave << 6) | width)]
    return writes


def psgNoteOn(channel: int, note: float, volume: int = 63, left: bool = True,
              right: bool = True) -> list[tuple[int, int]]:
    """The writes that start a note on a PSG channel: frequency, then volume.

    Args:
        channel: PSG channel, 0 to 15.
        note: MIDI note number, fractional for a bent pitch.
        volume: 0 to 63.
        left: play on the left.
        right: play on the right.

    Returns:
        list[tuple[int, int]]: (register offset, value) pairs.
    """
    word = midiToFreqword(note)
    pan = (0x80 if right else 0) | (0x40 if left else 0)
    writes = [(channel * 4, word & 0xFF), (channel * 4 + 1, word >> 8),
              (channel * 4 + 2, pan | volume)]
    return writes


def psgNoteOff(channel: int) -> list[tuple[int, int]]:
    """The write that silences a PSG channel.

    Args:
        channel: PSG channel, 0 to 15.

    Returns:
        list[tuple[int, int]]: one (register offset, value) pair.
    """
    writes = [(channel * 4 + 2, 0)]
    return writes


# --------------------------------------------------------------- stream
FRAME_RATE = 60
MAX_DELAY = 127
MAX_BATCH = 63            # YM2151 pairs one command can carry
END_OF_STREAM = 0x80
HEADER_SIZE = 16

Writes = dict[int, list[tuple[str, int, int]]]   # frame -> [(chip, register, value)]


def emitStream(writes: Writes, loopFrame: int | None = None) -> tuple[bytes, int]:
    """Serialise register writes into a ZSM stream.

    Frames are walked in order; a gap becomes delay bytes, split above
    127; a frame's OPM writes go out as one command of up to 63 pairs
    (more make a second), its PSG writes one command each; the stream ends
    with the end marker.

    Args:
        writes: per frame, the writes in order, chip "opm" or "psg".
        loopFrame: the frame the tune loops back to, or None for no loop.

    Returns:
        tuple[bytes, int]: the stream, and the offset within it of the loop
        frame's first command (0 when there is no loop).
    """
    out = bytearray()
    loopOffset = 0
    frames = sorted(writes)

    # The loop frame gets an entry even with nothing to write, so the loop
    # lands on a command boundary at exactly that frame.
    if loopFrame is not None and loopFrame not in writes:
        frames = sorted(frames + [loopFrame])

    current = 0

    for frame in frames:
        gap = frame - current

        while gap > 0:
            step = min(gap, MAX_DELAY)
            out.append(END_OF_STREAM | step)
            gap -= step

        current = frame

        if frame == loopFrame:
            loopOffset = len(out)

        opm = [(r, v) for chip, r, v in writes.get(frame, []) if chip == "opm"]
        psg = [(r, v) for chip, r, v in writes.get(frame, []) if chip == "psg"]

        for start in range(0, len(opm), MAX_BATCH):
            batch = opm[start:start + MAX_BATCH]
            out.append(0x40 | len(batch))

            for register, value in batch:
                out += bytes((register, value))

        for register, value in psg:
            out += bytes((register, value))

    out.append(END_OF_STREAM)
    return bytes(out), loopOffset


def channelMasks(writes: Writes) -> tuple[int, int]:
    """Which channels a set of writes touches.

    An OPM channel counts when one of its channel-indexed registers (`$20`
    up) is written; a PSG channel when any of its four registers is.

    Args:
        writes: per frame, the writes.

    Returns:
        tuple[int, int]: the FM mask (8 bits) and the PSG mask (16 bits).
    """
    fm = 0
    psg = 0

    for items in writes.values():
        for chip, register, _ in items:
            if chip == "opm" and register >= 0x20:
                fm |= 1 << (register & 0x07)
            elif chip == "psg":
                psg |= 1 << (register >> 2)

    return fm, psg


def emitHeader(fmMask: int, psgMask: int, loopOffset: int = 0, pcmOffset: int = 0) -> bytes:
    """The 16-byte ZSM header.

    Args:
        fmMask: OPM channels used, bit per channel.
        psgMask: PSG channels used, bit per channel.
        loopOffset: offset of the loop point from the start of the file, 0
            for no loop.
        pcmOffset: offset of the PCM block, 0 for none.

    Returns:
        bytes: the header.
    """
    header = (b"zm" + bytes([1]) + loopOffset.to_bytes(3, "little")
              + pcmOffset.to_bytes(3, "little") + bytes([fmMask])
              + psgMask.to_bytes(2, "little") + FRAME_RATE.to_bytes(2, "little")
              + (0).to_bytes(2, "little"))
    return header


def build(writes: Writes, loopFrame: int | None = None) -> bytes:
    """A whole ZSM file from register writes.

    Args:
        writes: per frame, the writes in order.
        loopFrame: the frame to loop back to, or None.

    Returns:
        bytes: header and stream.
    """
    stream, loopOffset = emitStream(writes, loopFrame)
    fm, psg = channelMasks(writes)
    data = emitHeader(fm, psg, HEADER_SIZE + loopOffset if loopFrame is not None else 0) + stream
    return data


def chromaticScale(chip: str, low: int = 36, high: int = 84, hold: int = 20,
                   patch: Patch | None = None) -> bytes:
    """A test file: every semitone from `low` to `high` on one voice.

    Each note holds for `hold` frames less a two-frame gap, on OPM channel
    0 with a patch or PSG channel 0 with a square wave, so equal steps and
    the octave boundaries can be checked by ear.

    Args:
        chip: "opm" or "psg".
        low: first MIDI note.
        high: last MIDI note, inclusive.
        hold: frames per note.
        patch: the OPM voice; `DEFAULT_PATCH` when omitted.

    Returns:
        bytes: the file.
    """
    writes: Writes = {}

    if chip == "opm":
        setup = patchWrites(0, patch or DEFAULT_PATCH)
        on = lambda note: opmNoteOn(0, note)      # noqa: E731
        off = lambda: opmNoteOff(0)               # noqa: E731
    else:
        setup = psgVoice(0)
        on = lambda note: psgNoteOn(0, note)      # noqa: E731
        off = lambda: psgNoteOff(0)               # noqa: E731

    writes[0] = [(chip, r, v) for r, v in setup]

    for i, note in enumerate(range(low, high + 1)):
        start = i * hold
        writes.setdefault(start, []).extend((chip, r, v) for r, v in on(note))
        writes.setdefault(start + hold - 2, []).extend((chip, r, v) for r, v in off())

    data = build(writes)
    return data


# --------------------------------------------------------------- export
CHIPS = (Chip("opm", 8, 300.0, sounds=OPM_SOUNDS),
         Chip("psg", PSG_CHANNELS, 0.0, 4, sounds=tuple(PSG_SOUNDS)))
CHIP_NAMES = tuple(c.name for c in CHIPS)
SOUNDS = tuple(name for c in CHIPS for name in c.sounds)


def frameOf(song: model.Song, tick: float, rate: int = FRAME_RATE) -> int:
    """The frame a tick falls on, through the song's tempo map.

    Absolute positions are rounded, and deltas are taken between the
    rounded positions, so the error is at most half a frame and never
    accumulates.

    Args:
        song: for the tempo map.
        tick: an absolute tick.
        rate: frames per second.

    Returns:
        int: the frame.
    """
    frame = round(song.tickToSeconds(tick) * rate)
    return frame


def channelPans(song: model.Song) -> dict[int, tuple[bool, bool]]:
    """Left and right for each channel from its first pan controller.

    CC10 below 43 is left only, above 85 right only, anything else or no
    controller both.

    Args:
        song: the song.

    Returns:
        dict[int, tuple[bool, bool]]: (left, right) per channel.
    """
    pans: dict[int, tuple[bool, bool]] = {}

    for track in song.mf.tracks:
        for e in track:
            if e.channel >= 0 and (e.status & 0xF0) == 0xB0 and e.data[0] == 10:
                if e.channel not in pans:
                    pans[e.channel] = (e.data[1] <= 85, e.data[1] >= 43)

    return pans


def channelBends(song: model.Song) -> dict[int, float]:
    """Each channel's pitch bend at tick 0 as a detune in cents.

    A bend held from the start of the song is a static detune, the way a
    second desk of strings sits a few cents off the first. Bends later in
    the song are not followed.

    Args:
        song: the song.

    Returns:
        dict[int, float]: cents per channel that has a bend at tick 0, at
        the default bend range of 200 cents either side.
    """
    bends: dict[int, float] = {}

    for track in song.mf.tracks:
        for e in track:
            if e.tick == 0 and e.channel >= 0 and (e.status & 0xF0) == 0xE0:
                value = e.data[0] | (e.data[1] << 7)
                bends[e.channel] = (value - 8192) / 8192 * 200

    return bends


def opmDetune(note: int, cents: float) -> tuple[int, int]:
    """A note and a detune as the YM2151 plays them: a note and a key fraction.

    The key fraction only raises a pitch, in 64 steps per semitone, so a
    flat detune becomes the semitone below plus the fraction that remains.

    Args:
        note: MIDI note number.
        cents: the detune, either sign.

    Returns:
        tuple[int, int]: the note folded into the chip's range, and the key
        fraction, 0 to 63.
    """
    total = note + cents / 100
    base = math.floor(total)
    fraction = round((total - base) * 64)

    if fraction == 64:
        base += 1
        fraction = 0

    pair = (foldToOpmRange(base), fraction)
    return pair


def channelVolumes(song: model.Song) -> dict[int, float]:
    """Each channel's first CC7 as a factor, 1.0 when it has none.

    Args:
        song: the song.

    Returns:
        dict[int, float]: 0.0 to 1.0 per channel with notes.
    """
    volumes = {ch: (info.volume / 127 if info.volume >= 0 else 1.0)
               for ch, info in song.channels.items()}
    return volumes


def _voiceMap(reduction: reduce.Reduction, plan: reduce.Plan) -> dict[int, tuple[str, int, str]]:
    """Map every voice of the reduction to its chip, chip channel and pool.

    Args:
        reduction: for the voices used and each tier's first voice.
        plan: for the pools and tiers.

    Returns:
        dict[int, tuple[str, int, str]]: voice -> (chip, channel, pool name).

    Raises:
        ValueError: a placed note's pool has no tier, or its tier no chip.
    """
    voices = sorted({p.voice for p in reduction.placed})
    mapping: dict[int, tuple[str, int, str]] = {}
    base = 0

    # Voices are numbered in tier order with a tier's pools contiguous,
    # so a voice belongs to the pool whose range it falls in.
    ranges: list[tuple[int, int, reduce.Pool]] = []

    for tier in plan.tiers:
        for pool in plan.pools:
            if pool.tier == tier.name:
                ranges.append((base, base + pool.voices, pool))
                base += pool.voices

    for pool in plan.pools:
        if not pool.tier:
            ranges.append((base, base + pool.voices, pool))
            base += pool.voices

    for voice in voices:
        pool = next(p for lo, hi, p in ranges if lo <= voice < hi)
        tier = plan.tierOf(pool.tier)

        if tier is None:
            raise ValueError(f"pool {pool.name} has no tier, so its voices have no chip")

        if tier.chip not in CHIP_NAMES:
            raise ValueError(f"tier {tier.name}: chip {tier.chip!r} is not opm or psg")

        mapping[voice] = (tier.chip, voice - reduction.tierBase[tier.name], pool.name)

    return mapping


def exportWrites(song: model.Song, reduction: reduce.Reduction, plan: reduce.Plan,
                 patches: dict[int, Patch] | None = None,
                 psgSounds: dict[int, PsgSound] | None = None,
                 start: int = 0, end: int | None = None, master: float = 1.0,
                 settings: dict[str, Any] | None = None) -> Writes:
    """Turn a reduction into register writes per frame.

    Every voice used gets its setup at frame 0: an OPM voice the patch and
    pan of its first note, a PSG voice a square wave. Each placed note
    becomes a note-on at its start frame and a note-off at its end frame,
    at least one frame later; within a frame note-offs come before
    note-ons, so a voice handed from one note to the next is released
    first. A note-off and a note-on on the same voice in the same frame at
    different pitches become a single pitch change, with no key cycle; at
    the same pitch they stay a release and a fresh attack.

    An OPM note plays its channel's patch: the one its pool is given by
    name in `settings["sounds"]`, else the one given for the channel, else
    the library patch for the channel's GM program. A voice holds the
    registers it was last written, and a note writes only those that
    differ, so a voice that keeps playing one part writes nothing but its
    notes.

    A note's loudness is its velocity times its channel's CC7: on the OPM
    it sets the carriers' TL before the key-on, up to `LEVEL_RANGE` steps
    below the patch; on the PSG it is the volume, 1 to 63. A master level
    below 1.0 takes both chips down together, as TL steps on every carrier
    and a factor on PSG volume. The channel's pan (CC10) places the note
    left, right or both, and a pitch bend at tick 0 detunes every note of
    the channel: the key fraction on the OPM, the frequency word on the
    PSG. A PSG note takes the sound its pool is given by
    name, else its channel's `PsgSound`, else one by GM program: the
    waveform is rewritten on the voice when it changes, and a decay fades
    the volume in steps to silence. Notes are taken from `start` to `end`
    in ticks, clipped to that range, and `start` is frame 0.

    Args:
        song: for the tempo map, pans, volumes and programs.
        reduction: the placed notes and the tiers' first voices.
        plan: the pools and tiers, for the chip each voice belongs to.
        patches: OPM patches by MIDI channel.
        psgSounds: sounds by MIDI channel.
        start: first tick to export.
        end: tick to stop at; the end of the song when None.
        master: an amplitude factor for the whole export, 1.0 for full.
        settings: the target's section of the plan file; `sounds` names a
            sound per pool.

    Returns:
        Writes: per frame, the writes in order.

    Raises:
        ValueError: a placed note's voice has no chip to go to.
    """
    voices = _voiceMap(reduction, plan)
    steps = masterSteps(master)
    scale = min(master, 1.0)
    pans = channelPans(song)
    volumes = channelVolumes(song)
    bends = channelBends(song)
    poolSounds = (settings or {}).get("sounds", {})
    placed = sorted(reduction.placed, key=lambda p: (p.start, p.voice))

    # The sound of each note: its pool's named sound, else the channel's,
    # else the library's choice for the channel's program.
    def patchOf(p: reduce.Placed) -> Patch:
        """The OPM patch a placed note plays."""
        named = poolSounds.get(voices[p.voice][2])
        program = song.channels[p.channel].program
        patch = (PATCHES[named] if named in PATCHES
                 else (patches or {}).get(p.channel) or PATCHES[patchFor(program)])
        return patch

    def soundOf(p: reduce.Placed) -> PsgSound:
        """The PSG sound a placed note plays."""
        named = poolSounds.get(voices[p.voice][2])
        program = song.channels[p.channel].program
        sound = (PSG_SOUNDS[named] if named in PSG_SOUNDS
                 else (psgSounds or {}).get(p.channel) or psgSoundFor(program))
        return sound

    origin = frameOf(song, start)
    last = end if end is not None else song.maxTick
    writes: Writes = {}

    # Setup for every voice in use, at frame 0, as its first note wants
    # it, and the registers each voice then holds so a note rewrites only
    # what differs.
    voiceRegs: dict[int, dict[int, int]] = {}
    voiceWave: dict[int, tuple[int, int]] = {}
    first = {}

    for p in placed:
        first.setdefault(p.voice, p)

    for voice in sorted(voices):
        chip, channel, pool = voices[voice]

        if chip == "opm":
            note = first[voice]
            left, right = pans.get(note.channel, (True, True))
            voiceRegs[voice] = dict(patchWrites(channel, patchOf(note), left, right))
            setup = list(voiceRegs[voice].items())
        else:
            setup = psgVoice(channel)
            voiceWave[voice] = (PSG_PULSE, 32)

        writes.setdefault(0, []).extend((chip, r, v) for r, v in setup)

    # Note-offs and note-ons as (frame, order, voice, pitch, writes); the
    # order puts offs before ons within a frame. Decay steps are written
    # straight away, since nothing else touches them.
    events: list[tuple[int, int, int, int, list[tuple[int, int]]]] = []

    for p in placed:
        if p.end <= start or p.start >= last:
            continue

        chip, channel, pool = voices[p.voice]
        on = frameOf(song, max(p.start, start)) - origin
        off = max(frameOf(song, min(p.end, last)) - origin, on + 1)
        loudness = p.velocity / 127 * volumes.get(p.channel, 1.0)
        left, right = pans.get(p.channel, (True, True))

        if chip == "opm":
            patch = patchOf(p)
            wanted = dict(patchWrites(channel, patch, left, right))
            wanted.update(opmLevelWrites(channel, patch, loudness, steps))
            held = voiceRegs[p.voice]

            # A note outside the chip's range plays in the nearest octave
            # it can reach, and a detuned channel adds its key fraction; a
            # voice that has never been detuned leaves the fraction alone.
            pitch, fraction = opmDetune(p.pitch, bends.get(p.channel, 0.0))

            if fraction or (0x30 + channel) in held:
                wanted[0x30 + channel] = fraction << 2

            regs = [(r, v) for r, v in wanted.items() if held.get(r) != v]
            held.update(regs)
            events.append((on, 1, p.voice, pitch, regs + opmNoteOn(channel, pitch)))
            events.append((off, 0, p.voice, pitch, opmNoteOff(channel)))
        else:
            sound = soundOf(p)
            volume = max(1, round(loudness * scale * 63))
            regs = []

            if voiceWave[p.voice] != (sound.wave, sound.width):
                regs += psgVoice(channel, sound.wave, sound.width)
                voiceWave[p.voice] = (sound.wave, sound.width)

            bent = p.pitch + bends.get(p.channel, 0.0) / 100
            events.append((on, 1, p.voice, p.pitch,
                           regs + psgNoteOn(channel, bent, volume, left, right)))
            events.append((off, 0, p.voice, p.pitch, psgNoteOff(channel)))

            # The decay: the volume stepped down every other frame until
            # the note is silent or over.
            pan = (0x80 if right else 0) | (0x40 if left else 0)

            for step in range(2, min(sound.decay, off - on), 2):
                level = round(volume * (1 - step / sound.decay))
                writes.setdefault(on + step, []).append(("psg", channel * 4 + 2, pan | level))

    events.sort(key=lambda e: (e[0], e[1], e[2]))

    # A voice released and struck again in one frame at another pitch
    # changes pitch instead: the KC and level writes without the key cycle
    # on the OPM, the frequency and volume writes without the silence on
    # the PSG.
    i = 0

    while i < len(events):
        frame, order, voice, pitch, regs = events[i]
        chip = voices[voice][0]
        partner = next((j for j in range(i + 1, len(events))
                        if events[j][0] == frame and events[j][2] == voice), None)

        if order == 0 and partner is not None and events[partner][3] != pitch:
            onRegs = events[partner][4]
            slide = [rv for rv in onRegs if rv[0] != 0x08] if chip == "opm" else onRegs
            writes.setdefault(frame, []).extend((chip, r, v) for r, v in slide)
            del events[partner]
            i += 1
            continue

        writes.setdefault(frame, []).extend((chip, r, v) for r, v in regs)
        i += 1

    return writes


def export(song: model.Song, reduction: reduce.Reduction, plan: reduce.Plan, *,
           start: int = 0, end: int | None = None, loop: int | None = None,
           master: float = 1.0, settings: dict[str, Any] | None = None,
           patches: dict[int, Patch] | None = None,
           psgSounds: dict[int, PsgSound] | None = None) -> bytes:
    """A ZSM file from a reduction.

    Args:
        song: for the tempo map, pans, volumes and programs.
        reduction: the placed notes.
        plan: the pools and tiers.
        start: first tick to export.
        end: tick to stop at; the end of the song when None.
        loop: tick to loop back to, or None for no loop.
        master: an amplitude factor for the whole export, 1.0 for full.
        settings: the target's section of the plan file.
        patches: OPM patches by MIDI channel.
        psgSounds: sounds by MIDI channel; by GM program when omitted.

    Returns:
        bytes: the file.

    Raises:
        ValueError: a placed note's voice has no chip to go to.
    """
    writes = exportWrites(song, reduction, plan, patches, psgSounds, start, end, master,
                          settings)
    loopFrame = frameOf(song, loop) - frameOf(song, start) if loop is not None else None
    data = build(writes, loopFrame)
    return data


def verify(data: bytes, plan: reduce.Plan) -> list[str]:
    """Problems in a written ZSM file.

    The bytes go through the decoder, the structural checks and the chip
    model, with the PSG's reserve and each tier's release from the plan.

    Args:
        data: the file.
        plan: the tiers it was exported under.

    Returns:
        list[str]: one line per problem, empty when the file is sound.
    """
    decoded = zsmdecode.decode(data)
    psgTier = next((t for t in plan.tiers if t.chip == "psg"), None)
    release = {t.chip: round(t.release * FRAME_RATE / 1000) for t in plan.tiers if t.chip}
    sim = zsmsim.simulate(decoded, psgTier.voices if psgTier else PSG_CHANNELS, release)
    problems = zsmcheck.check(decoded) + sim.problems
    return problems


def describe(data: bytes, plan: reduce.Plan) -> str:
    """One line about a written ZSM file.

    Args:
        data: the file.
        plan: the tiers it was exported under.

    Returns:
        str: its size in kilobytes and banks, its length, and the peak
        voices sounding on each chip.
    """
    decoded = zsmdecode.decode(data)
    sim = zsmsim.simulate(decoded)
    banks = (len(data) + BANK - 1) // BANK
    text = (f"{len(data) // 1024} KB, {banks} banks, {decoded.length / FRAME_RATE:.0f} s, "
            f"peak {sim.peakOpm} OPM and {sim.peakPsg} PSG voices")
    return text
