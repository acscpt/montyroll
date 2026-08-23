# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Editable song model built on top of the raw SMF event lists.

A Song wraps a parsed MidiFile and derives the views the editor and player
work from: a flat list of Note objects, a summary per channel, the tempo map,
the time signatures and the markers.  Notes are paired note-on/note-off views
over the underlying events, so editing a Note and applying it mutates those
events in place and saving re-serialises the same tracks, which preserves
everything the editor does not understand (controllers, sysex, lyrics and so
on).

Every tick/seconds conversion goes through the tempo map; nothing here assumes
a constant tempo.  This module knows nothing about tkinter or about how the
song is played.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field

from . import gm, smf

DRUM_CHANNEL = 9
DEFAULT_TEMPO = 500_000  # us per quarter = 120 bpm
MIN_TEMPO_USPB = 60_000  # 1000 bpm, the fastest a tempo edit will write
MAX_TEMPO_USPB = 0xFFFFFF  # the three-byte limit, about 3.6 bpm


def _tempoBytes(bpm: float) -> bytes:
    """Encode a tempo as the three-byte microseconds-per-quarter of a meta event.

    Args:
        bpm: quarter notes per minute.

    Returns:
        bytes: three bytes, clamped to the representable range.
    """
    uspb = round(60_000_000 / max(bpm, 1e-9))
    clamped = max(MIN_TEMPO_USPB, min(MAX_TEMPO_USPB, uspb))
    encoded = clamped.to_bytes(3, "big")
    return encoded


@dataclass(eq=False)
class Note:
    """A single note as a view over its note-on and note-off events.

    The fields are a working copy of what the two events carry; editing them
    changes nothing in the file until apply() pushes the values back into the
    events.  A note built by Song.rebuild() always has an onEvent, while
    offEvent is None for a note that was still sounding at the end of its
    track.

    eq=False is deliberate: notes live in selection sets and dict keys, which
    needs identity-based hashing.  The default dataclass equality would make
    instances unhashable and break selection.
    """
    track: int
    channel: int
    pitch: int
    velocity: int
    start: int                    # ticks
    end: int                      # ticks
    onEvent: smf.Event | None = None
    offEvent: smf.Event | None = None

    @property
    def length(self) -> int:
        """int: duration of the note in ticks."""
        return self.end - self.start

    def apply(self) -> None:
        """Push pitch, velocity and tick positions back into the underlying events.

        The note-off only takes the pitch; its release velocity is left as the
        file had it.
        """
        if self.onEvent is not None:
            self.onEvent.tick = self.start
            self.onEvent.data[0] = self.pitch
            self.onEvent.data[1] = self.velocity

        if self.offEvent is not None:
            self.offEvent.tick = self.end
            self.offEvent.data[0] = self.pitch


@dataclass
class ChannelInfo:
    """Summary of one MIDI channel, gathered by Song.rebuild().

    program is the program change in force when the channel's first note
    sounds and volume is the first CC7 seen, which is what the channel strip
    displays; volume is -1 when the file carries no CC7 and the synth's GM
    default of 100 applies.  tracks holds every track index with events on
    the channel, since several tracks can share one channel.
    """
    channel: int
    program: int = 0
    volume: int = -1              # first CC7 seen; -1 = none (GM default 100)
    noteCount: int = 0
    lo: int = 127
    hi: int = 0
    tracks: set[int] = field(default_factory=set)
    controllers: set[int] = field(default_factory=set)
    hasPitchBend: bool = False

    @property
    def isDrums(self) -> bool:
        """bool: whether this is the GM percussion channel (shown as Ch 10)."""
        return self.channel == DRUM_CHANNEL

    @property
    def instrument(self) -> str:
        """str: display name of the instrument, "Drum Kit" on the drum channel."""
        if self.isDrums:
            return "Drum Kit"

        name = gm.programName(self.program)
        return name


