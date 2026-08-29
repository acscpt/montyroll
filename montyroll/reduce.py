# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Reduce a song to a fixed voice budget.

A `Plan` divides the song's channels into `Pool`s, each with a number of
voices and a set of transforms, and the pools into `Tier`s, one per chip,
each with a capacity, a reserve and the release its patches need; `reduce`
applies the transforms to each pool's notes, allocates what remains to the
pool's voices, and returns a `Reduction`: every note that sounds with the
voice it plays on, every note that was dropped or cut short, and the
musical cost of those losses. A voice stays busy through its note's
release. The song itself is never modified; the reduction is a view over it,
and `toMidiFile` builds a playable copy for audition or saving. A plan is
saved as a `.monty` file, JSON that names the MIDI file it belongs to or
embeds it outright (`savePlan`, `readPlanFile`, `sidecarPath`).

Every decision rests on a weight per note (see `weights`), so a note is
dropped because of a number the user can inspect and steer through the
per-channel priorities. The module knows nothing about tkinter.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Any

from . import model, smf

RELEASE_WEIGHT = 0.25   # a voice in its release counts this much of its note's weight
PLAN_VERSION = 1        # written into every plan file
PLAN_SUFFIX = ".monty"


@dataclass
class Tier:
    """A chip: the hardware facts its pools share.

    `capacity` is the chip's channel count and `reserved` how many of those
    are kept for something other than music, taken off the top of the
    range; the pools naming the tier may not add up to more than the rest.
    `release` is the default time in milliseconds a voice stays busy after
    its note ends, which is the release of the patches the tier carries.
    `chip` names the hardware for the export: "opm" or "psg" on the X16,
    empty for a tier with no export target.
    """

    name: str
    capacity: int
    reserved: int = 0
    release: float = 0.0
    chip: str = ""

    @property
    def voices(self) -> int:
        """Channels available to music: the capacity less the reserve."""
        available = self.capacity - self.reserved
        return available


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
    tier: str = ""                # the Tier the voices come from; none for a plain pool


