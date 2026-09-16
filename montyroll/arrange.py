# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Split a flattened channel into parts and add depth to an arrangement.

The module takes a `Song` and returns a new `smf.MidiFile`. `splitChannel`
separates one channel's polyphony into voice streams and puts each stream
on its own channel, so a file with everything on one piano channel becomes
parts a channel strip can give instruments to. `double` and `echo` add a
channel that copies an existing part, transposed or delayed, at a scaled
velocity. The song itself is never modified.
"""

from __future__ import annotations

from . import model, smf

MAX_PARTS = 8            # streams one split may produce
_NO_NOTE = -1            # a stream that has not sounded yet


def _assign(pitches: list[int], recents: list[int]) -> list[int]:
    """Assign a chord's notes to voice streams without crossing.

    Both lists run top-down: `pitches` is the chord sorted descending and
    `recents` is each stream's last pitch by stream index, `_NO_NOTE` for a
    stream that has not sounded. The mapping is monotonic, so the top note
    never lands below a lower one, and it minimises the total distance
    between each note and its stream's last pitch. A stream that has not
    sounded costs nothing, and a stream may take several adjacent notes
    when the chord is bigger than the stream count.

    Args:
        pitches: the chord's pitches, descending.
        recents: each stream's last pitch, stream 0 first.

    Returns:
        list[int]: a stream index per note, non-decreasing.
    """
    count = len(pitches)
    streams = len(recents)

    # The first chord has no history to follow, so it is cut at its widest
    # pitch gaps: the dyad stays together and the bass stands alone.
    if all(r == _NO_NOTE for r in recents):
        if count <= streams:
            spread = list(range(count))
            return spread

        gaps = sorted(range(count - 1), key=lambda i: pitches[i] - pitches[i + 1],
                      reverse=True)
        cuts = set(gaps[:streams - 1])
        stream = 0
        spread = []

        for i in range(count):
            spread.append(stream)

            if i in cuts:
                stream += 1

        return spread

    # Distances are scaled so the one-point penalty for putting a second
    # note on a stream can never outweigh a real semitone; without it a
    # chord over fresh streams would collapse onto one stream on a tie.
    def cost(pitch: int, stream: int) -> int:
        """The scaled distance from a note to a stream's last pitch."""
        recent = recents[stream]
        distance = 0 if recent == _NO_NOTE else 256 * abs(pitch - recent)
        return distance

    # best[j] is the cheapest way to place the notes so far with the latest
    # note on stream j; parents remember the previous note's stream.
    best = [cost(pitches[0], j) for j in range(streams)]
    parents: list[list[int]] = []

    for i in range(1, count):
        parent = [0] * streams
        nxt = [0] * streams
        lowBefore = None
        lowAt = 0

        for j in range(streams):
            # Coming from a strictly lower stream is free; staying on the
            # same stream carries the doubling penalty.
            stay = best[j] + 1

            if lowBefore is not None and lowBefore <= stay:
                nxt[j] = cost(pitches[i], j) + lowBefore
                parent[j] = lowAt
            else:
                nxt[j] = cost(pitches[i], j) + stay
                parent[j] = j

            if lowBefore is None or best[j] < lowBefore:
                lowBefore = best[j]
                lowAt = j

        parents.append(parent)
        best = nxt

    # Walk back from the cheapest final stream to the first note.
    at = min(range(streams), key=lambda j: best[j])
    order = [at]

    for parent in reversed(parents):
        at = parent[at]
        order.append(at)

    order.reverse()
    return order


