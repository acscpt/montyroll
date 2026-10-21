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
from tkinter import font as tkfont
from tkinter import ttk
from typing import Any

import pytest

from conftest import TRACK_NAMES, needsDisplay
from montyroll import analysis, gm, model, player, reduce, smf, theme
from montyroll.app import (
    BUDGET_DRAG_PX,
    BUDGET_GRAB,
    BUDGET_SETTLE_MS,
    CHANNEL_COLORS,
    DEMAND_MAX_H,
    DEMAND_MIN_H,
    DRAWER_CLOSE_GLYPH,
    DRAWER_OPEN_GLYPH,
    ELLIPSIS,
    KEYS_W,
    LANE_GAP,
    MAX_EVENT_ROWS,
    NO_POOL,
    NOTE_HOVER_MIX,
    PLAY_GLYPH,
    STOP_GLYPH,
    TEMPO_GLYPH,
    X16_TIERS,
    MidiEditorApp,
    _elide,
    _shade,
)
from montyroll.export import zsm, zsmcheck, zsmdecode

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


def _toWidget(app: MidiEditorApp, x: float, y: float) -> tuple[float, float]:
    """Convert canvas coordinates to the widget coordinates an event carries.

    Args:
        app: the application.
        x: canvas x.
        y: canvas y.

    Returns:
        tuple[float, float]: (x, y) relative to the visible part of the roll.
    """
    position = (x - app.canvas.canvasx(0), y - app.canvas.canvasy(0))
    return position


def _noteCentre(app: MidiEditorApp, note: model.Note) -> tuple[float, float]:
    """The point an event would carry at the middle of a note's rectangle.

    Args:
        app: the application.
        note: the note.

    Returns:
        tuple[float, float]: (x, y) in widget coordinates, allowing for the
        roll's scroll position.
    """
    x1, y1, x2, y2 = app.canvas.coords(app.noteToItem[id(note)])
    centre = _toWidget(app, (x1 + x2) / 2, (y1 + y2) / 2)
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
        assert f"{len(TRACK_NAMES)} tracks" in summary
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


class TestTheme:
    """The window's flat look: the theme, the painted elements and the rows."""

    def testFlatClam(self, app: MidiEditorApp) -> None:
        """The ttk theme is clam with its bevels in the background colour."""
        style = ttk.Style(app)
        assert style.theme_use() == "clam"
        assert app.palette is theme.PALETTE
        assert app.cget("background") == theme.PALETTE["window"]
        assert style.lookup("TButton", "lightcolor") == theme.PALETTE["raised"]
        assert style.lookup("TButton", "darkcolor") == theme.PALETTE["raised"]

    def testSwitchIsPainted(self, app: MidiEditorApp) -> None:
        """The Reduce switch draws its indicator from the painted images."""
        style = ttk.Style(app)
        assert "Switch.indicator" in style.element_names()
        assert "Switch.indicator" in str(style.layout("Switch.TCheckbutton"))
        assert app.paintedImages["switchOn"].width() == 40

    def testSlidersArePainted(self, app: MidiEditorApp) -> None:
        """The toolbar sliders and the row sliders have painted knobs and troughs."""
        names = set(ttk.Style(app).element_names())
        assert {"Horizontal.Scale.slider", "Horizontal.Scale.trough",
                "Row.Horizontal.Scale.slider", "Row.Horizontal.Scale.trough"} <= names

    def testMenusAreFlat(self, app: MidiEditorApp) -> None:
        """The menus have a plain edge and the accent on the active entry; the bar is flat."""
        bar = app.nametowidget(app.cget("menu"))
        fileMenu = app.nametowidget(bar.entrycget("File", "menu"))
        assert str(bar.cget("relief")) == "flat"
        assert str(fileMenu.cget("relief")) == "solid"
        assert str(fileMenu.cget("activeborderwidth")) == "0"
        assert str(fileMenu.cget("activebackground")) == theme.PALETTE["accent"]
        assert str(fileMenu.cget("activeforeground")) == theme.PALETTE["ink"]

    def testRowsAreOnePixelApart(self, app: MidiEditorApp) -> None:
        """The channel rows are packed with a one-pixel etched line between them."""
        app.update_idletasks()
        rows = [app.channelRows[ch] for ch in sorted(app.song.channels)]

        for upper, lower in zip(rows, rows[1:]):
            assert lower.winfo_y() - (upper.winfo_y() + upper.winfo_height()) == 1

        assert str(app.chanHolder.cget("style")) == "Etch.TFrame"
        assert ttk.Style(app).lookup("Etch.TFrame", "background") == theme.PALETTE["border"]

    def testLaneSwitchOnTheDarkStrip(self, app: MidiEditorApp) -> None:
        """The lanes are accent text on the strip's dark; the lane on view is filled."""
        style = ttk.Style(app)
        dark = theme.ROLL["strip"]
        assert str(app.demandCanvas.cget("background")) == dark
        assert style.lookup("Lane.Toggle.TButton", "background") == dark
        assert style.lookup("Lane.Toggle.TButton", "foreground") == theme.PALETTE["accent"]
        assert (style.lookup("Lane.Toggle.TButton", "background", ["selected"])
                == theme.PALETTE["accent"])
        assert (style.lookup("Lane.Toggle.TButton", "foreground", ["selected"])
                == theme.PALETTE["ink"])

    def testMuteAndSoloIconsLeadTheRow(self, app: MidiEditorApp) -> None:
        """Each row opens with its mute and solo icons, on the header colour."""
        style = ttk.Style(app)
        assert {"Mute.indicator", "Solo.indicator"} <= set(style.element_names())

        for ch in sorted(app.song.channels):
            header = app.nameLabels[ch].master
            icons = [w for w in header.winfo_children() if isinstance(w, ttk.Checkbutton)]
            assert [str(w.cget("style")) for w in icons] == [
                f"Ch{ch}.Mute.Row.TCheckbutton", f"Ch{ch}.Solo.Row.TCheckbutton"]
            assert str(icons[0].cget("variable")) == str(app.muteVars[ch])
            assert icons[1].winfo_x() < app.nameLabels[ch].winfo_x()

            # The icon stays on the header colour under the pointer and when
            # on, not the checkbutton's hover grey.
            for state in ([], ["active"], ["active", "selected"], ["pressed"]):
                for icon in icons:
                    assert (style.lookup(icon.cget("style"), "background", state)
                            == CHANNEL_COLORS[ch])

    def testIconsHaveSeparators(self, app: MidiEditorApp) -> None:
        """A one-pixel line in a darker shade of the colour follows each icon."""
        ch = sorted(app.song.channels)[0]
        header = app.nameLabels[ch].master
        parts = header.pack_slaves()
        edge = theme.mix(CHANNEL_COLORS[ch], "#000000", 0.4)

        for i in (1, 3):
            assert isinstance(parts[i - 1], ttk.Checkbutton)
            assert isinstance(parts[i], tk.Frame)
            assert int(parts[i].cget("width")) == 1
            assert str(parts[i].cget("background")) == edge

    def testNameGivesWayOnANarrowStrip(self, app: MidiEditorApp) -> None:
        """Narrowed, a long name ends in an ellipsis and the slider stays clear of it."""
        ch = next(c for c, name in app.rowNames.items() if len(name) > 12)
        label = app.nameLabels[ch]

        # Narrow the strip by enough to leave the name about half its width,
        # measured, so the test holds at any font size.
        app.update_idletasks()
        full = app.rowNameFont.measure(app.rowNames[ch])
        squeeze = label.winfo_width() - full // 2
        wide = app.paned.sashpos(0)
        app.paned.sashpos(0, wide - squeeze)
        app.update()
        assert label.cget("text").endswith(ELLIPSIS)
        assert app.rowNameFont.measure(label.cget("text")) <= label.winfo_width()
        scale = next(w for w in label.master.winfo_children() if isinstance(w, ttk.Scale))
        assert label.winfo_x() + label.winfo_width() <= scale.winfo_x()
        app.paned.sashpos(0, wide)
        app.update()
        assert label.cget("text") == app.rowNames[ch]

    def testElide(self, app: MidiEditorApp) -> None:
        """Text that fits is kept; text that does not is cut to fit with an ellipsis."""
        font = app.rowNameFont
        text = "Acoustic Grand Piano"
        assert _elide(text, font, font.measure(text)) == text
        cut = _elide(text, font, font.measure("Acoustic"))
        assert cut.endswith(ELLIPSIS) and font.measure(cut) <= font.measure("Acoustic")
        assert _elide(text, font, 1) == ELLIPSIS

    def testPanelTitleBars(self, app: MidiEditorApp) -> None:
        """Channels, Reduce and the inspector each have a title bar in the title colour."""
        style = ttk.Style(app)
        assert style.lookup("Title.TFrame", "background") == theme.PALETTE["title"]
        titles = []
        stack: list[tk.Misc] = [app]

        while stack:
            w = stack.pop()
            stack.extend(w.winfo_children())

            if isinstance(w, ttk.Label) and str(w.cget("style")) == "Title.TLabel":
                titles.append(str(w.cget("text")))

        assert {"Channels", "Reduce", "Inspector"} <= set(titles)

    def testRuleUnderTheLanes(self, app: MidiEditorApp) -> None:
        """A one-pixel rule runs under the lane switch, flush on top of the strip."""
        app.update_idletasks()
        rule = app.laneRule
        assert rule.winfo_height() == 1
        assert str(rule.cget("background")) == theme.PALETTE["raised"]
        assert rule.winfo_y() + 1 == app.demandCanvas.winfo_y()
        assert rule.winfo_width() >= app.demandCanvas.winfo_width()

    def testRowHeaderTakesTheChannelColour(self, app: MidiEditorApp) -> None:
        """A row's header bar is its channel colour with dark text, active or not."""
        first, second = sorted(app.song.channels)[:2]
        app._setActiveChannel(second)

        for ch in (first, second):
            label = app.nameLabels[ch]
            assert label.cget("background") == CHANNEL_COLORS[ch]
            assert label.cget("foreground") == theme.PALETTE["ink"]
            assert label.master.cget("background") == CHANNEL_COLORS[ch]

    def testRowVolumeSitsOnTheHeader(self, app: MidiEditorApp) -> None:
        """The row volume slider sits on the channel colour, and moving it writes CC7."""
        ch = sorted(app.song.channels)[0]
        scale = next(w for w in app.nameLabels[ch].master.winfo_children()
                     if isinstance(w, ttk.Scale))
        style = scale.cget("style")
        assert ttk.Style(app).lookup(style, "background") == CHANNEL_COLORS[ch]
        scale.set(33)
        assert app.song.channels[ch].volume == 33

    def testFileDialogPaneIsDark(self, app: MidiEditorApp) -> None:
        """The file dialog's folder pane takes the field colour in place of its white."""
        dialog = tk.Toplevel(app, name="__tk_filedialog")
        pane = tk.Canvas(dialog, background="white")
        pane.pack()
        app.update()

        try:
            assert pane.cget("background") == app.palette["field"]
        finally:
            dialog.destroy()

    def testActiveRowTint(self, app: MidiEditorApp) -> None:
        """The active channel's row and detail take the palette's tint and the others the panel."""
        first, second = sorted(app.song.channels)[:2]
        app._setActiveChannel(second)
        assert app.channelRows[second].cget("background") == app.palette["active"]
        assert app.channelDetails[second].cget("background") == app.palette["active"]
        assert app.channelRows[first].cget("background") == app.palette["panel"]

    def testInterfaceFont(self, app: MidiEditorApp) -> None:
        """The named default font uses the interface family the theme chose."""
        family = tkfont.nametofont("TkDefaultFont", root=app).actual("family")
        assert family == app.uiFamily


