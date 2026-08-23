# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Reduce a song to a fixed voice budget.

A `Plan` divides the song's channels into `Pool`s, each with a number of
voices and a set of transforms; `reduce` applies the transforms to each
pool's notes, allocates what remains to the pool's voices, and returns a
`Reduction`: every note that sounds with the voice it plays on, every note
that was dropped or cut short, and the musical cost of those losses. The
song itself is never modified; the reduction is a view over it, and
`toMidiFile` builds a playable copy for audition or saving.

Every decision rests on a weight per note (see `weights`), so a note is
dropped because of a number the user can inspect and steer through the
per-channel priorities. The module knows nothing about tkinter.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from . import model, smf


@dataclass
class Pool:
    """A set of channels sharing a number of voices, with its transforms.

    The transforms run in the order listed here, cheapest loss first.
    """

    name: str
    channels: list[int]
    voices: int
    dedupUnison: bool = True      # the same pitch starting together on two channels
    dedupOctave: bool = False     # the same pitch class an octave apart, starting together
    tremoloGap: int = 0           # ticks: re-strike of a pitch within this gap continues it
    legatoGap: int = 0            # ticks: extend a note to the next onset within this gap
    monoTop: bool = False         # keep only the highest sounding note per channel
    maxChord: int = 0             # per channel, notes starting together are thinned to this


@dataclass
class Plan:
    """The whole reduction: pools, and a priority per channel.

    A priority scales every weight on that channel; 1.0 is neutral, 2.0
    makes the channel twice as hard to steal from, 0.5 half as hard.
    """

    pools: list[Pool] = field(default_factory=list)
    priority: dict[int, float] = field(default_factory=dict)

    def priorityOf(self, channel: int) -> float:
        """The priority for a channel, 1.0 when none is set.

        Args:
            channel: 0-based channel.

        Returns:
            float: the scale factor.
        """
        value = self.priority.get(channel, 1.0)
        return value

    @classmethod
    def single(cls, song: model.Song, voices: int, **transforms: object) -> Plan:
        """A plan with every channel that has notes in one pool.

        Args:
            song: the song.
            voices: the pool's voice count.
            **transforms: `Pool` switches to set.

        Returns:
            Plan: the plan.
        """
        channels = sorted({n.channel for n in song.notes})
        pool = Pool("all", channels, voices, **transforms)
        plan = cls([pool])
        return plan


@dataclass(eq=False)
class Placed:
    """A note as it sounds in the reduction.

    `note` is the original it came from; `start` and `end` may differ from
    the original's after legato, a top-line re-strike or a cut. Identity
    hashing, as for `Note`, because these go into sets.
    """

    note: model.Note
    start: int
    end: int
    pitch: int
    velocity: int
    channel: int
    weight: float
    voice: int = -1

    @property
    def length(self) -> int:
        """Duration in ticks."""
        ticks = self.end - self.start
        return ticks


@dataclass
class Reduction:
    """What a plan makes of a song.

    `placed` are the notes that sound, each with its voice. `dropped` are
    original notes that never sound at all, with the reason. `cut` maps an
    original note to the tick it was silenced at, when stealing ended it
    early. `loss` is the sum over dropped and cut material of weight times
    ticks lost, per channel and overall, so two plans can be compared.
    """

    placed: list[Placed] = field(default_factory=list)
    dropped: dict[int, str] = field(default_factory=dict)        # id(note) -> reason
    cut: dict[int, int] = field(default_factory=dict)            # id(note) -> new end
    lossByChannel: dict[int, float] = field(default_factory=dict)
    voicesUsed: int = 0

    @property
    def loss(self) -> float:
        """Total weighted loss across every channel."""
        total = sum(self.lossByChannel.values())
        return total

    def isDropped(self, note: model.Note) -> bool:
        """Report whether an original note never sounds.

        Args:
            note: the original note.

        Returns:
            bool: True when the reduction dropped it.
        """
        gone = id(note) in self.dropped
        return gone