def _buildFile(song: model.Song, byChannel: dict[int, list[tuple[int, int, int, int]]],
               programs: dict[int, int] | None = None) -> smf.MidiFile:
    """Build a file from note tuples per channel plus the song's other events.

    Track 0 carries every meta event of the original except track names;
    then one track per channel carries the original's non-note events for
    that channel followed by the channel's notes. A channel absent from
    `byChannel` keeps its original notes.

    Args:
        song: the original, for its metas, channel events and other notes.
        byChannel: (start, end, pitch, velocity) tuples per channel.
        programs: a program change written at tick 0, per new channel.

    Returns:
        smf.MidiFile: a new file at the song's division.
    """
    conductor: list[smf.Event] = []
    events: dict[int, list[smf.Event]] = {}

    for track in song.mf.tracks:
        for e in track:
            if e.status == smf.META:
                if e.metaType != smf.META_TRACK_NAME:
                    conductor.append(smf.Event(e.tick, e.status, bytearray(e.data), e.metaType))
            elif e.channel >= 0 and not (e.isNoteOn or e.isNoteOff):
                events.setdefault(e.channel, []).append(
                    smf.Event(e.tick, e.status, bytearray(e.data), e.metaType))

    # Channels the caller does not touch keep their notes as they are.
    notes = dict(byChannel)

    for n in song.notes:
        if n.channel not in notes:
            notes.setdefault(n.channel, [])

    for n in song.notes:
        if n.channel in byChannel:
            continue

        notes[n.channel].append((n.start, n.end, n.pitch, n.velocity))

    for ch, program in (programs or {}).items():
        events.setdefault(ch, []).insert(0, smf.Event(0, 0xC0 | ch, bytearray([program])))

    for ch, pairs in notes.items():
        out = events.setdefault(ch, [])

        for start, end, pitch, velocity in pairs:
            out.append(smf.Event(start, 0x90 | ch, bytearray([pitch, velocity])))
            out.append(smf.Event(end, 0x80 | ch, bytearray([pitch, 64])))

    conductor.sort(key=lambda e: e.tick)
    tracks = [conductor]

    for ch in sorted(events):
        track = sorted(events[ch], key=lambda e: e.tick)
        name = f"Ch {ch + 1}".encode("latin-1")
        track.insert(0, smf.Event(0, smf.META, bytearray(name), smf.META_TRACK_NAME))
        tracks.append(track)

    mf = smf.MidiFile(1, song.mf.division, tracks)
    return mf


def freeChannels(song: model.Song, needed: int, keep: int) -> list[int]:
    """The channels a split can place its streams on.

    Args:
        song: the song, for the channels already in use.
        needed: how many channels the split needs in all.
        keep: the source channel, which stays as the first.

    Returns:
        list[int]: `keep` first, then the lowest unused channels, never the
        drum channel.

    Raises:
        ValueError: the file does not have enough free channels.
    """
    used = set(song.channels) | {n.channel for n in song.notes}
    spare = [ch for ch in range(16)
             if ch not in used and ch != model.DRUM_CHANNEL]
    channels = [keep] + spare[:needed - 1]

    if len(channels) < needed:
        raise ValueError(f"a split into {needed} parts needs {needed - 1} free channels "
                         f"and the file has {len(spare)}")

    return channels