class Song:
    """A MidiFile together with the derived views the editor and player use.

    The MidiFile is the single source of truth: notes, channel summaries, the
    tempo map, time signatures and markers are all derived from its event
    lists by rebuild(), and every edit goes back into those lists so that
    save() writes exactly what the file contained plus the edits.  dirty
    records whether there are unsaved edits.
    """

    def __init__(self, mf: smf.MidiFile, path: str | None = None):
        """Wrap a parsed MidiFile and derive the editor views from it.

        Args:
            mf: the parsed file; it is held by reference and edited in place.
            path: file the song was loaded from, or None for a new song.
        """
        self.mf = mf
        self.path = path
        self.dirty = False
        self.notes: list[Note] = []
        self.channels: dict[int, ChannelInfo] = {}
        self.trackNames: list[str] = []
        self.tempoMap: list[tuple[int, int, float]] = []  # (tick, uspb, sec_at_tick)
        self.timeSigs: list[tuple[int, int, int]] = []    # (tick, num, denom)
        self.markers: list[tuple[int, str]] = []
        self.maxTick = 0
        self.rebuild()

    # ------------------------------------------------------------------ io
    @classmethod
    def load(cls, path: str) -> "Song":
        """Parse a Standard MIDI File from disk into a Song.

        Args:
            path: file to read.

        Returns:
            Song: the parsed song, with path recorded for a later save().

        Raises:
            ValueError: if the file is not an SMF or uses SMPTE time division.
            OSError: if the file cannot be read.
        """
        mf = smf.parse(path)
        song = cls(mf, path)
        return song

    @classmethod
    def new(cls) -> "Song":
        """Create an empty song with one track at 120 bpm in 4/4.

        Returns:
            Song: a format 1 song at 480 ticks per quarter note and no path.
        """
        # The single track carries just a tempo and a time signature (4/4, 24
        # clocks per metronome click, 8 demisemiquavers per quarter) so the
        # editor has a grid to draw before any notes exist.
        track = [
            smf.Event(0, smf.META, bytearray((500_000).to_bytes(3, "big")), smf.META_TEMPO),
            smf.Event(0, smf.META, bytearray([4, 2, 24, 8]), smf.META_TIME_SIG),
        ]
        mf = smf.MidiFile(format=1, division=480, tracks=[track])
        song = cls(mf)
        return song

    def save(self, path: str | None = None) -> None:
        """Serialise the file to disk and clear the dirty flag.

        Args:
            path: destination; defaults to the path the song was loaded from
                and becomes the song's path once written.

        Raises:
            ValueError: if no path is given and the song has none.
            OSError: if the file cannot be written.
        """
        path = path or self.path

        if not path:
            raise ValueError("no path to save to")

        smf.write(self.mf, path)
        self.path = path
        self.dirty = False

    # ------------------------------------------------------------- analysis
    def rebuild(self) -> None:
        """Recompute every derived view from the underlying event lists.

        Notes, channel summaries, track names, the tempo map, time signatures,
        markers and maxTick are all thrown away and rebuilt from self.mf in a
        single pass over the tracks.  It runs once from __init__; the editing
        methods keep the views up to date incrementally after that.
        """
        # Start from scratch; every derived view is recomputed below.
        self.notes = []
        self.channels = {}
        self.trackNames = ["" for _ in self.mf.tracks]
        self.timeSigs = []
        self.markers = []
        maxTick = 0

        for ti, track in enumerate(self.mf.tracks):
            # Note-ons waiting for their note-off, keyed by (channel, pitch).
            # A list per key copes with a pitch being re-struck before it is
            # released; each note-off closes the oldest open note first.
            pending: dict[tuple[int, int], list[Note]] = {}

            for e in track:
                maxTick = max(maxTick, e.tick)

                # Meta events feed the song-level tables.  Only the first
                # track name on a track is kept, and tempo and time signature
                # payloads are checked for length before they are decoded.
                if e.status == smf.META:
                    if e.metaType == smf.META_TRACK_NAME and not self.trackNames[ti]:
                        self.trackNames[ti] = e.data.decode("latin-1").strip()
                    elif e.metaType == smf.META_TIME_SIG and len(e.data) >= 2:
                        self.timeSigs.append((e.tick, e.data[0], 1 << e.data[1]))
                    elif e.metaType == smf.META_MARKER:
                        self.markers.append((e.tick, e.data.decode("latin-1").strip()))

                    continue

                # Sysex events carry no channel and contribute nothing further.
                ch = e.channel

                if ch < 0:
                    continue

                info = self.channels.setdefault(ch, ChannelInfo(ch))
                info.tracks.add(ti)
                kind = e.status & 0xF0

                # Program changes are recorded only until the channel's first
                # note, so info.program is the instrument actually in force
                # when the channel starts sounding.  The first CC7 seen is
                # taken as the channel volume.
                if kind == 0xC0:
                    if info.noteCount == 0:
                        info.program = e.data[0]
                elif kind == 0xB0:
                    info.controllers.add(e.data[0])
                    if e.data[0] == 7 and info.volume < 0:
                        info.volume = e.data[1]
                elif kind == 0xE0:
                    info.hasPitchBend = True
                elif e.isNoteOn:
                    # A note-on opens a Note whose end is provisionally its
                    # start; the matching note-off supplies the real end.
                    note = Note(ti, ch, e.data[0], e.data[1], e.tick, e.tick, e)
                    pending.setdefault((ch, e.data[0]), []).append(note)
                    self.notes.append(note)
                    info.noteCount += 1
                    info.lo = min(info.lo, e.data[0])
                    info.hi = max(info.hi, e.data[0])
                elif e.isNoteOff:
                    # Close the oldest open note of this pitch.  A zero-length
                    # note is stretched to one tick so it stays visible and
                    # editable on the roll.
                    stack = pending.get((ch, e.data[0]))

                    if stack:
                        note = stack.pop(0)
                        note.end = max(e.tick, note.start + 1)
                        note.offEvent = e

            # Notes still open at the end of the track never received a
            # note-off; they run to the furthest tick seen so far and keep
            # offEvent as None.
            for stack in pending.values():
                for note in stack:
                    note.end = max(maxTick, note.start + 1)

        self._rebuildTempoMap()

        # A file with no time signature is treated as 4/4 throughout.
        if not self.timeSigs:
            self.timeSigs = [(0, 4, 4)]

        self.timeSigs.sort()
        self.notes.sort(key=lambda n: (n.start, n.pitch))

        # An empty or very short file still gets at least sixteen beats of
        # roll to draw and edit in.
        self.maxTick = max(maxTick, self.mf.division * 16)

    # ---------------------------------------------------------------- time
    def tickToSeconds(self, tick: float) -> float:
        """Convert a tick position to seconds through the tempo map.

        Args:
            tick: absolute tick; fractional values and ticks past the last
                event are fine.

        Returns:
            float: seconds from the start of the song.
        """
        # Find the tempo segment in force at tick and extrapolate from its
        # start time; index 0 also serves any tick before the first entry.
        i = bisect_right([t for t, _, _ in self.tempoMap], tick) - 1
        t0, uspb, sec = self.tempoMap[max(i, 0)]
        return sec + (tick - t0) * uspb / 1e6 / self.mf.division

    def secondsToTick(self, seconds: float) -> float:
        """Convert a time in seconds to a tick position through the tempo map.

        Args:
            seconds: time from the start of the song.

        Returns:
            float: the corresponding absolute tick, fractional.
        """
        # Find the tempo segment whose start time precedes seconds and
        # extrapolate from its start tick; index 0 also serves negative times.
        i = bisect_right([s for _, _, s in self.tempoMap], seconds) - 1
        t0, uspb, sec = self.tempoMap[max(i, 0)]
        return t0 + (seconds - sec) * 1e6 * self.mf.division / uspb

    def tempoAt(self, tick: float) -> float:
        """Look up the tempo in force at a tick.

        Args:
            tick: absolute tick.

        Returns:
            float: tempo in beats per minute from the tempo map.
        """
        i = bisect_right([t for t, _, _ in self.tempoMap], tick) - 1
        uspb = self.tempoMap[max(i, 0)][1]
        bpm = 60e6 / uspb
        return bpm

    @property
    def duration(self) -> float:
        """float: length of the song in seconds, up to maxTick."""
        seconds = self.tickToSeconds(self.maxTick)
        return seconds

    def initialBpm(self) -> float:
        """Read the tempo the song starts at.

        Returns:
            float: tempo of the first tempo map entry in beats per minute.
        """
        return 60e6 / self.tempoMap[0][1]

    def barBeat(self, tick: float) -> tuple[int, int]:
        """Locate a tick as a 1-based (bar, beat) pair.

        Time signature changes are honoured: the span of each signature is
        measured in bars of its own length, so a 3/4 passage followed by 4/4
        numbers its bars correctly on both sides of the change.

        Args:
            tick: absolute tick, fractional allowed.

        Returns:
            tuple[int, int]: (bar, beat), both counted from 1.
        """
        bar = 1
        prev_tick = 0
        num, denom = 4, 4

        # Walk the signatures up to tick, adding the whole bars each
        # completed signature span contains at that signature's bar length.
        for ts_tick, ts_num, ts_denom in self.timeSigs:
            if ts_tick > tick:
                break

            bar += (int((ts_tick - prev_tick) // (num * self.mf.division * 4 // denom))
                    if ts_tick else 0)
            prev_tick, num, denom = ts_tick, ts_num, ts_denom

        # Position within the signature in force: whole bars, then the beat
        # within the bar.
        beats = (tick - prev_tick) / (self.mf.division * 4 // denom)
        bar_no = bar + int(beats // num)
        beat_no = int(beats % num) + 1
        return bar_no, beat_no

    # ---------------------------------------------------------------- edits
    def _trackForChannel(self, channel: int) -> int:
        """Choose the track that new events for a channel belong in.

        Args:
            channel: 0-based MIDI channel.

        Returns:
            int: index of the track with the most events on the channel, or
                the last track when no track uses the channel yet.
        """
        counts = [sum(1 for e in t if e.channel == channel) for t in self.mf.tracks]

        if any(counts):
            index = counts.index(max(counts))
            return index

        last = len(self.mf.tracks) - 1
        return last

    def addNote(self, channel: int, pitch: int, velocity: int,
                start: int, end: int) -> Note:
        """Append a note to the file and to the derived views.

        The note-on and note-off go on the track that already carries most of
        the channel's events, and the channel summary is updated in place
        rather than by a full rebuild().

        Args:
            channel: 0-based MIDI channel.
            pitch: MIDI note number, 0-127.
            velocity: note-on velocity, 1-127.
            start: note-on tick.
            end: note-off tick.

        Returns:
            Note: the new note, already wired to its two events.
        """
        # The events are appended to the end of the track; the writer orders
        # each track by tick when it serialises, so their position in the
        # list does not matter.  The note-off uses the conventional release
        # velocity of 64.
        ti = self._trackForChannel(channel)
        on = smf.Event(start, 0x90 | channel, bytearray([pitch, velocity]))
        off = smf.Event(end, 0x80 | channel, bytearray([pitch, 64]))
        self.mf.tracks[ti] += [on, off]
        note = Note(ti, channel, pitch, velocity, start, end, on, off)
        self.notes.append(note)

        # Keep the channel summary and the song extent current.
        info = self.channels.setdefault(channel, ChannelInfo(channel))
        info.noteCount += 1
        info.tracks.add(ti)
        info.lo, info.hi = min(info.lo, pitch), max(info.hi, pitch)
        self.maxTick = max(self.maxTick, end)
        self.dirty = True
        return note

    def deleteNotes(self, notes: list[Note]) -> None:
        """Remove notes from the file and from the derived views.

        Args:
            notes: notes to delete; one that is already gone is skipped.
        """
        for note in notes:
            # Drop the note's events from its track.  A hanging note has no
            # off event, and a note deleted twice has nothing left to remove.
            track = self.mf.tracks[note.track]

            for e in (note.onEvent, note.offEvent):
                if e is not None and e in track:
                    track.remove(e)

            if note in self.notes:
                self.notes.remove(note)

            # Only the count is adjusted; lo and hi keep the range the
            # channel had before the deletion.
            info = self.channels.get(note.channel)

            if info:
                info.noteCount -= 1

        self.dirty = True

    def setProgram(self, channel: int, program: int) -> None:
        """Set the instrument on a channel by rewriting every program change it has.

        Files commonly carry one program change per track, and several tracks
        can share a channel (an orchestral file might have "Flute I" and
        "Flute II" both on channel 1), so every matching event across every
        track is updated and the new instrument holds for the whole piece.
        Updating only the first would leave the remaining tracks switching
        the channel back to the old instrument part-way through.  A channel
        with no program change at all gets one inserted at tick 0 on its
        busiest track.

        Args:
            channel: 0-based MIDI channel.
            program: GM program number, 0-127.
        """
        # Rewrite every program change on the channel, whichever track it is
        # in.
        found = False

        for track in self.mf.tracks:
            for e in track:
                if e.status == 0xC0 | channel:
                    e.data[0] = program
                    found = True

        info = self.channels.setdefault(channel, ChannelInfo(channel))

        # A channel that never had a program change gets one at tick 0 on the
        # track that carries most of its events.
        if not found:
            ti = self._trackForChannel(channel)
            self.mf.tracks[ti].insert(0, smf.Event(0, 0xC0 | channel,
                                                   bytearray([program])))

        info.program = program
        self.dirty = True

    def setVolume(self, channel: int, volume: int) -> None:
        """Set the channel volume by rewriting every CC7 event on the channel.

        As with setProgram, every CC7 across every track is updated so the
        new level holds for the whole piece even when several tracks share
        the channel.  A channel with no CC7 at all gets one inserted at tick
        0 on its busiest track.

        Args:
            channel: 0-based MIDI channel.
            volume: CC7 value; clamped to 0-127.
        """
        volume = max(0, min(127, volume))
        info = self.channels.setdefault(channel, ChannelInfo(channel))

        # Rewrite every CC7 on the channel, whichever track it is in.
        found = False

        for track in self.mf.tracks:
            for e in track:
                if e.status == 0xB0 | channel and e.data[0] == 7:
                    e.data[1] = volume
                    found = True

        # A channel that never had a CC7 gets one at tick 0 on the track that
        # carries most of its events, and the summary learns controller 7.
        if not found:
            ti = self._trackForChannel(channel)
            self.mf.tracks[ti].insert(0, smf.Event(0, 0xB0 | channel,
                                                   bytearray([7, volume])))
            info.controllers.add(7)

        info.volume = volume
        self.dirty = True

    def _tempoTrack(self) -> int:
        """Find the track that carries tempo events.

        Returns:
            int: the index of the first track with a tempo event, else 0.
        """
        for ti, track in enumerate(self.mf.tracks):
            if any(e.status == smf.META and e.metaType == smf.META_TEMPO for e in track):
                return ti

        return 0

    def setTempo(self, tick: int, bpm: float) -> None:
        """Set the tempo from a tick onwards.

        A tempo event already at that tick is rewritten; otherwise one is
        inserted into the track that carries the tempo map. The tempo map is
        rebuilt, so every tick-to-seconds conversion follows at once.

        Args:
            tick: absolute tick of the change, clamped to 0 or more.
            bpm: quarter notes per minute, clamped to what three bytes of
                microseconds per quarter can hold, about 3.6 to 1000.
        """
        tick = max(0, tick)
        data = bytearray(_tempoBytes(bpm))
        ti = self._tempoTrack()
        track = self.mf.tracks[ti]

        # Rewrite an event at the tick if there is one; every tempo event at
        # that tick, since a file may carry duplicates.
        found = False

        for e in track:
            if e.status == smf.META and e.metaType == smf.META_TEMPO and e.tick == tick:
                e.data[:] = data
                found = True

        if not found:
            track.append(smf.Event(tick, smf.META, data, smf.META_TEMPO))

        self._rebuildTempoMap()
        self.dirty = True

    def removeTempo(self, tick: int) -> bool:
        """Remove the tempo event at a tick.

        The event at tick 0 stays: a file always has a tempo in force from
        the start.

        Args:
            tick: absolute tick.

        Returns:
            bool: True when an event was removed.
        """
        if tick <= 0:
            return False

        removed = False

        for track in self.mf.tracks:
            keep = [e for e in track
                    if not (e.status == smf.META and e.metaType == smf.META_TEMPO
                            and e.tick == tick)]

            if len(keep) != len(track):
                track[:] = keep
                removed = True

        if removed:
            self._rebuildTempoMap()
            self.dirty = True

        return removed

    def scaleTempos(self, factor: float) -> None:
        """Multiply every tempo in the file by a factor.

        A file with one tempo event gets a new base speed; a file with a
        tempo track keeps its shape. A file with no tempo event at all
        gains one at tick 0 so the change is recorded.

        Args:
            factor: the multiplier; each result is clamped as in `setTempo`.
        """
        events = [e for track in self.mf.tracks for e in track
                  if e.status == smf.META and e.metaType == smf.META_TEMPO and len(e.data) == 3]

        if not events:
            self.setTempo(0, 60_000_000 / DEFAULT_TEMPO * factor)
            return

        for e in events:
            bpm = 60_000_000 / int.from_bytes(e.data, "big") * factor
            e.data[:] = _tempoBytes(bpm)

        self._rebuildTempoMap()
        self.dirty = True

    def _rebuildTempoMap(self) -> None:
        """Recompute the tempo map from the tempo events after an edit.

        The notes, channel summaries and markers are untouched; only the
        tick-to-seconds table changes.
        """
        tempos: list[tuple[int, int]] = []

        for track in self.mf.tracks:
            for e in track:
                if e.status == smf.META and e.metaType == smf.META_TEMPO and len(e.data) == 3:
                    tempos.append((e.tick, int.from_bytes(e.data, "big")))

        if not tempos or tempos[0][0] > 0:
            tempos.insert(0, (0, DEFAULT_TEMPO))

        tempos.sort()
        self.tempoMap = []
        sec = 0.0

        for tick, uspb in tempos:
            if self.tempoMap:
                ptick, puspb, psec = self.tempoMap[-1]
                sec = psec + (tick - ptick) * puspb / 1e6 / self.mf.division

            self.tempoMap.append((tick, uspb, sec))

    # ------------------------------------------------------------- playback
    def buildSlice(self, start_tick: int) -> smf.MidiFile:
        """Build a playback copy of the file that starts at start_tick.

        Events from start_tick onwards are copied with their ticks shifted so
        the slice begins at 0, and the tempo, program, controller and pitch
        bend state accumulated before that point is re-emitted at tick 0, so
        an external synth resuming mid-song hears the right tempo,
        instruments, volumes and bends from its first event.  Every event in
        the slice is a fresh object, so the original file is untouched by
        whatever the player does to the copy.

        Args:
            start_tick: tick to start from.

        Returns:
            smf.MidiFile: the shifted copy, or self.mf itself (not a copy)
                when start_tick is 0 or less.
        """
        if start_tick <= 0:
            return self.mf

        # Split each track at the cut: events before it are collected for
        # state reconstruction, the rest are copied with ticks rebased to it.
        out = smf.MidiFile(self.mf.format, self.mf.division, [])
        past: list[smf.Event] = []

        for track in self.mf.tracks:
            new = []

            for e in track:
                if e.tick < start_tick:
                    past.append(e)
                else:
                    new.append(smf.Event(e.tick - start_tick, e.status,
                                         bytearray(e.data), e.metaType))

            out.tracks.append(new)

        # Replay the skipped events in tick order (the stable sort keeps
        # same-tick events in track order) so that the last tempo, and per
        # channel the last program, the last value of each controller and
        # the last pitch bend, are what survive.
        past.sort(key=lambda e: e.tick)
        tempo: bytes | None = None
        prog: dict[int, int] = {}
        ccs: dict[tuple[int, int], int] = {}
        bend: dict[int, bytes] = {}

        for e in past:
            if e.status == smf.META and e.metaType == smf.META_TEMPO and len(e.data) == 3:
                tempo = bytes(e.data)
                continue

            kind = e.status & 0xF0

            if kind == 0xC0:
                prog[e.channel] = e.data[0]
            elif kind == 0xB0:
                ccs[(e.channel, e.data[0])] = e.data[1]
            elif kind == 0xE0:
                bend[e.channel] = bytes(e.data)

        # Emit the reconstructed state at tick 0 of the first track, ahead of
        # the copied events, so it is in place before anything sounds.
        head: list[smf.Event] = []

        if tempo:
            head.append(smf.Event(0, smf.META, bytearray(tempo), smf.META_TEMPO))

        for ch, p in prog.items():
            head.append(smf.Event(0, 0xC0 | ch, bytearray([p])))

        for (ch, cc), v in ccs.items():
            head.append(smf.Event(0, 0xB0 | ch, bytearray([cc, v])))

        for ch, d in bend.items():
            head.append(smf.Event(0, 0xE0 | ch, bytearray(d)))

        out.tracks[0][:0] = head
        return out