@dataclass
class Plan:
    """The whole reduction: tiers, pools, and the per-channel steering.

    A priority scales every weight on that channel; 1.0 is neutral, 2.0
    makes the channel twice as hard to steal from, 0.5 half as hard. A
    release, in milliseconds, overrides the tier's default for a channel
    whose patch differs from the tier's.
    """

    pools: list[Pool] = field(default_factory=list)
    priority: dict[int, float] = field(default_factory=dict)
    tiers: list[Tier] = field(default_factory=list)
    release: dict[int, float] = field(default_factory=dict)

    def priorityOf(self, channel: int) -> float:
        """The priority for a channel, 1.0 when none is set.

        Args:
            channel: 0-based channel.

        Returns:
            float: the scale factor.
        """
        value = self.priority.get(channel, 1.0)
        return value

    def tierOf(self, name: str) -> Tier | None:
        """Find a tier by name.

        Args:
            name: the tier's name.

        Returns:
            Tier | None: the tier, or None when no tier has that name.
        """
        found = next((t for t in self.tiers if t.name == name), None)
        return found

    def releaseOf(self, channel: int, pool: Pool) -> float:
        """The release for a channel's notes in a pool, in milliseconds.

        The channel's own setting wins; otherwise the pool's tier's default;
        otherwise none.

        Args:
            channel: 0-based channel.
            pool: the pool the channel is placed through.

        Returns:
            float: milliseconds a voice stays busy after a note ends.
        """
        tier = self.tierOf(pool.tier)
        fallback = tier.release if tier is not None else 0.0
        ms = self.release.get(channel, fallback)
        return ms

    def validate(self) -> None:
        """Check that the pools fit their tiers.

        Raises:
            ValueError: two tiers share a name, a tier reserves more than
                it has, a pool names a tier the plan does not have, or a
                tier's pools want more voices than it has after the reserve.
        """
        names = [t.name for t in self.tiers]

        if len(set(names)) != len(names):
            raise ValueError("tier names must be unique")

        for tier in self.tiers:
            if tier.reserved > tier.capacity:
                raise ValueError(f"tier {tier.name}: reserves {tier.reserved} of "
                                 f"{tier.capacity} channels")

            wanted = sum(p.voices for p in self.pools if p.tier == tier.name)

            if wanted > tier.voices:
                raise ValueError(f"tier {tier.name}: pools want {wanted} voices, "
                                 f"{tier.voices} available")

        for pool in self.pools:
            if pool.tier and pool.tier not in names:
                raise ValueError(f"pool {pool.name}: no tier named {pool.tier}")

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
    the original's after legato, a top-line re-strike or a cut. `release`
    is how long the voice stays busy after `end`, in ticks, so `free` is
    when it can take another note. Identity hashing, as for `Note`, because
    these go into sets.
    """

    note: model.Note
    start: int
    end: int
    pitch: int
    velocity: int
    channel: int
    weight: float
    voice: int = -1
    release: int = 0

    @property
    def length(self) -> int:
        """Duration in ticks."""
        ticks = self.end - self.start
        return ticks

    @property
    def free(self) -> int:
        """The tick the voice is free again: the end plus the release."""
        tick = self.end + self.release
        return tick


@dataclass
class Reduction:
    """What a plan makes of a song.

    `placed` are the notes that sound, each with its voice. `dropped` are
    original notes that never sound at all, with the reason. `cut` maps an
    original note to the tick it was silenced at, when stealing ended it
    early. `clipped` maps an original note to the release ticks a steal took
    from its tail, priced at `RELEASE_WEIGHT`. `loss` is the sum over
    dropped, cut and clipped material of weight times ticks lost, per
    channel and overall, so two plans can be compared. `tierBase` is the
    first voice number of each tier, whose pools are numbered as one
    contiguous range.
    """

    placed: list[Placed] = field(default_factory=list)
    dropped: dict[int, str] = field(default_factory=dict)        # id(note) -> reason
    cut: dict[int, int] = field(default_factory=dict)            # id(note) -> new end
    clipped: dict[int, int] = field(default_factory=dict)        # id(note) -> tail ticks lost
    lossByChannel: dict[int, float] = field(default_factory=dict)
    tierBase: dict[str, int] = field(default_factory=dict)       # tier name -> first voice
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
    sound. Drum-channel notes pass through untouched.

    Args:
        placed: the pool's notes.
        reduction: records the drops.

    Returns:
        list[Placed]: the survivors.
    """
    byKey: dict[tuple[int, int], Placed] = {}
    survivors: list[Placed] = []

    for p in placed:
        # A drum key is a sound, not a pitch: two parts on one key are two
        # sounds, and a drum never doubles a pitched note.
        if p.channel == model.DRUM_CHANNEL:
            survivors.append(p)
            continue

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
                                     p.weight, release=p.release)
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
                                         top.channel, top.weight, release=top.release)
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
def releaseTicks(song: model.Song, tick: int, ms: float) -> int:
    """Convert a release time to ticks at a point in the song.

    A release belongs to a patch and so is a length of time; how many
    ticks that is depends on the tempo in force, which is why it is
    converted per note rather than once.

    Args:
        song: for the tempo map.
        tick: the note's end, where the release starts.
        ms: the release in milliseconds.

    Returns:
        int: the release in ticks, at least 0.
    """
    seconds = song.tickToSeconds(tick) + ms / 1000
    ticks = max(0, round(song.secondsToTick(seconds) - tick))
    return ticks


def _cost(p: Placed, tick: int) -> float:
    """What silencing a placed note at a tick loses.

    Args:
        p: the note, sounding or in its release.
        tick: the tick of the steal.

    Returns:
        float: the weight times the sound remaining, plus the release
        remaining at `RELEASE_WEIGHT`.
    """
    sounding = max(p.end - tick, 0)
    tail = p.free - max(tick, p.end)
    cost = p.weight * (sounding + RELEASE_WEIGHT * tail)
    return cost


