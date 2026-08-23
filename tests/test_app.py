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
from tkinter import ttk
from typing import Any

import pytest

from conftest import TRACK_NAMES, needsDisplay
from montyroll import analysis, model, player, smf
from montyroll.app import (
    DEMAND_MAX_H,
    DEMAND_MIN_H,
    MAX_EVENT_ROWS,
    PLAY_GLYPH,
    STOP_GLYPH,
    MidiEditorApp,
    _shade,
)

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

    def testProgramChangeWritesEvents(self, app: MidiEditorApp,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        """Choosing an instrument rewrites the channel's program changes and restarts play."""
        restarts: list[int] = []
        monkeypatch.setattr(app, "_liveUpdate", lambda *a, **k: restarts.append(1))
        var = tk.StringVar(value=" 40  Violin")
        app._programChanged(1, var)
        assert restarts == [1]
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


class TestDemandStrip:
    """The voice-demand strip under the roll and the figures on the cards."""

    def testStripIsDrawn(self, app: MidiEditorApp) -> None:
        """After layout the strip holds the stack, two lines, the budget and labels."""
        app._drawDemand()
        items = app.demandCanvas.find_all()
        assert len(items) > 5
        kinds = {app.demandCanvas.type(i) for i in items}
        assert kinds == {"rectangle", "line", "text"}

    def testSweepIsLazyAndCached(self, app: MidiEditorApp) -> None:
        """One sweep serves the cards and the strip until the notes change."""
        first = app._ensureDemand()
        app._drawDemand()
        assert app._ensureDemand() is first
        app._invalidateDemand()
        assert app.demand is None
        assert app._ensureDemand() is not first

    def testEditsInvalidateTheSweep(self, app: MidiEditorApp) -> None:
        """Adding or deleting notes discards the sweep; a redraw rebuilds it."""
        before = app._ensureDemand().peak
        app.selectAll()
        app.deleteSelection()
        app.update()
        assert app._ensureDemand().peak == 0
        app._setActiveChannel(0)
        app.onDouble(FakeEvent(50 * app.ppt, (127 - 100) * app.rowH + 2))
        app.update()
        assert app._ensureDemand().peak == 1
        assert before > 1

    def testBudgetLineFollowsTheSpinbox(self, app: MidiEditorApp) -> None:
        """The budget label on the strip shows the chosen number of voices."""
        app.budgetVar.set(3)
        app._drawDemand()
        labels = [app.demandCanvas.itemcget(i, "text") for i in app.demandCanvas.find_all()
                  if app.demandCanvas.type(i) == "text"]
        assert "3" in labels

    def testBudgetIsCappedAtThePeak(self, app: MidiEditorApp) -> None:
        """The scale is the piece's peak and a larger budget is pulled down to it."""
        peak = app._ensureDemand().peak
        app.budgetVar.set(1)
        app._drawDemand()
        labels = {app.demandCanvas.itemcget(i, "text") for i in app.demandCanvas.find_all()
                  if app.demandCanvas.type(i) == "text"}
        assert str(peak) in labels
        app.budgetVar.set(40)
        app._drawDemand()
        assert app.budgetVar.get() == peak
        assert int(float(app.budgetSpin.cget("to"))) == peak

    def testColumnsMergeIntoRuns(self, app: MidiEditorApp) -> None:
        """Identical neighbouring columns become one run covering them all."""
        runs = app._demandColumns(app.demandCanvas.winfo_width())
        assert runs
        assert all(x1 > x0 for x0, x1, _, _, _ in runs)
        assert all(runs[i][1] == runs[i + 1][0] for i in range(len(runs) - 1))
        assert any(x1 - x0 > 1 for x0, x1, _, _, _ in runs)

    def testHoverReportsCounts(self, app: MidiEditorApp) -> None:
        """Pointing at the strip names the bar, the counts and the busiest channels."""
        app._onDemandMotion(FakeEvent(x=int(100 * app.ppt)))
        text = app.status.cget("text")
        assert "bar 1:1" in text
        assert "voices" in text and "pitches" in text and "classes" in text
        assert "ch 1:" in text or "ch 2:" in text

    def testOverflowIsWashedAndReported(self, app: MidiEditorApp) -> None:
        """Demand above the budget gets a stippled wash and an 'over by' in the status."""
        app.budgetVar.set(1)
        app._drawDemand()
        washes = [i for i in app.demandCanvas.find_all()
                  if app.demandCanvas.type(i) == "rectangle"
                  and app.demandCanvas.itemcget(i, "stipple") == "gray50"]
        assert washes
        app._onDemandMotion(FakeEvent(x=int(100 * app.ppt)))
        assert "over by 3" in app.status.cget("text")
        app.budgetVar.set(64)
        app._drawDemand()
        washes = [i for i in app.demandCanvas.find_all()
                  if app.demandCanvas.itemcget(i, "stipple") == "gray50"]
        assert not washes
        app._onDemandMotion(FakeEvent(x=int(100 * app.ppt)))
        assert "over by" not in app.status.cget("text")

    def testHoverPastTheEndIsSilent(self, app: MidiEditorApp) -> None:
        """Beyond the last note the status bar is left alone."""
        app._setStatus("untouched")
        app._onDemandMotion(FakeEvent(x=int(app.song.maxTick * app.ppt) + 500))
        assert app.status.cget("text") == "untouched"

    def testStripEmptyForAnEmptySong(self, app: MidiEditorApp,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
        """A song with no notes draws nothing and does not fail."""
        monkeypatch.setattr("montyroll.app.messagebox.askyesno", lambda *a, **k: True)
        app.newFile()
        app.update()
        app._drawDemand()
        assert app.demandCanvas.find_all() == ()

    def testCardsShowDemandFigures(self, app: MidiEditorApp) -> None:
        """Each channel card carries its peak, mean and share of the piece."""
        texts = []

        for row in app.channelRows.values():
            for w in row.winfo_children():
                if isinstance(w, tk.Label) and "peak" in w.cget("text"):
                    texts.append(w.cget("text"))

        withNotes = {n.channel for n in app.song.notes}
        assert len(texts) == len(withNotes)
        assert any(t.startswith("peak 2 | mean") for t in texts)
        assert all("plays" in t and t.endswith("%") for t in texts)

    def testSashResizesTheStrip(self, app: MidiEditorApp) -> None:
        """Dragging the sash up makes the strip taller, within its limits."""
        before = app.demandCanvas.winfo_height()
        press = FakeEvent()
        press.y_root = 500
        app._onSashPress(press)
        drag = FakeEvent()
        drag.y_root = 440
        app._onSashDrag(drag)
        app.update()
        assert app.demandCanvas.winfo_height() == before + 60
        drag.y_root = 5000
        app._onSashDrag(drag)
        app.update()
        assert app.demandCanvas.winfo_height() == DEMAND_MIN_H
        drag.y_root = -5000
        app._onSashDrag(drag)
        app.update()
        assert app.demandCanvas.winfo_height() == DEMAND_MAX_H

    def testWheelOverTheStripScrollsTheRoll(self, app: MidiEditorApp) -> None:
        """The strip shares the roll's wheel bindings."""
        app.zoom(4.0)
        app.update()
        before = app.canvas.xview()[0]
        app.onWheel(FakeEvent(100, 10, state=0x1, num=5, widget=app.demandCanvas))
        assert app.canvas.xview()[0] > before


class TestTonal:
    """The chord row on the ruler, the chord under the pointer and the key."""

    def testKeyInTheSummary(self, app: MidiEditorApp) -> None:
        """The toolbar summary ends with the estimated key."""
        assert app.infoLbl.cget("text").endswith("C major")

    def testChordRowOnTheRuler(self, app: MidiEditorApp) -> None:
        """Chord names are drawn on the ruler's bottom row where the run is wide enough."""
        app.zoom(4.0)
        app.update()
        labels = [app.ruler.itemcget(i, "text") for i in app.ruler.find_all()
                  if app.ruler.type(i) == "text" and app.ruler.itemcget(i, "fill") == "#9cf"]
        assert "Am" in labels

    def testNarrowRunsAreNotLabelled(self, app: MidiEditorApp) -> None:
        """Zoomed right out no chord label fits, so none is drawn, but the ruler survives."""
        app.zoom(0.001)
        app.update()
        labels = [i for i in app.ruler.find_all()
                  if app.ruler.type(i) == "text" and app.ruler.itemcget(i, "fill") == "#9cf"]
        assert labels == []

    def testChordUnderThePointer(self, app: MidiEditorApp) -> None:
        """Moving over the roll puts the chord at that tick in the status bar."""
        app.onMotion(FakeEvent(10, 10))
        assert "  Am " in app.status.cget("text")
        assert app._chordAt(0) == "Am"
        assert app._chordAt(10 ** 9) == "N.C."

    def testChordsAreCachedAndInvalidated(self, app: MidiEditorApp) -> None:
        """One chord track serves until the notes change."""
        first = app._ensureChords()
        assert app._ensureChords() is first
        app.selectAll()
        app.deleteSelection()
        rebuilt = app._ensureChords()
        assert rebuilt is not first
        assert all(name == "N.C." for _, _, name in rebuilt)


class TestReduction:
    """The Reduce panel, the reduced roll, playback and Save Reduced As."""

    def _stippled(self, app: MidiEditorApp) -> list[int]:
        """Canvas items drawn as ghosts."""
        items = [i for i in app.canvas.find_withtag("note")
                 if app.canvas.itemcget(i, "stipple") == "gray25"]
        return items

    def testOffByDefault(self, app: MidiEditorApp) -> None:
        """With the reduction off the roll is the song and nothing is computed."""
        assert not app.reduceVar.get()
        assert app.reduction is None
        assert self._stippled(app) == []
        assert app.lossLbl.cget("text") == ""

    def testReducingDrawsGhosts(self, app: MidiEditorApp) -> None:
        """At one voice most notes are dropped and drawn stippled; the label reports it."""
        app.budgetVar.set(1)
        app.reduceVar.set(True)
        app._reductionChanged()
        app.update()
        ghosts = self._stippled(app)
        assert ghosts
        assert len(ghosts) == len(app.reduction.dropped) + len(app.reduction.cut)
        assert app.lossLbl.cget("text").startswith("kept ")
        assert "no voice" in app.lossLbl.cget("text")
        assert all(app.itemToNote[i] in app.song.notes for i in ghosts)

    def testStripFollowsTheReduction(self, app: MidiEditorApp) -> None:
        """The demand strip sweeps the reduced notes, so it never exceeds the budget."""
        app.budgetVar.set(2)
        app.reduceVar.set(True)
        app._reductionChanged()
        assert app._ensureDemand().peak <= 2
        app.reduceVar.set(False)
        app._reductionChanged()
        assert app._ensureDemand().peak > 2

    def testControlsRebuildThePlan(self, app: MidiEditorApp) -> None:
        """Every switch and gap ends up in the plan's pool; priorities too."""
        app.dedupOctaveVar.set(True)
        app.monoTopVar.set(True)
        app.tremoloVar.set(8)
        app.legatoVar.set(120)
        app.maxChordVar.set(3)
        app.priorityVars[0].set(2.5)
        pool = app._currentPlan().pools[0]
        assert (pool.dedupOctave, pool.monoTop, pool.tremoloGap, pool.legatoGap,
                pool.maxChord) == (True, True, 8, 120, 3)
        assert app._currentPlan().priorityOf(0) == 2.5
        assert pool.voices == app.budgetVar.get()

    def testPriorityMovesTheLoss(self, app: MidiEditorApp) -> None:
        """Raising a channel's priority keeps more of its notes."""
        app.budgetVar.set(2)
        app.reduceVar.set(True)
        app._reductionChanged()
        before = sum(1 for n in app.song.notes if n.channel == 1 and app.reduction.isDropped(n))
        app.priorityVars[1].set(5.0)
        app._reductionChanged()
        after = sum(1 for n in app.song.notes if n.channel == 1 and app.reduction.isDropped(n))
        assert after < before

    def testEditsInvalidateTheReduction(self, app: MidiEditorApp) -> None:
        """Deleting notes while reducing recomputes against the new song."""
        app.budgetVar.set(2)
        app.reduceVar.set(True)
        app._reductionChanged()
        first = app.reduction
        app.selectAll()
        app.deleteSelection()
        app.update()
        assert app._ensureReduction() is not first
        assert app._ensureReduction().placed == []

    def testPlaybackUsesTheReducedSong(self, app: MidiEditorApp,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
        """With the reduction on, the synth is handed the reduced file."""
        handed: list[smf.MidiFile] = []
        monkeypatch.setattr(app.player, "play", lambda mf, *a, **k: handed.append(mf) or True)
        monkeypatch.setattr(player, "findPlayer", lambda: ["fakesynth"])
        app.budgetVar.set(2)
        app.reduceVar.set(True)
        app._reductionChanged()
        app.togglePlay()
        assert handed
        assert handed[0] is not app.song.mf
        reduced = model.Song(handed[0])
        assert analysis.sweep(reduced).peak <= 2
        app.stopPlayback()

    def testPlaybackUsesTheSongWhenOff(self, app: MidiEditorApp,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
        """With the reduction off the synth gets the song itself."""
        handed: list[smf.MidiFile] = []
        monkeypatch.setattr(app.player, "play", lambda mf, *a, **k: handed.append(mf) or True)
        monkeypatch.setattr(player, "findPlayer", lambda: ["fakesynth"])
        app.togglePlay()
        assert handed[0] is app.song.mf
        app.stopPlayback()

    def testSaveReducedAs(self, app: MidiEditorApp, tmp_path: Path,
                          monkeypatch: pytest.MonkeyPatch) -> None:
        """Save Reduced As writes a file within the budget and leaves the song alone."""
        out = tmp_path / "reduced.mid"
        monkeypatch.setattr("montyroll.app.filedialog.asksaveasfilename", lambda **k: str(out))
        app.budgetVar.set(2)
        app.saveReducedAs()
        assert out.exists()
        saved = model.Song.load(str(out))
        assert analysis.sweep(saved).peak <= 2
        assert len(saved.notes) < len(app.song.notes)
        assert not app.song.dirty
        assert "Saved reduced song" in app.status.cget("text")
class TestTempoEditing:
    """Tempo changes from the Edit menu and the ruler."""

    def testTempoLinesOnTheRoll(self, app: MidiEditorApp) -> None:
        """Each tempo change after tick 0 is a dashed line down the roll."""
        lines = app.canvas.find_withtag("tempo")
        assert len(lines) == 2
        xs = sorted(app.canvas.coords(i)[0] for i in lines)
        assert xs == pytest.approx([1920 * app.ppt, 5280 * app.ppt])

    def testSetTempoFromTheMenu(self, app: MidiEditorApp, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bar 2 at 90 bpm: the map, the roll, the summary and the event list follow."""
        monkeypatch.setattr("montyroll.app.simpledialog.askinteger", lambda *a, **k: 2)
        asked = {}

        def askfloat(title, prompt, **k):
            asked.update(k)
            return 90.0

        monkeypatch.setattr("montyroll.app.simpledialog.askfloat", askfloat)
        app.setTempoDialog()
        assert asked["initialvalue"] == 150.0       # the tempo in force at bar 2
        assert app.song.tempoAt(1920) == pytest.approx(90.0)
        assert app.song.dirty
        assert "Tempo 90 bpm from bar 2" in app.status.cget("text")
        assert len(app.canvas.find_withtag("tempo")) == 2
        rows = [app.evTree.item(i)["values"] for i in app.evTree.get_children()]
        assert any(r[5] == "tempo" and r[6] == "90.0 bpm" for r in rows)

    def testSetTempoCancelled(self, app: MidiEditorApp, monkeypatch: pytest.MonkeyPatch) -> None:
        """Cancelling either dialog changes nothing."""
        monkeypatch.setattr("montyroll.app.simpledialog.askinteger", lambda *a, **k: None)
        app.setTempoDialog()
        monkeypatch.setattr("montyroll.app.simpledialog.askinteger", lambda *a, **k: 3)
        monkeypatch.setattr("montyroll.app.simpledialog.askfloat", lambda *a, **k: None)
        app.setTempoDialog()
        assert not app.song.dirty
        assert len(app.song.tempoMap) == 3

    def testScaleTempos(self, app: MidiEditorApp, monkeypatch: pytest.MonkeyPatch) -> None:
        """Scaling by 200% doubles every tempo and updates the summary's bpm."""
        monkeypatch.setattr("montyroll.app.simpledialog.askfloat", lambda *a, **k: 200.0)
        app.scaleTemposDialog()
        assert [round(60e6 / u) for _, u, _ in app.song.tempoMap] == [240, 300, 200]
        assert "240 bpm" in app.infoLbl.cget("text")
        assert app.song.dirty

    def testRulerDoubleClickOnTheTempoRow(self, app: MidiEditorApp,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
        """A double-click on the bottom row sets the tempo at the nearest bar."""
        monkeypatch.setattr("montyroll.app.simpledialog.askfloat", lambda *a, **k: 80.0)
        y = (app.rulerRows[2] + app.rulerRows[3]) // 2
        app.onRulerDouble(FakeEvent(x=int(3900 * app.ppt), y=y))       # near bar 3 (3840)
        assert app.song.tempoAt(3840) == pytest.approx(80.0)
        assert app.song.tempoAt(3839) == pytest.approx(150.0)

    def testRulerDoubleClickElsewhereIsIgnored(self, app: MidiEditorApp,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
        """The seconds and bar rows do nothing on double-click."""
        monkeypatch.setattr("montyroll.app.simpledialog.askfloat",
                            lambda *a, **k: pytest.fail("should not ask"))
        app.onRulerDouble(FakeEvent(x=100, y=app.rulerRows[0] + 2))
        app.onRulerDouble(FakeEvent(x=100, y=app.rulerRows[1] + 2))
        app.onRulerDouble(FakeEvent(x=100, y=app.rulerRows[3] + 2))
        assert not app.song.dirty

    def testRulerContextMenu(self, app: MidiEditorApp, monkeypatch: pytest.MonkeyPatch) -> None:
        """The context menu offers set, remove (only where a change exists) and scale."""
        menus = []

        class FakeMenu:
            def __init__(self, *a, **k):
                self.items = []
                menus.append(self)

            def add_command(self, label, command=None, state="normal"):
                self.items.append((label, state, command))

            def add_separator(self):
                pass

            def tk_popup(self, x, y):
                pass

        monkeypatch.setattr("montyroll.app.tk.Menu", FakeMenu)
        y = (app.rulerRows[2] + app.rulerRows[3]) // 2
        app.onRulerContext(FakeEvent(x=int(1920 * app.ppt), y=y))
        labels = [(label, state) for label, state, _ in menus[-1].items]
        assert ("Set tempo from bar 2...", "normal") in labels
        assert ("Remove tempo change at bar 2", "normal") in labels
        app.onRulerContext(FakeEvent(x=int(960 * app.ppt), y=y))
        labels = [(label, state) for label, state, _ in menus[-1].items]
        assert ("Remove tempo change at bar 1", "disabled") in labels

    def testRemoveTempoFromTheMenu(self, app: MidiEditorApp) -> None:
        """Removing the bar 2 change leaves 120 bpm in force until bar 4."""
        app._removeTempo(1920)
        assert [t for t, _, _ in app.song.tempoMap] == [0, 5280]
        assert app.song.tempoAt(3000) == pytest.approx(120.0)
        assert len(app.canvas.find_withtag("tempo")) == 1
        assert "removed" in app.status.cget("text")

    def testTempoEditRestartsPlayback(self, app: MidiEditorApp,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        """While playing, a tempo change schedules the debounced restart."""
        app.playStart = 0.0
        scheduled = []
        monkeypatch.setattr(app, "_liveUpdate", lambda *a, **k: scheduled.append(1))
        app._applyTempo(960, 100)
        assert scheduled == [1]


class TestReportTab:
    """The Report tab: Original and Reduced sub-tabs and the Save button."""

    def testOriginalIsGeneratedWhenShown(self, app: MidiEditorApp) -> None:
        """Nothing is generated until the tab is selected; then the text appears."""
        assert app.reports == {"original": None, "reduced": None}
        app.nb.select(2)
        app.update()
        text = app.reportTexts["original"].get("1.0", "end")
        assert text.startswith("MontyRoll report: song.mid")
        assert "Instruments sounding at once" in text
        assert app.reportTexts["original"].cget("state") == "disabled"
        assert app.reports["reduced"] is None

    def testReducedIsANoteWhileReductionIsOff(self, app: MidiEditorApp) -> None:
        """The Reduced sub-tab explains itself until the reduction is on."""
        app.nb.select(2)
        app.reportNb.select(1)
        app.update()
        assert app.reportTexts["reduced"].get("1.0", "end").startswith("Reduction is off.")

    def testReducedFollowsTheReduction(self, app: MidiEditorApp) -> None:
        """With Reduce on, the Reduced sub-tab describes the reduced song and says so."""
        app.nb.select(2)
        app.reportNb.select(1)
        app.budgetVar.set(1)
        app.reduceVar.set(True)
        app._reductionChanged()
        app.update()
        text = app.reportTexts["reduced"].get("1.0", "end")
        assert text.startswith("MontyRoll report: song.mid (reduced to 1 voices)")
        assert "notes as written           1" in text
        original = app._ensureReport("original")
        assert original.startswith("MontyRoll report: song.mid\n")
        app.reduceVar.set(False)
        app._reductionChanged()
        app.update()
        assert app.reportTexts["reduced"].get("1.0", "end").startswith("Reduction is off.")

    def testReportsFollowEdits(self, app: MidiEditorApp) -> None:
        """Deleting every note regenerates the report on view."""
        app.nb.select(2)
        app.update()
        app.selectAll()
        app.deleteSelection()
        app.update()
        assert "No notes." in app.reportTexts["original"].get("1.0", "end")

    def testSaveReportWritesTheOneOnView(self, app: MidiEditorApp, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
        """Save writes the visible sub-tab's text, as ASCII."""
        out = tmp_path / "report.txt"
        monkeypatch.setattr("montyroll.app.filedialog.asksaveasfilename", lambda **k: str(out))
        app.saveReport()
        assert out.read_text(encoding="ascii") == app._ensureReport("original")
        assert "Saved original report" in app.status.cget("text")
        app.nb.select(2)
        app.reportNb.select(1)
        app.update()
        app.saveReport()
        assert out.read_text(encoding="ascii").startswith("Reduction is off.")


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

    def testCursorAlsoRunsOnTheStrip(self, app: MidiEditorApp,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
        """While playing, the demand strip carries the cursor too; stop clears both."""
        calls: list[int] = []
        monkeypatch.setattr(app.player, "play", lambda *a, **k: calls.append(1) or True)
        monkeypatch.setattr(player, "findPlayer", lambda: ["fakesynth"])
        monkeypatch.setattr(type(app.player), "playing", property(lambda self: bool(calls)))
        app.togglePlay()
        app._tickCursor()
        assert app.cursorItem is not None
        assert app.demandCanvas.type(app.demandCursorItem) == "line"
        app._drawDemand()
        app._tickCursor()
        assert app.demandCanvas.type(app.demandCursorItem) == "line"
        app.stopPlayback()
        assert app.cursorItem is None
        assert app.demandCursorItem is None

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


class TestPlayFromAndChangeAll:
    """Play from here, Change All and Clear restoring instruments."""

    def testBeatStart(self, app: MidiEditorApp) -> None:
        """Ticks snap to the nearest beat, numbered within the bar."""
        assert app._beatStart(0) == (0, 1)
        assert app._beatStart(700) == (480, 2)
        assert app._beatStart(1900) == (1920, 1)
        assert app._beatStart(2200) == (2400, 2)

    def testContextMenuOffersPlayFrom(self, app: MidiEditorApp,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        """Right-click anywhere offers bar and beat; on a note the edit items follow."""
        menus = []

        class FakeMenu:
            def __init__(self, *a, **k):
                self.items = []
                menus.append(self)

            def add_command(self, label, command=None, state="normal"):
                self.items.append(label)

            def add_separator(self):
                self.items.append("---")

            def tk_popup(self, x, y):
                pass

        monkeypatch.setattr("montyroll.app.tk.Menu", FakeMenu)
        app.onContext(FakeEvent(x=int(3500 * app.ppt), y=3))     # late in bar 2, empty
        assert menus[-1].items == ["Play from bar 3", "Play from beat 2:4"]
        note = app.song.notes[0]
        app.onContext(FakeEvent(*_noteCentre(app, note)))
        assert menus[-1].items[2:] == ["---", "Delete", "Set Velocity..."]
        assert app.selection == {note}

    def testPlayFromStartsAtTheTick(self, app: MidiEditorApp,
                                    monkeypatch: pytest.MonkeyPatch) -> None:
        """Playback begins at the chosen tick's time, from a slice of the song."""
        handed: list[Any] = []
        monkeypatch.setattr(app.player, "play", lambda mf, *a, **k: handed.append(mf) or True)
        monkeypatch.setattr(player, "findPlayer", lambda: ["fakesynth"])
        monkeypatch.setattr(type(app.player), "playing", property(lambda self: bool(handed)))
        app.playFrom(1920)
        assert app.playOffset == pytest.approx(app.song.tickToSeconds(1920))
        assert handed[0] is not app.song.mf
        assert "Playing from bar 2:1" in app.status.cget("text")
        assert app.playBtn.cget("text") == f"{STOP_GLYPH} Stop"
        app.playFrom(0)                                   # restarts from the top
        assert len(handed) == 2 and handed[1] is app.song.mf
        app.stopPlayback()

    def testPlayFromWithoutSynthWarns(self, app: MidiEditorApp,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        """With no synth the same dialog as Play appears."""
        warned: list[str] = []
        monkeypatch.setattr("montyroll.app.messagebox.showwarning",
                            lambda title, msg: warned.append(title))
        app.playFrom(960)
        assert warned == ["No usable MIDI synth found"]

    def testChangeAllSkipsTheDrums(self, app: MidiEditorApp) -> None:
        """Every channel but Ch 10 takes the program; the cards and song follow."""
        app.changeAll(40)
        assert app.song.channels[0].program == 40
        assert app.song.channels[1].program == 40
        assert app.song.channels[9].program == 0
        programs = {e.channel: e.data[0] for t in app.song.mf.tracks for e in t
                    if e.status & 0xF0 == 0xC0}
        assert programs[0] == 40 and programs[1] == 40
        assert app.song.dirty
        assert "set to Violin" in app.status.cget("text")

    def testClearRestoresTheInstruments(self, app: MidiEditorApp) -> None:
        """Clear puts every program back to what the file was opened with."""
        assert app.originalPrograms == {0: 0, 1: 73, 2: 0, 9: 0}
        app.changeAll(40)
        app.muteVars[0].set(True)
        app.clearMuteSolo()
        assert app.song.channels[0].program == 0
        assert app.song.channels[1].program == 73
        assert not app.muteVars[0].get()
        assert "3 instruments restored" in app.status.cget("text")
        app.clearMuteSolo()
        assert app.status.cget("text") == "Mutes and solos cleared"

    def testChangeAllDialog(self, app: MidiEditorApp, monkeypatch: pytest.MonkeyPatch) -> None:
        """The dialog applies the chosen program on Change and nothing on Cancel."""
        applied: list[int] = []
        monkeypatch.setattr(app, "changeAll", lambda program: applied.append(program))
        app.changeAllDialog()
        dialog = [w for w in app.winfo_children() if isinstance(w, tk.Toplevel)][-1]
        combo = [w for w in dialog.winfo_children() if isinstance(w, ttk.Combobox)][0]
        combo.set(" 73  Flute")
        buttons = [w for f in dialog.winfo_children() if isinstance(f, ttk.Frame)
                   for w in f.winfo_children() if isinstance(w, ttk.Button)]
        next(b for b in buttons if b.cget("text") == "Change").invoke()
        app.update()
        assert applied == [73]
        assert dialog not in app.winfo_children()


    def testInstrumentChangesReachReducedPlayback(self, app: MidiEditorApp,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
        """With Reduce on, Change All, a card's instrument and a volume all reach the synth."""
        handed: list[Any] = []
        monkeypatch.setattr(app.player, "play", lambda mf, *a, **k: handed.append(mf) or True)
        monkeypatch.setattr(player, "findPlayer", lambda: ["fakesynth"])
        app.budgetVar.set(8)
        app.reduceVar.set(True)
        app._reductionChanged()
        app._ensureReducedSong()                  # the cached copy, with the old programs

        def programs(mf: smf.MidiFile) -> dict[int, int]:
            return {e.channel: e.data[0] for t in mf.tracks for e in t
                    if e.status & 0xF0 == 0xC0}

        app.changeAll(40)
        app._startPlayback(0.0)
        assert programs(handed[-1])[0] == 40 and programs(handed[-1])[1] == 40
        app._programChanged(1, tk.StringVar(value=" 73  Flute"))
        app._startPlayback(0.0)
        assert programs(handed[-1])[1] == 73
        app._volumeChanged(0, "33")
        app._startPlayback(0.0)
        volumes = [e.data[1] for t in handed[-1].tracks for e in t
                   if e.status == 0xB0 and e.data[0] == 7]
        assert volumes == [33]
        app.clearMuteSolo()                        # back to the file's programs
        app._startPlayback(0.0)
        assert programs(handed[-1])[0] == 0
        assert programs(handed[-1])[1] == 73


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
