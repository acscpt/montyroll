# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Song model tests for `model.py`.

Note pairing, the tempo map, bar and beat arithmetic, edits that write back
into the event lists, and `buildSlice` state reconstruction, all against the
synthetic song from `conftest.py`. The expected figures are worked out from
the fixture's constants: 480 ticks per quarter, tempo changes at ticks 0,
1920 and 5280, and a switch from 4/4 to 3/4 at tick 3840.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import DIVISION, MARKERS, TEMPO_EVENTS, TRACK_NAMES, eventTuples
from montyroll import model, smf


def _notesOn(song: model.Song, channel: int) -> list[tuple[int, int, int, int]]:
    """List (start, end, pitch, velocity) for a channel's notes in start order.

    Args:
        song: the song.
        channel: 0-based channel.

    Returns:
        list[tuple[int, int, int, int]]: the note tuples.
    """
    notes = [(n.start, n.end, n.pitch, n.velocity)
             for n in song.notes if n.channel == channel]
    return notes


class TestRebuild:
    """What `Song.rebuild` derives from the raw tracks."""

    def testTrackNamesAreRead(self, song: model.Song) -> None:
        """Each track's first name meta becomes its display name."""
        assert song.trackNames == TRACK_NAMES

    def testMarkersAreCollected(self, song: model.Song) -> None:
        """Marker metas are kept with their ticks and text."""
        assert song.markers == MARKERS

    def testTimeSignaturesAreDecoded(self, song: model.Song) -> None:
        """The denominator power is expanded to the real denominator."""
        assert song.timeSigs == [(0, 4, 4), (3840, 3, 4)]

    def testNotesArePairedFirstInFirstOut(self, song: model.Song) -> None:
        """Two overlapping C4s pair with their offs in the order they started."""
        c4 = [n for n in _notesOn(song, 0) if n[2] == 60 and n[0] >= 1920]
        assert c4 == [(1920, 2400, 60, 70), (2160, 2640, 60, 60)]

    def testVelocityZeroNoteOnEndsANote(self, song: model.Song) -> None:
        """A note-on with velocity zero closes the D4 that started at 3840."""
        d4 = [n for n in _notesOn(song, 0) if n[2] == 62]
        assert d4 == [(3840, 4320, 62, 50)]

    def testHangingNoteIsClosedAtTrackEnd(self, song: model.Song) -> None:
        """A note with no note-off is closed at the furthest tick seen when its track ends.

        The hanging flute note is itself the last event, so that is one tick
        after its own start; it has no off event to edit.
        """
        hanging = [n for n in song.notes if n.pitch == 77]
        assert len(hanging) == 1
        assert hanging[0].offEvent is None
        assert hanging[0].end == hanging[0].start + 1

    def testNotesAreSortedByStartThenPitch(self, song: model.Song) -> None:
        """The note list is ordered for drawing and hit-testing."""
        keys = [(n.start, n.pitch) for n in song.notes]
        assert keys == sorted(keys)

    def testMaxTickHasAFloor(self) -> None:
        """An empty song still spans sixteen quarter notes so the roll is usable."""
        empty = model.Song(smf.MidiFile(1, DIVISION, [[]]))
        assert empty.maxTick == DIVISION * 16

    def testNoteViewsShareTheEvents(self, song: model.Song) -> None:
        """A Note's on and off events are the very objects in the track."""
        note = next(n for n in song.notes if n.pitch == 67)
        track = song.mf.tracks[note.track]
        assert any(e is note.onEvent for e in track)
        assert any(e is note.offEvent for e in track)

    def testNotesAreHashableByIdentity(self, song: model.Song) -> None:
        """Notes go into selection sets, so equal-looking notes stay distinct."""
        a, b = song.notes[0], song.notes[1]
        assert len({a, b}) == 2
        assert a != model.Note(a.track, a.channel, a.pitch, a.velocity, a.start, a.end)


