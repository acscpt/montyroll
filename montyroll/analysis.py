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

from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from itertools import accumulate, combinations

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


def _sweepRange(song: model.Song, notes: list[model.Note], start: int | None,
                end: int | None, channels: list[int]) -> Demand:
    """Cut notes into constant-state segments between two ticks and count each.

    Every note's start and end are boundaries, and at a shared tick
    note-offs come before note-ons, so a note re-struck where its
    predecessor ends counts as one voice. Each count is a running tally:
    the multisets of keys hold how many notes share a key, and the distinct
    count changes only when a key appears or vanishes.

    Args:
        song: for the tempo map.
        notes: the notes to sweep.
        start: a tick from which to begin, the notes sounding there struck
            at it; None to begin at the first note.
        end: a tick at which to stop, with the last segment recorded up to
            it and no note released at or after it; None to run to the last
            note's end.
        channels: the channels to count, every note's among them.

    Returns:
        Demand: the segments, with `endTick` the last tick reached.
    """
    # Boundary events, offs before ons at a tick; the sequence number keeps
    # the sort from comparing notes.
    events: list[tuple[int, int, int, model.Note]] = []

    for seq, n in enumerate(notes):
        events.append((n.start if start is None else max(n.start, start), 1, seq, n))

        if end is None or n.end < end:
            events.append((n.end, 0, seq, n))

    events.sort()
    keys = [((n.channel, n.pitch), _pitchKey(n), _classKey(n)) for n in notes]
    column = {ch: i for i, ch in enumerate(channels)}
    perChannel = [0] * len(channels)
    voiceKeys: dict = {}
    pitchKeys: dict = {}
    classKeys: dict = {}
    raw = voices = pitches = classes = 0
    demand = Demand()
    rows: list[tuple[int, ...]] = []
    prevTick = start
    prevSec = song.tickToSeconds(start) if start is not None else 0.0

    def record(tick: int):
        """Append the segment from prevTick to tick with the counts in force."""
        nonlocal prevSec
        sec = song.tickToSeconds(tick)
        demand.ticks.append(prevTick)
        demand.seconds.append(sec - prevSec)
        demand.raw.append(raw)
        demand.voices.append(voices)
        demand.pitches.append(pitches)
        demand.classes.append(classes)
        rows.append(tuple(perChannel))
        prevSec = sec

    for tick, kind, seq, n in events:
        # A new tick closes the segment that ran up to it.
        if tick != prevTick:
            if prevTick is not None:
                record(tick)
            else:
                prevSec = song.tickToSeconds(tick)

            prevTick = tick

        delta = 1 if kind else -1
        raw += delta
        perChannel[column[n.channel]] += delta
        voiceKey, pitchKey, classKey = keys[seq]

        before = voiceKeys.get(voiceKey, 0)
        after = before + delta
        voices += (after != 0) - (before != 0)

        if after:
            voiceKeys[voiceKey] = after
        else:
            del voiceKeys[voiceKey]

        before = pitchKeys.get(pitchKey, 0)
        after = before + delta
        pitches += (after != 0) - (before != 0)

        if after:
            pitchKeys[pitchKey] = after
        else:
            del pitchKeys[pitchKey]

        before = classKeys.get(classKey, 0)
        after = before + delta
        classes += (after != 0) - (before != 0)

        if after:
            classKeys[classKey] = after
        else:
            del classKeys[classKey]

    if end is not None and prevTick is not None and end > prevTick:
        record(end)
        prevTick = end

    # The per-channel counts are kept a row per segment and turned into a
    # column per channel once.
    columns = list(zip(*rows)) if rows else [()] * len(channels)
    demand.channels = {ch: list(columns[i]) for i, ch in enumerate(channels)}
    demand.endTick = prevTick if prevTick is not None else 0
    return demand


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

    if not notes:
        empty = Demand()
        return empty

    demand = _sweepRange(song, notes, None, None, sorted({n.channel for n in notes}))
    return demand


