# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""A state model of the YM2151 and the VERA PSG replayed over a decoded ZSM stream.

`simulate` walks a `zsmdecode.Decoded` frame by frame keeping each OPM
channel's key state, key code and patch registers and each PSG channel's
frequency word, volume and waveform, and returns a `Simulation`: the notes
played, the peak number of channels sounding on each chip, the count of
key-ons that fell inside the previous note's release, and the problems
found (more channels sounding than allowed, a key-on to a channel already
keyed, a key-off to a silent one, a key-on before the channel's patch, and
anything still sounding after the last event). Nothing is synthesised.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import zsmdecode

OPM_CHANNELS = 8
PSG_CHANNELS = 16
PATCH_REGISTERS = 26        # the channel pair and six per operator
KEY_ON_MASK = 0x78


@dataclass
class Played:
    """One note as the chips played it."""

    frame: int
    chip: str
    channel: int
    pitch: float            # MIDI note number; fractional from a PSG word
    end: int = -1           # the frame it stopped, -1 while sounding


@dataclass
class Simulation:
    """What the replay found."""

    notes: list[Played] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    peakOpm: int = 0
    peakPsg: int = 0
    shortGaps: int = 0      # key-ons sooner after a key-off than the release allows


def _kcToMidi(kc: int) -> int:
    """MIDI note number of a YM2151 key code, C at the top of its block.

    Args:
        kc: the key code.

    Returns:
        int: the note number; an unused note field gives the nearest below.
    """
    nibbles = (14, 0, 1, 2, 4, 5, 6, 8, 9, 10, 12, 13)
    nibble = kc & 0x0F
    semitone = max(range(12), key=lambda i: nibbles[i] if nibbles[i] <= nibble else -1)
    note = ((kc >> 4) + 1) * 12 + semitone + (12 if semitone == 0 else 0)
    return note


def _wordToMidi(word: int) -> float:
    """MIDI note number of a VERA PSG frequency word.

    Args:
        word: the 16-bit word.

    Returns:
        float: the note number, 0.0 for a word of zero.
    """
    if word == 0:
        return 0.0

    hz = word * 25_000_000 / (1 << 25)
    note = 69 + 12 * math.log2(hz / 440.0)
    return note


def simulate(decoded: zsmdecode.Decoded, psgVoices: int = PSG_CHANNELS,
             release: dict[str, int] | None = None) -> Simulation:
    """Replay a decoded stream through the chip model.

    Args:
        decoded: the stream.
        psgVoices: PSG channels the music may use; the rest are reserved.
        release: frames a channel needs after a key-off before the next
            key-on, per chip ("opm", "psg"); a key-on sooner is counted in
            `shortGaps`.

    Returns:
        Simulation: the notes, peaks, short gaps and problems.
    """
    release = release or {}
    sim = Simulation()
    opmKc = [0] * OPM_CHANNELS
    opmOn: list[Played | None] = [None] * OPM_CHANNELS
    opmOff = [-10_000] * OPM_CHANNELS          # frame of the last key-off
    opmPatched: list[set[int]] = [set() for _ in range(OPM_CHANNELS)]
    psgWord = [0] * PSG_CHANNELS
    psgOn: list[Played | None] = [None] * PSG_CHANNELS
    psgOff = [-10_000] * PSG_CHANNELS
    last = 0

    for frame in decoded.frames:
        last = frame.frame

        for item in frame.items:
            if not isinstance(item, zsmdecode.Write):
                continue

            if item.chip == "opm":
                _opmWrite(sim, item, frame.frame, opmKc, opmOn, opmOff, opmPatched,
                          release.get("opm", 0))
            else:
                _psgWrite(sim, item, frame.frame, psgWord, psgOn, psgOff, psgVoices,
                          release.get("psg", 0))

        sim.peakOpm = max(sim.peakOpm, sum(1 for n in opmOn if n is not None))
        sim.peakPsg = max(sim.peakPsg, sum(1 for n in psgOn if n is not None))

    # Whatever is still sounding at the end would sound forever.
    for ch, note in enumerate(opmOn):
        if note is not None:
            sim.problems.append(f"end: OPM channel {ch} is still keyed on at frame {last}")

    for ch, note in enumerate(psgOn):
        if note is not None:
            sim.problems.append(f"end: PSG channel {ch} is still sounding at frame {last}")

    return sim