# ---------------------------------------------------------------- weights
def weights(song: model.Song, plan: Plan) -> dict[int, float]:
    """Score every note by what is lost if it is not played.

    The score is the channel's priority times a velocity factor (0.5 to
    1.0), times a texture factor (1.5 for the highest note sounding on its
    channel at its onset, 1.3 for the lowest, 1.0 for an inner voice),
    times a doubling factor (0.1 when the same pitch is already sounding on
    another channel, 0.5 when the same pitch class is). Remaining duration
    is not part of it; the allocator multiplies by that at the moment of a
    steal.

    Args:
        song: the song.
        plan: for the priorities.

    Returns:
        dict[int, float]: keyed by `id(note)`.
    """
    notes = sorted(song.notes, key=lambda n: (n.start, n.pitch))
    result: dict[int, float] = {}

    # Notes sounding at each onset: a sweep over starts with an active list
    # pruned of anything that has ended. Notes starting together are one
    # group, so every note of a chord sees the whole chord.
    active: list[model.Note] = []
    i = 0

    while i < len(notes):
        tick = notes[i].start
        j = i

        while j < len(notes) and notes[j].start == tick:
            j += 1

        group = notes[i:j]
        active = [a for a in active if a.end > tick]
        context = active + group

        for n in group:
            sameChannel = [a for a in context if a.channel == n.channel]
            others = [a for a in context if a.channel != n.channel and a is not n]
            pitches = sorted(a.pitch for a in sameChannel)

            if len(pitches) == 1 or n.pitch == pitches[-1]:
                texture = 1.5          # the line, or alone on its channel
            elif n.pitch == pitches[0]:
                texture = 1.3
            else:
                texture = 1.0

            # A note doubles another when that other started earlier or,
            # starting together, sits on a lower channel: the first part in
            # the file is the original and the later one the doubling.
            originals = [a for a in others if a.channel != model.DRUM_CHANNEL
                         and (a.start < tick or a.channel < n.channel)]

            if n.channel == model.DRUM_CHANNEL:
                doubling = 1.0
            elif any(a.pitch == n.pitch for a in originals):
                doubling = 0.1
            elif any(a.pitch % 12 == n.pitch % 12 for a in originals):
                doubling = 0.5
            else:
                doubling = 1.0

            velocity = 0.5 + 0.5 * n.velocity / 127
            result[id(n)] = plan.priorityOf(n.channel) * velocity * texture * doubling

        active.extend(group)
        i = j

    return result


# ------------------------------------------------------------- transforms
def _place(notes: list[model.Note], weight: dict[int, float]) -> list[Placed]:
    """Wrap original notes as placed notes with their weights.

    Args:
        notes: the originals.
        weight: weights keyed by `id(note)`.

    Returns:
        list[Placed]: one per note, in (start, pitch) order.
    """
    placed = [Placed(n, n.start, n.end, n.pitch, n.velocity, n.channel, weight[id(n)])
              for n in sorted(notes, key=lambda n: (n.start, n.pitch))]
    return placed


def dedupUnison(placed: list[Placed], reduction: Reduction) -> list[Placed]:
    """Drop a note that starts together with the same pitch on another part.

    The one with the higher weight survives, so a melody keeps its note and
    the doubling part loses it. Two tracks on one channel doubling each
    other count as well: the synth would play the pitch twice for one
    sound.

    Args:
        placed: the pool's notes.
        reduction: records the drops.

    Returns:
        list[Placed]: the survivors.
    """
    byKey: dict[tuple[int, int], Placed] = {}
    survivors: list[Placed] = []

    for p in placed:
        key = (p.start, p.pitch)
        other = byKey.get(key)

        if other is None:
            byKey[key] = p
            survivors.append(p)
        elif p.weight > other.weight:
            survivors.remove(other)
            reduction.dropped[id(other.note)] = "unison"
            byKey[key] = p
            survivors.append(p)
        else:
            reduction.dropped[id(p.note)] = "unison"

    return survivors


