# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Standard MIDI File (SMF) parser and writer.  Pure stdlib.

Supports format 0 and 1 files with PPQ (ticks-per-quarter-note) division,
running status, meta events and sysex.  Events use absolute ticks in memory
and are delta-encoded on write.  SMPTE time division is rejected.

This module knows nothing about songs, notes or tempo maps.  It deals only
in chunks, events and bytes, and preserves every event it reads so that the
layers above can edit what they understand and write back what they do not.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

META = 0xFF
SYSEX = 0xF0
SYSEX_CONT = 0xF7

# meta types
META_TRACK_NAME = 0x03
META_INSTRUMENT = 0x04
META_LYRIC = 0x05
META_MARKER = 0x06
META_CHANNEL_PREFIX = 0x20
META_END_OF_TRACK = 0x2F
META_TEMPO = 0x51
META_TIME_SIG = 0x58
META_KEY_SIG = 0x59

_VOICE_DATA_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}


@dataclass
class Event:
    """One MIDI event at an absolute tick position.

    Voice events hold their data bytes without the status byte; meta events
    hold the payload that follows the type and length; sysex events hold the
    payload that follows the length.  The data is a bytearray so that callers
    can edit an event in place and have the change reach the written file.
    """

    tick: int
    status: int            # 0x80-0xEF voice, 0xF0/0xF7 sysex, 0xFF meta
    data: bytearray = field(default_factory=bytearray)
    metaType: int = -1    # valid when status == META

    @property
    def channel(self) -> int:
        """Channel of a voice event.

        Returns:
            int: the 0-based channel, or -1 for meta and sysex events.
        """
        return self.status & 0x0F if self.status < 0xF0 else -1

    @property
    def isNoteOn(self) -> bool:
        """Whether this event starts a note.

        A note-on with velocity zero is a note-off in disguise, as the MIDI
        specification permits, so the velocity must be positive to count.

        Returns:
            bool: True for a note-on with non-zero velocity.
        """
        on = self.status & 0xF0 == 0x90 and len(self.data) > 1 and self.data[1] > 0
        return on

    @property
    def isNoteOff(self) -> bool:
        """Whether this event ends a note.

        Both forms are recognised: a true note-off status and a note-on with
        velocity zero, which many files use under running status.

        Returns:
            bool: True for a note-off or a zero-velocity note-on.
        """
        s = self.status & 0xF0
        off = s == 0x80 or (s == 0x90 and len(self.data) > 1 and self.data[1] == 0)
        return off

    def kind(self) -> str:
        """Classify the event by its status byte alone.

        A zero-velocity note-on reports "note_on" here; use isNoteOff to
        distinguish it.

        Returns:
            str: "meta", "sysex", or one of the voice message names
            "note_off", "note_on", "poly_pressure", "control", "program",
            "channel_pressure" and "pitch_bend".
        """
        if self.status == META:
            return "meta"

        if self.status in (SYSEX, SYSEX_CONT):
            return "sysex"

        name = {
            0x80: "note_off", 0x90: "note_on", 0xA0: "poly_pressure",
            0xB0: "control", 0xC0: "program", 0xD0: "channel_pressure",
            0xE0: "pitch_bend",
        }[self.status & 0xF0]
        return name


@dataclass
class MidiFile:
    """A parsed SMF: the header fields plus one event list per track."""

    format: int = 1
    division: int = 480               # ticks per quarter note
    tracks: list[list[Event]] = field(default_factory=list)


def _readVlq(buf: bytes, pos: int) -> tuple[int, int]:
    """Decode a variable-length quantity.

    Args:
        buf: the bytes being parsed.
        pos: offset of the first byte of the quantity.

    Returns:
        tuple[int, int]: the decoded value and the offset just past it.
    """
    value = 0

    while True:
        # Each byte contributes seven bits, most significant group first; a
        # set high bit means another byte follows.
        b = buf[pos]
        pos += 1
        value = (value << 7) | (b & 0x7F)

        if not b & 0x80:
            return value, pos


def _encodeVlq(value: int) -> bytes:
    """Encode a non-negative integer as a variable-length quantity.

    Args:
        value: the number to encode.

    Returns:
        bytes: seven bits per byte, most significant group first, with the
        continuation bit set on every byte except the last.
    """
    # The low seven bits form the final byte; each remaining group is
    # inserted in front of it with the continuation bit set.
    out = bytearray([value & 0x7F])
    value >>= 7

    while value:
        out.insert(0, 0x80 | (value & 0x7F))
        value >>= 7

    encoded = bytes(out)
    return encoded