def resweep(song: model.Song, demand: Demand, t0: int, t1: int,
            index: StepIndex | None = None) -> Demand:
    """Sweep a song again after an edit, keeping the segments the edit cannot reach.

    `demand` is the sweep of the song before the edit, and every note the
    edit changed lay between `t0` and `t1` both before and after it, so the
    boundaries outside that span are unchanged. The segments before the
    last boundary ahead of `t0`, and from the first boundary past `t1`, are
    kept; the stretch between is swept from the notes sounding at its
    start, or from the first note when no boundary lies ahead of `t0`. The
    result equals `sweep(song)`.

    Args:
        song: the song after the edit.
        demand: its sweep before the edit.
        t0: the first tick the edit touched.
        t1: the last.
        index: the song's `stepIndex` after the edit, built when omitted.

    Returns:
        Demand: the sweep of the song as it now is.
    """
    # A sweep with no segments has nothing to keep, and a song with no
    # notes has nothing to sweep.
    if not demand.ticks or not song.notes:
        fresh = sweep(song)
        return fresh

    boundaries = demand.ticks + [demand.endTick]
    i = bisect_left(boundaries, t0) - 1
    j = bisect_right(boundaries, t1)
    a = boundaries[i] if i >= 0 else None
    b = boundaries[j] if j < len(boundaries) else None
    index = index or stepIndex(song)
    last = bisect_left(index.starts, b) if b is not None else len(index.notes)

    if a is None:
        notes = index.notes[:last]
    else:
        notes = [n for n in index.notes[bisect_right(index.reach, a):last] if n.end > a]

    # The stretch is swept from the notes sounding at its start, counting
    # the channels as a full sweep would list them.
    channelsInUse = sorted({n.channel for n in song.notes})
    middle = _sweepRange(song, notes, a, b, channelsInUse)

    # The kept segments either side, with a channel the edit introduced
    # silent in them and one it emptied dropped.
    head = slice(0, max(i, 0))
    tail = slice(j, len(demand.ticks)) if b is not None else slice(0, 0)
    result = Demand(
        ticks=demand.ticks[head] + middle.ticks + demand.ticks[tail],
        endTick=demand.endTick if b is not None else middle.endTick,
        seconds=demand.seconds[head] + middle.seconds + demand.seconds[tail],
        raw=demand.raw[head] + middle.raw + demand.raw[tail],
        voices=demand.voices[head] + middle.voices + demand.voices[tail],
        pitches=demand.pitches[head] + middle.pitches + demand.pitches[tail],
        classes=demand.classes[head] + middle.classes + demand.classes[tail])
    kept = len(demand.ticks)

    for ch in channelsInUse:
        old = demand.channels.get(ch, [0] * kept)
        result.channels[ch] = old[head] + middle.channels[ch] + old[tail]

    return result


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


def poolOverlap(demand: Demand, pool: list[int]) -> dict[int, float]:
    """For every channel, the share of its sounding time during which a pool sounds.

    A channel in the pool is measured against the pool's other members and
    a channel outside it against them all, so a low figure marks a channel
    that could share the pool's voices. This is the figure the pool editor
    shows.

    Args:
        demand: a sweep of the whole song.
        pool: the pool's channels; any the demand does not know are ignored.

    Returns:
        dict[int, float]: 0.0 to 1.0 per channel of the demand; 0.0 when the
        channel never sounds or nothing else of the pool ever does.
    """
    members = [ch for ch in pool if ch in demand.channels]
    columns = [demand.channels[ch] for ch in members]

    # How many of the pool's members sound in each segment; a member itself
    # needs a second one sounding, a channel outside the pool needs one.
    count = [sum(1 for col in columns if col[i] > 0) for i in range(len(demand.seconds))]
    result: dict[int, float] = {}

    for ch, mine in demand.channels.items():
        needed = 2 if ch in members else 1
        sounding = 0.0
        together = 0.0

        for d, m, c in zip(demand.seconds, mine, count):
            if m:
                sounding += d

                if c >= needed:
                    together += d

        result[ch] = together / sounding if sounding else 0.0

    return result


# ------------------------------------------------------------------ tonal
PITCH_CLASS_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Kessler key profiles: how strongly each scale degree is heard as
# belonging to a major or a minor key, from the probe-tone experiments. A
# pitch-class histogram is correlated against every rotation of each.
MAJOR_PROFILE = (6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88)
MINOR_PROFILE = (6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17)