def splitChannel(song: model.Song, channel: int, parts: int,
                 channels: list[int] | None = None) -> smf.MidiFile:
    """Separate one channel's polyphony into parts on their own channels.

    Notes that start together are one chord. Each chord's notes go to
    voice streams top-down without crossing, each note to the stream whose
    last pitch is nearest, so a line stays in one part while it is the
    highest, lowest or nearest voice. Stream 0 is the top voice and keeps
    the source channel with its programs and controllers; the other
    streams take the lowest free channels. A chord bigger than `parts`
    puts its extra notes in the nearest stream as chord tones.

    Args:
        song: the song; never modified.
        channel: 0-based channel to split.
        parts: streams to separate, 2 to `MAX_PARTS`.
        channels: the channel per stream; the lowest free channels when
            omitted, with the source channel first.

    Returns:
        smf.MidiFile: a new file with the channel's notes spread over
        `parts` channels.

    Raises:
        ValueError: `parts` is out of range, the channel has no notes, or
            the file does not have enough free channels.
    """
    if not 2 <= parts <= MAX_PARTS:
        raise ValueError(f"parts must be 2 to {MAX_PARTS}, not {parts}")

    mine = sorted((n for n in song.notes if n.channel == channel),
                  key=lambda n: (n.start, -n.pitch))

    if not mine:
        raise ValueError(f"Ch {channel + 1} has no notes to split")

    if channels is None:
        channels = freeChannels(song, parts, channel)

    # One chord per onset tick, notes top-down, streamed through the
    # assignment so each part follows its own line.
    recents = [_NO_NOTE] * parts
    streams: dict[int, list[tuple[int, int, int, int]]] = {s: [] for s in range(parts)}
    i = 0

    while i < len(mine):
        j = i

        while j < len(mine) and mine[j].start == mine[i].start:
            j += 1

        chord = mine[i:j]
        order = _assign([n.pitch for n in chord], recents)

        for n, stream in zip(chord, order):
            streams[stream].append((n.start, n.end, n.pitch, n.velocity))

        # A stream's last pitch is the top note it was just given; notes run
        # top-down, so the first note per stream is that top.
        seen: set[int] = set()

        for n, stream in zip(chord, order):
            if stream not in seen:
                recents[stream] = n.pitch
                seen.add(stream)

        i = j

    byChannel = {channels[s]: notes for s, notes in streams.items()}
    mf = _buildFile(song, byChannel)
    return mf


def double(song: model.Song, channel: int, semitones: int, target: int,
           velocity: float = 0.7, program: int | None = None) -> smf.MidiFile:
    """Copy a channel's notes onto another channel, transposed.

    Notes that would leave the MIDI range are dropped rather than folded.

    Args:
        song: the song; never modified.
        channel: 0-based channel to copy.
        semitones: transposition; +12 doubles the octave above.
        target: 0-based channel for the copy, which must be free.
        velocity: factor on each copied note's velocity, floored at 1.
        program: GM program for the copy, or None for none.

    Returns:
        smf.MidiFile: a new file with the copy added.

    Raises:
        ValueError: the target channel is in use or is the drum channel.
    """
    _checkTarget(song, target)
    copied = [(n.start, n.end, n.pitch + semitones, max(1, round(n.velocity * velocity)))
              for n in song.notes
              if n.channel == channel and 0 <= n.pitch + semitones <= 127]
    programs = {target: program} if program is not None else None
    mf = _buildFile(song, {target: copied}, programs)
    return mf


def echo(song: model.Song, channel: int, delay: int, target: int,
         velocity: float = 0.5, program: int | None = None) -> smf.MidiFile:
    """Copy a channel's notes onto another channel, delayed.

    Args:
        song: the song; never modified.
        channel: 0-based channel to copy.
        delay: ticks the copy lags behind.
        target: 0-based channel for the copy, which must be free.
        velocity: factor on each copied note's velocity, floored at 1.
        program: GM program for the copy, or None for none.

    Returns:
        smf.MidiFile: a new file with the echo added.

    Raises:
        ValueError: the target channel is in use or is the drum channel.
    """
    _checkTarget(song, target)
    copied = [(n.start + delay, n.end + delay, n.pitch, max(1, round(n.velocity * velocity)))
              for n in song.notes if n.channel == channel]
    programs = {target: program} if program is not None else None
    mf = _buildFile(song, {target: copied}, programs)
    return mf


def _checkTarget(song: model.Song, target: int) -> None:
    """Refuse a copy onto a channel that is in use or is the drum channel.

    Args:
        song: the song.
        target: 0-based channel the copy wants.

    Raises:
        ValueError: the channel is unavailable.
    """
    if target == model.DRUM_CHANNEL:
        raise ValueError(f"Ch {target + 1} is the drum channel")

    if any(n.channel == target for n in song.notes):
        raise ValueError(f"Ch {target + 1} already has notes")
