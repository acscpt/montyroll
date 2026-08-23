# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""User-interface tests for `app.py`.

These build a real `MidiEditorApp` and drive its handlers with synthetic
events, so they need a display; without one the whole module is skipped. On
CI they run under `xvfb-run`. Dialogs and the synth are monkeypatched so
nothing blocks and nothing makes a sound.
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from typing import Any

import pytest

from conftest import TRACK_NAMES, needsDisplay
from montyroll import model, player, smf
from montyroll.app import MAX_EVENT_ROWS, PLAY_GLYPH, STOP_GLYPH, MidiEditorApp, _shade

pytestmark = needsDisplay


class FakeEvent:
    """A stand-in for a tkinter event carrying only the fields handlers read."""

    def __init__(self, x: float = 0, y: float = 0, state: int = 0, num: int = 0,
                 delta: int = 0, widget: Any = None) -> None:
        """Set the coordinates and modifier state.

        Args:
            x: widget x.
            y: widget y.
            state: modifier bits; 0x1 Shift, 0x4 Control.
            num: wheel button number on X11 (4 up, 5 down).
            delta: wheel delta on other platforms.
            widget: the widget the event is reported on.
        """
        self.x, self.y, self.state, self.num, self.delta = x, y, state, num, delta
        self.x_root, self.y_root = x, y
        self.widget = widget


@pytest.fixture
def app(songPath: Path, monkeypatch: pytest.MonkeyPatch) -> MidiEditorApp:
    """A MidiEditorApp with the synthetic song open and no synth attached.

    Returns:
        MidiEditorApp: laid out and drawn once.
    """
    monkeypatch.setattr(player, "findPlayer", lambda: None)
    application = MidiEditorApp(str(songPath))
    application.update()
    yield application
    application.destroy()


def _noteCentre(app: MidiEditorApp, note: model.Note) -> tuple[float, float]:
    """Canvas coordinates at the middle of a note's rectangle.

    Args:
        app: the application.
        note: the note.

    Returns:
        tuple[float, float]: (x, y) on the canvas.
    """
    x1, y1, x2, y2 = app.canvas.coords(app.noteToItem[id(note)])
    centre = ((x1 + x2) / 2, (y1 + y2) / 2)
    return centre