# Chord templates as intervals above the root, tried in this order so that a
# plain triad wins a tie against a seventh that adds nothing.
CHORD_TEMPLATES = (
    ("", (0, 4, 7)),            # major
    ("m", (0, 3, 7)),           # minor
    ("dim", (0, 3, 6)),
    ("aug", (0, 4, 8)),
    ("7", (0, 4, 7, 10)),       # dominant seventh
    ("maj7", (0, 4, 7, 11)),
    ("m7", (0, 3, 7, 10)),
    ("m7b5", (0, 3, 6, 10)),
    ("dim7", (0, 3, 6, 9)),
    ("sus4", (0, 5, 7)),
    ("sus2", (0, 2, 7)),
)
RARE_CHORD_COST = {"sus4": 0.1, "sus2": 0.1}    # only win when complete
NO_CHORD = "N.C."


@dataclass
class Key:
    """An estimated key with the strength of the estimate."""

    tonic: int                # pitch class 0-11
    major: bool
    correlation: float        # against the winning profile
    margin: float             # lead over the runner-up; small means ambiguous

    @property
    def name(self) -> str:
        """The key as text, e.g. "C major" or "A minor"."""
        label = f"{PITCH_CLASS_NAMES[self.tonic]} {'major' if self.major else 'minor'}"
        return label


def pitchClassProfile(song: model.Song, t0: float, t1: float,
                      notes: list[model.Note] | None = None) -> list[float]:
    """Weigh each pitch class by how long it sounds within a tick range.

    Args:
        song: the song.
        t0: start tick, inclusive.
        t1: end tick, exclusive.
        notes: the notes to consider; defaults to the song's notes. Drum
            channel notes are ignored.

    Returns:
        list[float]: twelve weights in ticks, C first.
    """
    if notes is None:
        notes = song.notes

    profile = [0.0] * 12

    for n in notes:
        if n.channel == model.DRUM_CHANNEL:
            continue

        overlap = min(n.end, t1) - max(n.start, t0)

        if overlap > 0:
            profile[n.pitch % 12] += overlap

    return profile


def _correlation(a: list[float], b: tuple[float, ...]) -> float:
    """Pearson correlation of two twelve-element vectors.

    Args:
        a: the observed profile.
        b: a key profile.

    Returns:
        float: the correlation, or 0.0 when either vector is flat.
    """
    meanA = sum(a) / 12
    meanB = sum(b) / 12
    cov = sum((x - meanA) * (y - meanB) for x, y in zip(a, b))
    varA = sum((x - meanA) ** 2 for x in a)
    varB = sum((y - meanB) ** 2 for y in b)

    if varA == 0 or varB == 0:
        return 0.0

    r = cov / (varA * varB) ** 0.5
    return r


def estimateKey(profile: list[float]) -> Key | None:
    """Pick the key whose profile best matches a pitch-class histogram.

    Args:
        profile: twelve weights, C first.

    Returns:
        Key | None: the best of the 24 keys, or None when the profile is
        empty.
    """
    if not any(profile):
        return None

    scores: list[tuple[float, int, bool]] = []

    for tonic in range(12):
        rotated = profile[tonic:] + profile[:tonic]
        scores.append((_correlation(rotated, MAJOR_PROFILE), tonic, True))
        scores.append((_correlation(rotated, MINOR_PROFILE), tonic, False))

    scores.sort(key=lambda s: -s[0])
    best, runnerUp = scores[0], scores[1]
    key = Key(best[1], best[2], best[0], best[0] - runnerUp[0])
    return key


def songKey(song: model.Song) -> Key | None:
    """Estimate the key of the whole piece.

    Args:
        song: the song.

    Returns:
        Key | None: the key, or None for a song with no pitched notes.
    """
    profile = pitchClassProfile(song, 0, song.maxTick + 1)
    key = estimateKey(profile)
    return key