def allocate(placed: list[Placed], voices: int, base: int, reduction: Reduction) -> list[Placed]:
    """Give each note a voice, stealing the cheapest when the pool is full.

    A voice is busy from its note's onset to `free`, the end plus the
    release. At an onset with no free voice the candidate to silence is
    the active note with the lowest cost: its weight times the sound
    remaining, plus the release remaining at `RELEASE_WEIGHT`. If the new
    note is itself worth less than every active one it is not started at
    all, which is the lookahead that keeps a note from starting only to be
    stolen a moment later. A stolen note is cut at the onset and whatever
    remained of its release is recorded as clipped. The same pitch struck
    again on a channel whose voice is still releasing takes that voice
    back: a retrigger, not a steal, at no cost. Among free voices, one that
    last played the note's channel is preferred, so a part keeps its voices
    where it can.

    Args:
        placed: the pool's notes after its transforms.
        voices: the pool's voice count.
        base: the first voice number of the pool.
        reduction: records drops, cuts and clipped releases.

    Returns:
        list[Placed]: the notes that sound, with `voice` set.
    """
    events: list[tuple[int, int, Placed]] = []

    for p in placed:
        events.append((p.start, 1, p))
        events.append((p.free, 0, p))

    events.sort(key=lambda e: (e[0], e[1], -e[2].weight))
    free = list(range(base, base + voices))
    active: dict[int, Placed] = {}
    lastChannel: dict[int, int] = {}
    out: list[Placed] = []

    for tick, on, p in events:
        if not on:
            if p.voice >= 0 and active.get(p.voice) is p:
                del active[p.voice]
                free.append(p.voice)
                free.sort()

            continue

        # The channel's own pitch, still releasing: keep its voice.
        again = next((v for v, a in active.items()
                      if a.channel == p.channel and a.pitch == p.pitch and a.end <= tick),
                     None)

        if again is not None:
            p.voice = again
            active[again] = p
            out.append(p)
            continue

        if free:
            same = [v for v in free if lastChannel.get(v) == p.channel]
            p.voice = same[0] if same else free[0]
            free.remove(p.voice)
            active[p.voice] = p
            lastChannel[p.voice] = p.channel
            out.append(p)
            continue

        # Full: weigh the newcomer against the cheapest of what is playing.
        victimVoice = min(active, key=lambda v: _cost(active[v], tick))
        victim = active[victimVoice]
        newcomer = p.weight * (p.end - p.start)

        if newcomer <= _cost(victim, tick):
            reduction.dropped[id(p.note)] = "no voice"
            continue

        # A steal takes what remains: the sound, if the victim is still
        # playing, and the rest of its release either way.
        tail = victim.free - max(tick, victim.end)

        if tail > 0:
            reduction.clipped[id(victim.note)] = tail

        if tick < victim.end:
            victim.end = tick
            reduction.cut[id(victim.note)] = tick

        p.voice = victimVoice
        active[victimVoice] = p
        lastChannel[victimVoice] = p.channel
        out.append(p)

    # A note stolen at its own onset never sounded: that is a drop, not a
    # cut, and it is not written out.
    sounding: list[Placed] = []

    for p in out:
        if p.end > p.start:
            sounding.append(p)
        else:
            reduction.cut.pop(id(p.note), None)
            reduction.clipped.pop(id(p.note), None)
            reduction.dropped[id(p.note)] = "no voice"

    return sounding


# -------------------------------------------------------------- plan file
@dataclass
class PlanFile:
    """A plan as read from a `.monty` file, with the MIDI it belongs to.

    `midiFile` is the path the file names, made absolute against the
    file's own directory when it is relative, whether or not it exists;
    `midiData` is the MIDI embedded in the file, its checksum verified.
    Either or both may be None. `targets` is each export target's own
    section, keyed by the target's name, kept as it was read. `range` is
    the export's bars: `start`, `end` and `loop`, 1-based as the ruler
    shows them, each absent when not set.
    """

    plan: Plan
    midiFile: str | None = None
    midiData: bytes | None = None
    targets: dict[str, Any] = field(default_factory=dict)
    range: dict[str, int] = field(default_factory=dict)


def sidecarPath(path: str) -> str:
    """The plan file that belongs beside a MIDI file.

    Args:
        path: the MIDI file's path.

    Returns:
        str: the same path with `.monty` in place of the extension.
    """
    base, _ = os.path.splitext(path)
    sidecar = base + PLAN_SUFFIX
    return sidecar