def dedupOctave(placed: list[Placed], reduction: Reduction) -> list[Placed]:
    """Drop a note that starts together with its octave on another channel.

    Applied after unison dedup. The higher weight survives; on a tie the
    lower note does, since a bass doubling carries the root.

    Args:
        placed: the pool's notes.
        reduction: records the drops.

    Returns:
        list[Placed]: the survivors.
    """
    byKey: dict[tuple[int, int], Placed] = {}
    survivors: list[Placed] = []

    for p in placed:
        key = (p.start, p.pitch % 12)
        other = byKey.get(key)

        if other is None or p.channel == other.channel or p.channel == model.DRUM_CHANNEL:
            byKey.setdefault(key, p)
            survivors.append(p)
        elif p.weight > other.weight or (p.weight == other.weight and p.pitch < other.pitch):
            survivors.remove(other)
            reduction.dropped[id(other.note)] = "octave"
            byKey[key] = p
            survivors.append(p)
        else:
            reduction.dropped[id(p.note)] = "octave"

    return survivors


def mergeTremolo(placed: list[Placed], gap: int, reduction: Reduction) -> list[Placed]:
    """Fold a re-struck pitch into the note before it.

    Per channel and pitch, a note starting within `gap` ticks of the
    previous note's end continues that note instead of sounding again.

    Args:
        placed: the pool's notes.
        gap: the maximum gap in ticks; overlaps count as zero.
        reduction: records the drops.

    Returns:
        list[Placed]: the survivors, ends extended where a merge happened.
    """
    last: dict[tuple[int, int], Placed] = {}
    survivors: list[Placed] = []

    for p in placed:
        key = (p.channel, p.pitch)
        prev = last.get(key)

        if prev is not None and p.start - prev.end <= gap:
            prev.end = max(prev.end, p.end)
            reduction.dropped[id(p.note)] = "tremolo"
        else:
            survivors.append(p)
            last[key] = p

    return survivors


def legato(placed: list[Placed], gap: int) -> list[Placed]:
    """Extend each note to the next onset on its channel when the gap is short.

    A note is never shortened, and a gap longer than `gap` ticks is a rest
    and stays one.

    Args:
        placed: the pool's notes.
        gap: the longest gap to bridge, in ticks.

    Returns:
        list[Placed]: the same notes, ends extended.
    """
    byChannel: dict[int, list[Placed]] = {}

    for p in placed:
        byChannel.setdefault(p.channel, []).append(p)

    for notes in byChannel.values():
        starts = sorted({p.start for p in notes})

        for p in notes:
            later = [t for t in starts if t > p.start]

            if later:
                nxt = later[0]

                if p.end < nxt <= p.end + gap:
                    p.end = nxt

    return placed


def monoTop(placed: list[Placed], reduction: Reduction) -> list[Placed]:
    """Reduce each channel to its top line.

    The sounding note is always the highest active one: a lower note that
    starts under it is dropped, a note under which a higher one starts is
    cut, and when the higher note ends the highest survivor is re-struck
    for its remaining length.

    Args:
        placed: the pool's notes.
        reduction: records the drops.

    Returns:
        list[Placed]: the segments that sound; a re-struck note appears as
        a second segment sharing the original.
    """
    out: list[Placed] = []
    byChannel: dict[int, list[Placed]] = {}

    for p in placed:
        byChannel.setdefault(p.channel, []).append(p)

    for notes in byChannel.values():
        events: list[tuple[int, int, Placed]] = []

        for p in notes:
            events.append((p.start, 1, p))
            events.append((p.end, 0, p))

        events.sort(key=lambda e: (e[0], e[1], e[2].pitch))
        active: list[Placed] = []
        current: Placed | None = None
        segment: Placed | None = None

        for tick, on, p in events:
            if on:
                active.append(p)

                if current is None or p.pitch >= current.pitch:
                    if segment is not None:
                        segment.end = tick

                        if segment.end <= segment.start:
                            out.remove(segment)

                    current = p
                    segment = Placed(p.note, tick, p.end, p.pitch, p.velocity, p.channel,
                                     p.weight)
                    out.append(segment)
                else:
                    reduction.dropped[id(p.note)] = "under the top line"
            else:
                if p in active:
                    active.remove(p)

                if p is current:
                    segment.end = tick
                    current = None
                    segment = None

                    if active:
                        top = max(active, key=lambda a: a.pitch)
                        current = top
                        segment = Placed(top.note, tick, top.end, top.pitch, top.velocity,
                                         top.channel, top.weight)
                        out.append(segment)
                        reduction.dropped.pop(id(top.note), None)

    out.sort(key=lambda p: (p.start, p.pitch))
    return out