def keyOverTime(song: model.Song, windowTicks: int, stepTicks: int) -> list[tuple[int, Key]]:
    """Estimate the key in a sliding window, reporting where it changes.

    Args:
        song: the song.
        windowTicks: the width of the window.
        stepTicks: how far the window advances each time.

    Returns:
        list[tuple[int, Key]]: (tick, key) wherever the estimate differs
        from the previous window's, starting at tick 0.
    """
    changes: list[tuple[int, Key]] = []
    tick = 0
    previous: tuple[int, bool] | None = None

    while tick < song.maxTick:
        key = estimateKey(pitchClassProfile(song, tick, tick + windowTicks))

        if key is not None and (key.tonic, key.major) != previous:
            changes.append((tick, key))
            previous = (key.tonic, key.major)

        tick += stepTicks

    return changes


def _omissionCost(interval: int) -> float:
    """The penalty for a chord tone that is not sounding.

    Args:
        interval: the missing tone's interval above the root.

    Returns:
        float: 0.2 for the root, 0.1 for a fifth (perfect, diminished or
        augmented), 0.15 for anything else.
    """
    if interval == 0:
        cost = 0.2
    elif interval in (6, 7, 8):
        cost = 0.1
    else:
        cost = 0.15

    return cost


# Every template on every root, ready to score: the root, its pitch classes
# as a bit mask and in the template's order, the cost of each one being
# absent by pitch class, the template's own cost, and the chord's name.
_CANDIDATES = tuple(
    (root, sum(1 << ((root + i) % 12) for i in intervals),
     tuple((root + i) % 12 for i in intervals),
     {(root + i) % 12: _omissionCost(i) for i in intervals},
     RARE_CHORD_COST.get(suffix, 0.0), f"{PITCH_CLASS_NAMES[root]}{suffix}")
    for root in range(12) for suffix, intervals in CHORD_TEMPLATES)


def nameChord(profile: list[float], bass: int | None = None, threshold: float = 0.6) -> str:
    """Name the chord a pitch-class profile most resembles.

    Every template on every root is scored by the share of the weight it
    covers, less a cost per chord tone that is absent, plus a bonus when
    its root is in the bass; the best wins if it covers enough of the
    sound, otherwise there is no chord.

    Args:
        profile: twelve weights, C first.
        bass: pitch class of the lowest sounding note, or None. Two notes a
            tone apart over a G bass are G7 rather than a suspended C, and
            the bass is what says so.
        threshold: the minimum score a chord must reach.

    Returns:
        str: e.g. "C", "Am", "G7", "Bdim", or `NO_CHORD`.
    """
    total = sum(profile)

    if total <= 0:
        return NO_CHORD

    # A dyad fits too many templates for the bass to settle it sensibly, so
    # the bass only counts once there are three pitch classes to read.
    sounding = sum(1 for w in profile if w > 0)

    # One pitch class is no chord, but naming the note says more than
    # saying nothing; an octave of C is labelled C.
    if sounding == 1:
        name = PITCH_CLASS_NAMES[profile.index(max(profile))]
        return name

    useBass = bass is not None and sounding >= 3
    best = (-1.0, NO_CHORD)
    present = sum(1 << pc for pc, w in enumerate(profile) if w != 0)

    for root, mask, classes, omission, rare, name in _CANDIDATES:
        absent = mask & ~present

        # A chord may lack one of its tones but not two: a seventh is never
        # named from two notes.
        if absent & (absent - 1):
            continue

        covered = 0

        for pc in classes:
            covered += profile[pc]

        # Coverage less a cost for the absent tone that follows how
        # dispensable it is: a fifth is dropped all the time, a third less
        # often, a root rarely. C and A are therefore A minor without its
        # fifth rather than F without its root, and a bare fifth is C rather
        # than a seventh. The bass breaks ties between readings of a fuller
        # chord.
        score = covered / total - rare

        if absent:
            score -= omission[absent.bit_length() - 1]

        if useBass and root == bass:
            score += 0.2

        if score > best[0]:
            best = (score, name)

    name = best[1] if best[0] >= threshold else NO_CHORD
    return name


@dataclass
class StepIndex:
    """A song's notes in start order, arranged so a step finds its notes by bisection."""

    notes: list[model.Note]
    starts: list[int]       # each note's start, in the same order
    reach: list[int]        # the latest end among the notes up to and including each


def stepIndex(song: model.Song) -> StepIndex:
    """Index a song's notes for naming chords one step at a time.

    Args:
        song: the song.

    Returns:
        StepIndex: the notes sorted by start, their starts, and the running
        maximum of their ends.
    """
    notes = sorted(song.notes, key=lambda n: n.start)
    reach = list(accumulate((n.end for n in notes), max))
    index = StepIndex(notes, [n.start for n in notes], reach)
    return index