def planToDict(plan: Plan, midiFile: str = "", midiData: bytes | None = None,
               targets: dict[str, Any] | None = None,
               exportRange: dict[str, int] | None = None) -> dict[str, Any]:
    """Serialise a plan.

    Channels are written 1-based, as the channel strip shows them, because
    the file is meant to be read and tuned by hand. Neutral priorities are
    left out. The MIDI file is named under `midiFile`, a path taken as
    relative to the plan file's directory unless absolute, and embedded
    under `midiBlob` as base64 with its SHA-256 when its bytes are given.
    Each export target's own section goes under `targets` by its name, and
    the export's bars under `range`.

    Args:
        plan: the plan.
        midiFile: the MIDI file's path or name, empty to name none.
        midiData: the MIDI file's bytes to embed, None to embed none.
        targets: per-target sections, such as a ZSM target's patches.
        exportRange: `start`, `end` and `loop` bars, those that are set.

    Returns:
        dict[str, Any]: JSON-ready data with a version number.
    """
    data: dict[str, Any] = {
        "version": PLAN_VERSION,
        "tiers": [{"name": t.name, "capacity": t.capacity, "reserved": t.reserved,
                   "release": t.release, "chip": t.chip} for t in plan.tiers],
        "pools": [{"name": p.name, "tier": p.tier, "voices": p.voices,
                   "channels": [ch + 1 for ch in p.channels],
                   "dedupUnison": p.dedupUnison, "dedupOctave": p.dedupOctave,
                   "tremoloGap": p.tremoloGap, "legatoGap": p.legatoGap,
                   "monoTop": p.monoTop, "maxChord": p.maxChord} for p in plan.pools],
        "priority": {str(ch + 1): v for ch, v in sorted(plan.priority.items()) if v != 1.0},
        "release": {str(ch + 1): v for ch, v in sorted(plan.release.items())},
    }

    if midiFile:
        data["midiFile"] = midiFile

    if midiData is not None:
        data["midiBlob"] = {"sha256": hashlib.sha256(midiData).hexdigest(),
                            "base64": base64.b64encode(midiData).decode("ascii")}

    if targets:
        data["targets"] = targets

    if exportRange:
        data["range"] = exportRange

    return data


def _channelIndex(value: Any, source: str) -> int:
    """Turn a 1-based channel number from a plan file into a 0-based index.

    Args:
        value: the number as read.
        source: the file, for the error.

    Returns:
        int: 0 to 15.

    Raises:
        ValueError: not a channel number.
    """
    if not isinstance(value, int) or not 1 <= value <= 16:
        raise ValueError(f"{source}: channel {value!r} is not 1 to 16")

    index = value - 1
    return index


def planFromDict(data: dict[str, Any], source: str = "plan") -> Plan:
    """Rebuild a plan from its serialised form.

    Keys the plan does not know are ignored, so a file may carry other
    sections; a missing switch takes the `Pool` default. The plan is
    validated before it is returned.

    Args:
        data: as `planToDict` wrote it.
        source: the file, named in any error.

    Returns:
        Plan: the plan, channels 0-based again.

    Raises:
        ValueError: the data is not a plan, or its pools do not fit its
            tiers.
    """
    try:
        tiers = [Tier(str(t["name"]), int(t["capacity"]), int(t.get("reserved", 0)),
                      float(t.get("release", 0.0)), str(t.get("chip", "")))
                 for t in data.get("tiers", [])]
        pools = [Pool(str(p["name"]),
                      [_channelIndex(ch, source) for ch in p["channels"]],
                      int(p["voices"]),
                      dedupUnison=bool(p.get("dedupUnison", True)),
                      dedupOctave=bool(p.get("dedupOctave", False)),
                      tremoloGap=int(p.get("tremoloGap", 0)),
                      legatoGap=int(p.get("legatoGap", 0)),
                      monoTop=bool(p.get("monoTop", False)),
                      maxChord=int(p.get("maxChord", 0)),
                      tier=str(p.get("tier", ""))) for p in data.get("pools", [])]
        priority = {_channelIndex(int(ch), source): float(v)
                    for ch, v in data.get("priority", {}).items()}
        release = {_channelIndex(int(ch), source): float(v)
                   for ch, v in data.get("release", {}).items()}
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"{source}: not a plan ({exc!r})") from exc

    plan = Plan(pools, priority, tiers, release)

    try:
        plan.validate()
    except ValueError as exc:
        raise ValueError(f"{source}: {exc}") from exc

    return plan