def thinChords(placed: list[Placed], maxChord: int, reduction: Reduction) -> list[Placed]:
    """Keep at most `maxChord` notes of each chord struck together on a channel.

    The top and bottom notes are kept first, then the rest by weight.

    Args:
        placed: the pool's notes.
        maxChord: notes to keep per simultaneous onset, at least 1.
        reduction: records the drops.

    Returns:
        list[Placed]: the survivors.
    """
    groups: dict[tuple[int, int], list[Placed]] = {}

    for p in placed:
        groups.setdefault((p.channel, p.start), []).append(p)

    survivors: list[Placed] = []

    for chord in groups.values():
        if len(chord) <= maxChord:
            survivors.extend(chord)
            continue

        ordered = sorted(chord, key=lambda p: p.pitch)
        keep = [ordered[-1]]

        if maxChord > 1:
            keep.append(ordered[0])

        inner = sorted(ordered[1:-1], key=lambda p: -p.weight)
        keep.extend(inner[:max(0, maxChord - 2)])

        for p in chord:
            if p in keep:
                survivors.append(p)
            else:
                reduction.dropped[id(p.note)] = "thinned"

    survivors.sort(key=lambda p: (p.start, p.pitch))
    return survivors


# -------------------------------------------------------------- allocator
def allocate(placed: list[Placed], voices: int, base: int, reduction: Reduction) -> list[Placed]:
    """Give each note a voice, stealing the weakest when the pool is full.

    At an onset with no free voice the candidate to silence is the active
    note with the lowest weight times remaining ticks. If the new note is
    itself weaker than every active one it is not started at all, which is
    the lookahead that keeps a note from starting only to be stolen a
    moment later. A stolen note is cut at the onset.

    Args:
        placed: the pool's notes after its transforms.
        voices: the pool's voice count.
        base: the first voice number of the pool.
        reduction: records drops and cuts.

    Returns:
        list[Placed]: the notes that sound, with `voice` set.
    """
    events: list[tuple[int, int, Placed]] = []

    for p in placed:
        events.append((p.start, 1, p))
        events.append((p.end, 0, p))

    events.sort(key=lambda e: (e[0], e[1], -e[2].weight))
    free = list(range(base, base + voices))
    active: dict[int, Placed] = {}
    out: list[Placed] = []

    for tick, on, p in events:
        if not on:
            if p.voice >= 0 and active.get(p.voice) is p:
                del active[p.voice]
                free.append(p.voice)
                free.sort()

            continue

        if free:
            p.voice = free.pop(0)
            active[p.voice] = p
            out.append(p)
            continue

        # Full: weigh the newcomer against the weakest of what is playing.
        victimVoice = min(active, key=lambda v: active[v].weight * (active[v].end - tick))
        victim = active[victimVoice]
        newcomer = p.weight * (p.end - p.start)

        if newcomer <= victim.weight * (victim.end - tick):
            reduction.dropped[id(p.note)] = "no voice"
            continue

        victim.end = tick
        reduction.cut[id(victim.note)] = tick
        p.voice = victimVoice
        active[victimVoice] = p
        out.append(p)

    # A note stolen at its own onset never sounded: that is a drop, not a
    # cut, and it is not written out.
    sounding: list[Placed] = []

    for p in out:
        if p.end > p.start:
            sounding.append(p)
        else:
            reduction.cut.pop(id(p.note), None)
            reduction.dropped[id(p.note)] = "no voice"

    return sounding


