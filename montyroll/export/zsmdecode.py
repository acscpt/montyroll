# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""A decoder for ZSM version 1 files.

`decode` parses the 16-byte header into a `Header` and walks the stream
with a running frame counter: delay bytes advance it, writes attach to the
current frame tagged with their chip, and EXTCMD blocks are kept as their
bytes. Anything malformed raises `ValueError` naming the byte offset. The
result also carries the offset of every command, the runs of consecutive
delay bytes and the count of bytes after the end marker, which the checks
in `zsmcheck.py` read. The decoder shares no code with the emitter in
`zsm.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAGIC = b"zm"
VERSION = 1
HEADER_SIZE = 16
END_OF_STREAM = 0x80
EXTCMD = 0x40


@dataclass
class Header:
    """The 16-byte ZSM header, offsets relative to the start of the file."""

    loopOffset: int         # 0 when the tune does not loop
    pcmOffset: int          # 0 when there is no PCM block
    fmMask: int             # bit n set when OPM channel n is used
    psgMask: int            # bit n set when PSG channel n is used
    rate: int               # frames per second
    reserved: int


@dataclass
class Write:
    """One register write, to the OPM or the PSG."""

    chip: str               # "opm" or "psg"
    register: int
    value: int


@dataclass
class ExtCmd:
    """An extension command, kept as the bytes it came with."""

    channel: int            # 0 PCM, 1 expansion sound, 2 synchronisation, 3 custom
    data: bytes


@dataclass
class Frame:
    """Everything that happens on one frame, in stream order."""

    frame: int
    items: list[Write | ExtCmd] = field(default_factory=list)


@dataclass
class Decoded:
    """A whole file as the decoder read it."""

    header: Header
    frames: list[Frame] = field(default_factory=list)
    length: int = 0                                  # total frames to the end marker
    offsets: set[int] = field(default_factory=set)   # where every command starts
    delayRuns: list[list[tuple[int, int]]] = field(default_factory=list)   # (offset, frames)
    trailing: int = 0                                # bytes after the end marker


def _need(data: bytes, offset: int, count: int, what: str) -> None:
    """Check that `count` bytes exist at `offset`.

    Args:
        data: the file.
        offset: where the bytes should start.
        count: how many are needed.
        what: named in the error.

    Raises:
        ValueError: the file ends first.
    """
    if offset + count > len(data):
        raise ValueError(f"offset {offset}: {what} runs past the end of the file "
                         f"({len(data)} bytes)")


def decodeHeader(data: bytes) -> Header:
    """Parse the header.

    Args:
        data: the file, or at least its first 16 bytes.

    Returns:
        Header: the fields.

    Raises:
        ValueError: too short, wrong magic or a version other than 1.
    """
    _need(data, 0, HEADER_SIZE, "the header")

    if data[0:2] != MAGIC:
        raise ValueError(f"offset 0: magic is {data[0:2]!r}, not {MAGIC!r}")

    if data[2] != VERSION:
        raise ValueError(f"offset 2: ZSM version {data[2]} is not supported (only {VERSION})")

    header = Header(loopOffset=int.from_bytes(data[3:6], "little"),
                    pcmOffset=int.from_bytes(data[6:9], "little"),
                    fmMask=data[9],
                    psgMask=int.from_bytes(data[10:12], "little"),
                    rate=int.from_bytes(data[12:14], "little"),
                    reserved=int.from_bytes(data[14:16], "little"))
    return header


def decode(data: bytes) -> Decoded:
    """Parse a ZSM file.

    Args:
        data: the whole file.

    Returns:
        Decoded: the header, the frames with their writes, and the
        bookkeeping the structural checks use.

    Raises:
        ValueError: anything malformed, with the byte offset.
    """
    header = decodeHeader(data)
    out = Decoded(header)
    frame = 0
    current: Frame | None = None
    run: list[tuple[int, int]] = []
    i = HEADER_SIZE

    # One command per iteration; the loop leaves on the end marker, and a
    # stream without one runs off the end of the data.
    while True:
        _need(data, i, 1, "the stream")
        out.offsets.add(i)
        command = data[i]

        # A delay closes the current frame and joins the run of delays.
        if command > END_OF_STREAM:
            frame += command & 0x7F
            current = None
            run.append((i, command & 0x7F))
            i += 1
            continue

        if run:
            out.delayRuns.append(run)
            run = []

        if command == END_OF_STREAM:
            i += 1
            break

        if current is None:
            current = Frame(frame)
            out.frames.append(current)

        if command < EXTCMD:
            _need(data, i + 1, 1, "a PSG write")
            current.items.append(Write("psg", command, data[i + 1]))
            i += 2
        elif command == EXTCMD:
            _need(data, i + 1, 1, "an EXTCMD")
            channel = data[i + 1] >> 6
            count = data[i + 1] & 0x3F
            _need(data, i + 2, count, f"an EXTCMD of {count} bytes")
            current.items.append(ExtCmd(channel, bytes(data[i + 2:i + 2 + count])))
            i += 2 + count
        else:
            pairs = command & 0x3F
            _need(data, i + 1, 2 * pairs, f"a YM2151 command of {pairs} pairs")

            for k in range(pairs):
                current.items.append(Write("opm", data[i + 1 + 2 * k], data[i + 2 + 2 * k]))

            i += 1 + 2 * pairs

    out.length = frame
    out.trailing = len(data) - i
    return out