def parse(path: str) -> MidiFile:
    """Read a Standard MIDI File from disk.

    Args:
        path: filesystem path of the .mid file.

    Returns:
        MidiFile: the header fields and one event list per MTrk chunk, with
        ticks made absolute.

    Raises:
        ValueError: if the file has no MThd header or uses SMPTE time
            division, which this module does not support.
    """
    with open(path, "rb") as f:
        raw = f.read()

    # Validate the header chunk; only PPQ division is accepted.
    pos = 0

    if raw[pos:pos + 4] != b"MThd":
        raise ValueError(f"{path}: not a Standard MIDI File (missing MThd)")

    hdr_len = struct.unpack(">I", raw[pos + 4:pos + 8])[0]
    fmt, ntracks, division = struct.unpack(">HHH", raw[pos + 8:pos + 14])

    if division & 0x8000:
        raise ValueError(f"{path}: SMPTE time division is not supported")

    # Advance by the declared header length, which tolerates headers longer
    # than the standard six bytes.
    pos += 8 + hdr_len

    # Walk the chunks until the declared track count is met or the data runs
    # out, keeping only MTrk bodies.
    mf = MidiFile(format=fmt, division=division)

    while len(mf.tracks) < ntracks and pos + 8 <= len(raw):
        chunk_id = raw[pos:pos + 4]
        chunk_len = struct.unpack(">I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + chunk_len]
        pos += 8 + chunk_len

        if chunk_id != b"MTrk":
            continue  # skip alien chunks per spec

        mf.tracks.append(_parseTrack(body))

    return mf


def _parseTrack(buf: bytes) -> list[Event]:
    """Decode the body of one MTrk chunk into events.

    Args:
        buf: the chunk body, without the MTrk id and length prefix.

    Returns:
        list[Event]: the track's events with absolute ticks.  The end-of-track
        meta event is consumed rather than stored; write() regenerates it.

    Raises:
        ValueError: if a data byte relies on running status before any status
            byte has been seen.
    """
    events: list[Event] = []
    pos = 0
    tick = 0
    running = 0

    while pos < len(buf):
        # Every event opens with a delta time; the deltas are accumulated so
        # events carry absolute positions in memory.
        delta, pos = _readVlq(buf, pos)
        tick += delta
        b = buf[pos]

        if b == META:
            # Meta events carry a type byte and a length-prefixed payload.
            # They cancel running status, and end-of-track terminates the
            # chunk instead of being stored.
            metaType = buf[pos + 1]
            length, dpos = _readVlq(buf, pos + 2)
            data = bytearray(buf[dpos:dpos + length])
            pos = dpos + length
            running = 0

            if metaType == META_END_OF_TRACK:
                break

            events.append(Event(tick, META, data, metaType))
        elif b in (SYSEX, SYSEX_CONT):
            # Sysex keeps the status byte that introduced it and the
            # length-prefixed payload, and also cancels running status.
            length, dpos = _readVlq(buf, pos + 1)
            data = bytearray(buf[dpos:dpos + length])
            pos = dpos + length
            running = 0
            events.append(Event(tick, b, data))
        else:
            # Voice events: a status byte becomes the new running status,
            # while a data byte reuses the previous one.
            if b & 0x80:
                status = b
                pos += 1
                running = status
            else:
                if not running:
                    raise ValueError("running status data byte with no prior status")

                status = running

            # The number of data bytes is fixed by the message type.
            n = _VOICE_DATA_LEN[status & 0xF0]
            data = bytearray(buf[pos:pos + n])
            pos += n
            events.append(Event(tick, status, data))

    return events


def _sortKey(seq_event: tuple[int, Event]) -> tuple[int, int, int]:
    """Ordering key for the events of a track when it is written.

    Events sort by tick, then by a priority that resolves ties within a tick:
    meta and sysex first, then controllers and program changes, then
    note-offs together with the remaining voice messages, and note-ons last.
    Releasing a pitch before it is re-struck at the same tick lets a synth
    sound the repeated note instead of swallowing it, and program and
    controller changes land before the notes they are meant to shape.  The
    original sequence number is the final component, so events the priority
    does not separate keep the order they were read in.

    Args:
        seq_event: an (index, event) pair as produced by enumerate().

    Returns:
        tuple[int, int, int]: (tick, priority, original index).
    """
    seq, e = seq_event

    if e.status == META or e.status in (SYSEX, SYSEX_CONT):
        prio = 0
    elif e.status & 0xF0 in (0xB0, 0xC0):
        prio = 1
    elif e.isNoteOff:
        prio = 2
    elif e.isNoteOn:
        prio = 3
    else:
        prio = 2

    return e.tick, prio, seq


def write(mf: MidiFile, path: str) -> None:
    """Serialise a MidiFile to disk.

    Every voice event is written with an explicit status byte, so the output
    never relies on running status.  Each track ends with exactly one
    end-of-track meta event regardless of what the event list holds.

    Args:
        mf: the file to write; its events are left untouched.
        path: destination path, overwritten if it already exists.
    """
    out = bytearray()
    out += b"MThd" + struct.pack(">IHHH", 6, mf.format, len(mf.tracks), mf.division)

    for track in mf.tracks:
        body = bytearray()
        last_tick = 0

        # Order events by tick and priority; enumerate supplies the original
        # index so the sort is stable for events the priority cannot separate.
        ordered = [e for _, e in sorted(enumerate(track), key=_sortKey)]

        for e in ordered:
            # Any stored end-of-track is dropped here; a single one is
            # appended after the last event below.
            if e.status == META and e.metaType == META_END_OF_TRACK:
                continue

            # Absolute ticks become deltas from the previous written event.
            body += _encodeVlq(e.tick - last_tick)
            last_tick = e.tick

            if e.status == META:
                body += bytes([META, e.metaType]) + _encodeVlq(len(e.data)) + e.data
            elif e.status in (SYSEX, SYSEX_CONT):
                body += bytes([e.status]) + _encodeVlq(len(e.data)) + e.data
            else:
                body += bytes([e.status]) + e.data

        body += _encodeVlq(0) + bytes([META, META_END_OF_TRACK, 0])
        out += b"MTrk" + struct.pack(">I", len(body)) + body

    with open(path, "wb") as f:
        f.write(out)