# ------------------------------------------------------------------ drive
def reduce(song: model.Song, plan: Plan) -> Reduction:
    """Apply a plan to a song.

    Args:
        song: the song; never modified.
        plan: the pools and priorities.

    Returns:
        Reduction: the result, with voices numbered across pools in plan
        order.
    """
    weight = weights(song, plan)
    reduction = Reduction()
    base = 0

    for pool in plan.pools:
        members = set(pool.channels)
        placed = _place([n for n in song.notes if n.channel in members], weight)

        if pool.dedupUnison:
            placed = dedupUnison(placed, reduction)

        if pool.dedupOctave:
            placed = dedupOctave(placed, reduction)

        if pool.tremoloGap > 0:
            placed = mergeTremolo(placed, pool.tremoloGap, reduction)

        if pool.legatoGap > 0:
            placed = legato(placed, pool.legatoGap)

        if pool.monoTop:
            placed = monoTop(placed, reduction)

        if pool.maxChord > 0:
            placed = thinChords(placed, pool.maxChord, reduction)

        reduction.placed.extend(allocate(placed, pool.voices, base, reduction))
        base += pool.voices

    reduction.voicesUsed = base

    # Loss: weight times ticks for every dropped note and every cut tail.
    for n in song.notes:
        lost = 0

        if id(n) in reduction.dropped:
            lost = n.length
        elif id(n) in reduction.cut:
            lost = n.end - reduction.cut[id(n)]

        if lost > 0:
            total = reduction.lossByChannel.get(n.channel, 0.0)
            reduction.lossByChannel[n.channel] = total + weight[id(n)] * lost

    reduction.placed.sort(key=lambda p: (p.start, p.voice))
    return reduction


def toMidiFile(song: model.Song, reduction: Reduction) -> smf.MidiFile:
    """Build a playable file from a reduction.

    Track 0 carries every meta event of the original (tempo map, time
    signatures, markers); then one track per channel carries that channel's
    non-note events from the original followed by its placed notes. Nothing
    refers back to the original's events.

    Args:
        song: the original, for its metas and channel events.
        reduction: the notes to write.

    Returns:
        smf.MidiFile: a new file.
    """
    conductor: list[smf.Event] = []
    byChannel: dict[int, list[smf.Event]] = {}

    for track in song.mf.tracks:
        for e in track:
            if e.status == smf.META:
                if e.metaType != smf.META_TRACK_NAME:
                    conductor.append(smf.Event(e.tick, e.status, bytearray(e.data), e.metaType))
            elif e.channel >= 0 and not (e.isNoteOn or e.isNoteOff):
                byChannel.setdefault(e.channel, []).append(
                    smf.Event(e.tick, e.status, bytearray(e.data), e.metaType))

    for p in reduction.placed:
        events = byChannel.setdefault(p.channel, [])
        events.append(smf.Event(p.start, 0x90 | p.channel, bytearray([p.pitch, p.velocity])))
        events.append(smf.Event(p.end, 0x80 | p.channel, bytearray([p.pitch, 64])))

    conductor.sort(key=lambda e: e.tick)
    tracks = [conductor]

    for ch in sorted(byChannel):
        events = sorted(byChannel[ch], key=lambda e: e.tick)
        name = f"Ch {ch + 1}".encode("latin-1")
        events.insert(0, smf.Event(0, smf.META, bytearray(name), smf.META_TRACK_NAME))
        tracks.append(events)

    out = smf.MidiFile(1, song.mf.division, tracks)
    return out