def _opmWrite(sim: Simulation, w: zsmdecode.Write, frame: int, kc: list[int],
              on: list[Played | None], off: list[int], patched: list[set[int]],
              release: int) -> None:
    """Apply one YM2151 write to the model.

    Args:
        sim: collects notes and problems.
        w: the write.
        frame: when.
        kc: key code per channel.
        on: the note sounding per channel, or None.
        off: frame of the last key-off per channel.
        patched: registers written per channel.
        release: frames a channel needs between a key-off and a key-on.
    """
    if 0x20 <= w.register:
        ch = w.register & 0x07

        if w.register < 0x40:
            block = w.register & 0xF8
        else:
            block = w.register & 0xE0

        if block in (0x28, 0x30):
            if block == 0x28:
                kc[ch] = w.value

                # A key code change on a keyed channel is a legato pitch
                # change: the note ends and another starts.
                if on[ch] is not None:
                    on[ch].end = frame
                    on[ch] = Played(frame, "opm", ch, _kcToMidi(w.value))
                    sim.notes.append(on[ch])
        else:
            patched[ch].add(w.register)

        return

    if w.register != 0x08:
        return

    ch = w.value & 0x07

    if w.value & KEY_ON_MASK:
        if on[ch] is not None:
            sim.problems.append(f"frame {frame}: key-on to OPM channel {ch} while keyed on")
            on[ch].end = frame

        if len(patched[ch]) < PATCH_REGISTERS:
            sim.problems.append(f"frame {frame}: OPM channel {ch} keyed on with "
                                f"{len(patched[ch])} of {PATCH_REGISTERS} patch registers set")

        if frame - off[ch] < release:
            sim.shortGaps += 1

        on[ch] = Played(frame, "opm", ch, _kcToMidi(kc[ch]))
        sim.notes.append(on[ch])
        keyed = sum(1 for n in on if n is not None)

        if keyed > OPM_CHANNELS:
            sim.problems.append(f"frame {frame}: {keyed} OPM channels keyed on")
    else:
        if on[ch] is None:
            sim.problems.append(f"frame {frame}: key-off to OPM channel {ch} while off")
        else:
            on[ch].end = frame
            on[ch] = None
            off[ch] = frame


def _psgWrite(sim: Simulation, w: zsmdecode.Write, frame: int, word: list[int],
              on: list[Played | None], off: list[int], voices: int, release: int) -> None:
    """Apply one VERA PSG write to the model.

    Args:
        sim: collects notes and problems.
        w: the write.
        frame: when.
        word: frequency word per channel.
        on: the note sounding per channel, or None.
        off: frame of the last silence per channel.
        voices: channels the music may use.
        release: frames a channel needs between a silence and a note.
    """
    ch, reg = divmod(w.register, 4)

    if reg == 0:
        word[ch] = (word[ch] & 0xFF00) | w.value
    elif reg == 1:
        word[ch] = (word[ch] & 0x00FF) | (w.value << 8)

    if reg in (0, 1) and on[ch] is not None:
        # A frequency change while sounding is a pitch change.
        on[ch].end = frame
        on[ch] = Played(frame, "psg", ch, _wordToMidi(word[ch]))
        sim.notes.append(on[ch])
        return

    if reg != 2:
        return

    if w.value & 0x3F:
        if on[ch] is None:
            if frame - off[ch] < release:
                sim.shortGaps += 1

            if ch >= voices:
                sim.problems.append(f"frame {frame}: PSG channel {ch} sounds but is reserved")

            on[ch] = Played(frame, "psg", ch, _wordToMidi(word[ch]))
            sim.notes.append(on[ch])
    elif on[ch] is not None:
        on[ch].end = frame
        on[ch] = None
        off[ch] = frame