class TestChannelInfo:
    """The per-channel summary the channel strip displays."""

    def testChannelsInUse(self, song: model.Song) -> None:
        """Channels 1, 2, 3 and 10 carry events; nothing else does."""
        assert sorted(song.channels) == [0, 1, 2, 9]

    def testProgramIsTheOneInForceAtFirstNote(self, song: model.Song) -> None:
        """A program change after the first note does not override the summary."""
        assert song.channels[0].program == 0
        assert song.channels[1].program == 73

    def testVolumeIsFirstCc7(self, song: model.Song) -> None:
        """The summary volume is the first CC7 seen, -1 when there is none."""
        assert song.channels[0].volume == 100
        assert song.channels[1].volume == 90
        assert song.channels[9].volume == -1

    def testCountsAndRanges(self, song: model.Song) -> None:
        """Note count, pitch range and track membership per channel."""
        piano = song.channels[0]
        assert (piano.noteCount, piano.lo, piano.hi) == (6, 60, 67)
        flutes = song.channels[1]
        assert flutes.tracks == {2, 3}
        assert (flutes.lo, flutes.hi) == (69, 77)

    def testControllersAndBend(self, song: model.Song) -> None:
        """Controllers used and whether pitch bend appears are recorded."""
        assert song.channels[0].controllers == {7, 10}
        assert song.channels[0].hasPitchBend
        assert not song.channels[1].hasPitchBend

    def testDrumChannelIsNamedAsSuch(self, song: model.Song) -> None:
        """Channel 10 reports a drum kit regardless of program."""
        drums = song.channels[model.DRUM_CHANNEL]
        assert drums.isDrums
        assert drums.instrument == "Drum Kit"
        assert song.channels[1].instrument == "Flute"


class TestTempoMap:
    """Tick and time conversions through the tempo map."""

    def testTempoMapAccumulatesSeconds(self, song: model.Song) -> None:
        """Each entry carries the wall time at which its tempo takes effect."""
        expected = [(0, 500_000, 0.0), (1920, 400_000, 2.0), (5280, 600_000, 4.8)]
        assert [(t, u, round(s, 9)) for t, u, s in song.tempoMap] == expected

    @pytest.mark.parametrize("tick, seconds", [
        (0, 0.0), (480, 0.5), (1920, 2.0), (2400, 2.4), (5280, 4.8), (5760, 5.4),
    ])
    def testTickToSeconds(self, song: model.Song, tick: int, seconds: float) -> None:
        """Known ticks land on the expected wall time across all three tempi."""
        assert song.tickToSeconds(tick) == pytest.approx(seconds)

    @pytest.mark.parametrize("tick", [0, 1, 479, 480, 1919, 1920, 3000, 5280, 6000])
    def testSecondsToTickInverts(self, song: model.Song, tick: int) -> None:
        """Converting to seconds and back recovers the tick."""
        assert song.secondsToTick(song.tickToSeconds(tick)) == pytest.approx(tick)

    def testTempoAtFollowsTheMap(self, song: model.Song) -> None:
        """The bpm in force changes exactly at the tempo event ticks."""
        assert song.tempoAt(0) == pytest.approx(120.0)
        assert song.tempoAt(1919) == pytest.approx(120.0)
        assert song.tempoAt(1920) == pytest.approx(150.0)
        assert song.tempoAt(99_999) == pytest.approx(100.0)
        assert song.initialBpm() == pytest.approx(120.0)

    def testDefaultTempoWhenFileHasNone(self) -> None:
        """A file with no tempo event plays at 120 bpm from tick 0."""
        track = [smf.Event(0, 0x90, bytearray([60, 100])),
                 smf.Event(480, 0x80, bytearray([60, 64]))]
        quiet = model.Song(smf.MidiFile(0, DIVISION, [track]))
        assert quiet.tempoMap == [(0, model.DEFAULT_TEMPO, 0.0)]
        assert quiet.tickToSeconds(480) == pytest.approx(0.5)

    def testLateFirstTempoGetsADefaultBefore(self) -> None:
        """A first tempo event after tick 0 is preceded by the 120 bpm default."""
        track = [smf.Event(960, smf.META, bytearray((250_000).to_bytes(3, "big")),
                           smf.META_TEMPO)]
        late = model.Song(smf.MidiFile(0, DIVISION, [track]))
        assert late.tempoMap[0] == (0, model.DEFAULT_TEMPO, 0.0)
        assert late.tempoMap[1] == (960, 250_000, 1.0)

    def testDurationIsTheLastTick(self, song: model.Song) -> None:
        """Duration is the wall time of the furthest event."""
        assert song.duration == pytest.approx(song.tickToSeconds(song.maxTick))


