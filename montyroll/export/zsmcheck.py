# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Structural checks on a decoded ZSM stream.

Each check reads a `zsmdecode.Decoded` and returns its problems as a list
of lines, empty when the stream is sound: the header's frame rate, reserved
word, channel masks and loop point; the stream's length, its delay bytes
and what follows the end marker; and the registers its writes address.
"""

from __future__ import annotations

from . import zsmdecode

FRAME_RATE = 60
MAX_DELAY = 127

# The YM2151 registers the export writes: key on and off, noise, the LFO,
# and the per-channel and per-operator blocks. TEST at $01 and the timers
# are outside the set.
OPM_REGISTERS = ({0x08, 0x0F, 0x18, 0x19, 0x1B} | set(range(0x20, 0x40))
                 | set(range(0x40, 0x100)))
OPM_KC_REGISTERS = range(0x28, 0x30)
OPM_INVALID_NIBBLES = (3, 7, 11, 15)


def check(decoded: zsmdecode.Decoded, expectedFrames: int | None = None) -> list[str]:
    """Run every structural check.

    Args:
        decoded: the stream as `zsmdecode.decode` read it.
        expectedFrames: the length the stream should have, when known.

    Returns:
        list[str]: one line per problem; empty when the stream is sound.
    """
    problems = checkHeader(decoded) + checkStream(decoded, expectedFrames) + checkWrites(decoded)
    return problems


def checkHeader(decoded: zsmdecode.Decoded) -> list[str]:
    """Check the header against the stream it describes.

    The channel masks must name exactly the channels written, since the
    player suspends and restores those when ducking for a sound effect;
    a loop offset must land on a command boundary.

    Args:
        decoded: the stream.

    Returns:
        list[str]: the problems.
    """
    header = decoded.header
    problems: list[str] = []

    if header.rate != FRAME_RATE:
        problems.append(f"header: frame rate is {header.rate}, not {FRAME_RATE}")

    if header.reserved != 0:
        problems.append(f"header: reserved field is {header.reserved}, not 0")

    fmUsed = 0
    psgUsed = 0

    for frame in decoded.frames:
        for item in frame.items:
            if isinstance(item, zsmdecode.Write):
                if item.chip == "opm" and 0x20 <= item.register:
                    fmUsed |= 1 << (item.register & 0x07)
                elif item.chip == "psg":
                    psgUsed |= 1 << (item.register >> 2)

    for name, used, mask in (("FM", fmUsed, header.fmMask), ("PSG", psgUsed, header.psgMask)):
        missing = used & ~mask
        spare = mask & ~used

        if missing:
            problems.append(f"header: {name} mask lacks channels that are written: "
                            f"{_bits(missing)}")

        if spare:
            problems.append(f"header: {name} mask claims channels never written: "
                            f"{_bits(spare)}")

    if header.loopOffset and header.loopOffset not in decoded.offsets:
        problems.append(f"header: loop offset {header.loopOffset} is not a command boundary")

    return problems


def checkStream(decoded: zsmdecode.Decoded, expectedFrames: int | None = None) -> list[str]:
    """Check the stream's shape: its length, its delays and what follows the end.

    Args:
        decoded: the stream.
        expectedFrames: the length it should have, when known.

    Returns:
        list[str]: the problems.
    """
    problems: list[str] = []

    if expectedFrames is not None and decoded.length != expectedFrames:
        problems.append(f"stream: {decoded.length} frames long, expected {expectedFrames}")

    # A gap takes one delay byte per 127 frames. A loop point inside a gap
    # splits it, so each side is judged alone.
    for run in decoded.delayRuns:
        parts: list[list[int]] = [[]]

        for offset, frames in run:
            if offset == decoded.header.loopOffset and parts[-1]:
                parts.append([])

            parts[-1].append(frames)

        for part in parts:
            if len(part) > 1 and sum(part) <= MAX_DELAY * (len(part) - 1):
                problems.append(f"stream: {len(part)} adjacent delays for a gap of "
                                f"{sum(part)} frames")

    if decoded.trailing and not decoded.header.pcmOffset:
        problems.append(f"stream: {decoded.trailing} bytes follow the end marker "
                        f"with no PCM block declared")

    return problems


def checkWrites(decoded: zsmdecode.Decoded) -> list[str]:
    """Check every register write is one the chips expect.

    Args:
        decoded: the stream.

    Returns:
        list[str]: the problems.
    """
    problems: list[str] = []

    for frame in decoded.frames:
        for item in frame.items:
            if not isinstance(item, zsmdecode.Write) or item.chip != "opm":
                continue

            if item.register not in OPM_REGISTERS:
                problems.append(f"frame {frame.frame}: write to YM2151 register "
                                f"${item.register:02X}, which the export never uses")
            elif item.register in OPM_KC_REGISTERS and (item.value & 0x0F) in OPM_INVALID_NIBBLES:
                problems.append(f"frame {frame.frame}: KC ${item.value:02X} on channel "
                                f"{item.register & 0x07} has note field {item.value & 0x0F}, "
                                f"which is not a note")

    return problems


def _bits(mask: int) -> str:
    """Name the set bits of a mask as channel numbers.

    Args:
        mask: the bits.

    Returns:
        str: comma-separated channel numbers.
    """
    names = ", ".join(str(n) for n in range(16) if mask & (1 << n))
    return names
