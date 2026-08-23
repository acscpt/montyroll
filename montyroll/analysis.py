# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Voice-demand analysis over a Song: polyphony, doubling and co-activity.

Everything here is a pure function of a `Song`; the module knows nothing
about tkinter and mutates nothing. The central object is `Demand`, the
result of one sweep over the notes: the piece cut into segments between
consecutive note boundaries, with the number of sounding notes in each
segment counted four ways. Those counts are what the demand strip draws and
what the per-channel figures and doubling report are derived from.

Time weighting is by seconds through the tempo map, so a file with a tempo
track is measured as heard. Drum-channel notes are counted but never merged
with pitched notes in the unique-pitch or pitch-class counts: a kick drum
on MIDI note 36 is not a unison with a bass C2.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

from . import model

THRESHOLDS = (3, 5, 8, 12, 16, 24)       # "more than N voices" columns


@dataclass
class Demand:
    """The sweep result: per-segment voice counts for the whole piece.

    Segment i runs from `ticks[i]` to `ticks[i + 1]` (the last to `endTick`)
    and lasts `seconds[i]`. The four count lists are parallel to `ticks`:
    `raw` counts sounding notes; `voices` counts distinct (channel, pitch)
    pairs, so a channel re-striking a pitch it is already holding counts
    once; `pitches` counts distinct pitches across channels, so a unison on
    another channel counts once; `classes` counts distinct pitch classes, so
    an octave doubling counts once too. `channels` holds the raw count per
    channel.
    """

    ticks: list[int] = field(default_factory=list)
    endTick: int = 0
    seconds: list[float] = field(default_factory=list)
    raw: list[int] = field(default_factory=list)
    voices: list[int] = field(default_factory=list)
    pitches: list[int] = field(default_factory=list)
    classes: list[int] = field(default_factory=list)
    channels: dict[int, list[int]] = field(default_factory=dict)

    @property
    def length(self) -> float:
        """Total length of the swept span in seconds."""
        total = sum(self.seconds)
        return total

    @property
    def peak(self) -> int:
        """The highest raw count anywhere in the piece."""
        highest = max(self.raw, default=0)
        return highest

    def index(self, tick: float) -> int:
        """Find the segment containing a tick.

        Args:
            tick: absolute tick; ticks before the first boundary map to
                segment 0 and ticks past the end to the last segment.

        Returns:
            int: the segment index, or -1 when there are no segments.
        """
        if not self.ticks:
            return -1

        i = bisect_right(self.ticks, tick) - 1
        clamped = max(0, min(i, len(self.ticks) - 1))
        return clamped

    def window(self, t0: float, t1: float) -> tuple[int, int, int, dict[int, int]]:
        """Take the maximum of each count over a tick range.

        Args:
            t0: start tick, inclusive.
            t1: end tick, exclusive; a range no wider than a segment still
                reports that segment.

        Returns:
            tuple[int, int, int, dict[int, int]]: the maxima of `raw`,
            `pitches` and `classes`, and the per-channel maxima for every
            channel that sounds in the range.
        """
        if not self.ticks:
            return 0, 0, 0, {}

        first = self.index(t0)
        last = self.index(max(t0, t1 - 1e-9))
        raw = max(self.raw[first:last + 1])
        pitches = max(self.pitches[first:last + 1])
        classes = max(self.classes[first:last + 1])
        perChannel: dict[int, int] = {}

        for ch, counts in self.channels.items():
            top = max(counts[first:last + 1])

            if top:
                perChannel[ch] = top

        return raw, pitches, classes, perChannel


@dataclass
class DemandStats:
    """Summary figures for one count series over sounding time.

    `above` maps each threshold to the fraction of sounding time the count
    exceeded it; `sounding` is the number of seconds with at least one
    note.
    """

    peak: int
    mean: float
    sounding: float
    above: dict[int, float]


@dataclass
class ChannelDemand:
    """A channel's own voice demand, for the channel strip."""

    channel: int
    peak: int
    mean: float               # mean count while the channel sounds
    sounding: float           # seconds the channel sounds at all
    fraction: float           # that as a fraction of the piece


@dataclass
class Doubling:
    """How the piece's note-time divides into distinct and doubled sound.

    All figures are in note-seconds: the integral of the relevant count over
    time, so `total` equals the sum of every note's duration. The four parts
    add up to `total`.
    """

    total: float
    sameChannel: float        # a channel holding one pitch twice
    unison: float             # the same pitch already sounding on another channel
    octave: float             # the same pitch class already sounding
    distinct: float           # what is left: harmonically distinct sound


def _pitchKey(note: model.Note) -> tuple[str, int]:
    """Key under which notes merge as the same pitch.

    Args:
        note: the note.

    Returns:
        tuple[str, int]: the pitch, namespaced so drum hits never merge with
        pitched notes.
    """
    if note.channel == model.DRUM_CHANNEL:
        key = ("drum", note.pitch)
    else:
        key = ("p", note.pitch)

    return key


def _classKey(note: model.Note) -> tuple[str, int]:
    """Key under which notes merge as the same pitch class.

    Args:
        note: the note.

    Returns:
        tuple[str, int]: the pitch class, with drum hits kept apart.
    """
    if note.channel == model.DRUM_CHANNEL:
        key = ("drum", note.pitch)
    else:
        key = ("pc", note.pitch % 12)

    return key


def _bump(counter: Counter, key: object, delta: int) -> int:
    """Adjust a multiset count and report the change in distinct keys.

    Args:
        counter: the multiset.
        key: the key to adjust.
        delta: +1 or -1.

    Returns:
        int: +1 when the key appeared, -1 when it vanished, else 0.
    """
    before = counter[key]
    after = before + delta

    if after:
        counter[key] = after
    else:
        del counter[key]

    change = (1 if after else 0) - (1 if before else 0)
    return change