def chordAt(song: model.Song, index: StepIndex, t0: int, t1: int) -> str:
    """Name the chord in one step, from the notes sounding in it.

    The notes are found by bisection: the first that can still sound is the
    first whose running end reaches past the step's start, and the last is
    the last to start before its end. The bass is the lowest pitched note
    still sounding at the step's start or starting within it.

    Args:
        song: the song.
        index: the song's `stepIndex`.
        t0: the step's start tick.
        t1: its end tick.

    Returns:
        str: the chord name.
    """
    window = index.notes[bisect_right(index.reach, t0):bisect_left(index.starts, t1)]
    lowest = min((n for n in window if n.channel != model.DRUM_CHANNEL and n.end > t0),
                 key=lambda n: n.pitch, default=None)
    bass = lowest.pitch % 12 if lowest is not None else None
    name = nameChord(pitchClassProfile(song, t0, t1, window), bass)
    return name


def chordRuns(names: list[str], stepTicks: int, smooth: int = 1) -> list[tuple[int, int, str]]:
    """Merge a chord name per step into runs, absorbing short islands.

    Args:
        names: the chord in each step, from tick 0.
        stepTicks: the step's length.
        smooth: a run of at most this many steps sitting between two runs of
            the same name is absorbed into them, so a bass note on the
            downbeat does not split a bar of one chord into three labels.
            Zero keeps every step as named.

    Returns:
        list[tuple[int, int, str]]: (start tick, end tick, chord name) runs,
        consecutive equal names merged.
    """
    names = list(names)

    # Smoothing: a short island between two stretches of the same name
    # takes that name.
    if smooth > 0:
        i = 0

        while i < len(names):
            j = i

            while j < len(names) and names[j] == names[i]:
                j += 1

            # names[i:j] is one island; absorb it when it is short and both
            # neighbours agree.
            if 0 < i and j < len(names) and j - i <= smooth and names[i - 1] == names[j]:
                names[i:j] = [names[i - 1]] * (j - i)

            i = j

    runs: list[tuple[int, int, str]] = []

    for step, name in enumerate(names):
        start, end = step * stepTicks, (step + 1) * stepTicks

        if runs and runs[-1][2] == name:
            runs[-1] = (runs[-1][0], end, name)
        else:
            runs.append((start, end, name))

    return runs


def chordTrack(song: model.Song, stepTicks: int, smooth: int = 1) -> list[tuple[int, int, str]]:
    """Name the chord in each step of the piece and merge repeats.

    Args:
        song: the song.
        stepTicks: the analysis step, typically a beat or a bar.
        smooth: as `chordRuns` takes it.

    Returns:
        list[tuple[int, int, str]]: (start tick, end tick, chord name) runs
        covering the piece, consecutive equal names merged.
    """
    index = stepIndex(song)
    names = [chordAt(song, index, t, t + stepTicks) for t in range(0, song.maxTick, stepTicks)]
    runs = chordRuns(names, stepTicks, smooth)
    return runs


# ----------------------------------------------------------------- report
def instrumentsAtOnce(demand: Demand) -> list[int]:
    """Count the channels sounding in each segment.

    A note counts its channel once however many notes the channel has
    going, so this is the number of instruments playing together, which is
    what a target that can re-patch voices on the fly needs to know.

    Args:
        demand: a sweep.

    Returns:
        list[int]: one count per segment, parallel to `demand.ticks`.
    """
    columns = list(demand.channels.values())
    counts = [sum(1 for c in columns if c[i]) for i in range(len(demand.ticks))]
    return counts


def atLeast(counts: list[int], seconds: list[float]) -> dict[int, float]:
    """The share of sounding time at or above each count.

    Args:
        counts: a per-segment count series.
        seconds: the matching durations.

    Returns:
        dict[int, float]: for n from 1 to the peak, the fraction of sounding
        time with the count at least n.
    """
    peak = max(counts, default=0)
    sounding = sum(d for c, d in zip(counts, seconds) if c)
    shares: dict[int, float] = {}

    for n in range(1, peak + 1):
        above = sum(d for c, d in zip(counts, seconds) if c >= n)
        shares[n] = above / sounding if sounding else 0.0

    return shares