class TestBarBeat:
    """Bar and beat numbering across a time-signature change."""

    @pytest.mark.parametrize("tick, barBeat", [
        (0, (1, 1)), (480, (1, 2)), (1919, (1, 4)), (1920, (2, 1)),
        (3840, (3, 1)), (4320, (3, 2)), (5280, (4, 1)), (5760, (4, 2)), (6720, (5, 1)),
    ])
    def testBarBeat(self, song: model.Song, tick: int, barBeat: tuple[int, int]) -> None:
        """Bars are 4/4 up to tick 3840 and 3/4 after it."""
        assert song.barBeat(tick) == barBeat


class TestEdits:
    """Edits that must write through to the underlying events."""

    def testApplyPushesNoteIntoEvents(self, song: model.Song) -> None:
        """Changing a note and calling apply mutates its on and off events."""
        note = next(n for n in song.notes if n.pitch == 67)
        note.pitch, note.velocity, note.start, note.end = 70, 33, 1000, 1300
        note.apply()
        assert (note.onEvent.tick, list(note.onEvent.data)) == (1000, [70, 33])
        assert (note.offEvent.tick, note.offEvent.data[0]) == (1300, 70)

    def testAddNoteLandsInTheChannelsBusiestTrack(self, song: model.Song) -> None:
        """A new note goes to the track with the most events on that channel."""
        note = song.addNote(1, 60, 99, 100, 200)
        assert note.track == 2
        assert note.onEvent in song.mf.tracks[2]
        assert note.offEvent in song.mf.tracks[2]
        assert song.channels[1].noteCount == 6
        assert song.dirty

    def testAddNoteOnNewChannelUsesLastTrack(self, song: model.Song) -> None:
        """A channel with no events yet is given the last track."""
        note = song.addNote(5, 60, 99, 0, 480)
        assert note.track == len(song.mf.tracks) - 1
        assert song.channels[5].noteCount == 1

    def testDeleteNotesRemovesEvents(self, song: model.Song) -> None:
        """Deleting notes drops their events and updates the counts."""
        victims = [n for n in song.notes if n.channel == 9]
        song.deleteNotes(victims)
        assert not any(n.channel == 9 for n in song.notes)
        assert not any(e.isNoteOn or e.isNoteOff for e in song.mf.tracks[4])
        assert song.channels[9].noteCount == 0
        assert song.dirty

    def testSetProgramTouchesEveryMatchingEvent(self, song: model.Song) -> None:
        """All three program changes on channel 2, across two tracks, are rewritten."""
        song.setProgram(1, 40)
        programs = [e.data[0] for t in song.mf.tracks for e in t if e.status == 0xC1]
        assert programs == [40, 40, 40]
        assert song.channels[1].program == 40
        assert song.dirty

    def testSetProgramInsertsWhenAbsent(self, song: model.Song) -> None:
        """A channel with no program change gets one at tick 0."""
        song.setProgram(9, 0)
        inserted = [e for e in song.mf.tracks[4] if e.status == 0xC9]
        assert [(e.tick, e.data[0]) for e in inserted] == [(0, 0)]

    def testSetVolumeTouchesEveryMatchingEvent(self, song: model.Song) -> None:
        """Both CC7 events on the flute channel are rewritten and clamped."""
        song.setVolume(1, 200)
        volumes = [e.data[1] for t in song.mf.tracks for e in t
                   if e.status == 0xB1 and e.data[0] == 7]
        assert volumes == [127, 127]
        assert song.channels[1].volume == 127

    def testSetVolumeInsertsWhenAbsent(self, song: model.Song) -> None:
        """A channel with no CC7 gets one at tick 0 and records controller 7."""
        song.setVolume(9, 50)
        inserted = [e for e in song.mf.tracks[4] if e.status == 0xB9 and e.data[0] == 7]
        assert [(e.tick, e.data[1]) for e in inserted] == [(0, 50)]
        assert 7 in song.channels[9].controllers

    def testEditsSurviveSaveAndReload(self, song: model.Song, tmp_path: Path) -> None:
        """Edited notes and programs come back from disk as edited."""
        note = next(n for n in song.notes if n.pitch == 64)
        note.pitch = 65
        note.apply()
        song.setProgram(0, 5)
        song.deleteNotes([n for n in song.notes if n.pitch == 60][:1])
        out = tmp_path / "edited.mid"
        song.save(str(out))
        assert not song.dirty
        assert song.path == str(out)
        reloaded = model.Song.load(str(out))
        assert eventTuples(reloaded.mf) == eventTuples(song.mf)
        assert reloaded.channels[0].program == 5
        assert any(n.pitch == 65 for n in reloaded.notes)

    def testSaveWithoutPathIsAnError(self) -> None:
        """A new song has no path, so a bare save is refused."""
        fresh = model.Song.new()

        with pytest.raises(ValueError):
            fresh.save()

    def testNewSongHasTempoAndTimeSignature(self) -> None:
        """A new song carries a 120 bpm tempo and 4/4 so the roll draws sensibly."""
        fresh = model.Song.new()
        assert fresh.tempoMap == [(0, 500_000, 0.0)]
        assert fresh.timeSigs == [(0, 4, 4)]
        assert fresh.notes == []
        assert fresh.path is None