class TestInspector:
    """The inspector: the active channel, the selected note, and the fold."""

    def testShowsTheActiveChannel(self, app: MidiEditorApp) -> None:
        """The channel card follows the active channel."""
        ch = sorted(app.song.channels)[1]
        app._setActiveChannel(ch)
        info = app.song.channels[ch]
        assert app.inspTitle.cget("text") == f"Ch {ch + 1}  {info.instrument}"
        assert app.inspChannelFields["Notes"].cget("text") == f"{info.noteCount:,}"
        assert app.inspChannelFields["Pool"].cget("text") == app._poolNameOf(ch)

    def testShowsOneSelectedNote(self, app: MidiEditorApp) -> None:
        """One selected note is described; none or several are counted."""
        note = next(n for n in app.song.notes if n.channel != model.DRUM_CHANNEL)
        app.selection = {note}
        app._applySelectionStyle()
        assert app.inspPitch.cget("text") == gm.noteName(note.pitch)
        assert app.inspNoteFields["Velocity"].cget("text") == str(note.velocity)
        assert app.inspNoteFields["Reduction"].cget("text") == "reduction off"
        app.selectAll()
        assert app.inspPitch.cget("text") == f"{len(app.song.notes)} notes selected"
        app._clearSelection()
        assert app.inspPitch.cget("text") == "No note selected"

    def testVerdictFollowsTheReduction(self, app: MidiEditorApp) -> None:
        """With the reduction on, a dropped note shows its reason."""
        app.budgetVar.set(1)
        app.reduceVar.set(True)
        app._reductionChanged()
        reduction = app._ensureReduction()
        dropped = next(n for n in app.song.notes if id(n) in reduction.dropped)
        app.selection = {dropped}
        app._applySelectionStyle()
        assert app.inspNoteFields["Reduction"].cget("text") == reduction.dropped[id(dropped)]

    def testPickerChangesTheActiveChannel(self, app: MidiEditorApp) -> None:
        """The inspector's instrument picker writes a program change on the active channel."""
        ch = next(c for c in sorted(app.song.channels) if c != model.DRUM_CHANNEL)
        app._setActiveChannel(ch)
        app.inspProgramVar.set(" 40  Violin")
        app._programChanged(app.activeChannel, app.inspProgramVar)
        assert app.song.channels[ch].program == 40
        assert app.inspTitle.cget("text").endswith("Violin")

    def testFoldGivesTheRollTheWidth(self, app: MidiEditorApp) -> None:
        """Folding shows the strip, hides the cards, and widens the roll; opening reverses it."""
        app.update()
        before = app.canvas.winfo_width()
        app.toggleInspector()
        app.update()
        assert app.inspectorFolded
        assert app.inspectorStrip.winfo_ismapped()
        assert not app.inspectorBody.winfo_ismapped()
        assert app.canvas.winfo_width() > before
        app.toggleInspector()
        app.update()
        assert not app.inspectorFolded
        assert app.inspectorBody.winfo_ismapped()
        assert app.canvas.winfo_width() == pytest.approx(before, abs=4)


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
        x, y = _toWidget(app, x2 - 2, (y1 + y2) / 2)
        app.onPress(FakeEvent(x, y))
        assert app.drag["mode"] == "resize"
        app.onDrag(FakeEvent(x + snap * app.ppt, y))
        app.onRelease(FakeEvent())
        assert (note.start, note.end) == (start, end + snap)
        assert app.status.cget("text") == "Resized 1 note"

    def testDoubleClickAddsANote(self, app: MidiEditorApp) -> None:
        """Double-clicking empty space adds a snapped note on the active channel."""
        before = len(app.song.notes)
        app._setActiveChannel(1)
        x = 20 * app.ppt * app.song.mf.division   # bar 5 and a bit
        y = (127 - 100) * app.rowH + 2           # pitch 100, nothing there
        app.onDouble(FakeEvent(*_toWidget(app, x, y)))
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

    def testOnlyTheActiveRowShowsItsDetail(self, app: MidiEditorApp) -> None:
        """The active row shows its figures and controls; the others are one line."""
        first, second = sorted(app.song.channels)[:2]
        app._setActiveChannel(second)
        app.update()
        assert app.channelDetails[second].winfo_ismapped()
        assert not app.channelDetails[first].winfo_ismapped()

        # Every channel keeps its controls, shown or not.
        assert set(app.poolVars) >= {first, second}
        assert set(app.priorityVars) >= {first, second}

    def testRowNamesTheInstrument(self, app: MidiEditorApp) -> None:
        """The row's instrument name follows a program change."""
        var = tk.StringVar(value=" 56  Trumpet")
        app._programChanged(0, var)
        assert app.nameLabels[0].cget("text") == "Trumpet"

    def testProgramChangeRefreshesTheFamily(self, app: MidiEditorApp) -> None:
        """Picking an instrument updates the card's family text, not just the picker."""
        assert "Piano" in app.descLabels[0].cget("text")
        var = tk.StringVar(value=" 56  Trumpet")
        app._programChanged(0, var)
        assert "Brass" in app.descLabels[0].cget("text")
        assert "Piano" not in app.descLabels[0].cget("text")

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

    def testOpensOnTheNotes(self, app: MidiEditorApp) -> None:
        """The roll opens scrolled so the middle of the file's pitches is in view."""
        pitches = sorted(n.pitch for n in app.song.notes if n.channel != model.DRUM_CHANNEL)
        middle = pitches[len(pitches) // 2]
        top = app.canvas.canvasy(0)
        bottom = top + app.canvas.winfo_height()
        assert top <= (127 - middle) * app.rowH <= bottom

    def testNotesHaveADarkerEdge(self, app: MidiEditorApp) -> None:
        """An unselected note's edge is its fill mixed towards black; selection is white."""
        note = next(n for n in app.song.notes if n.channel != model.DRUM_CHANNEL)
        item = app.noteToItem[id(note)]
        fill = app.canvas.itemcget(item, "fill")
        assert app.canvas.itemcget(item, "outline") == theme.mix(fill, "#000000", 0.4)
        app.selection = {note}
        app._applySelectionStyle()
        assert app.canvas.itemcget(item, "outline") == "#ffffff"
        app._clearSelection()
        assert app.canvas.itemcget(item, "outline") == theme.mix(fill, "#000000", 0.4)

    def testMutedNotesShowOnTheRoll(self, app: MidiEditorApp) -> None:
        """A muted channel's notes take the muted grey, which differs from the grid."""
        note = next(n for n in app.song.notes if n.channel != model.DRUM_CHANNEL)
        app.muteVars[note.channel].set(True)
        app._channelsChanged()
        item = app.noteToItem[id(note)]
        assert app.canvas.itemcget(item, "fill") == theme.ROLL["muted"]
        assert theme.ROLL["muted"] != theme.ROLL["roll"]

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
        assert app.budgetLimit == peak

    def testBudgetCanRiseWhileReduced(self, app: MidiEditorApp) -> None:
        """With the reduction on, the budget still reaches the song's peak, not the reduced one."""
        peak = app._ensureSongDemand().peak
        app.budgetVar.set(peak - 2)
        app.reduceVar.set(True)
        app._reductionChanged()
        app._drawDemand()
        assert app.budgetLimit == peak
        app._stepBudget(1)
        assert app.budgetVar.get() == peak - 1

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
        """Each channel row's detail carries its peak, mean and share of the piece."""
        texts = [label.cget("text") for label in app.usageLabels.values()]

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


class TestLanes:
    """The lane switch under the roll: Demand, Velocity and Weight."""

    def _visible(self, app: MidiEditorApp) -> list[model.Note]:
        """The notes whose start is in view, as a note lane draws them."""
        x0 = app.canvas.canvasx(0)
        t0, t1 = x0 / app.ppt, (x0 + app.demandCanvas.winfo_width()) / app.ppt
        notes = [n for n in app.song.notes if t0 - 1 <= n.start <= t1]
        return notes

    def testVelocityLaneDrawsABarPerNote(self, app: MidiEditorApp) -> None:
        """Every note in view has a bar, and the loudest reaches the top of the scale."""
        app.laneVar.set("Velocity")
        app._laneChanged()
        app._drawDemand()
        visible = self._visible(app)
        assert len(app.laneBars) == len(visible)
        loudest = max(visible, key=lambda n: n.velocity)
        x = loudest.start * app.ppt - app.canvas.canvasx(0)
        top = min(app.demandCanvas.coords(i)[1] for i in app.demandCanvas.find_all()
                  if app.demandCanvas.type(i) == "rectangle"
                  and app.demandCanvas.coords(i)[0] == pytest.approx(x))
        height = app.demandCanvas.winfo_height()
        expected = height - 2 - loudest.velocity * (height - 8) / 127
        assert top <= expected + 0.5

    def testWeightLaneOutlinesDroppedNotes(self, app: MidiEditorApp) -> None:
        """With the reduction on, dropped notes are outlines and kept notes are filled."""
        app.budgetVar.set(1)
        app.reduceVar.set(True)
        app._reductionChanged()
        app.laneVar.set("Weight")
        app._laneChanged()
        app._drawDemand()
        rects = [i for i in app.demandCanvas.find_all() if app.demandCanvas.type(i) == "rectangle"]
        fills = {app.demandCanvas.itemcget(i, "fill") for i in rects}
        assert "" in fills
        assert len(fills) > 1

    def testHoverInALaneDescribesTheNote(self, app: MidiEditorApp) -> None:
        """Pointing at a bar in a note lane names its note on the status bar."""
        app.laneVar.set("Velocity")
        app._laneChanged()
        app._drawDemand()
        xa, xb, note = app.laneBars[-1]
        app._onDemandMotion(FakeEvent((xa + xb) / 2, 10))
        assert f"vel {note.velocity}" in app.status.cget("text")

    def testBackToDemand(self, app: MidiEditorApp) -> None:
        """Switching back to Demand draws the stack and the budget again."""
        app.laneVar.set("Weight")
        app._laneChanged()
        app._drawDemand()
        app.laneVar.set("Demand")
        app._laneChanged()
        app._drawDemand()
        kinds = {app.demandCanvas.type(i) for i in app.demandCanvas.find_all()}
        assert kinds == {"rectangle", "line", "text"}


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


class TestHover:
    """The bar, beat and slot under the pointer, each lit a step more, and the note under it."""

    def _hoverOn(self, app: MidiEditorApp, note: model.Note) -> int:
        """Move the pointer onto a note's middle.

        Args:
            app: the window.
            note: the note.

        Returns:
            int: the note's canvas item.
        """
        app.onMotion(FakeEvent(*_noteCentre(app, note)))
        item = app.noteToItem[id(note)]
        return item

    def testBandCoversTheBar(self, app: MidiEditorApp) -> None:
        """The band spans the bar under the pointer, on the roll and the ruler."""
        note = next(n for n in app.song.notes if n.channel != model.DRUM_CHANNEL)
        self._hoverOn(app, note)
        start, end = app._hoverBar
        assert start <= note.start < end
        assert end - start == app.song.barTick(2) - app.song.barTick(1)
        base = app.canvas.find_withtag("hoverBar")[0]
        assert app.canvas.coords(base)[0] == pytest.approx(start * app.ppt)
        assert app.canvas.coords(base)[2] == pytest.approx(end * app.ppt)
        assert app.ruler.find_withtag("hoverBar")

    def testLevelsStackUnderTheLinesAndNotes(self, app: MidiEditorApp) -> None:
        """Bar base, beat base, row shading, bar rows, beat rows, slot, then lines and notes."""
        note = next(n for n in app.song.notes if n.channel != model.DRUM_CHANNEL)
        self._hoverOn(app, note)
        c = app.canvas
        order = list(c.find_all())

        def at(tag: str) -> list[int]:
            return [order.index(i) for i in c.find_withtag(tag)]

        assert order[0] == c.find_withtag("hoverBarBase")[0]
        assert order[1] == c.find_withtag("hoverBeatBase")[0]
        assert max(at("rowShade")) < min(at("hoverBarRow"))
        assert max(at("hoverBarRow")) < min(at("hoverBeatRow"))
        assert max(at("hoverBeatRow")) < min(at("hoverCell"))
        notes = [order.index(i) for i in app.itemToNote]
        assert max(at("hoverCell")) < min(notes)
        assert max(at("hoverCell")) < min(at("tempo"))

    def testBeatAndSlotSitInsideTheBar(self, app: MidiEditorApp) -> None:
        """The beat lies within the bar, and the slot is the note a double-click would add."""
        note = next(n for n in app.song.notes if n.channel != model.DRUM_CHANNEL)
        self._hoverOn(app, note)
        bar, beat = app._hoverBar, app._hoverBeat
        num, den = app.song.timeSigs[0][1], app.song.timeSigs[0][2]
        assert bar[0] <= beat[0] < beat[1] <= bar[1]
        assert beat[1] - beat[0] == app.song.mf.division * 4 // den
        assert (bar[1] - bar[0]) == (beat[1] - beat[0]) * num
        start, end, pitch = app._hoverCell
        assert beat[0] <= start < end and pitch == note.pitch
        assert end - start == app._snapTicks()
        cell = app.canvas.find_withtag("hoverCell")[0]
        black = note.pitch % 12 in (1, 3, 6, 8, 10)
        assert (app.canvas.itemcget(cell, "fill")
                == theme.ROLL["hoverCellRow" if black else "hoverCell"])

    def testDoubleClickFillsTheSlot(self, app: MidiEditorApp) -> None:
        """A note added by double-click lies exactly in the slot the pointer lit."""
        app.snapVar.set("1/8")
        x = (app.song.maxTick + app.song.mf.division) * app.ppt
        y = (127 - 64) * app.rowH + app.rowH / 2
        event = FakeEvent(*_toWidget(app, x, y))
        app.onMotion(event)
        slot = app._hoverCell
        app.onDouble(event)
        added = next(iter(app.selection))
        assert (added.start, added.end, added.pitch) == slot

    def testNoteUnderThePointerIsLit(self, app: MidiEditorApp) -> None:
        """The note under the pointer is mixed towards white, and given its fill back after."""
        note = next(n for n in app.song.notes if n.channel != model.DRUM_CHANNEL)
        item = app.noteToItem[id(note)]
        fill = app.canvas.itemcget(item, "fill")
        self._hoverOn(app, note)
        assert app.canvas.itemcget(item, "fill") == theme.mix(fill, "#ffffff", NOTE_HOVER_MIX)
        x, y = _noteCentre(app, note)
        app.onMotion(FakeEvent(x, y - 3 * app.rowH))
        assert app.canvas.itemcget(item, "fill") == fill

    def testLeavingTheRollClearsIt(self, app: MidiEditorApp) -> None:
        """Leaving the roll takes down the band and gives the note its fill back."""
        note = next(n for n in app.song.notes if n.channel != model.DRUM_CHANNEL)
        item = app.noteToItem[id(note)]
        fill = app.canvas.itemcget(item, "fill")
        self._hoverOn(app, note)
        app._clearHover()

        for tag in ("hoverBar", "hoverBeat", "hoverCell"):
            assert not app.canvas.find_withtag(tag)

        assert not app.ruler.find_withtag("hoverBar")
        assert app.canvas.itemcget(item, "fill") == fill

    def testBandOutlivesARedraw(self, app: MidiEditorApp) -> None:
        """A full redraw, as after a zoom, keeps the band on the same bar."""
        note = next(n for n in app.song.notes if n.channel != model.DRUM_CHANNEL)
        self._hoverOn(app, note)
        bar = app._hoverBar
        app.zoom(1.25)
        assert app._hoverBar == bar
        base = app.canvas.find_withtag("hoverBar")[0]
        assert app.canvas.coords(base)[0] == pytest.approx(bar[0] * app.ppt)


class TestBudget:
    """The voice budget beside the strip: its arrows, its number and its line."""

    def _countRecomputes(self, app: MidiEditorApp,
                         monkeypatch: pytest.MonkeyPatch) -> list[int]:
        """Count the reduction's recomputes from here on.

        Args:
            app: the window.
            monkeypatch: the fixture that wraps `_reductionChanged`.

        Returns:
            list[int]: grows by one at each recompute, holding the budget.
        """
        calls: list[int] = []
        original = app._reductionChanged

        def counted() -> None:
            calls.append(app.budgetVar.get())
            original()

        monkeypatch.setattr(app, "_reductionChanged", counted)
        return calls

    def testLayout(self, app: MidiEditorApp) -> None:
        """Voices heads the gutter, the lanes clear its rule, and the number is centred."""
        app.update_idletasks()
        roll = app.demandCanvas.master
        corner = [w for w in roll.grid_slaves(row=3, column=0)
                  if isinstance(w, tk.Label) and w.cget("text") == "Voices"]
        assert corner and str(corner[0].cget("foreground")) == theme.PALETTE["text"]
        switch = next(w for w in roll.grid_slaves(row=3) if isinstance(w, ttk.Frame))
        assert int(str(switch.cget("padding")[0])) == KEYS_W + LANE_GAP
        up, down = app.budgetArrows["budgetUp"], app.budgetArrows["budgetDown"]
        middle = (up.winfo_y() + up.winfo_height() + down.winfo_y()) / 2
        box = app.budgetBox
        assert abs(box.winfo_y() + box.winfo_height() / 2 - middle) <= 2

    def testArrowsStepAndRecomputeOnce(self, app: MidiEditorApp,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
        """Each click steps a voice at once; the reduction recomputes once the clicks pause."""
        app.budgetVar.set(2)
        app._drawDemand()
        calls = self._countRecomputes(app, monkeypatch)
        up = app.budgetArrows["budgetUp"]
        app._pressBudgetArrow(FakeEvent(num=1), "budgetUp", 1)
        assert str(up.cget("image")) == str(app.paintedImages["budgetUpPressed"])
        app._pressBudgetArrow(FakeEvent(num=1), "budgetUp", 1)
        app._pressBudgetArrow(FakeEvent(num=1), "budgetDown", -1)
        assert app.budgetVar.get() == 3
        assert calls == []
        app.after(BUDGET_SETTLE_MS + 50)
        app.update()
        assert calls == [3]
        assert app.plan.pools[0].voices == 3

    def testWheelAndKeysStep(self, app: MidiEditorApp) -> None:
        """The wheel and the arrow keys step the budget, held between 1 and the peak."""
        app.budgetVar.set(1)
        app._drawDemand()
        app._onBudgetWheel(FakeEvent(num=5))
        assert app.budgetVar.get() == 1
        app._onBudgetWheel(FakeEvent(num=4))
        app._onBudgetWheel(FakeEvent(delta=120))
        assert app.budgetVar.get() == 3
        app._stepBudget(100)
        assert app.budgetVar.get() == app.budgetLimit

    def testDragTheNumber(self, app: MidiEditorApp, monkeypatch: pytest.MonkeyPatch) -> None:
        """Dragging the number up raises the budget, filled with the accent; release recomputes."""
        app.budgetVar.set(1)
        app._drawDemand()
        calls = self._countRecomputes(app, monkeypatch)
        app._onBudgetPress(FakeEvent(y=500, num=1))
        assert app.budgetDragging
        assert str(app.budgetBox.cget("background")) == theme.PALETTE["accent"]
        app._onBudgetDrag(FakeEvent(y=500 - 2 * BUDGET_DRAG_PX))
        assert app.budgetVar.get() == 3
        assert app.demandCanvas.find_withtag("budgetTag")
        assert calls == []
        app._onBudgetRelease()
        assert not app.budgetDragging
        assert calls == [3]
        assert not app.demandCanvas.find_withtag("budgetTag")

    def testClickWithoutMovingKeepsTheReduction(self, app: MidiEditorApp,
                                                monkeypatch: pytest.MonkeyPatch) -> None:
        """A press and release that leave the budget alone recompute nothing."""
        calls = self._countRecomputes(app, monkeypatch)
        app._onBudgetPress(FakeEvent(y=500, num=1))
        app._onBudgetRelease()
        assert calls == []

    def testTypeABudget(self, app: MidiEditorApp) -> None:
        """A double click opens the number for typing; Return keeps it, Escape drops it."""
        app.budgetVar.set(1)
        app._drawDemand()
        app._editBudget()
        editor = app._budgetEditor
        editor.delete(0, "end")
        editor.insert(0, "2")
        app._closeBudgetEditor(True)
        assert app.budgetVar.get() == 2
        assert app._budgetEditor is None
        app._editBudget()
        app._budgetEditor.delete(0, "end")
        app._budgetEditor.insert(0, "3")
        app._closeBudgetEditor(False)
        assert app.budgetVar.get() == 2
        app._editBudget()
        app._budgetEditor.delete(0, "end")
        app._budgetEditor.insert(0, "999")
        app._closeBudgetEditor(True)
        assert app.budgetVar.get() == app.budgetLimit

    def testDragTheLine(self, app: MidiEditorApp, monkeypatch: pytest.MonkeyPatch) -> None:
        """The budget line is grabbed within a few pixels and dragged in whole voices."""
        app.budgetVar.set(3)
        app._drawDemand()
        calls = self._countRecomputes(app, monkeypatch)
        base, scale = app._stripScale
        y = base - 3 * scale
        assert app._nearBudgetLine(y + BUDGET_GRAB)
        assert not app._nearBudgetLine(y + BUDGET_GRAB + 2)
        app._onDemandMotion(FakeEvent(x=10, y=y))
        assert app.budgetLineHover
        assert str(app.demandCanvas.cget("cursor")) == "sb_v_double_arrow"
        app._onStripPress(FakeEvent(x=10, y=y, num=1))
        assert app.budgetDragging
        app._onStripDrag(FakeEvent(x=10, y=base - 2 * scale))
        assert app.budgetVar.get() == 2
        app._onBudgetRelease()
        assert calls == [2]

    def testSeveralPoolsLockTheBudget(self, app: MidiEditorApp) -> None:
        """With several pools the budget is their total: greyed, and nothing changes it."""
        app.addPool()
        app.poolVars[1].set("pool 2")
        app._channelPoolChanged(1)
        app._drawDemand()
        total = app.budgetVar.get()
        assert not app.budgetEditable
        assert str(app.budgetBox.cget("foreground")) == theme.PALETTE["muted"]
        assert (str(app.budgetArrows["budgetUp"].cget("image"))
                == str(app.paintedImages["budgetUpDisabled"]))
        app._pressBudgetArrow(FakeEvent(num=1), "budgetUp", 1)
        app._onBudgetWheel(FakeEvent(num=4))
        app._editBudget()
        assert app._budgetEditor is None
        assert app._stripScale is not None
        base, scale = app._stripScale
        assert not app._nearBudgetLine(base - total * scale)
        assert app.budgetVar.get() == total


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

    def testExportZsm(self, app: MidiEditorApp, tmp_path: Path,
                      monkeypatch: pytest.MonkeyPatch) -> None:
        """Export through the ZSM target writes a file that verifies clean and is described."""
        out = tmp_path / "song.zsm"
        monkeypatch.setattr("montyroll.app.filedialog.asksaveasfilename", lambda **k: str(out))
        errors: list[str] = []
        monkeypatch.setattr("montyroll.app.messagebox.showerror",
                            lambda title, text, **k: errors.append(text))

        # With no tiers the export is refused and explains itself.
        app.exportTarget("zsm")
        assert not out.exists()
        assert errors and "no tier" in errors[0] and "preset" in errors[0]

        app._applyTiers(list(X16_TIERS))
        app.poolTierVar.set("opm")
        app._reductionChanged()
        app.exportTarget("zsm")
        assert out.exists()
        decoded = zsmdecode.decode(out.read_bytes())
        assert zsmcheck.check(decoded) == []
        assert decoded.header.fmMask and not decoded.header.psgMask
        status = app.status.cget("text")
        assert status.startswith("Exported ") and "no problems" in status
        assert f"{len(app.reduction.placed)} notes, no problems" in status

        # The toolbar's master volume is the export's level.
        app.masterVar.set(60)
        app.exportTarget("zsm")
        assert "at 60% level" in app.status.cget("text")
        assert zsmcheck.check(zsmdecode.decode(out.read_bytes())) == []


class TestWeightShading:
    """The Shade by weight switch on the Reduce panel."""

    def _brightness(self, colour: str) -> int:
        """Sum of the RGB components of a #rrggbb colour."""
        total = sum(int(colour[i:i + 2], 16) for i in (1, 3, 5))
        return total

    def testShadingFollowsTheWeights(self, app: MidiEditorApp) -> None:
        """A quieter note that carries more weight is darker by velocity and brighter by weight."""
        assert not app.shadeVar.get()
        weights = app._ensureWeights()
        louder, heavier = next((a, b) for a in app.song.notes for b in app.song.notes
                               if a.channel == b.channel and a.velocity > b.velocity
                               and weights[id(a)] < weights[id(b)])
        fill = lambda n: app.canvas.itemcget(app.noteToItem[id(n)], "fill")   # noqa: E731
        assert self._brightness(fill(louder)) > self._brightness(fill(heavier))
        assert fill(louder) == _shade(CHANNEL_COLORS[louder.channel],
                                      0.65 + 0.35 * louder.velocity / 127)
        app.shadeVar.set(True)
        app.redraw()
        assert self._brightness(fill(louder)) < self._brightness(fill(heavier))
        heaviest = max(weights.values())
        assert fill(heavier) == _shade(CHANNEL_COLORS[heavier.channel],
                                       0.3 + 0.7 * weights[id(heavier)] / heaviest)
        app.shadeVar.set(False)
        app.redraw()
        assert self._brightness(fill(louder)) > self._brightness(fill(heavier))

    def testPriorityBrightensItsChannel(self, app: MidiEditorApp) -> None:
        """Raising a channel's priority recomputes the weights and lifts its notes."""
        app.shadeVar.set(True)
        app.redraw()
        note = next(n for n in app.song.notes if n.channel == 1)
        before = self._brightness(app.canvas.itemcget(app.noteToItem[id(note)], "fill"))
        first = app._ensureWeights()
        app.priorityVars[1].set(5.0)
        app._reductionChanged()
        assert app._ensureWeights() is not first
        after = self._brightness(app.canvas.itemcget(app.noteToItem[id(note)], "fill"))
        assert after > before

    def testHoverNamesTheWeight(self, app: MidiEditorApp) -> None:
        """The status bar carries the weight only while shading by it."""
        note = app.song.notes[0]
        app._describeNote(note)
        assert "weight" not in app.status.cget("text")
        app.shadeVar.set(True)
        app._describeNote(note)
        assert f"weight {app._ensureWeights()[id(note)]:.2f}" in app.status.cget("text")


class TestPlanControls:
    """Pools, tiers and releases on the panel and the cards, and the plan sidecar."""

    def testDefaultPlanIsOnePoolOfEveryChannel(self, app: MidiEditorApp) -> None:
        """A file with no sidecar starts with one pool, on every card, at the strip's budget."""
        assert [p.name for p in app.plan.pools] == ["all"]
        assert app.plan.pools[0].channels == [0, 1, 9]
        assert app.plan.pools[0].voices == app.budgetVar.get()
        assert all(v.get() == "all" for v in app.poolVars.values())
        assert app.poolVar.get() == "all"
        assert app.editPool is app.plan.pools[0]
        assert app.budgetEditable

    def testBudgetAndPoolVoicesAreOneNumber(self, app: MidiEditorApp) -> None:
        """With one pool the strip's budget and the panel's Voices mirror each other."""
        app.budgetVar.set(3)
        assert app.poolVoicesVar.get() == 3
        app.poolVoicesVar.set(5)
        assert app.budgetVar.get() == 5
        app._reductionChanged()
        assert app.plan.pools[0].voices == 5

    def testAddPoolAndMoveAChannel(self, app: MidiEditorApp) -> None:
        """A second pool takes a channel from the first; the strip shows the total, read-only."""
        app.addPool()
        assert [p.name for p in app.plan.pools] == ["all", "pool 2"]
        assert app.editPool is app.plan.pools[1]
        assert NO_POOL in app.poolCombos[0].cget("values")
        app.poolVars[1].set("pool 2")
        app._channelPoolChanged(1)
        assert app.plan.pools[0].channels == [0, 9]
        assert app.plan.pools[1].channels == [1]
        app._drawDemand()
        assert not app.budgetEditable
        assert app.budgetVar.get() == app._totalVoices()
        app.poolVars[1].set(NO_POOL)
        app._channelPoolChanged(1)
        assert all(1 not in p.channels for p in app.plan.pools)
        app.removePool()
        assert [p.name for p in app.plan.pools] == ["all"]
        assert app._currentPlan().pools[0].channels == [0, 9]
        app.removePool()
        assert len(app.plan.pools) == 1

    def testPanelEditsTheChosenPool(self, app: MidiEditorApp) -> None:
        """The switches and Voices go to the edited pool and leave the other alone."""
        app.addPool()
        app.poolVar.set("all")
        app._poolSelected()
        assert app.editPool is app.plan.pools[0]
        app.monoTopVar.set(True)
        app.poolVoicesVar.set(4)
        app._reductionChanged()
        assert app.plan.pools[0].monoTop and app.plan.pools[0].voices == 4
        assert not app.plan.pools[1].monoTop and app.plan.pools[1].voices == 1
        app.poolVar.set("pool 2")
        app._poolSelected()
        assert not app.monoTopVar.get() and app.poolVoicesVar.get() == 1

    def testRenamePool(self, app: MidiEditorApp) -> None:
        """A name typed into the pool box and Return renames the pool everywhere."""
        app.poolVar.set("strings")
        app._renamePool()
        assert app.plan.pools[0].name == "strings"
        assert all(v.get() == "strings" for v in app.poolVars.values())
        assert "strings" in app.poolCombos[0].cget("values")
        app.addPool()
        app.poolVar.set("strings")
        app._renamePool()
        assert [p.name for p in app.plan.pools] == ["strings", "pool 2"]
        assert app.poolVar.get() == "pool 2"

    def testTiersDrawALinePerTier(self, app: MidiEditorApp) -> None:
        """Tiers applied and pools assigned: one labelled budget line per tier."""
        assert app._applyTiers(list(X16_TIERS)) is None
        assert app.tierCombo.cget("values") == ("(none)", "opm", "psg")
        app.addPool()
        app.poolVars[9].set("pool 2")
        app._channelPoolChanged(9)
        app.poolVar.set("all")
        app._poolSelected()
        app.poolTierVar.set("opm")
        app.poolVoicesVar.set(6)
        app._reductionChanged()
        app.poolVar.set("pool 2")
        app._poolSelected()
        app.poolTierVar.set("psg")
        app.poolVoicesVar.set(2)
        app._reductionChanged()
        app._drawDemand()
        c = app.demandCanvas
        labels = [c.itemcget(i, "text") for i in c.find_all() if c.type(i) == "text"]
        assert "opm 6" in labels and "psg 8" in labels
        dashed = [i for i in c.find_all() if c.type(i) == "line" and c.itemcget(i, "dash")]
        assert len(dashed) == 2
        app._onDemandMotion(FakeEvent(x=int(100 * app.ppt)))
        assert "opm " in app.status.cget("text") and "/6" in app.status.cget("text")
        assert app._stackOrder(app._ensureDemand().channels) == [0, 1, 9]

    def testOverfullTiersAreRefused(self, app: MidiEditorApp) -> None:
        """Tiers the pools no longer fit are refused with a reason and nothing changes."""
        assert app._applyTiers([reduce.Tier("tiny", 2)]) is None
        app.poolTierVar.set("tiny")
        app.poolVoicesVar.set(2)
        app._reductionChanged()
        assert app.plan.pools[0].tier == "tiny"
        problem = app._applyTiers([reduce.Tier("tiny", 1)])
        assert problem and "tiny" in problem
        assert app.plan.tiers == [reduce.Tier("tiny", 2)]
        assert app._applyTiers([]) is None
        assert app.plan.pools[0].tier == ""

    def testRefusedPlanIsReported(self, app: MidiEditorApp) -> None:
        """A pool pushed past its tier shows up on the label, not as a crash."""
        app._applyTiers([reduce.Tier("tiny", 2)])
        app.poolTierVar.set("tiny")
        app.poolVoicesVar.set(3)
        app.reduceVar.set(True)
        app._reductionChanged()
        assert app.lossLbl.cget("text").startswith("plan refused")
        assert app.reduction.placed == []

    def testReleaseOnACard(self, app: MidiEditorApp) -> None:
        """A release typed on a card reaches the plan and stretches the strip's occupancy."""
        app.reduceVar.set(True)
        app._reductionChanged()
        before = analysis.channelDemand(app._ensureDemand())[0].fraction
        app.releaseVars[0].set("500")
        app._channelReleaseChanged(0)
        assert app.plan.release == {0: 500.0}
        assert analysis.channelDemand(app._ensureDemand())[0].fraction > before
        assert "tails clipped" in app.lossLbl.cget("text")
        app.releaseVars[0].set("")
        app._channelReleaseChanged(0)
        assert app.plan.release == {}

    def testSidecarRoundTrip(self, app: MidiEditorApp, songPath: Path) -> None:
        """Save Plan writes beside the song and opening the song again loads it back."""
        app._applyTiers(list(X16_TIERS))
        app.addPool()
        app.poolVars[9].set("pool 2")
        app._channelPoolChanged(9)
        app.poolTierVar.set("psg")
        app._reductionChanged()
        app.priorityVars[0].set(2.5)
        app.releaseVars[1].set("40")
        app._channelReleaseChanged(1)
        app.savePlan()
        sidecar = Path(reduce.sidecarPath(str(songPath)))
        assert sidecar.exists()
        assert "Saved plan" in app.status.cget("text")
        saved = app._currentPlan()
        app.openFile(str(songPath))
        assert "Loaded plan" in app.status.cget("text")
        assert app.plan is not saved
        assert app.plan.tiers == saved.tiers
        assert app.plan.pools == saved.pools
        assert app.plan.priority[0] == 2.5 and app.plan.release == {1: 40.0}
        assert app.poolVars[9].get() == "pool 2"
        assert app.priorityVars[0].get() == 2.5
        assert app.releaseVars[1].get() == "40"
        assert app.budgetVar.get() == app._totalVoices()

    def testLoadPlanOpensTheSongItNames(self, app: MidiEditorApp, songPath: Path, tmp_path: Path,
                                        monkeypatch: pytest.MonkeyPatch) -> None:
        """Load Plan on a .monty that names a MIDI file opens that file, then applies the plan."""
        app.priorityVars[0].set(3.0)
        app.savePlan()
        sidecar = Path(reduce.sidecarPath(str(songPath)))
        assert sidecar.suffix == ".monty"
        assert '"midiFile": "song.mid"' in sidecar.read_text()

        # Another song is open; loading the plan brings the named one back.
        other = tmp_path / "other.mid"
        smf.write(app.song.mf, str(other))
        app.openFile(str(other))
        assert Path(app.song.path).name == "other.mid"
        monkeypatch.setattr("montyroll.app.filedialog.askopenfilename", lambda **k: str(sidecar))
        app.loadPlanDialog()
        assert Path(app.song.path).name == "song.mid"
        assert app.plan.priority[0] == 3.0
        assert "opened" in app.status.cget("text")

    def testBundleOpensWithoutItsFile(self, app: MidiEditorApp, songPath: Path, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
        """A bundle carries the song: it loads from an empty directory and keeps the edits."""
        app.selectAll()
        app.deleteSelection()
        notes = len(app.song.notes)
        bundle = tmp_path / "away" / "bundle.monty"
        bundle.parent.mkdir()
        monkeypatch.setattr("montyroll.app.filedialog.asksaveasfilename", lambda **k: str(bundle))
        app.savePlanBundleAs()
        assert '"midiBlob"' in bundle.read_text()
        monkeypatch.setattr("montyroll.app.messagebox.askyesno", lambda *a, **k: True)
        app.newFile()
        assert not app.song.notes
        monkeypatch.setattr("montyroll.app.filedialog.askopenfilename", lambda **k: str(bundle))
        app.loadPlanDialog()
        assert len(app.song.notes) == notes
        assert "embedded" in app.status.cget("text")

    def testSoundBoxIsSavedAndUsed(self, app: MidiEditorApp, songPath: Path, tmp_path: Path,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
        """A pool's sound goes into the plan's target section, is saved, and reaches the export."""
        assert str(app.soundCombo.cget("state")) == "disabled"      # no chip, no sounds
        app._applyTiers(list(X16_TIERS))
        app.poolTierVar.set("opm")
        app._reductionChanged()
        assert "strings" in app.soundCombo.cget("values")
        app.poolSoundVar.set("brass")
        app._poolSoundChanged()
        assert app.planTargets == {"zsm": {"sounds": {"all": "brass"}}}
        app.poolVar.set("core")
        app._renamePool()
        assert app.planTargets["zsm"]["sounds"] == {"core": "brass"}
        app.savePlan()
        assert '"brass"' in Path(reduce.sidecarPath(str(songPath))).read_text()
        app.openFile(str(songPath))
        assert app.planTargets["zsm"]["sounds"] == {"core": "brass"}
        assert app.poolSoundVar.get() == "brass"
        out = tmp_path / "brass.zsm"
        monkeypatch.setattr("montyroll.app.filedialog.asksaveasfilename", lambda **k: str(out))
        app.exportTarget("zsm")
        decoded = zsmdecode.decode(out.read_bytes())
        setup = {w.register: w.value for w in decoded.frames[0].items
                 if isinstance(w, zsmdecode.Write) and w.chip == "opm"}
        # Every operator register but the carriers' levels, which the first
        # note's velocity sets, matches the brass patch.
        brass = dict(zsm.patchWrites(0, zsm.PATCHES["brass"]))
        assert all(setup[r] == v for r, v in brass.items() if 0x40 <= r and not 0x60 <= r < 0x80)
        assert setup[0x40] == brass[0x40]

    def testExportRangeIsKeptAndUsed(self, app: MidiEditorApp, songPath: Path, tmp_path: Path,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
        """The range dialog's bars are checked, saved with the plan and cut the export."""
        assert app._applyExportRange({"start": "2", "end": "1", "loop": ""}) is not None
        assert app._applyExportRange({"start": "x", "end": "", "loop": ""}) is not None
        assert app._applyExportRange({"start": "2", "end": "3", "loop": "4"}) is not None
        assert app._applyExportRange({"start": "0", "end": "", "loop": ""}) is not None
        assert app._applyExportRange({"start": "2", "end": "3", "loop": " 2 "}) is None
        assert app.exportRange == {"start": 2, "end": 3, "loop": 2}
        app.savePlan()
        assert '"range"' in Path(reduce.sidecarPath(str(songPath))).read_text()
        app.openFile(str(songPath))
        assert app.exportRange == {"start": 2, "end": 3, "loop": 2}

        app._applyTiers(list(X16_TIERS))
        app.poolTierVar.set("opm")
        app._reductionChanged()
        out = tmp_path / "cut.zsm"
        monkeypatch.setattr("montyroll.app.filedialog.asksaveasfilename", lambda **k: str(out))
        app.exportTarget("zsm")
        decoded = zsmdecode.decode(out.read_bytes())
        whole = (zsm.frameOf(app.song, app.song.barTick(4))
                 - zsm.frameOf(app.song, app.song.barTick(2)))
        assert decoded.length <= whole
        assert decoded.header.loopOffset == 16     # bar 2 is the start, so the loop is frame 0
        assert "bars 2 to 3 looping to 2" in app.status.cget("text")

    def testBadSidecarIsReported(self, app: MidiEditorApp, songPath: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
        """A broken plan file beside the song is reported and the default plan stands."""
        Path(reduce.sidecarPath(str(songPath))).write_text("{oops")
        errors: list[tuple[Any, ...]] = []
        monkeypatch.setattr("montyroll.app.messagebox.showerror",
                            lambda *a, **k: errors.append(a))
        app.openFile(str(songPath))
        assert errors and errors[0][0] == "Plan not loaded"
        assert [p.name for p in app.plan.pools] == ["all"]

    def testOverlapFollowsTheEditedPool(self, app: MidiEditorApp) -> None:
        """Cards show their overlap with the edited pool; the panel shows the pool's worst."""
        shares = analysis.poolOverlap(app._ensureSongDemand(), [0, 1, 9])
        worst = max(shares[ch] for ch in (0, 1, 9))
        assert app.overlapLbl.cget("text") == f"all: its channels overlap {worst:.0%} at most"

        for ch in (0, 1, 9):
            assert app.usageLabels[ch].cget("text").endswith(f"| overlaps all {shares[ch]:.0%}")

        # Move channel 1 out: the pool "all" is now measured without it and
        # channel 1 against all of it.
        app.poolVars[1].set(NO_POOL)
        app._channelPoolChanged(1)
        shares = analysis.poolOverlap(app._ensureSongDemand(), [0, 9])
        assert app.usageLabels[1].cget("text").endswith(f"| overlaps all {shares[1]:.0%}")
        assert f"{max(shares[0], shares[9]):.0%}" in app.overlapLbl.cget("text")

        # An empty new pool, then one channel in it.
        app.addPool()
        assert app.overlapLbl.cget("text") == "pool 2: no channels"
        app.poolVars[1].set("pool 2")
        app._channelPoolChanged(1)
        assert app.overlapLbl.cget("text") == "pool 2: one channel"
        against = analysis.poolOverlap(app._ensureSongDemand(), [1])
        assert app.usageLabels[0].cget("text").endswith(f"| overlaps pool 2 {against[0]:.0%}")

        # Editing the notes refreshes the figures at idle.
        app.selectAll()
        app.deleteSelection()
        app.update()
        assert app.overlapLbl.cget("text") == ""
        assert app.usageLabels[0].cget("text") == app.usageText[0]

    def testReducedReportCarriesTheTotals(self, app: MidiEditorApp) -> None:
        """The reduced report's second line is the kept, cut, clipped and loss line."""
        app.budgetVar.set(2)
        app.reduceVar.set(True)
        app._reductionChanged()
        text = app._ensureReport("reduced")
        assert text.splitlines()[1].startswith("kept ")
        assert "tails clipped" in text.splitlines()[1]


class TestArrangeMenu:
    """Split, double and echo from the Edit menu."""

    def testSplitReplacesTheSong(self, app: MidiEditorApp,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
        """The active channel's polyphony spreads over new channels; the song is dirty."""
        monkeypatch.setattr("montyroll.app.simpledialog.askinteger", lambda *a, **k: 2)
        app._setActiveChannel(0)
        path = app.song.path
        before = len(app.song.notes)
        app.splitChannelDialog()
        assert len(app.song.notes) == before
        assert app.song.dirty and app.song.path == path
        assert any(n.channel == 3 for n in app.song.notes)   # the first note-free channel
        assert "Split Ch 1 into 2 parts" in app.status.cget("text")

    def testDoubleAddsAChannel(self, app: MidiEditorApp,
                               monkeypatch: pytest.MonkeyPatch) -> None:
        """The copy lands an octave up on the lowest free channel."""
        monkeypatch.setattr("montyroll.app.simpledialog.askinteger", lambda *a, **k: 12)
        app._setActiveChannel(0)
        originals = [n.pitch for n in app.song.notes if n.channel == 0]
        app.doubleChannelDialog()
        copies = [n.pitch for n in app.song.notes if n.channel == 3]
        assert sorted(copies) == sorted(p + 12 for p in originals if p + 12 <= 127)

    def testEchoDelaysOntoAFreeChannel(self, app: MidiEditorApp,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
        """The copy lags by the asked delay at lower velocity."""
        monkeypatch.setattr("montyroll.app.simpledialog.askinteger", lambda *a, **k: 240)
        app._setActiveChannel(0)
        starts = sorted(n.start for n in app.song.notes if n.channel == 0)
        app.echoChannelDialog()
        copies = sorted(n.start for n in app.song.notes if n.channel == 3)
        assert copies == [t + 240 for t in starts]

    def testRefusalIsReported(self, app: MidiEditorApp,
                              monkeypatch: pytest.MonkeyPatch) -> None:
        """A channel with no notes reports the error and the song stands."""
        monkeypatch.setattr("montyroll.app.simpledialog.askinteger", lambda *a, **k: 3)
        errors: list[str] = []
        monkeypatch.setattr("montyroll.app.messagebox.showerror",
                            lambda title, text, **k: errors.append(text))
        app._setActiveChannel(5)
        song = app.song
        app.splitChannelDialog()
        assert errors and "no notes" in errors[0]
        assert app.song is song


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


class TestDrawer:
    """Events and Report in the drawer under the roll."""

    def testClosedAtStart(self, app: MidiEditorApp) -> None:
        """The window opens with the drawer shut and both buttons pointing up."""
        assert not app.drawerOpen
        assert not app.nb.winfo_ismapped()
        assert all(b.cget("text").startswith(DRAWER_OPEN_GLYPH)
                   for b in app.drawerButtons.values())

    def testButtonOpensAndClosesItsPage(self, app: MidiEditorApp) -> None:
        """A button opens its page; the same button again closes the drawer."""
        app.toggleDrawer("Events")
        app.update()
        assert app.drawerOpen and app.nb.winfo_ismapped()
        assert app.drawerButtons["Events"].cget("text").startswith(DRAWER_CLOSE_GLYPH)
        app.toggleDrawer("Report")
        assert app.drawerOpen
        assert app.drawerButtons["Report"].cget("text").startswith(DRAWER_CLOSE_GLYPH)
        assert app.drawerButtons["Events"].cget("text").startswith(DRAWER_OPEN_GLYPH)
        app.toggleDrawer("Report")
        app.update()
        assert not app.drawerOpen and not app.nb.winfo_ismapped()

    def testEventCount(self, app: MidiEditorApp) -> None:
        """The bar counts every event in the file."""
        total = sum(len(track) for track in app.song.mf.tracks)
        assert app.eventCountLbl.cget("text") == f"{total:,} events"


class TestReportTab:
    """The Report tab: Original and Reduced sub-tabs and the Save button."""

    def testOriginalIsGeneratedWhenShown(self, app: MidiEditorApp) -> None:
        """Nothing is generated until the tab is selected; then the text appears."""
        assert app.reports == {"original": None, "reduced": None}
        app.openDrawer("Report")
        app.update()
        text = app.reportTexts["original"].get("1.0", "end")
        assert text.startswith("MontyRoll report: song.mid")
        assert "Instruments sounding at once" in text
        assert app.reportTexts["original"].cget("state") == "disabled"
        assert app.reports["reduced"] is None

    def testReducedIsANoteWhileReductionIsOff(self, app: MidiEditorApp) -> None:
        """The Reduced sub-tab explains itself until the reduction is on."""
        app.openDrawer("Report")
        app.reportNb.select(1)
        app.update()
        assert app.reportTexts["reduced"].get("1.0", "end").startswith("Reduction is off.")

    def testReducedFollowsTheReduction(self, app: MidiEditorApp) -> None:
        """With Reduce on, the Reduced sub-tab describes the reduced song and says so."""
        app.openDrawer("Report")
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
        app.openDrawer("Report")
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
        app.openDrawer("Report")
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

    def testReadoutWhenStopped(self, app: MidiEditorApp) -> None:
        """Stopped, the readout shows the opening position and tempo."""
        assert app.timeLbl.cget("text") == "0:00.00"
        bpm = round(app.song.tempoAt(0))
        assert app.posLbl.cget("text") == f"bar 1:1   {TEMPO_GLYPH}={bpm}"

    def testReadoutFollowsPlayback(self, app: MidiEditorApp,
                                   monkeypatch: pytest.MonkeyPatch) -> None:
        """Playing, the readout shows the cursor; stopping puts the opening position back."""
        calls: list[int] = []
        monkeypatch.setattr(app.player, "play", lambda *a, **k: calls.append(1) or True)
        monkeypatch.setattr(player, "findPlayer", lambda: ["fakesynth"])
        monkeypatch.setattr(type(app.player), "playing", property(lambda self: bool(calls)))
        bar2 = app.song.barTick(2)
        app.playFrom(bar2)
        app._tickCursor()
        assert app.posLbl.cget("text").startswith("bar 2:1")
        assert app.timeLbl.cget("text") != "0:00.00"
        app.stopPlayback()
        assert app.timeLbl.cget("text") == "0:00.00"
        assert app.posLbl.cget("text").startswith("bar 1:1")

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
        app.onContext(FakeEvent(*_toWidget(app, int(3500 * app.ppt), 3)))   # late in bar 2, empty
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