def sweep(song: model.Song, notes: list[model.Note] | None = None) -> Demand:
    """Cut the piece into constant-state segments and count voices in each.

    Every note's start and end form one sorted boundary list; between two
    consecutive boundaries the set of sounding notes is constant. At a
    shared tick note-offs are applied before note-ons, so a note re-struck
    exactly where its predecessor ends counts as one voice. Zero-length
    segments are dropped.

    Args:
        song: the song, for its notes and tempo map.
        notes: the notes to sweep; defaults to all of the song's notes, and
            a subset gives the demand of that subset alone.

    Returns:
        Demand: the segment list with its counts.
    """
    if notes is None:
        notes = song.notes

    demand = Demand()

    if not notes:
        return demand

    # Boundary events, offs before ons at the same tick. The sequence number
    # keeps the sort from ever comparing Note objects.
    events: list[tuple[int, int, int, model.Note]] = []

    for seq, n in enumerate(notes):
        events.append((n.start, 1, seq, n))
        events.append((n.end, 0, seq, n))

    events.sort(key=lambda e: (e[0], e[1], e[2]))

    # Running multisets, one per way of counting, and the distinct-key count
    # of each kept alongside so a segment costs nothing to record.
    channelsInUse = sorted({n.channel for n in notes})
    perChannel: dict[int, int] = {ch: 0 for ch in channelsInUse}
    voiceKeys: Counter = Counter()
    pitchKeys: Counter = Counter()
    classKeys: Counter = Counter()
    raw = voices = pitches = classes = 0
    demand.channels = {ch: [] for ch in channelsInUse}
    prevTick: int | None = None
    i = 0

    while i < len(events):
        tick = events[i][0]

        # Record the segment that just ended, with the state in force
        # during it.
        if prevTick is not None and tick > prevTick:
            demand.ticks.append(prevTick)
            demand.seconds.append(song.tickToSeconds(tick) - song.tickToSeconds(prevTick))
            demand.raw.append(raw)
            demand.voices.append(voices)
            demand.pitches.append(pitches)
            demand.classes.append(classes)

            for ch in channelsInUse:
                demand.channels[ch].append(perChannel[ch])

        # Apply every event at this tick before looking at the next.
        while i < len(events) and events[i][0] == tick:
            _, kind, _, n = events[i]
            delta = 1 if kind else -1
            raw += delta
            perChannel[n.channel] += delta
            voices += _bump(voiceKeys, (n.channel, n.pitch), delta)
            pitches += _bump(pitchKeys, _pitchKey(n), delta)
            classes += _bump(classKeys, _classKey(n), delta)
            i += 1

        prevTick = tick

    demand.endTick = prevTick if prevTick is not None else 0
    return demand


def stats(counts: list[int], seconds: list[float],
          thresholds: tuple[int, ...] = THRESHOLDS) -> DemandStats:
    """Summarise one count series over the time it is non-zero.

    Args:
        counts: a per-segment count series from a `Demand`.
        seconds: the matching segment durations.
        thresholds: the "more than N" levels to report.

    Returns:
        DemandStats: peak, duration-weighted mean, sounding seconds and the
        fraction of sounding time above each threshold.
    """
    peak = 0
    sounding = 0.0
    weighted = 0.0
    above = {k: 0.0 for k in thresholds}

    for c, d in zip(counts, seconds):
        if c == 0:
            continue

        sounding += d
        weighted += c * d
        peak = max(peak, c)

        for k in thresholds:
            if c > k:
                above[k] += d

    mean = weighted / sounding if sounding else 0.0
    fractions = {k: (above[k] / sounding if sounding else 0.0) for k in thresholds}
    result = DemandStats(peak, mean, sounding, fractions)
    return result


def channelDemand(demand: Demand) -> dict[int, ChannelDemand]:
    """Per-channel demand figures for the channel strip.

    Args:
        demand: a sweep of the whole song.

    Returns:
        dict[int, ChannelDemand]: keyed by 0-based channel.
    """
    length = demand.length
    figures: dict[int, ChannelDemand] = {}

    for ch, counts in demand.channels.items():
        st = stats(counts, demand.seconds, ())
        fraction = st.sounding / length if length else 0.0
        figures[ch] = ChannelDemand(ch, st.peak, st.mean, st.sounding, fraction)

    return figures


def doubling(demand: Demand) -> Doubling:
    """Split the piece's note-seconds into distinct and doubled sound.

    Args:
        demand: a sweep of the whole song.

    Returns:
        Doubling: the four-way split; see the class for what each part is.
    """
    total = sameChannel = unison = octave = distinct = 0.0

    for raw, voices, pitches, classes, d in zip(demand.raw, demand.voices, demand.pitches,
                                                 demand.classes, demand.seconds):
        total += raw * d
        sameChannel += (raw - voices) * d
        unison += (voices - pitches) * d
        octave += (pitches - classes) * d
        distinct += classes * d

    result = Doubling(total, sameChannel, unison, octave, distinct)
    return result


def coActivity(demand: Demand) -> dict[tuple[int, int], float]:
    """Seconds during which each pair of channels is sounding at once.

    Channels that are rarely active together are candidates to share a
    voice pool.

    Args:
        demand: a sweep of the whole song.

    Returns:
        dict[tuple[int, int], float]: keyed by (lower channel, higher
        channel), every pair present even when the value is zero.
    """
    channels = sorted(demand.channels)
    together = {pair: 0.0 for pair in combinations(channels, 2)}
    columns = [demand.channels[ch] for ch in channels]

    for i, d in enumerate(demand.seconds):
        active = [ch for ch, counts in zip(channels, columns) if counts[i]]

        for pair in combinations(active, 2):
            together[pair] += d

    return together