class TestBuildSlice:
    """Playback copies that start mid-song."""

    def testZeroStartReturnsTheFileItself(self, song: model.Song) -> None:
        """Starting at tick 0 needs no copy."""
        assert song.buildSlice(0) is song.mf

    def testTicksAreShifted(self, song: model.Song) -> None:
        """Every surviving event is moved so the start tick becomes 0."""
        cut = song.buildSlice(2000)
        drumTicks = [e.tick for e in cut.tracks[4] if e.isNoteOn]
        assert drumTicks == []
        fluteTicks = [e.tick for e in cut.tracks[2] if e.isNoteOn]
        assert fluteTicks == [5760 - 2000]
        assert all(e.tick >= 0 for t in cut.tracks for e in t)

    def testStateIsReEmittedAtTickZero(self, song: model.Song) -> None:
        """Tempo, programs, controllers and bend in force at the cut lead track 0."""
        cut = song.buildSlice(3000)
        head = [e for e in cut.tracks[0] if e.tick == 0]
        tempo = [int.from_bytes(e.data, "big") for e in head
                 if e.status == smf.META and e.metaType == smf.META_TEMPO]
        assert tempo == [400_000]
        programs = {e.channel: e.data[0] for e in head if e.status & 0xF0 == 0xC0}
        assert programs == {0: 0, 1: 73}
        controllers = {(e.channel, e.data[0]): e.data[1]
                       for e in head if e.status & 0xF0 == 0xB0}
        assert controllers == {(0, 7): 100, (0, 10): 64, (1, 7): 80}
        bends = [bytes(e.data) for e in head if e.status & 0xF0 == 0xE0]
        assert bends == [b"\x00\x50"]

    def testLastStateWins(self, song: model.Song) -> None:
        """The CC7 re-emitted for channel 2 is the later value, 80, not the first, 90."""
        cut = song.buildSlice(1921)
        cc7 = [e.data[1] for e in cut.tracks[0]
               if e.tick == 0 and e.status == 0xB1 and e.data[0] == 7]
        assert cc7 == [80]

    def testSliceDoesNotAliasTheOriginal(self, song: model.Song) -> None:
        """Editing the copy leaves the file's own events untouched."""
        cut = song.buildSlice(100)
        before = eventTuples(song.mf)

        for track in cut.tracks:
            for e in track:
                e.tick += 1000

                if e.data:
                    e.data[0] = 0

        assert eventTuples(song.mf) == before

    def testSliceIsWritable(self, song: model.Song, tmp_path: Path) -> None:
        """The copy is a complete file that the writer and parser accept."""
        out = tmp_path / "slice.mid"
        smf.write(song.buildSlice(2500), str(out))
        assert len(smf.parse(str(out)).tracks) == len(TRACK_NAMES)


def testTempoEventsMatchFixture(song: model.Song) -> None:
    """Sanity check that the fixture's tempo constants are what the song carries."""
    assert [(t, u) for t, u, _ in song.tempoMap] == TEMPO_EVENTS