def _table(rows: list[list[str]], align: str) -> list[str]:
    """Lay out rows as fixed-width text columns.

    Args:
        rows: the header row followed by data rows; every row the same length.
        align: one character per column, "l" or "r".

    Returns:
        list[str]: the lines, with a rule under the header.
    """
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines: list[str] = []

    for k, row in enumerate(rows):
        cells = [c.rjust(w) if a == "r" else c.ljust(w)
                 for c, w, a in zip(row, widths, align)]
        lines.append("  ".join(cells).rstrip())

        if k == 0:
            lines.append("  ".join("-" * w for w in widths))

    return lines


def report(song: model.Song, instrumentNames: dict[int, str] | None = None) -> str:
    """Write the file's analysis as plain text.

    Args:
        song: the song.
        instrumentNames: a display name per channel, as the channel strip
            shows them; channel numbers alone when omitted.

    Returns:
        str: the report, ASCII, fixed-width tables.
    """
    demand = sweep(song)
    names = instrumentNames or {}
    out: list[str] = []
    name = song.path.rsplit("/", 1)[-1] if song.path else "untitled"
    withNotes = sorted({n.channel for n in song.notes})
    tempos = [60e6 / u for _, u, _ in song.tempoMap]
    key = songKey(song)
    out += [f"MontyRoll report: {name}", "",
            "How to read this report", "-----------------------",
            "",
            "File",
            "  Format, tracks, resolution, note and channel counts, length, time",
            "  signatures, the number of tempo events and their range, and the",
            "  estimated key.",
            "",
            "Voices sounding at once",
            "  Simultaneous notes, counted three ways: every note as written;",
            "  distinct pitches, so a unison on two channels counts once; distinct",
            "  pitch classes, so an octave doubling counts once too. Peak and mean",
            "  are over the time anything sounds, and each 'more than N' column is",
            "  the share of that time with the count above N.",
            "",
            "Instruments sounding at once",
            "  Channels with a note sounding, counted together however many notes",
            "  each has. Peak and mean, then the share of sounding time with at",
            "  least N channels playing, for every N up to the peak.",
            "",
            "Channels",
            "  Per channel: the instrument, note count, pitch range as MIDI note",
            "  numbers, most notes at once, mean notes while sounding, and the share",
            "  of the piece during which the channel sounds.",
            "",
            "Doubling",
            "  Every note's duration summed, divided into the share that is a unison",
            "  of a pitch already sounding on another channel, an octave of one, the",
            "  same pitch already held on the same channel, and the remainder.",
            "",
            "Channels playing together",
            "  For channels sounding at least a tenth of the piece, the share of the",
            "  piece during which both channels of a pair sound: the eight pairs",
            "  that coincide least and the eight that coincide most.",
            "",
            "Busiest moments",
            "  The five bars with the most simultaneous notes: the bar, the time, the",
            "  three counts at that moment, and each channel sounding with its note",
            "  count, as 16(6) for six notes on channel 16.",
            "",
            "File", "----",
            f"format {song.mf.format}, {len(song.mf.tracks)} tracks, "
            f"{song.mf.division} ticks per quarter",
            f"{len(song.notes)} notes on {len(withNotes)} channels, "
            f"{song.barBeat(song.maxTick)[0]} bars, {song.duration:.1f} s",
            "time signatures: " + ", ".join(f"{n}/{d} at bar {song.barBeat(t)[0]}"
                                             for t, n, d in song.timeSigs),
            f"tempo: {len(song.tempoMap)} event(s), {min(tempos):.0f} to {max(tempos):.0f} bpm",
            f"key: {key.name if key else 'none'}", ""]

    if not song.notes:
        out += ["No notes.", ""]
        return "\n".join(out) + "\n"

    # Demand: the three counts with the threshold columns.
    out += ["Voices sounding at once", "-----------------------",
            "Over the time anything sounds; 'more than N' is the share of that time",
            "with the count above N.", ""]
    rows = [["count of", "peak", "mean"] + [f">{k}" for k in THRESHOLDS]]

    for label, series in (("notes as written", demand.raw),
                          ("distinct pitches", demand.pitches),
                          ("distinct pitch classes", demand.classes)):
        st = stats(series, demand.seconds)
        rows.append([label, str(st.peak), f"{st.mean:.1f}"]
                    + [f"{st.above[k] * 100:.0f}%" for k in THRESHOLDS])

    out += _table(rows, "lrr" + "r" * len(THRESHOLDS)) + [""]

    # Instruments at once.
    together = instrumentsAtOnce(demand)
    togetherStats = stats(together, demand.seconds, ())
    shares = atLeast(together, demand.seconds)
    out += ["Instruments sounding at once", "----------------------------",
            f"peak {togetherStats.peak} channels, mean {togetherStats.mean:.1f} while anything "
            "sounds.", "Share of sounding time with at least N channels playing:", ""]
    rows = [["channels", "at least"]] + [[str(n), f"{f * 100:.0f}%"] for n, f in shares.items()]
    out += _table(rows, "rr") + [""]

    # Per channel.
    figures = channelDemand(demand)
    out += ["Channels", "--------"]
    rows = [["ch", "instrument", "notes", "range", "peak", "mean", "plays"]]

    for ch in withNotes:
        info = song.channels[ch]
        f = figures[ch]
        rows.append([str(ch + 1), names.get(ch, ""), str(info.noteCount),
                     f"{info.lo}-{info.hi}", str(f.peak), f"{f.mean:.1f}",
                     f"{f.fraction * 100:.0f}%"])

    out += _table(rows, "rlrrrrr") + [""]

    # Doubling.
    d = doubling(demand)
    out += ["Doubling", "--------",
            "Every note's duration summed, divided by kind:", ""]
    rows = [["kind", "share"],
            ["unison with another channel", f"{d.unison / d.total * 100:.0f}%"],
            ["octave of another channel", f"{d.octave / d.total * 100:.0f}%"],
            ["same channel, same pitch", f"{d.sameChannel / d.total * 100:.0f}%"],
            ["harmonically distinct", f"{d.distinct / d.total * 100:.0f}%"]]
    out += _table(rows, "lr") + [""]

    # Co-activity among channels that play at least a tenth of the piece.
    busy = [ch for ch in withNotes if figures[ch].fraction >= 0.1]
    pairs = [(a, b, sec) for (a, b), sec in coActivity(demand).items()
             if a in busy and b in busy]

    if pairs:
        ordered = sorted(pairs, key=lambda p: p[2])
        out += ["Channels playing together", "-------------------------",
                "Share of the piece during which both channels sound, for channels that",
                "play at least a tenth of it.", ""]
        rows = [["least together", "share", "", "most together", "share"]]

        for (a, b, sec), (c, d2, sec2) in zip(ordered[:8], ordered[::-1][:8]):
            rows.append([f"ch {a + 1} + ch {b + 1}", f"{sec / demand.length * 100:.0f}%", "",
                         f"ch {c + 1} + ch {d2 + 1}", f"{sec2 / demand.length * 100:.0f}%"])

        out += _table(rows, "lrllr") + [""]

    # Busiest moments.
    out += ["Busiest moments", "---------------"]
    byBar: dict[int, tuple[int, int]] = {}

    for i, tick in enumerate(demand.ticks):
        bar = song.barBeat(tick)[0]

        if demand.raw[i] > byBar.get(bar, (0, 0))[0]:
            byBar[bar] = (demand.raw[i], i)

    rows = [["bar", "time", "notes", "pitches", "classes", "channels"]]

    for bar, (count, i) in sorted(byBar.items(), key=lambda kv: (-kv[1][0], kv[0]))[:5]:
        tick = demand.ticks[i]
        secs = song.tickToSeconds(tick)
        playing = [f"{ch + 1}({c[i]})" for ch, c in sorted(demand.channels.items()) if c[i]]
        rows.append([str(bar), f"{int(secs) // 60}:{secs % 60:04.1f}", str(count),
                     str(demand.pitches[i]), str(demand.classes[i]), " ".join(playing)])

    out += _table(rows, "rrrrrl") + [""]
    text = "\n".join(out) + "\n"
    return text