class TestStartup:
    """The window after opening a file."""

    def testNotesAreDrawn(self, app: MidiEditorApp) -> None:
        """Every note has a canvas item and every item maps back to its note."""
        assert len(app.noteToItem) == len(app.song.notes)
        assert set(app.itemToNote.values()) == set(app.song.notes)

    def testChannelRowsMatchChannelsInUse(self, app: MidiEditorApp) -> None:
        """One mute and one solo variable per channel in use."""
        assert sorted(app.muteVars) == sorted(app.song.channels)
        assert sorted(app.soloVars) == sorted(app.song.channels)

    def testTitleAndSummary(self, app: MidiEditorApp) -> None:
        """The title names the file and the summary counts tracks and notes."""
        assert app.title().startswith("MontyRoll - ")
        summary = app.infoLbl.cget("text")
        assert f"{len(TRACK_NAMES)} trk" in summary
        assert f"{len(app.song.notes)} notes" in summary

    def testEmptyStart(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Starting with no file gives an empty, clean song."""
        monkeypatch.setattr(player, "findPlayer", lambda: None)
        empty = MidiEditorApp()
        empty.update()
        assert empty.song.notes == []
        assert not empty.song.dirty
        assert "untitled" in empty.title()
        empty.destroy()

    def testOpenFailureIsReported(self, app: MidiEditorApp, tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
        """A file that does not parse produces an error dialog and keeps the old song."""
        bad = tmp_path / "bad.mid"
        bad.write_bytes(b"not midi")
        shown: list[tuple[str, str]] = []
        monkeypatch.setattr("montyroll.app.messagebox.showerror",
                            lambda title, msg: shown.append((title, msg)))
        before = app.song
        app.openFile(str(bad))
        assert shown and shown[0][0] == "Open failed"
        assert app.song is before


class TestSelection:
    """Selecting, deleting and describing notes."""

    def testSelectAllAndClear(self, app: MidiEditorApp) -> None:
        """Ctrl+A selects everything; Escape clears it."""
        app.selectAll()
        assert app.selection == set(app.song.notes)
        app._clearSelection()
        assert app.selection == set()

    def testClickSelectsOneNote(self, app: MidiEditorApp) -> None:
        """A plain click selects just the note under the pointer."""
        target = app.song.notes[0]
        x, y = _noteCentre(app, target)
        app.onPress(FakeEvent(x, y))
        app.onRelease(FakeEvent(x, y))
        assert app.selection == {target}
        assert app.activeChannel == target.channel

    def testShiftClickTogglesMembership(self, app: MidiEditorApp) -> None:
        """Shift+click adds an unselected note and removes a selected one."""
        first, second = app.song.notes[0], app.song.notes[1]
        app.onPress(FakeEvent(*_noteCentre(app, first)))
        app.onRelease(FakeEvent())
        app.onPress(FakeEvent(*_noteCentre(app, second), state=0x1))
        app.onRelease(FakeEvent())
        assert app.selection == {first, second}
        app.onPress(FakeEvent(*_noteCentre(app, first), state=0x1))
        app.onRelease(FakeEvent())
        assert app.selection == {second}

    def testClickOnEmptySpaceClears(self, app: MidiEditorApp) -> None:
        """Clicking where there is no note drops the selection."""
        app.selectAll()
        app.onPress(FakeEvent(app.canvas.winfo_width() - 5, 3))
        app.onRelease(FakeEvent())
        assert app.selection == set()

    def testDeleteSelection(self, app: MidiEditorApp) -> None:
        """Deleting removes the notes from the song and the canvas and marks dirty."""
        victims = set(n for n in app.song.notes if n.channel == 9)
        app.selection = set(victims)
        app.deleteSelection()
        assert not victims & set(app.song.notes)
        assert len(app.noteToItem) == len(app.song.notes)
        assert app.song.dirty
        assert app.title().endswith("*")

    def testDescribeNoteInStatus(self, app: MidiEditorApp) -> None:
        """The status bar names the note, its channel and its ticks."""
        note = next(n for n in app.song.notes if n.pitch == 60)
        app._describeNote(note)
        text = app.status.cget("text")
        assert "C4" in text and "ch 1" in text and f"tick {note.start}-{note.end}" in text

    def testDrumNotesAreNamedAsDrums(self, app: MidiEditorApp) -> None:
        """A note on channel 10 is described by its drum sound."""
        kick = next(n for n in app.song.notes if n.channel == 9 and n.pitch == 36)
        app._describeNote(kick)
        assert "Bass Drum 1" in app.status.cget("text")


class TestEditing:
    """Moving, resizing, adding and re-velocitying notes."""

    def testDragMovesTheSelection(self, app: MidiEditorApp) -> None:
        """Dragging a note right by one grid step shifts its start and end."""
        note = next(n for n in app.song.notes if n.pitch == 67)
        start, end, pitch = note.start, note.end, note.pitch
        snap = app._snapTicks()
        x, y = _noteCentre(app, note)
        app.onPress(FakeEvent(x, y))
        app.onDrag(FakeEvent(x + snap * app.ppt, y))
        app.onRelease(FakeEvent())
        assert (note.start, note.end, note.pitch) == (start + snap, end + snap, pitch)
        assert note.onEvent.tick == start + snap
        assert app.song.dirty
        assert app.status.cget("text") == "Moved 1 note"

    def testDragUpChangesPitch(self, app: MidiEditorApp) -> None:
        """Dragging a note up one row raises its pitch by a semitone."""
        note = next(n for n in app.song.notes if n.pitch == 64)
        x, y = _noteCentre(app, note)
        app.onPress(FakeEvent(x, y))
        app.onDrag(FakeEvent(x, y - app.rowH))
        app.onRelease(FakeEvent())
        assert note.pitch == 65
        assert note.onEvent.data[0] == 65

    def testDragRightEdgeResizes(self, app: MidiEditorApp) -> None:
        """Grabbing within five pixels of the right edge changes the length only."""
        note = next(n for n in app.song.notes if n.pitch == 67)
        start, end = note.start, note.end
        snap = app._snapTicks()
        x1, y1, x2, y2 = app.canvas.coords(app.noteToItem[id(note)])
        y = (y1 + y2) / 2
        app.onPress(FakeEvent(x2 - 2, y))
        assert app.drag["mode"] == "resize"
        app.onDrag(FakeEvent(x2 - 2 + snap * app.ppt, y))
        app.onRelease(FakeEvent())
        assert (note.start, note.end) == (start, end + snap)
        assert app.status.cget("text") == "Resized 1 note"

    def testDoubleClickAddsANote(self, app: MidiEditorApp) -> None:
        """Double-clicking empty space adds a snapped note on the active channel."""
        before = len(app.song.notes)
        app._setActiveChannel(1)
        x = 20 * app.ppt * app.song.mf.division   # bar 5 and a bit
        y = (127 - 100) * app.rowH + 2           # pitch 100, nothing there
        app.onDouble(FakeEvent(x, y))
        assert len(app.song.notes) == before + 1
        added = next(iter(app.selection))
        assert added.channel == 1
        assert added.pitch == 100
        assert added.start % app._snapTicks() == 0
        assert added.length == app._snapTicks()
        assert app.song.dirty

    def testDoubleClickOnANoteDoesNothing(self, app: MidiEditorApp) -> None:
        """Double-clicking an existing note does not add another."""
        before = len(app.song.notes)
        app.onDouble(FakeEvent(*_noteCentre(app, app.song.notes[0])))
        assert len(app.song.notes) == before

    def testSnapChoices(self, app: MidiEditorApp) -> None:
        """The snap combobox maps to ticks, and 'off' means one tick."""
        division = app.song.mf.division
        app.snapVar.set("1/4")
        assert app._snapTicks() == division
        app.snapVar.set("1/32")
        assert app._snapTicks() == division // 8
        app.snapVar.set("off")
        assert app._snapTicks() == 1

    def testVelocityDialogAppliesToSelection(self, app: MidiEditorApp,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
        """The entered velocity is written to every selected note's event."""
        monkeypatch.setattr("montyroll.app.simpledialog.askinteger", lambda *a, **k: 42)
        chosen = [n for n in app.song.notes if n.channel == 0][:3]
        app.selection = set(chosen)
        app.setVelocityDialog()
        assert all(n.velocity == 42 and n.onEvent.data[1] == 42 for n in chosen)
        assert app.song.dirty

    def testVelocityDialogCancelled(self, app: MidiEditorApp,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
        """Cancelling the dialog changes nothing."""
        monkeypatch.setattr("montyroll.app.simpledialog.askinteger", lambda *a, **k: None)
        note = app.song.notes[0]
        app.selection = {note}
        before = note.velocity
        app.setVelocityDialog()
        assert note.velocity == before
        assert not app.song.dirty


class TestChannelStrip:
    """Mute, solo, instrument and volume controls."""

    def testMuteAndSoloLogic(self, app: MidiEditorApp) -> None:
        """Solo overrides mute; with no solo the unmuted channels sound."""
        assert app._audibleChannels() == set(app.song.channels)
        app.muteVars[0].set(True)
        assert app._audibleChannels() == set(app.song.channels) - {0}
        app.soloVars[9].set(True)
        assert app._audibleChannels() == {9}

    def testMuteAllAndClear(self, app: MidiEditorApp) -> None:
        """Mute All silences everything; Clear restores it."""
        app.muteAll()
        assert app._audibleChannels() == set()
        app.soloVars[1].set(True)
        app.clearMuteSolo()
        assert app._audibleChannels() == set(app.song.channels)
        assert not any(v.get() for v in app.soloVars.values())

    def testProgramChangeWritesEvents(self, app: MidiEditorApp) -> None:
        """Choosing an instrument rewrites the channel's program changes."""
        var = tk.StringVar(value=" 40  Violin")
        app._programChanged(1, var)
        programs = {e.data[0] for t in app.song.mf.tracks for e in t if e.status == 0xC1}
        assert programs == {40}
        assert app.song.dirty
        assert "Violin" in app.status.cget("text")

    def testVolumeChangeWritesCc7(self, app: MidiEditorApp) -> None:
        """Moving the volume slider rewrites the channel's CC7 events."""
        app._volumeChanged(0, "33")
        values = [e.data[1] for t in app.song.mf.tracks for e in t
                  if e.status == 0xB0 and e.data[0] == 7]
        assert values == [33]
        assert app.song.dirty

    def testActiveChannelHighlight(self, app: MidiEditorApp) -> None:
        """Clicking a channel card makes it the target for new notes."""
        app._setActiveChannel(9)
        assert app.activeChannel == 9
        assert "Active channel: 10" in app.status.cget("text")

    def testPreviewWithoutSynthIsReported(self, app: MidiEditorApp) -> None:
        """With no synth, the preview button explains rather than failing."""
        app._previewChannel(1)
        assert "No synth" in app.status.cget("text")

    def testPreviewBuildsAShortFile(self, app: MidiEditorApp,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
        """A preview hands the preview player a few notes on the right channel."""
        played: list[Any] = []
        monkeypatch.setattr(app.preview, "play", lambda mf, *a, **k: played.append(mf) or True)
        app._previewChannel(9)
        assert played
        channels = {e.channel for t in played[0].tracks for e in t if e.isNoteOn}
        assert channels == {9}


class TestViewport:
    """Zoom, scroll and coordinate mapping."""

    def testHorizontalZoomChangesScale(self, app: MidiEditorApp) -> None:
        """Zooming in multiplies pixels per tick and redraws the notes."""
        before = app.ppt
        app.zoom(2.0)
        app.update()
        assert app.ppt == pytest.approx(before * 2)
        note = app.song.notes[0]
        x1, _, x2, _ = app.canvas.coords(app.noteToItem[id(note)])
        assert x2 - x1 == pytest.approx(note.length * app.ppt, abs=2)

    def testZoomIsClamped(self, app: MidiEditorApp) -> None:
        """Zoom stops at the limits rather than running away."""
        app.zoom(1e9)
        assert app.zoomX == 600
        app.zoom(1e-9)
        assert app.zoomX == 4

    def testVerticalZoomIsClamped(self, app: MidiEditorApp) -> None:
        """Row height stays between 4 and 24 pixels."""
        app.vzoom(100)
        assert app.rowH == 24
        app.vzoom(-100)
        assert app.rowH == 4

    def testPositionToTickPitch(self, app: MidiEditorApp) -> None:
        """Row 0 is pitch 127 and x divides by pixels per tick."""
        assert app._posToTickPitch(0, 0) == (0, 127)
        tick, pitch = app._posToTickPitch(app.ppt * 960, app.rowH * 67.5)
        assert (tick, pitch) == (960, 60)
        assert app._posToTickPitch(0, app.rowH * 1000)[1] == 0

    def testWheelWithControlZooms(self, app: MidiEditorApp) -> None:
        """Ctrl+wheel up zooms in; wheel up alone scrolls."""
        before = app.zoomX
        app.onWheel(FakeEvent(100, 100, state=0x4, num=4, widget=app.canvas))
        assert app.zoomX > before
        app.onWheel(FakeEvent(100, 100, num=5, widget=app.canvas))
        assert app.zoomX > before

    def testMotionUpdatesStatus(self, app: MidiEditorApp) -> None:
        """Moving the pointer reports bar, beat and the note under it."""
        note = next(n for n in app.song.notes if n.pitch == 60)
        x, y = _noteCentre(app, note)
        app.onMotion(FakeEvent(x, y))
        assert "bar 1:1" in app.status.cget("text")
        assert "C4" in app.status.cget("text")


class TestEventList:
    """The decoded event table."""

    def testEveryEventIsListed(self, app: MidiEditorApp) -> None:
        """One row per event, all of them for a small file."""
        total = sum(len(t) for t in app.song.mf.tracks)
        assert len(app.evTree.get_children()) == total

    def testRowsAreCapped(self, app: MidiEditorApp) -> None:
        """A huge file shows the first MAX_EVENT_ROWS rows plus one overflow line."""
        big = [smf.Event(i, 0x90, bytearray([60, 1])) for i in range(MAX_EVENT_ROWS + 50)]
        app.song.mf.tracks.append(big)
        app._fillEvents()
        rows = app.evTree.get_children()
        assert len(rows) == MAX_EVENT_ROWS + 1
        assert "more events not shown" in app.evTree.item(rows[-1])["values"][-1]

    @pytest.mark.parametrize("event, kind, detail", [
        (smf.Event(0, smf.META, bytearray((500_000).to_bytes(3, "big")), smf.META_TEMPO),
         "tempo", "120.0 bpm"),
        (smf.Event(0, smf.META, bytearray([3, 2, 24, 8]), smf.META_TIME_SIG), "time sig", "3/4"),
        (smf.Event(0, smf.META, bytearray(b"Verse"), smf.META_MARKER), "marker", "Verse"),
        (smf.Event(0, 0x90, bytearray([60, 100])), "note on", "C4 vel 100"),
        (smf.Event(0, 0x89, bytearray([36, 64])), "note off", "Bass Drum 1 vel 64"),
        (smf.Event(0, 0xC0, bytearray([73])), "program", "Flute"),
        (smf.Event(0, 0xB0, bytearray([7, 99])), "control", "Volume = 99"),
        (smf.Event(0, 0xB0, bytearray([3, 5])), "control", "CC 3 = 5"),
        (smf.Event(0, 0xE0, bytearray([0, 64])), "pitch bend", "bend +0"),
        (smf.Event(0, 0xE0, bytearray([127, 127])), "pitch bend", "bend +8191"),
        (smf.Event(0, 0xD0, bytearray([40])), "channel pressure", "pressure 40"),
    ])
    def testEventDecoding(self, event: smf.Event, kind: str, detail: str) -> None:
        """Each event kind decodes to its readable type and detail text."""
        assert MidiEditorApp._evType(event) == kind
        assert MidiEditorApp._evDetail(event) == detail


class TestPlayback:
    """Transport behaviour with the synth faked."""

    def testPlayWithoutSynthWarns(self, app: MidiEditorApp,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
        """With no synth the Play button shows the install hint and stays on Play."""
        warned: list[str] = []
        monkeypatch.setattr("montyroll.app.messagebox.showwarning",
                            lambda title, msg: warned.append(title))
        app.togglePlay()
        assert warned == ["No usable MIDI synth found"]
        assert app.playBtn.cget("text") == f"{PLAY_GLYPH} Play"

    def testPlayAndStopDriveThePlayer(self, app: MidiEditorApp,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        """Play passes the audible channels, master and speed; Stop resets the button."""
        calls: list[dict[str, Any]] = []

        def fakePlay(mf: smf.MidiFile, audible: set[int], master: float = 1.0,
                     speed: float = 1.0) -> bool:
            calls.append({"mf": mf, "audible": audible, "master": master, "speed": speed})
            return True

        monkeypatch.setattr(app.player, "play", fakePlay)
        monkeypatch.setattr(player, "findPlayer", lambda: ["fakesynth"])
        monkeypatch.setattr(type(app.player), "playing", property(lambda self: bool(calls)))
        app.muteVars[0].set(True)
        app.masterVar.set(50)
        app.speedVar.set(200)
        app.togglePlay()
        assert calls[0]["audible"] == set(app.song.channels) - {0}
        assert calls[0]["master"] == pytest.approx(0.5)
        assert calls[0]["speed"] == pytest.approx(2.0)
        assert calls[0]["mf"] is app.song.mf
        assert app.playBtn.cget("text") == f"{STOP_GLYPH} Stop"
        app.stopPlayback()
        assert app.playBtn.cget("text") == f"{PLAY_GLYPH} Play"
        assert app.cursorItem is None

    def testLiveUpdateIsDebounced(self, app: MidiEditorApp,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
        """Several quick changes while playing restart the synth once."""
        app.playStart = 0.0
        restarts: list[int] = []
        monkeypatch.setattr(app, "_restartPlayback", lambda: restarts.append(1))
        app._liveUpdate(delay=20)
        app._liveUpdate(delay=20)
        app._liveUpdate(delay=20)
        app.after(80, app.quit)
        app.mainloop()
        assert restarts == [1]

    def testLiveUpdateIgnoredWhenStopped(self, app: MidiEditorApp,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
        """Changing a slider while stopped schedules nothing."""
        monkeypatch.setattr(app, "_restartPlayback", lambda: pytest.fail("should not restart"))
        app._liveUpdate(delay=10)
        assert app._replayAfter is None


class TestFileOperations:
    """New, save and the dirty flag."""

    def testSaveAsWritesAndCleans(self, app: MidiEditorApp, tmp_path: Path,
                                  monkeypatch: pytest.MonkeyPatch) -> None:
        """Save As writes the file, clears the dirty flag and retitles the window."""
        out = tmp_path / "saved.mid"
        monkeypatch.setattr("montyroll.app.filedialog.asksaveasfilename",
                            lambda **k: str(out))
        app.selection = {app.song.notes[0]}
        app.deleteSelection()
        app.saveAs()
        assert out.exists()
        assert not app.song.dirty
        assert app.title().endswith("saved.mid")
        assert len(model.Song.load(str(out)).notes) == len(app.song.notes)

    def testSaveWithoutPathFallsBackToSaveAs(self, app: MidiEditorApp, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
        """Saving an untitled song asks for a name."""
        out = tmp_path / "new.mid"
        monkeypatch.setattr("montyroll.app.filedialog.asksaveasfilename",
                            lambda **k: str(out))
        monkeypatch.setattr("montyroll.app.messagebox.askyesno", lambda *a, **k: True)
        app.newFile()
        app.save()
        assert out.exists()

    def testNewFileAsksBeforeDiscarding(self, app: MidiEditorApp,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
        """With unsaved changes, declining the prompt keeps the current song."""
        monkeypatch.setattr("montyroll.app.messagebox.askyesno", lambda *a, **k: False)
        app._markDirty()
        before = app.song
        app.newFile()
        assert app.song is before

    def testCloseStopsPlayers(self, songPath: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Closing the window stops both synth processes."""
        monkeypatch.setattr(player, "findPlayer", lambda: None)
        application = MidiEditorApp(str(songPath))
        application.update()
        stopped: list[str] = []
        monkeypatch.setattr(application.player, "stop", lambda: stopped.append("player"))
        monkeypatch.setattr(application.preview, "stop", lambda: stopped.append("preview"))
        application.onClose()
        assert sorted(stopped) == ["player", "preview"]


def testShade() -> None:
    """Shading scales each RGB component and keeps the hash form."""
    assert _shade("#ff8000", 0.5) == "#7f4000"
    assert _shade("#102030", 1.0) == "#102030"