def savePlan(plan: Plan, path: str, midiFile: str = "", midiData: bytes | None = None,
             targets: dict[str, Any] | None = None,
             exportRange: dict[str, int] | None = None) -> None:
    """Write a plan as a `.monty` file.

    Args:
        plan: the plan.
        path: where to write it, normally `sidecarPath` of the MIDI file.
        midiFile: the MIDI file's path or name to record, empty for none.
        midiData: the MIDI file's bytes to embed, None to embed none.
        targets: per-target sections to carry.
        exportRange: the export's bars to carry.
    """
    with open(path, "w", encoding="utf-8") as f:
        json.dump(planToDict(plan, midiFile, midiData, targets, exportRange), f, indent=2)
        f.write("\n")


def readPlanFile(path: str) -> PlanFile:
    """Read a `.monty` file: the plan and the MIDI it names or embeds.

    Args:
        path: the plan file.

    Returns:
        PlanFile: the plan, the named MIDI file's path made absolute
        against the plan file's directory, and the embedded MIDI bytes.

    Raises:
        ValueError: the file is not JSON, not a plan, its pools do not fit
            its tiers, or the embedded MIDI is malformed or does not match
            its checksum; the message carries the path.
        OSError: the file cannot be read.
    """
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}: not JSON ({exc})") from exc

    if not isinstance(data, dict):
        raise ValueError(f"{path}: not a plan")

    result = PlanFile(planFromDict(data, path))
    named = data.get("midiFile")

    if named:
        folder = os.path.dirname(os.path.abspath(path))
        result.midiFile = named if os.path.isabs(named) else os.path.join(folder, named)

    blob = data.get("midiBlob")

    if blob is not None:
        try:
            raw = base64.b64decode(blob["base64"], validate=True)
            digest = blob["sha256"]
        except (KeyError, TypeError, binascii.Error) as exc:
            raise ValueError(f"{path}: the embedded MIDI is malformed ({exc!r})") from exc

        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError(f"{path}: the embedded MIDI does not match its checksum")

        result.midiData = raw

    targets = data.get("targets", {})

    if not isinstance(targets, dict):
        raise ValueError(f"{path}: targets is not a table")

    result.targets = targets
    bars = data.get("range", {})

    try:
        result.range = {k: int(bars[k]) for k in ("start", "end", "loop") if bars.get(k)}
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{path}: range is not bars ({exc!r})") from exc

    return result


def loadPlan(path: str) -> Plan:
    """Read the plan from a `.monty` file.

    Args:
        path: the plan file.

    Returns:
        Plan: the plan.

    Raises:
        ValueError: as `readPlanFile`.
        OSError: the file cannot be read.
    """
    plan = readPlanFile(path).plan
    return plan


# ------------------------------------------------------------------ drive
def reduce(song: model.Song, plan: Plan) -> Reduction:
    """Apply a plan to a song.

    Args:
        song: the song; never modified.
        plan: the tiers, pools and priorities.

    Returns:
        Reduction: the result, with voices numbered as one contiguous range
        per tier in tier order, plan order within a tier, and the pools
        with no tier last.

    Raises:
        ValueError: the plan's pools do not fit its tiers.
    """
    plan.validate()
    weight = weights(song, plan)
    reduction = Reduction()
    base = 0

    # Pools in tier order, so each tier's voices are one range starting at
    # its base; pools with no tier follow.
    ordered: list[Pool] = []
    offset = 0

    for tier in plan.tiers:
        reduction.tierBase[tier.name] = offset
        mine = [p for p in plan.pools if p.tier == tier.name]
        ordered.extend(mine)
        offset += sum(p.voices for p in mine)

    ordered.extend(p for p in plan.pools if not p.tier)

    for pool in ordered:
        members = set(pool.channels)
        placed = _place([n for n in song.notes if n.channel in members], weight)

        # A release is a time; it becomes ticks where each note ends.
        for p in placed:
            ms = plan.releaseOf(p.channel, pool)

            if ms > 0:
                p.release = releaseTicks(song, p.end, ms)

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

    # Loss: weight times ticks for every dropped note and every cut tail,
    # and at RELEASE_WEIGHT for every clipped release.
    for n in song.notes:
        lost = 0.0

        if id(n) in reduction.dropped:
            lost = n.length
        elif id(n) in reduction.cut:
            lost = n.end - reduction.cut[id(n)]

        lost += RELEASE_WEIGHT * reduction.clipped.get(id(n), 0)

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
