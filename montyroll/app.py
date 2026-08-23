# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""MontyRoll: tkinter MIDI visualiser/editor.

Layout: channel strip on the left (colour, instrument, mute/solo), a
zoomable piano-roll in the middle tab, a raw event list in the second tab,
transport + grid controls in the toolbar and a status bar that tracks the
mouse in musical time.

This is the only module that knows about tkinter. Musical state lives in
`model.Song`, and sound comes from an external synth process managed by
`player.Player`; nothing can be pushed into a running synth, so every live
change during playback (mute, solo, volume, speed) respawns it from the
cursor position rather than talking to the running process.
"""

from __future__ import annotations

import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

from . import gm, model, player, smf

CHANNEL_COLORS = [
    "#ff4b3e", "#ff9518", "#ffe014", "#a4ff2e", "#2eff6e", "#14ffd0",
    "#29c5ff", "#4b6bff", "#8d4bff", "#c92eff", "#ff2ee0", "#ff2e7c",
    "#ffb46b", "#96ff96", "#6bd9ff", "#e09bff",
]

KEYS_W = 52          # piano keyboard gutter width
RULER_H = 44         # seconds row + bar row + tempo/marker row
SNAP_CHOICES = ["1/1", "1/2", "1/4", "1/8", "1/16", "1/32", "off"]
MAX_EVENT_ROWS = 8000

# UI glyphs kept as escapes so the source stays plain ASCII.
PLAY_GLYPH = "\u25b6"       # black right-pointing triangle on the Play button
STOP_GLYPH = "\u25a0"       # black square on the Stop button
PREVIEW_GLYPH = "\u266a"    # eighth note on each channel's preview button
TEMPO_GLYPH = "\u2669"      # quarter note before a bpm figure in the ruler and readout
MINUS = "\u2212"            # true minus sign, the same width as "+" on the zoom buttons
ARROW = "\u2192"            # rightwards arrow in the "Ch 1 -> Flute" status text
NEWLINE_GLYPH = "\u23ce"    # return symbol standing in for newlines in event text


def _shade(color: str, factor: float) -> str:
    """Scale each component of a "#rrggbb" colour by a factor.

    Args:
        color: hex colour string in "#rrggbb" form.
        factor: multiplier applied to the red, green and blue components; 1.0
            leaves the colour unchanged and smaller values darken it.

    Returns:
        str: the scaled colour in "#rrggbb" form.
    """
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    shaded = f"#{int(r * factor):02x}{int(g * factor):02x}{int(b * factor):02x}"
    return shaded


class Tooltip:
    """Hover help: shows `text` in a small popup after a short delay.

    The popup is an undecorated Toplevel placed just below the widget. It is
    scheduled on <Enter> and torn down on <Leave> or any button press, so a
    click on the widget dismisses the tip instead of leaving it floating over
    whatever the click opened. The bindings are added with `add="+"` so the
    widget keeps any handlers of its own.
    """

    def __init__(self, widget, text: str, delay: int = 500):
        """Attach hover help to a widget.

        Args:
            widget: the tkinter widget to annotate.
            text: the help text; newlines produce a multi-line tip.
            delay: milliseconds the pointer must rest on the widget before
                the popup appears.
        """
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip: tk.Toplevel | None = None
        self.afterId: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        """Start (or restart) the delay timer when the pointer enters.

        Args:
            _event: the <Enter> event; unused.
        """
        self._cancel()
        self.afterId = self.widget.after(self.delay, self._show)

    def _cancel(self):
        """Cancel a pending show timer, if one is running."""
        if self.afterId:
            self.widget.after_cancel(self.afterId)
            self.afterId = None

    def _show(self):
        """Create the popup below the widget once the delay has elapsed.

        Nothing happens when a tip is already showing or the widget was
        destroyed while the timer was pending.
        """
        if self.tip or not self.widget.winfo_exists():
            return

        # Place the tip a little below the widget's bottom-left corner, in
        # screen coordinates since the Toplevel is not managed by any parent.
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tk.Toplevel(self.widget)
        self.tip.wm_overrideredirect(True)
        self.tip.wm_geometry(f"+{x}+{y}")
        tk.Label(self.tip, text=self.text, justify="left", bg="#ffffe0",
                 fg="#000", relief="solid", bd=1, padx=6, pady=3,
                 font=("TkDefaultFont", 9)).pack()

    def _hide(self, _event=None):
        """Cancel any pending timer and take down the popup.

        Args:
            _event: the <Leave> or <ButtonPress> event; unused.
        """
        self._cancel()

        if self.tip:
            self.tip.destroy()
            self.tip = None


class MidiEditorApp(tk.Tk):
    """Main window: channel strip, piano roll, event list and transport.

    The app owns one `model.Song` and redraws the piano roll in full on every
    structural change (load, add, delete, zoom, mute), mapping canvas items
    to notes through `itemToNote` and `noteToItem`. While a drag is in
    progress it moves the existing items with direct coordinate updates
    instead of redrawing.

    Playback runs in an external synth process through `player.Player`.
    Nothing can be pushed into a running synth, so every live change (mute,
    solo, channel volume, master volume, speed) restarts it from the cursor
    position using `Song.buildSlice`; `_liveUpdate` debounces those restarts.
    Cursor time is `playOffset + (monotonic() - playStart) * playSpeed`. A
    second `Player` handles instrument previews so auditioning a sound never
    interrupts playback.
    """

    def __init__(self, path: str | None = None):
        """Build the window and open `path` if one was given.

        Args:
            path: MIDI file to open at start-up; None starts with an empty
                song.
        """
        super().__init__()
        self.geometry("1280x760")

        # The model and two synth processes: one for playback and a separate
        # one for instrument previews, so auditioning never stops the music.
        self.song = model.Song.new()
        self.player = player.Player()
        self.preview = player.Player()   # separate synth for instrument previews

        # Selection plus the canvas-item <-> note maps that redraw() refills.
        self.selection: set[model.Note] = set()
        self.itemToNote: dict[int, model.Note] = {}
        self.noteToItem: dict[int, int] = {}

        # Channel strip state.
        self.muteVars: dict[int, tk.BooleanVar] = {}
        self.soloVars: dict[int, tk.BooleanVar] = {}
        self.channelRows: dict[int, tk.Frame] = {}
        self.activeChannel = 0

        # View geometry and the drag in progress, if any.
        self.zoomX = 60.0            # pixels per quarter note
        self.rowH = 10
        self.drag = None

        # Playback clock: cursor time is
        # playOffset + (monotonic() - playStart) * playSpeed, and playStart is
        # None whenever nothing is playing. _replayAfter is the pending
        # debounced restart from _liveUpdate.
        self.playStart: float | None = None
        self.playOffset = 0.0          # song seconds where playback began
        self.playSpeed = 1.0           # speed factor of the running synth
        self._replayAfter: str | None = None
        self.cursorItem = None

        # The status bar is packed before the body on purpose: tkinter starves
        # the last-packed widget when space runs short, and a bottom bar
        # packed after the expanding body silently drops off the window.
        self._buildMenu()
        self._buildToolbar()
        self._buildStatusbar()   # before body: last-packed widgets get squeezed out
        self._buildBody()
        self._bindKeys()

        if path:
            self.openFile(path)
        else:
            self._refreshAll()

    # ------------------------------------------------------------------ UI
    def _buildMenu(self):
        """Create the File and Edit menus and route window close to onClose."""
        m = tk.Menu(self)

        # The accelerator captions are display only; the key bindings that
        # back them live in _bindKeys.
        fm = tk.Menu(m, tearoff=0)
        fm.add_command(label="New", accelerator="Ctrl+N", command=self.newFile)
        fm.add_command(label="Open...", accelerator="Ctrl+O", command=self.openDialog)
        fm.add_command(label="Save", accelerator="Ctrl+S", command=self.save)
        fm.add_command(label="Save As...", command=self.saveAs)
        fm.add_separator()
        fm.add_command(label="Quit", command=self.onClose)
        m.add_cascade(label="File", menu=fm)

        em = tk.Menu(m, tearoff=0)
        em.add_command(label="Select All", accelerator="Ctrl+A", command=self.selectAll)
        em.add_command(label="Delete Selection", accelerator="Del", command=self.deleteSelection)
        em.add_command(label="Set Velocity...", command=self.setVelocityDialog)
        em.add_separator()
        em.add_command(label="Set Tempo at Bar...", command=self.setTempoDialog)
        em.add_command(label="Scale All Tempos...", command=self.scaleTemposDialog)
        m.add_cascade(label="Edit", menu=em)

        # Closing the window goes through the same unsaved-changes check as
        # File > Quit.
        self.config(menu=m)
        self.protocol("WM_DELETE_WINDOW", self.onClose)

    def _buildToolbar(self):
        """Build the toolbar across the top of the window.

        Left to right: Open and Save, Play/Stop, master volume, speed, the
        playback position readout, grid snap, the four zoom buttons and, at
        the right-hand end, the file summary label.
        """
        bar = ttk.Frame(self, padding=(6, 4))
        bar.pack(fill="x")

        b = ttk.Button(bar, text="Open", command=self.openDialog)
        b.pack(side="left")
        Tooltip(b, "Open a MIDI file (Ctrl+O)")

        b = ttk.Button(bar, text="Save", command=self.save)
        b.pack(side="left", padx=(4, 12))
        Tooltip(b, "Save the file (Ctrl+S)")

        # Transport: one button whose caption flips between Play and Stop.
        self.playBtn = ttk.Button(bar, text=f"{PLAY_GLYPH} Play", command=self.togglePlay)
        self.playBtn.pack(side="left")
        Tooltip(self.playBtn, "Play / stop (Space).\n"
                "Mute/solo and volume changes apply live:\n"
                "playback resumes from the cursor position.")

        # Master volume scales note velocities in the playback copy only. The
        # slider's command goes through _liveUpdate, so moving it during
        # playback restarts the synth once the drag settles.
        ttk.Label(bar, text="Vol:").pack(side="left", padx=(10, 0))
        self.masterVar = tk.IntVar(value=100)
        mv = tk.Scale(bar, from_=0, to=150, orient="horizontal", showvalue=0,
                      length=90, variable=self.masterVar,
                      command=lambda v: self._liveUpdate())
        mv.pack(side="left")
        Tooltip(mv, "Master playback volume (0-150%).\n"
                "Scales note velocities in the playback copy only -\n"
                "the file is not changed. Applies live while playing.")

        # Speed scales the tempo events in the playback copy. The label beside
        # the slider mirrors its value; _speedChanged updates it and then
        # feeds _liveUpdate like the other live controls.
        ttk.Label(bar, text="Speed:").pack(side="left", padx=(10, 0))
        self.speedVar = tk.IntVar(value=100)
        sv = tk.Scale(bar, from_=25, to=300, orient="horizontal", showvalue=0,
                      length=90, variable=self.speedVar,
                      command=lambda v: self._speedChanged())
        sv.pack(side="left")
        self.speedLbl = ttk.Label(bar, text="100%", width=5)
        self.speedLbl.pack(side="left")
        Tooltip(sv, "Playback speed (25-300%): scales the file's tempo\n"
                "in the playback copy only - pitch is unaffected and\n"
                "the file is not changed. Applies live while playing.\n"
                "Handy for files sequenced at the wrong base tempo.")

        # Position readout, refreshed by _tickCursor at 30 Hz while playing.
        # A fixed-width font and a reserved width keep the digits from
        # shuffling the rest of the toolbar as they change.
        self.posLbl = ttk.Label(bar, text="", width=26,
                                 font=("TkFixedFont", 9))
        self.posLbl.pack(side="left", padx=(10, 0))
        Tooltip(self.posLbl, "Playback position: time, bar:beat and current\n"
                "tempo from the file's tempo events. (If a file has\n"
                "no tempo events, this stays constant even when the\n"
                "music speeds up - the rubato is in the note spacing.)")

        # Grid snap: read back by _snapTicks whenever a note is moved,
        # resized or added.
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=10)
        ttk.Label(bar, text="Snap:").pack(side="left")
        self.snapVar = tk.StringVar(value="1/16")
        snap = ttk.Combobox(bar, textvariable=self.snapVar, values=SNAP_CHOICES,
                            width=5, state="readonly")
        snap.pack(side="left", padx=(2, 12))
        Tooltip(snap, "Grid snap for moving, resizing and adding notes.\n"
                "1/16 = sixteenth notes; 'off' = free placement.")

        # The four zoom buttons share one construction loop, with their
        # tooltips looked up by caption.
        zoom_tips = {
            "H+": "Zoom in horizontally (or Ctrl+mouse wheel)",
            f"H{MINUS}": "Zoom out horizontally (or Ctrl+mouse wheel)",
            "V+": "Taller note rows",
            f"V{MINUS}": "Shorter note rows",
        }

        for text, cmd in (("H+", lambda: self.zoom(1.25)), (f"H{MINUS}", lambda: self.zoom(0.8)),
                          ("V+", lambda: self.vzoom(2)), (f"V{MINUS}", lambda: self.vzoom(-2))):
            b = ttk.Button(bar, text=text, width=4, command=cmd)
            b.pack(side="left", padx=1)
            Tooltip(b, zoom_tips[text])

        # File summary at the right-hand end, filled in by _refreshAll.
        self.infoLbl = ttk.Label(bar, text="", anchor="e")
        self.infoLbl.pack(side="right")
        Tooltip(self.infoLbl, "File summary: format, tracks, ticks per quarter\n"
                "note, time signature, tempo, note count, duration")

    def _buildBody(self):
        """Build the paned body: channel strip on the left, notebook on the right.

        The channel strip is a canvas holding an inner frame, because sixteen
        channel cards never fit on screen and need a scrollbar. The notebook
        holds the piano roll (ruler, keyboard gutter, note canvas and
        scrollbars wired so the ruler and keys track the roll) and the raw
        event list.
        """
        paned = ttk.PanedWindow(self, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # -- channel strip
        # Header row with the title and the two bulk mute/solo buttons.
        left = ttk.Frame(paned, width=330)
        paned.add(left, weight=0)
        head = ttk.Frame(left, padding=(6, 4))
        head.pack(fill="x")
        ttk.Label(head, text="Channels",
                  font=("TkDefaultFont", 10, "bold")).pack(side="left")
        b = ttk.Button(head, text="Mute All", width=8, command=self.muteAll)
        b.pack(side="right", padx=(2, 0))
        Tooltip(b, "Mute every channel")
        b = ttk.Button(head, text="Clear", width=6, command=self.clearMuteSolo)
        b.pack(side="right")
        Tooltip(b, "Clear all mutes and solos")

        # Scrollable strip: the cards live in chanHolder, which is embedded in
        # chanCanvas through create_window. The holder's <Configure> keeps the
        # scrollregion in step with the cards' total height, and the canvas's
        # <Configure> stretches the holder to the canvas width so the cards
        # fill it edge to edge.
        scroller = ttk.Frame(left)
        scroller.pack(fill="both", expand=True)
        self.chanCanvas = tk.Canvas(scroller, highlightthickness=0, width=340)
        cbar = ttk.Scrollbar(scroller, orient="vertical",
                             command=self.chanCanvas.yview)
        self.chanCanvas.configure(yscrollcommand=cbar.set)
        cbar.pack(side="right", fill="y")
        self.chanCanvas.pack(side="left", fill="both", expand=True)
        self.chanHolder = ttk.Frame(self.chanCanvas)
        self._chanWindow = self.chanCanvas.create_window(
            (0, 0), window=self.chanHolder, anchor="nw")
        self.chanHolder.bind(
            "<Configure>",
            lambda e: self.chanCanvas.configure(
                scrollregion=self.chanCanvas.bbox("all")))
        self.chanCanvas.bind(
            "<Configure>",
            lambda e: self.chanCanvas.itemconfigure(self._chanWindow, width=e.width))

        # Wheel scrolling is bound on the canvas and the holder here, and on
        # every card in _buildChannelRows: X11 delivers wheel events to the
        # widget under the pointer, which is nearly always a card. Button-4/5
        # are the X11 wheel events; MouseWheel covers Windows and macOS.
        for w in (self.chanCanvas, self.chanHolder):
            for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
                w.bind(seq, self._onChanWheel)

        # -- notebook: piano roll + events
        self.nb = ttk.Notebook(paned)
        paned.add(self.nb, weight=1)

        # The roll is a 3x3 grid: ruler above the note canvas, keyboard gutter
        # to its left, scrollbars to the right and below. Row 1 / column 1
        # (the note canvas) take all the spare space.
        roll = ttk.Frame(self.nb)
        self.nb.add(roll, text="Piano Roll")
        roll.rowconfigure(1, weight=1)
        roll.columnconfigure(1, weight=1)
        self.ruler = tk.Canvas(roll, height=RULER_H, bg="#2a2a30", highlightthickness=0)
        self.ruler.grid(row=0, column=1, sticky="ew")
        self.keys = tk.Canvas(roll, width=KEYS_W, bg="#1c1c20", highlightthickness=0)
        self.keys.grid(row=1, column=0, sticky="ns")
        self.canvas = tk.Canvas(roll, bg="#18181c", highlightthickness=0)
        self.canvas.grid(row=1, column=1, sticky="nsew")

        # The ruler and keyboard gutter have no scrollbars of their own: the
        # scrollbars drive _xview/_yview, which move them with the canvas, and
        # the canvas's scroll callbacks move them again whenever the canvas
        # scrolls by itself (wheel, drag, zoom) so they never drift apart.
        vbar = ttk.Scrollbar(roll, orient="vertical", command=self._yview)
        vbar.grid(row=1, column=2, sticky="ns")
        hbar = ttk.Scrollbar(roll, orient="horizontal", command=self._xview)
        hbar.grid(row=2, column=1, sticky="ew")
        self.canvas.configure(
            xscrollcommand=lambda a, b: (hbar.set(a, b), self.ruler.xview_moveto(a)),
            yscrollcommand=lambda a, b: (vbar.set(a, b), self.keys.yview_moveto(a)))

        # Events tab: a Treeview with fixed column widths except for the
        # detail column, which stretches to fill the remaining width. Its
        # rows are sized from the default font: the ttk default is a fixed
        # 20 px, which clips the text on a display where Tk scales the font
        # taller than that.
        events = ttk.Frame(self.nb)
        self.nb.add(events, text="Events")
        defaultFont = tkfont.nametofont("TkDefaultFont")
        lineHeight = defaultFont.metrics("linespace")
        ttk.Style(self).configure("Treeview", rowheight=lineHeight + 4)
        cols = ("tick", "time", "bar", "track", "ch", "type", "detail")
        self.evTree = ttk.Treeview(events, columns=cols, show="headings")

        # Column widths are in characters of the default font, so they scale
        # with the display's DPI the same way the text does.
        charWidth = defaultFont.measure("0")
        widths = (8, 8, 7, 7, 5, 16, 48)

        for col, chars in zip(cols, widths):
            self.evTree.heading(col, text=col.capitalize())
            self.evTree.column(col, width=chars * charWidth, anchor="w",
                               stretch=(col == "detail"))

        ev_bar = ttk.Scrollbar(events, orient="vertical", command=self.evTree.yview)
        self.evTree.configure(yscrollcommand=ev_bar.set)
        self.evTree.pack(side="left", fill="both", expand=True)
        ev_bar.pack(side="right", fill="y")

        # Mouse bindings for the roll. Wheel events are bound on the ruler and
        # keys as well, so scrolling and Ctrl-zoom work wherever the pointer
        # sits within the roll.
        self.canvas.bind("<Button-1>", self.onPress)
        self.canvas.bind("<B1-Motion>", self.onDrag)
        self.canvas.bind("<ButtonRelease-1>", self.onRelease)
        self.canvas.bind("<Double-Button-1>", self.onDouble)
        self.canvas.bind("<Button-3>", self.onContext)
        self.ruler.bind("<Double-Button-1>", self.onRulerDouble)
        self.ruler.bind("<Button-3>", self.onRulerContext)
        self.canvas.bind("<Motion>", self.onMotion)

        for w in (self.canvas, self.keys, self.ruler):
            w.bind("<Button-4>", self.onWheel)
            w.bind("<Button-5>", self.onWheel)
            w.bind("<MouseWheel>", self.onWheel)

    def _buildStatusbar(self):
        """Create the status bar along the bottom of the window.

        Called before `_buildBody` on purpose: tkinter squeezes the last-packed
        widget when the window runs short of space, so a status bar packed
        after the expanding body silently disappears off the bottom.
        """
        self.status = ttk.Label(self, text="Ready", padding=(8, 3), relief="sunken")
        self.status.pack(fill="x", side="bottom")

    def _bindKeys(self):
        """Bind the keyboard shortcuts advertised in the menus and tooltips.

        They are bound on the toplevel so they work whichever widget has the
        keyboard focus.
        """
        self.bind("<Control-n>", lambda e: self.newFile())
        self.bind("<Control-o>", lambda e: self.openDialog())
        self.bind("<Control-s>", lambda e: self.save())
        self.bind("<Control-a>", lambda e: self.selectAll())
        self.bind("<Delete>", lambda e: self.deleteSelection())
        self.bind("<space>", lambda e: self.togglePlay())
        self.bind("<Escape>", lambda e: self._clearSelection())

    def _xview(self, *args):
        """Scroll the note canvas and the ruler together horizontally.

        Args:
            *args: scrollbar-style arguments, ("moveto", fraction) or
                ("scroll", count, "units" | "pages").
        """
        self.canvas.xview(*args)
        self.ruler.xview(*args)

    def _yview(self, *args):
        """Scroll the note canvas and the keyboard gutter together vertically.

        Args:
            *args: scrollbar-style arguments, ("moveto", fraction) or
                ("scroll", count, "units" | "pages").
        """
        self.canvas.yview(*args)
        self.keys.yview(*args)

    # ------------------------------------------------------------- file ops
    def newFile(self):
        """Replace the song with an empty one, after the unsaved-changes check."""
        if not self._confirmDiscard():
            return

        self.song = model.Song.new()
        self._refreshAll()

    def openDialog(self):
        """Ask for a MIDI file and open it, after the unsaved-changes check."""
        if not self._confirmDiscard():
            return

        path = filedialog.askopenfilename(
            title="Open MIDI file",
            filetypes=[("MIDI files", "*.mid *.midi *.MID"), ("All files", "*")])

        if path:
            self.openFile(path)

    def openFile(self, path: str):
        """Load a MIDI file into the editor and rebuild every view.

        A file that fails to parse is reported in an error dialog and the
        current song is left in place.

        Args:
            path: filesystem path of the MIDI file.
        """
        try:
            self.song = model.Song.load(path)
        except Exception as exc:
            messagebox.showerror("Open failed", f"{path}\n\n{exc}")
            return

        self._refreshAll()

    def save(self):
        """Save to the song's own path, or go through Save As when it has none."""
        if not self.song.path:
            self.saveAs()
            return

        self.song.save()
        self._updateTitle()
        self._setStatus(f"Saved {self.song.path}")

    def saveAs(self):
        """Ask for a destination and save there; cancelling changes nothing."""
        path = filedialog.asksaveasfilename(
            defaultextension=".mid",
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*")])

        if path:
            self.song.save(path)
            self._updateTitle()
            self._setStatus(f"Saved {path}")

    def _confirmDiscard(self) -> bool:
        """Ask whether unsaved changes may be discarded.

        Returns:
            bool: True when the song is clean or the user agreed to discard.
        """
        if not self.song.dirty:
            return True

        discard = messagebox.askyesno("Unsaved changes",
                                      "Discard unsaved changes?")
        return discard

    def onClose(self):
        """Quit: stop both synths and destroy the window, unless the user
        decides to keep unsaved changes."""
        if self._confirmDiscard():
            self.player.stop()
            self.preview.stop()
            self.destroy()

    # ------------------------------------------------------------ refresh
    def _refreshAll(self):
        """Rebuild every view after the song object has been replaced.

        Stops playback, drops the selection and mute/solo state that referred
        to the old song, rebuilds the channel strip, redraws the roll, refills
        the event list and updates the title and the toolbar file summary.
        """
        # Playback and the Play button belong to the old song.
        self.player.stop()
        self.playStart = None
        self.playBtn.config(text=f"{PLAY_GLYPH} Play")

        # Selection and mute/solo variables refer to the old song's notes and
        # channels; _buildChannelRows recreates the variables.
        self.selection.clear()
        self.muteVars.clear()
        self.soloVars.clear()

        # New notes go to the lowest channel the file uses.
        chans = sorted(self.song.channels)
        self.activeChannel = chans[0] if chans else 0
        self._buildChannelRows()
        self.redraw()
        self._fillEvents()
        self._updateTitle()
        self._updateSummary()

    def _updateSummary(self):
        """Refresh the toolbar file summary: name, format, tracks, resolution,
        time signature, starting tempo, note count and duration."""
        s = self.song
        name = s.path.rsplit("/", 1)[-1] if s.path else "untitled"
        num, den = s.timeSigs[0][1], s.timeSigs[0][2]
        self.infoLbl.config(text=(
            f"{name}   fmt {s.mf.format} | {len(s.mf.tracks)} trk | "
            f"{s.mf.division} tpq | {num}/{den} | {s.initialBpm():.0f} bpm | "
            f"{len(s.notes)} notes | {s.duration:.1f}s"))

    def _updateTitle(self):
        """Set the window title to the file path, starred when unsaved."""
        name = self.song.path or "untitled"
        star = " *" if self.song.dirty else ""
        self.title(f"MontyRoll - {name}{star}")

    def _markDirty(self):
        """Flag the song as modified and show the star in the title."""
        self.song.dirty = True
        self._updateTitle()

    def _setStatus(self, text: str):
        """Show a message in the status bar.

        Args:
            text: the message; replaces whatever was there.
        """
        self.status.config(text=text)

    # ------------------------------------------------------- channel strip
    def _buildChannelRows(self):
        """Rebuild the channel strip, one card per channel the song uses.

        Each card carries the channel colour swatch and number, a preview
        button, the GM instrument picker (a fixed label on the drum channel),
        mute and solo checkbuttons, a CC7 volume slider and a one-line
        summary. Clicking a card makes it the active channel for adding
        notes, and wheel events on the card scroll the strip.
        """
        # Throw away the previous song's cards.
        for w in self.chanHolder.winfo_children():
            w.destroy()

        self.channelRows.clear()

        for ch in sorted(self.song.channels):
            info = self.song.channels[ch]
            color = CHANNEL_COLORS[ch]
            row = tk.Frame(self.chanHolder, bd=1, relief="solid",
                           padx=4, pady=3)
            row.pack(fill="x", padx=4, pady=2)

            # Top line: colour swatch, channel number, preview button and the
            # instrument. The lambda default pins ch to this card.
            top = tk.Frame(row)
            top.pack(fill="x")
            sw = tk.Canvas(top, width=14, height=14, highlightthickness=0)
            sw.create_rectangle(0, 0, 14, 14, fill=color, outline="")
            sw.pack(side="left")
            tk.Label(top, text=f"Ch {ch + 1}", width=5, anchor="w",
                     font=("TkDefaultFont", 9, "bold")).pack(side="left", padx=(4, 0))
            pb = ttk.Button(top, text=PREVIEW_GLYPH, width=2,
                            command=lambda c=ch: self._previewChannel(c))
            pb.pack(side="left", padx=(0, 2))
            Tooltip(pb, "Play a short sample of this channel's instrument\n"
                    "(in the channel's own pitch range)")

            # Channel 10 is fixed to GM percussion, so it gets a label where
            # the other channels get a program picker. Picker entries are
            # "nnn  Name"; _programChanged parses the number back out.
            if info.isDrums:
                dl = tk.Label(top, text="Drum Kit (GM percussion)", anchor="w")
                dl.pack(side="left", fill="x", expand=True)
                Tooltip(dl, "Channel 10 is the GM percussion channel:\n"
                        "each pitch is a different drum sound")
            else:
                var = tk.StringVar(value=f"{info.program:3d}  {info.instrument}")
                combo = ttk.Combobox(
                    top, textvariable=var, state="readonly",
                    values=[f"{i:3d}  {n}" for i, n in enumerate(gm.GM_PROGRAMS)])
                combo.pack(side="left", fill="x", expand=True, padx=(4, 0))
                combo.bind("<<ComboboxSelected>>",
                           lambda e, c=ch, v=var: self._programChanged(c, v))
                Tooltip(combo, "GM instrument for this channel.\n"
                        "Picking one writes a program-change event\n"
                        "into the file (saved on Ctrl+S).")

            # Bottom line: mute and solo share _channelsChanged, which dims
            # the roll and restarts the synth when playing.
            bottom = tk.Frame(row)
            bottom.pack(fill="x")
            self.muteVars[ch] = tk.BooleanVar(value=False)
            self.soloVars[ch] = tk.BooleanVar(value=False)
            mb = tk.Checkbutton(bottom, text="M", variable=self.muteVars[ch],
                                command=self._channelsChanged)
            mb.pack(side="left")
            Tooltip(mb, f"Mute channel {ch + 1}: silence it during playback\n"
                    "and dim its notes in the piano roll")
            sb = tk.Checkbutton(bottom, text="S", variable=self.soloVars[ch],
                                command=self._channelsChanged)
            sb.pack(side="left")
            Tooltip(sb, f"Solo channel {ch + 1}: when any channel is soloed,\n"
                    "only soloed channels play (solo overrides mute)")

            # Volume slider starts at the file's CC7 value, or 100 when the
            # channel has none. set() fires the command once during
            # construction; _volumeChanged treats that no-op as a no-op.
            vol = tk.Scale(bottom, from_=0, to=127, orient="horizontal",
                           showvalue=0, length=70, width=9,
                           command=lambda v, c=ch: self._volumeChanged(c, v))
            vol.set(info.volume if info.volume >= 0 else 100)
            vol.pack(side="right", padx=(4, 0))
            Tooltip(vol, f"Channel {ch + 1} volume (MIDI CC7, 0-127).\n"
                    "Writes a volume event into the file (saved on\n"
                    "Ctrl+S). Applies live while playing.")

            # Summary: note count, pitch range and GM family, flagged when
            # the channel uses pitch bend.
            fam = "Percussion" if info.isDrums else gm.familyName(info.program)
            desc = (f"{info.noteCount} notes | "
                    f"{gm.noteName(info.lo)}-{gm.noteName(info.hi)} | {fam}")

            if info.hasPitchBend:
                desc += " | bends"

            dl = tk.Label(bottom, text=desc, anchor="w",
                          font=("TkDefaultFont", 8))
            dl.pack(side="left", padx=(8, 0))
            Tooltip(dl, "Note count, pitch range, GM instrument family")

            # A click on any part of the card makes it the active channel,
            # and the wheel scrolls the strip from the card too: X11 delivers
            # wheel events to the widget under the pointer, so the canvas
            # binding alone never sees them while the pointer is on a card.
            for w in (row, top, bottom):
                w.bind("<Button-1>", lambda e, c=ch: self._setActiveChannel(c))
                for seq in ("<Button-4>", "<Button-5>", "<MouseWheel>"):
                    w.bind(seq, self._onChanWheel)

            Tooltip(row, f"Click to make channel {ch + 1} the active channel:\n"
                    "double-clicking the piano roll adds notes there")
            self.channelRows[ch] = row

        self._highlightActiveRow()

    def _channelsChanged(self):
        """Mute or solo toggled: dim the roll and restart the synth if playing."""
        self.redraw()
        self._liveUpdate()

    def muteAll(self):
        """Mute every channel, redraw and restart the synth if playing."""
        for var in self.muteVars.values():
            var.set(True)

        self.redraw()
        self._liveUpdate()
        self._setStatus("All channels muted")

    def clearMuteSolo(self):
        """Clear every mute and solo, redraw and restart the synth if playing."""
        for var in list(self.muteVars.values()) + list(self.soloVars.values()):
            var.set(False)

        self.redraw()
        self._liveUpdate()
        self._setStatus("Mutes and solos cleared")

    def _previewChannel(self, ch: int):
        """Play a short sample of the channel's instrument on that channel.

        Builds a tiny format-0 file at 120 bpm: a four-note arpeggio using the
        channel's program, placed in the channel's own pitch range, or a
        kick/snare/hat pattern on the drum channel. It plays through the
        separate preview Player so current playback carries on uninterrupted.

        Args:
            ch: 0-based channel index.
        """
        # Header: a fixed 120 bpm tempo and CC7 at 110 so the sample is heard
        # at a sensible level whatever the file's own settings.
        info = self.song.channels.get(ch)
        track = [smf.Event(0, smf.META, bytearray((500_000).to_bytes(3, "big")),
                           smf.META_TEMPO),
                 smf.Event(0, 0xB0 | ch, bytearray([7, 110]))]

        if ch == model.DRUM_CHANNEL:
            # kick, snare, closed hat, crash
            for i, pitch in enumerate((36, 38, 42, 36, 38, 49)):
                track.append(smf.Event(i * 240, 0x90 | ch, bytearray([pitch, 110])))
                track.append(smf.Event(i * 240 + 200, 0x80 | ch, bytearray([pitch, 64])))

            name = "Drum Kit"
        else:
            # Arpeggio (root, third, fifth, octave) on the C nearest the
            # middle of the channel's range, clamped to C2-C5; the octave
            # rings on longer than the other three.
            program = info.program if info else 0
            track.append(smf.Event(0, 0xC0 | ch, bytearray([program])))
            centre = (info.lo + info.hi) // 2 if info and info.noteCount else 60
            root = max(36, min(72, centre - centre % 12))  # C nearest the range

            for i, off in enumerate((0, 4, 7, 12)):
                start = i * 240
                dur = 220 if off < 12 else 720
                track.append(smf.Event(start, 0x90 | ch, bytearray([root + off, 100])))
                track.append(smf.Event(start + dur, 0x80 | ch, bytearray([root + off, 64])))

            name = gm.programName(program)

        # The preview Player is separate from the playback one, so auditioning
        # an instrument never stops the music.
        mf = smf.MidiFile(format=0, division=480, tracks=[track])

        if self.preview.play(mf):
            self._setStatus(f"Preview: ch {ch + 1} - {name}")
        else:
            self._setStatus("No synth available for preview")

    def _onChanWheel(self, event):
        """Scroll the channel strip in response to the mouse wheel.

        Args:
            event: wheel event; `num` is 4 (up) or 5 (down) on X11, and
                `delta` carries the signed amount on Windows and macOS.
        """
        step = -1 if (event.num == 4 or getattr(event, "delta", 0) > 0) else 1
        self.chanCanvas.yview_scroll(step * 2, "units")

    def _setActiveChannel(self, ch: int):
        """Make a channel the target for added notes and highlight its card.

        Args:
            ch: 0-based channel index.
        """
        self.activeChannel = ch
        self._highlightActiveRow()
        self._setStatus(f"Active channel: {ch + 1} - double-click the roll to add notes")

    def _highlightActiveRow(self):
        """Tint the active channel's card and reset every other card."""
        for ch, row in self.channelRows.items():
            active = ch == self.activeChannel

            # SystemButtonFace is the platform button colour where Tk knows
            # the name; elsewhere the default Tk grey stands in for it.
            try:
                row.config(bg="#d0e0ff" if active else "SystemButtonFace")
            except tk.TclError:
                row.config(bg="#d0e0ff" if active else "#d9d9d9")

    def _programChanged(self, ch: int, var: tk.StringVar):
        """Instrument picked in a card: write the program change into the song.

        Args:
            ch: 0-based channel index.
            var: the combobox variable, holding "nnn  Name"; the leading
                number is the GM program.
        """
        program = int(var.get().split()[0])
        self.song.setProgram(ch, program)
        self._markDirty()
        self._setStatus(f"Ch {ch + 1} {ARROW} {gm.programName(program)}")

    def _volumeChanged(self, ch: int, value: str):
        """Channel volume slider moved: write CC7 into the song and restart
        the synth if playing.

        Args:
            ch: 0-based channel index.
            value: the slider position as the string tk.Scale passes to its
                command.
        """
        value = int(float(value))
        info = self.song.channels.get(ch)
        current = info.volume if info and info.volume >= 0 else 100

        if value == current:
            return  # ignore slider initialisation / no-op moves

        self.song.setVolume(ch, value)
        self._markDirty()
        self._liveUpdate()
        self._setStatus(f"Ch {ch + 1} volume {value}")

    def _audibleChannels(self) -> set[int]:
        """Work out which channels should sound from the mute and solo states.

        Returns:
            set[int]: the soloed channels when any solo is on (solo overrides
            mute), otherwise every channel that is not muted.
        """
        solos = {c for c, v in self.soloVars.items() if v.get()}

        if solos:
            return solos

        return {c for c, v in self.muteVars.items() if not v.get()}

    # ------------------------------------------------------------- drawing
    @property
    def ppt(self) -> float:
        """Pixels per tick at the current horizontal zoom."""
        return self.zoomX / self.song.mf.division

    def _snapTicks(self) -> int:
        """Read the grid size from the snap combobox.

        Returns:
            int: ticks per grid step, or 1 when snapping is off.
        """
        choice = self.snapVar.get()

        if choice == "off":
            return 1

        ticks = max(1, self.song.mf.division * 4 // int(choice.split("/")[1]))
        return ticks

    def redraw(self):
        """Redraw the whole piano roll, ruler and keyboard from the song.

        Every structural change (load, add, delete, zoom, mute) comes through
        here: the canvas is cleared and rebuilt and the item <-> note maps are
        refilled, which is also why the playback cursor item is forgotten and
        recreated by the next `_tickCursor`. Dragging bypasses this and moves
        items directly through `_placeNoteItem`.
        """
        c = self.canvas
        c.delete("all")
        self.itemToNote.clear()
        self.noteToItem.clear()
        self.cursorItem = None

        # Scrollregion leaves 200 px of slack past the last note; zoom() uses
        # the same figure when it recentres the view after a redraw.
        s = self.song
        width = int(s.maxTick * self.ppt) + 200
        height = 128 * self.rowH
        c.configure(scrollregion=(0, 0, width, height))

        # row shading for black keys + octave lines
        for pitch in range(128):
            y = (127 - pitch) * self.rowH

            if pitch % 12 in (1, 3, 6, 8, 10):
                c.create_rectangle(0, y, width, y + self.rowH,
                                   fill="#1e1e24", outline="")

            if pitch % 12 == 0:
                c.create_line(0, y + self.rowH, width, y + self.rowH,
                              fill="#33333c")

        # bar / beat grid from the first time signature
        num, den = s.timeSigs[0][1], s.timeSigs[0][2]
        beat = s.mf.division * 4 // den
        bar = beat * num
        t = 0

        while t <= s.maxTick + bar:
            x = t * self.ppt
            is_bar = t % bar == 0
            c.create_line(x, 0, x, height,
                          fill="#3d3d48" if is_bar else "#26262e")
            t += beat

        # Tempo changes as lines down the roll in the ruler's tempo colour,
        # collapsing runs of the same bpm as the ruler does, so a file with
        # hundreds of tempo events is not a picket fence.
        lastBpm = None

        for tick, uspb, _sec in s.tempoMap:
            bpm = round(60e6 / uspb, 1)

            if bpm == lastBpm:
                continue

            lastBpm = bpm

            if tick > 0:
                x = tick * self.ppt
                c.create_line(x, 0, x, height, fill="#a04848", dash=(6, 4), tags="tempo")

        # Notes: brighter with velocity on audible channels, flat grey on
        # muted ones so the mute/solo state shows in the roll. Both maps are
        # filled here so hit-testing and dragging can find the items.
        audible = self._audibleChannels()

        for note in s.notes:
            x1 = note.start * self.ppt
            x2 = max(note.end * self.ppt, x1 + 2)
            y1 = (127 - note.pitch) * self.rowH + 1
            y2 = y1 + self.rowH - 2
            base = CHANNEL_COLORS[note.channel]

            if note.channel in audible:
                fill = _shade(base, 0.65 + 0.35 * note.velocity / 127)
                outline = _shade(base, 0.45)
            else:
                fill, outline = "#3a3a40", "#2c2c31"

            item = c.create_rectangle(x1, y1, x2, y2, fill=fill,
                                      outline=outline, tags="note")
            self.itemToNote[item] = note
            self.noteToItem[id(note)] = item

        # Selection outlines, then the ruler and keys at the same width and
        # height as the roll so they scroll in step with it.
        self._applySelectionStyle()
        self._drawRuler(width)
        self._drawKeys(height)

    def _drawRuler(self, width: int):
        """Redraw the three-row ruler above the roll.

        Top row: a seconds scale. Middle row: bar numbers. Bottom row: tempo
        changes and markers.

        Args:
            width: scrollregion width in pixels, matching the note canvas.
        """
        r = self.ruler
        r.delete("all")
        r.configure(scrollregion=(0, 0, width, RULER_H))
        s = self.song
        r.create_line(0, 14, width, 14, fill="#44444e")
        r.create_line(0, 29, width, 29, fill="#44444e")

        # -- seconds scale (top row); tempo-aware: x from seconds_to_tick
        # The step is the smallest one whose ticks sit at least 45 px apart
        # at the song's average pixels per second; each tick's x then goes
        # through secondsToTick so the scale stays true across tempo changes.
        px_per_sec = s.maxTick * self.ppt / max(s.duration, 0.001)

        for step in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300):
            if step * px_per_sec >= 45:
                break

        t = 0.0

        while t <= s.duration + step:
            x = s.secondsToTick(t) * self.ppt
            r.create_line(x, 9, x, 14, fill="#7a9")

            # Labels switch to m:ss past a minute, keeping tenths only while
            # the step is shorter than a second.
            if t >= 60:
                label = f"{int(t) // 60}:{t % 60:04.1f}" if step < 1 else \
                        f"{int(t) // 60}:{int(t) % 60:02d}"
            else:
                label = f"{t:.4g}s"

            r.create_text(x + 3, 1, text=label, anchor="nw",
                          fill="#8fb", font=("TkDefaultFont", 7))
            t += step

        # -- bar numbers (middle row)
        num, den = s.timeSigs[0][1], s.timeSigs[0][2]
        bar = s.mf.division * 4 * num // den
        n = 1
        t = 0

        while t <= s.maxTick + bar:
            x = t * self.ppt
            r.create_line(x, 22, x, 29, fill="#888")
            r.create_text(x + 3, 15, text=str(n), anchor="nw",
                          fill="#bbb", font=("TkDefaultFont", 8))
            t += bar
            n += 1

        # -- tempo changes and markers (bottom row)
        # Consecutive tempo events at the same bpm (to 0.1) collapse into one
        # label, which keeps files with hundreds of tempo events readable.
        last_bpm = None

        for tick, uspb, _sec in s.tempoMap:
            bpm = round(60e6 / uspb, 1)

            if bpm == last_bpm:
                continue

            last_bpm = bpm
            x = tick * self.ppt
            r.create_line(x, 30, x, RULER_H, fill="#d66")
            r.create_text(x + 3, 31, text=f"{TEMPO_GLYPH}={bpm:g}", anchor="nw",
                          fill="#f99", font=("TkDefaultFont", 7))

        for tick, text in s.markers:
            x = tick * self.ppt
            r.create_text(x + 2, RULER_H - 1, text=text, anchor="sw",
                          fill="#e8d44d", font=("TkDefaultFont", 7))

    def _drawKeys(self, height: int):
        """Redraw the keyboard gutter to the left of the roll.

        Args:
            height: scrollregion height in pixels, matching the note canvas.
        """
        k = self.keys
        k.delete("all")
        k.configure(scrollregion=(0, 0, KEYS_W, height))

        for pitch in range(128):
            y = (127 - pitch) * self.rowH
            black = pitch % 12 in (1, 3, 6, 8, 10)
            k.create_rectangle(0, y, KEYS_W, y + self.rowH,
                               fill="#2b2b31" if black else "#e8e8e8",
                               outline="#555")

            # Octave labels on each C, once the rows are tall enough to hold
            # the text.
            if pitch % 12 == 0 and self.rowH >= 7:
                k.create_text(KEYS_W - 4, y + self.rowH / 2,
                              text=gm.noteName(pitch), anchor="e",
                              fill="#333", font=("TkDefaultFont", 7))

    def _applySelectionStyle(self):
        """Outline selected notes in white and restore the others' outlines."""
        for item, note in self.itemToNote.items():
            if note in self.selection:
                self.canvas.itemconfigure(item, outline="#ffffff", width=2)
            else:
                base = CHANNEL_COLORS[note.channel]
                self.canvas.itemconfigure(item, outline=_shade(base, 0.5), width=1)

    def zoom(self, factor: float, anchor_x: float | None = None):
        """Zoom horizontally, keeping the tick under `anchor_x` still on screen.

        Args:
            factor: multiplier for the zoom, which is clamped to 4-600 pixels
                per quarter note.
            anchor_x: widget x coordinate in pixels to hold stationary;
                defaults to the centre of the view.
        """
        # At either limit the zoom does not change and nothing is redrawn.
        old = self.zoomX
        self.zoomX = min(600, max(4, self.zoomX * factor))

        if self.zoomX == old:
            return

        if anchor_x is None:
            anchor_x = self.canvas.winfo_width() / 2

        # Note which canvas x sits under the anchor before redrawing at the
        # new scale, then scroll so that same tick lands back under it.
        canvas_x = self.canvas.canvasx(anchor_x)
        self.redraw()
        total = self.song.maxTick * self.ppt + 200   # matches redraw scrollregion
        new_left = canvas_x * (self.zoomX / old) - anchor_x
        self._xview("moveto", max(0.0, new_left / total))

    def vzoom(self, delta: int):
        """Change the note row height, clamped to 4-24 pixels, and redraw.

        Args:
            delta: pixels to add to the row height; negative shrinks it.
        """
        self.rowH = min(24, max(4, self.rowH + delta))
        self.redraw()

    # -------------------------------------------------------------- tempo
    def _barStart(self, tick: float) -> tuple[int, int]:
        """Snap a tick to the nearest bar line.

        Args:
            tick: absolute tick.

        Returns:
            tuple[int, int]: (tick of the bar line, 1-based bar number).
        """
        num, den = self.song.timeSigs[0][1], self.song.timeSigs[0][2]
        barLen = self.song.mf.division * 4 * num // den
        index = max(0, int(round(tick / barLen)))
        snapped = (index * barLen, index + 1)
        return snapped

    def _applyTempo(self, tick: int, bpm: float):
        """Set a tempo and refresh everything that depends on timing.

        Args:
            tick: absolute tick of the change.
            bpm: the new tempo.
        """
        self.song.setTempo(tick, bpm)
        self._afterTempoEdit(f"Tempo {bpm:g} bpm from bar {self._barStart(tick)[1]}")

    def _afterTempoEdit(self, message: str):
        """Redraw after the tempo map changed and restart playback if running.

        Args:
            message: status bar text.
        """
        self._markDirty()
        self.redraw()
        self._fillEvents()
        self._updateSummary()
        self._setStatus(message)
        self._liveUpdate()

    def _askTempo(self, tick: int) -> None:
        """Ask for a tempo at a bar and apply it.

        Args:
            tick: the bar line's tick.
        """
        bar = self._barStart(tick)[1]
        current = self.song.tempoAt(tick)
        bpm = simpledialog.askfloat("Tempo", f"Tempo from bar {bar} (bpm):",
                                    initialvalue=round(current, 1), minvalue=4,
                                    maxvalue=1000, parent=self)

        if bpm is not None:
            self._applyTempo(tick, bpm)

    def setTempoDialog(self):
        """Edit menu: ask for a bar number, then the tempo from that bar."""
        lastBar = self.song.barBeat(self.song.maxTick)[0]
        bar = simpledialog.askinteger("Tempo", f"Bar number (1 to {lastBar}):",
                                      initialvalue=1, minvalue=1, maxvalue=lastBar,
                                      parent=self)

        if bar is not None:
            num, den = self.song.timeSigs[0][1], self.song.timeSigs[0][2]
            barLen = self.song.mf.division * 4 * num // den
            self._askTempo((bar - 1) * barLen)

    def scaleTemposDialog(self):
        """Edit menu: multiply every tempo in the file by a percentage."""
        percent = simpledialog.askfloat("Scale tempos", "Scale every tempo by (%):",
                                        initialvalue=100.0, minvalue=1, maxvalue=10000,
                                        parent=self)

        if percent is not None and percent != 100.0:
            self.song.scaleTempos(percent / 100)
            self._afterTempoEdit(f"Tempos scaled by {percent:g}%")

    def _rulerTempoRow(self, event) -> bool:
        """Report whether a ruler event lies on the tempo and marker row.

        Args:
            event: `y` within the ruler.

        Returns:
            bool: True on the bottom row.
        """
        onRow = event.y >= 29
        return onRow

    def onRulerDouble(self, event):
        """Double-click on the ruler's tempo row: set the tempo from that bar.

        Args:
            event: `x` and `y` within the ruler.
        """
        if not self._rulerTempoRow(event):
            return

        tick = self._barStart(self.ruler.canvasx(event.x) / self.ppt)[0]
        self._askTempo(tick)

    def onRulerContext(self, event):
        """Right-click on the ruler's tempo row: the tempo menu for that bar.

        Args:
            event: `x`, `y`, `x_root` and `y_root`.
        """
        if not self._rulerTempoRow(event):
            return

        tick, bar = self._barStart(self.ruler.canvasx(event.x) / self.ppt)
        hasEvent = any(t == tick for t, _, _ in self.song.tempoMap) and tick > 0
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label=f"Set tempo from bar {bar}...", command=lambda: self._askTempo(tick))
        menu.add_command(label=f"Remove tempo change at bar {bar}",
                         state="normal" if hasEvent else "disabled",
                         command=lambda: self._removeTempo(tick))
        menu.add_separator()
        menu.add_command(label="Scale all tempos...", command=self.scaleTemposDialog)
        menu.tk_popup(event.x_root, event.y_root)

    def _removeTempo(self, tick: int):
        """Remove the tempo change at a bar line.

        Args:
            tick: the bar line's tick.
        """
        if self.song.removeTempo(tick):
            self._afterTempoEdit(f"Tempo change at bar {self._barStart(tick)[1]} removed")

    # ---------------------------------------------------------- interaction
    def _eventPos(self, event) -> tuple[float, float]:
        """Convert a mouse event to canvas coordinates, allowing for scrolling.

        Args:
            event: mouse event; `x` and `y` are relative to the note canvas.

        Returns:
            tuple[float, float]: the (x, y) position in canvas coordinates.
        """
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        return x, y

    def _posToTickPitch(self, x: float, y: float) -> tuple[int, int]:
        """Convert a canvas position to a tick and a MIDI pitch.

        Args:
            x: canvas x coordinate.
            y: canvas y coordinate; row 0 at the top is pitch 127.

        Returns:
            tuple[int, int]: (tick, pitch) with the pitch clamped to 0-127.
        """
        tick = int(x / self.ppt)
        pitch = max(0, min(127, 127 - int(y / self.rowH)))
        return tick, pitch

    def _noteAt(self, x: float, y: float) -> model.Note | None:
        """Find the note drawn under a canvas position.

        Args:
            x: canvas x coordinate.
            y: canvas y coordinate.

        Returns:
            model.Note | None: the note whose rectangle covers the point, or
            None when only grid lines or empty space are there.
        """
        # Grid lines and the cursor overlap too; only items in the map count.
        for item in self.canvas.find_overlapping(x - 1, y - 1, x + 1, y + 1):
            if item in self.itemToNote:
                return self.itemToNote[item]

        return None

    def onPress(self, event):
        """Left button down: update the selection and begin a move or resize.

        Args:
            event: `x` and `y` for the hit test; `state` bit 0 is Shift.
        """
        # Focus the canvas so Delete and Escape act on the roll.
        self.canvas.focus_set()
        x, y = self._eventPos(event)
        note = self._noteAt(x, y)
        shift = event.state & 0x1

        # Empty space: a plain click clears the selection, Shift+click keeps
        # it; either way there is nothing to drag.
        if note is None:
            if not shift:
                self._clearSelection()

            self.drag = None
            return

        # Shift toggles the note in the selection. A plain click on an
        # unselected note selects it alone; a click on an already selected
        # note keeps the group, so a multi-note selection can be dragged.
        if shift:
            self.selection.symmetric_difference_update({note})
        elif note not in self.selection:
            self.selection = {note}

        self._applySelectionStyle()

        # Grabbing within 5 px of the note's right edge resizes; anywhere
        # else moves. The original geometry of every selected note is kept
        # so onDrag applies the offset from the press point each time rather
        # than accumulating rounded steps.
        item = self.noteToItem[id(note)]
        x2 = self.canvas.coords(item)[2]
        mode = "resize" if x2 - x <= 5 else "move"
        self.drag = {
            "mode": mode, "x": x, "y": y, "moved": False,
            "orig": {id(n): (n.start, n.end, n.pitch) for n in self.selection},
        }
        self._setActiveChannel(note.channel)
        self._describeNote(note)

    def onDrag(self, event):
        """Left button drag: move or resize every selected note.

        Args:
            event: `x` and `y`, the current pointer position.
        """
        if not self.drag:
            return

        # Offsets are measured from the press point and snapped to the grid,
        # so the notes never accumulate rounding drift across motion events.
        x, y = self._eventPos(event)
        snap = self._snapTicks()
        dticks = int(round((x - self.drag["x"]) / self.ppt / snap)) * snap
        drows = int(round((self.drag["y"] - y) / self.rowH))

        if not dticks and not drows:
            return

        self.drag["moved"] = True

        # A move shifts start and pitch keeping the length; a resize moves
        # only the end and never shorter than one grid step. apply() writes
        # the events, and the item is repositioned directly instead of
        # redrawing the whole roll.
        for note in self.selection:
            start0, end0, pitch0 = self.drag["orig"][id(note)]

            if self.drag["mode"] == "move":
                note.start = max(0, start0 + dticks)
                note.end = note.start + (end0 - start0)
                note.pitch = max(0, min(127, pitch0 + drows))
            else:
                note.end = max(start0 + snap, end0 + dticks)

            note.apply()
            self._placeNoteItem(note)

    def _placeNoteItem(self, note: model.Note):
        """Move a note's rectangle to match the note, without a full redraw.

        Args:
            note: the note whose canvas item is repositioned.
        """
        item = self.noteToItem.get(id(note))

        if item:
            x1 = note.start * self.ppt
            x2 = max(note.end * self.ppt, x1 + 2)
            y1 = (127 - note.pitch) * self.rowH + 1
            self.canvas.coords(item, x1, y1, x2, y1 + self.rowH - 2)

    def onRelease(self, event):
        """Left button up: commit a drag that moved something.

        Args:
            event: the release event; unused.
        """
        # Only a drag that actually moved notes dirties the song. maxTick
        # grows when notes were dragged past the end, so the scrollregion and
        # ruler follow on the next redraw.
        if self.drag and self.drag["moved"]:
            self._markDirty()
            self.song.maxTick = max(self.song.maxTick,
                                     max((n.end for n in self.selection), default=0))
            n = len(self.selection)
            verb = "Resized" if self.drag["mode"] == "resize" else "Moved"
            self._setStatus(f"{verb} {n} note{'s' if n != 1 else ''}")

        self.drag = None

    def onDouble(self, event):
        """Double-click on empty space: add a note on the active channel.

        Args:
            event: `x` and `y`, the position of the click.
        """
        x, y = self._eventPos(event)

        if self._noteAt(x, y):
            return

        # The start snaps down to the grid; the length is one grid step, or a
        # sixteenth note when snapping is off.
        tick, pitch = self._posToTickPitch(x, y)
        snap = self._snapTicks()
        start = (tick // snap) * snap
        length = snap if snap > 1 else self.song.mf.division // 4
        note = self.song.addNote(self.activeChannel, pitch, 100,
                                  start, start + length)
        self.selection = {note}
        self._markDirty()
        self.redraw()
        self._describeNote(note)

    def onContext(self, event):
        """Right-click on a note: select it and pop up the note menu.

        Args:
            event: `x` and `y` for the hit test; `x_root` and `y_root`
                position the menu on screen.
        """
        x, y = self._eventPos(event)
        note = self._noteAt(x, y)

        if note is None:
            return

        # A right-click on a note outside the selection selects it alone; on
        # a selected note the menu acts on the whole selection.
        if note not in self.selection:
            self.selection = {note}
            self._applySelectionStyle()

        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Delete", command=self.deleteSelection)
        menu.add_command(label="Set Velocity...", command=self.setVelocityDialog)
        menu.tk_popup(event.x_root, event.y_root)

    def onMotion(self, event):
        """Track the pointer in the status bar as musical time and pitch.

        Args:
            event: `x` and `y`, the pointer position.
        """
        x, y = self._eventPos(event)
        tick, pitch = self._posToTickPitch(x, y)
        bar, beat = self.song.barBeat(tick)
        secs = self.song.tickToSeconds(tick)
        note = self._noteAt(x, y)

        # Over a note the status shows that note; elsewhere it shows the row's
        # pitch, as a drum name when the active channel is the drum channel.
        if note:
            self._describeNote(note, prefix=f"bar {bar}:{beat}  {secs:6.2f}s   ")
        else:
            name = (gm.drumName(pitch) if self.activeChannel == model.DRUM_CHANNEL
                    else gm.noteName(pitch))
            self._setStatus(f"bar {bar}:{beat}  {secs:6.2f}s   {name}")

    def _describeNote(self, note: model.Note, prefix: str = ""):
        """Show a note's pitch, velocity, channel, ticks and track in the
        status bar.

        Args:
            note: the note to describe.
            prefix: text placed in front of the description.
        """
        pname = (gm.drumName(note.pitch) if note.channel == model.DRUM_CHANNEL
                 else gm.noteName(note.pitch))
        info = self.song.channels.get(note.channel)
        inst = info.instrument if info else "?"
        self._setStatus(
            f"{prefix}{pname} vel {note.velocity} | ch {note.channel + 1} ({inst}) | "
            f"tick {note.start}-{note.end} ({note.length}) | track {note.track}")

    def onWheel(self, event):
        """Mouse wheel over the roll: scroll, Shift-scroll sideways or
        Ctrl-zoom.

        Args:
            event: `num` (4 up, 5 down on X11) or `delta` (signed, on Windows
                and macOS) give the direction; `state` bits 0x1 and 0x4 are
                Shift and Ctrl; `widget` tells whether the pointer is on the
                note canvas.
        """
        # Button-4 and a positive delta both mean wheel-up.
        if event.num == 4 or event.delta > 0:
            direction = -1
        else:
            direction = 1

        # Zooming anchors at the pointer only over the note canvas; from the
        # ruler or keys the anchor falls back to the centre of the view.
        if event.state & 0x4:            # Ctrl: zoom anchored at pointer
            anchor = event.x if event.widget is self.canvas else None
            self.zoom(1.25 if direction < 0 else 0.8, anchor)
        elif event.state & 0x1:          # Shift: horizontal scroll
            self._xview("scroll", direction * 3, "units")
        else:
            self._yview("scroll", direction * 3, "units")

    # ------------------------------------------------------------ editing
    def selectAll(self):
        """Select every note in the song."""
        self.selection = set(self.song.notes)
        self._applySelectionStyle()
        self._setStatus(f"Selected {len(self.selection)} notes")

    def _clearSelection(self):
        """Deselect every note."""
        self.selection.clear()
        self._applySelectionStyle()

    def deleteSelection(self):
        """Delete the selected notes from the song and redraw."""
        if not self.selection:
            return

        n = len(self.selection)
        self.song.deleteNotes(list(self.selection))
        self.selection.clear()
        self._markDirty()
        self.redraw()
        self._setStatus(f"Deleted {n} note{'s' if n != 1 else ''}")

    def setVelocityDialog(self):
        """Ask for a velocity and apply it to every selected note."""
        if not self.selection:
            return

        # The dialog opens on the velocity of one of the selected notes.
        current = next(iter(self.selection)).velocity
        value = simpledialog.askinteger("Velocity", "Velocity (1-127):",
                                        initialvalue=current, minvalue=1,
                                        maxvalue=127, parent=self)

        if value is None:
            return

        for note in self.selection:
            note.velocity = value
            note.apply()

        self._markDirty()
        self.redraw()

    # ------------------------------------------------------------ playback
    def togglePlay(self):
        """Play button or Space: stop if playing, otherwise start from the top.

        When no synth can be found a dialog explains what to install.
        """
        if self.player.playing:
            self.stopPlayback()
            return

        # Starting fails only when findPlayer finds no usable synth. aplaymidi
        # with nothing but the Midi Through port counts as unusable, since
        # that port goes nowhere and would play silence.
        if not self._startPlayback(0.0):
            messagebox.showwarning(
                "No usable MIDI synth found",
                "No software synth is available to make sound.\n\n"
                "Easiest fix:\n"
                "  sudo apt install timidity\n\n"
                "or:  sudo apt install fluidsynth fluid-soundfont-gm\n\n"
                "(aplaymidi alone is not enough - it needs a synth "
                "listening on an ALSA MIDI port.)")
            return

        # Start the cursor loop; it reschedules itself and stops playback
        # when the song ends or the synth exits.
        self._setStatus(f"Playing via {player.findPlayer()[0]}")
        self.playBtn.config(text=f"{STOP_GLYPH} Stop")
        self._tickCursor()

    def _startPlayback(self, offset_sec: float) -> bool:
        """Spawn the synth playing the song from a point in time.

        Args:
            offset_sec: song seconds to start from; 0 plays the whole file.

        Returns:
            bool: True when a synth was started, False when none is available.
        """
        # buildSlice re-emits the tempo, programs, controllers and pitch bend
        # in force at start_tick, so a mid-song start sounds right.
        start_tick = int(self.song.secondsToTick(offset_sec)) if offset_sec > 0 else 0
        mf = self.song.buildSlice(start_tick)
        speed = self.speedVar.get() / 100

        # Mute/solo, master volume and speed are applied to the playback copy
        # by the player; the file on disk is never touched.
        if not self.player.play(mf, self._audibleChannels(),
                                master=self.masterVar.get() / 100,
                                speed=speed):
            return False

        # Playback clock for the cursor: playOffset is the song time of the
        # tick actually started from (rounding through the tick), and wall
        # time from playStart is scaled by the speed the synth is running at.
        self.playOffset = self.song.tickToSeconds(start_tick) if start_tick else 0.0
        self.playSpeed = speed
        self.playStart = time.monotonic()
        return True

    def _speedChanged(self):
        """Speed slider moved: update its label and restart the synth if
        playing."""
        self.speedLbl.config(text=f"{self.speedVar.get()}%")
        self._liveUpdate()

    def _liveUpdate(self, delay: int = 300):
        """Restart the synth at the current position after a live change.

        Mute, solo, channel volume, master volume and speed cannot be pushed
        into a running synth, so they respawn it from the cursor position.
        The restart is deferred by `delay` milliseconds and every further
        change resets the timer, so dragging a slider spawns one process when
        the drag settles instead of one per pixel. Does nothing while stopped.

        Args:
            delay: debounce interval in milliseconds.
        """
        if self.playStart is None:
            return

        if self._replayAfter:
            self.after_cancel(self._replayAfter)

        self._replayAfter = self.after(delay, self._restartPlayback)

    def _restartPlayback(self):
        """Debounce timer fired: respawn the synth from the current cursor
        time."""
        self._replayAfter = None

        if self.playStart is None:
            return

        # Cursor time from the playback clock; once past the end nothing is
        # restarted and _tickCursor winds playback down.
        elapsed = self.playOffset + (time.monotonic() - self.playStart) * self.playSpeed

        if elapsed < self.song.duration:
            self._startPlayback(elapsed)

    def stopPlayback(self):
        """Stop the synth, drop any pending restart and clear the cursor and
        position readout."""
        if self._replayAfter:
            self.after_cancel(self._replayAfter)
            self._replayAfter = None

        self.player.stop()
        self.playStart = None
        self.playOffset = 0.0
        self.playBtn.config(text=f"{PLAY_GLYPH} Play")
        self.posLbl.config(text="")

        if self.cursorItem:
            self.canvas.delete(self.cursorItem)
            self.cursorItem = None

    def _tickCursor(self):
        """Advance the playback cursor; reschedules itself every 33 ms.

        Cursor time is `playOffset + (monotonic() - playStart) * playSpeed`,
        so it stays right across debounced restarts and speed changes. The
        loop ends itself when the song runs out or the synth exits.
        """
        if self.playStart is None:
            return

        elapsed = self.playOffset + (time.monotonic() - self.playStart) * self.playSpeed

        # Playback is over when the clock runs past the end (plus a second of
        # decay) or the synth exited on its own. An exited synth with a
        # restart pending is a live update in flight, not the end.
        if elapsed > self.song.duration + 1 or (
                not self.player.playing and self._replayAfter is None):
            self.stopPlayback()
            return

        # Position readout: time, bar:beat and the file tempo at the cursor
        # scaled by the playback speed.
        tick = self.song.secondsToTick(elapsed)
        x = tick * self.ppt
        bar, beat = self.song.barBeat(tick)
        bpm = self.song.tempoAt(tick) * self.playSpeed
        self.posLbl.config(text=(
            f"{int(elapsed) // 60}:{elapsed % 60:04.1f} "
            f"bar {bar}:{beat} {TEMPO_GLYPH}={bpm:.0f}"))

        # The cursor line is created on the first tick after a redraw and
        # moved on every tick after that.
        if self.cursorItem:
            self.canvas.coords(self.cursorItem, x, 0, x, 128 * self.rowH)
        else:
            self.cursorItem = self.canvas.create_line(
                x, 0, x, 128 * self.rowH, fill="#ffffff", width=1)

        # keep the cursor on screen: once it leaves the visible span, scroll
        # so it sits 40 px in from the left edge
        left = self.canvas.canvasx(0)
        vis_w = self.canvas.winfo_width()

        if x < left or x > left + vis_w - 40:
            total = float(self.canvas.cget("scrollregion").split()[2])
            self._xview("moveto", max(0.0, (x - 40) / total))

        self.after(33, self._tickCursor)

    # ---------------------------------------------------------- event list
    def _fillEvents(self):
        """Refill the Events tab with every event of every track in tick order.

        The list is capped at MAX_EVENT_ROWS, with a final row saying how
        many events were left out, to keep the Treeview responsive on large
        files.
        """
        tree = self.evTree
        tree.delete(*tree.get_children())
        s = self.song
        rows = []

        # Gather (tick, track, event) across all tracks and sort by tick; the
        # sort is stable, so events at the same tick keep their track order.
        for ti, track in enumerate(s.mf.tracks):
            for e in track:
                rows.append((e.tick, ti, e))

        rows.sort(key=lambda r: r[0])
        truncated = len(rows) > MAX_EVENT_ROWS

        # One row per event; the channel column is blank for meta and sysex
        # events, whose channel is -1.
        for tick, ti, e in rows[:MAX_EVENT_ROWS]:
            bar, beat = s.barBeat(tick)
            ch = "" if e.channel < 0 else str(e.channel + 1)
            tree.insert("", "end", values=(
                tick, f"{s.tickToSeconds(tick):.2f}", f"{bar}:{beat}",
                ti, ch, self._evType(e), self._evDetail(e)))

        if truncated:
            tree.insert("", "end", values=(
                "", "", "", "", "", "...",
                f"{len(rows) - MAX_EVENT_ROWS} more events not shown"))

    @staticmethod
    def _evType(e: smf.Event) -> str:
        """Name an event's type for the Events tab.

        Args:
            e: the event.

        Returns:
            str: the meta type name, "meta 0xNN" for an unlisted meta type,
            or the channel event kind with underscores turned into spaces.
        """
        if e.status == smf.META:
            name = {
                0x01: "text", 0x02: "copyright",
                smf.META_TRACK_NAME: "track name", smf.META_INSTRUMENT: "instrument",
                smf.META_LYRIC: "lyric", smf.META_MARKER: "marker",
                0x07: "cue point", smf.META_CHANNEL_PREFIX: "channel prefix",
                smf.META_TEMPO: "tempo", smf.META_TIME_SIG: "time sig",
                smf.META_KEY_SIG: "key sig", 0x21: "port",
            }.get(e.metaType, f"meta 0x{e.metaType:02X}")
            return name

        kind = e.kind().replace("_", " ")
        return kind

    @staticmethod
    def _evDetail(e: smf.Event) -> str:
        """Decode an event's data for the Events tab detail column.

        Args:
            e: the event.

        Returns:
            str: bpm for a tempo, n/d for a time signature, the text of a
            text-bearing meta, note name and velocity, controller name and
            value, program name, pressure or bend for channel events, and
            the raw bytes in hex for anything else.
        """
        # Meta events: tempo and time signature decode to musical values,
        # text-bearing metas decode as latin-1 with newlines made visible,
        # and any other meta shows its bytes.
        if e.status == smf.META:
            if e.metaType == smf.META_TEMPO and len(e.data) == 3:
                uspb = int.from_bytes(e.data, "big")
                return f"{60e6 / uspb:.1f} bpm"

            if e.metaType == smf.META_TIME_SIG and len(e.data) >= 2:
                return f"{e.data[0]}/{1 << e.data[1]}"

            if e.metaType in (0x01, 0x02, 0x07, smf.META_TRACK_NAME,
                               smf.META_INSTRUMENT, smf.META_LYRIC,
                               smf.META_MARKER):
                text = e.data.decode("latin-1", "replace").replace("\n", f" {NEWLINE_GLYPH} ")
                return text

            meta_hex = e.data.hex(" ")
            return meta_hex

        # Channel events, by status nibble. Note names become drum names on
        # the drum channel.
        kind = e.status & 0xF0

        if kind in (0x80, 0x90, 0xA0):
            name = (gm.drumName(e.data[0]) if e.channel == model.DRUM_CHANNEL
                    else gm.noteName(e.data[0]))
            return f"{name} vel {e.data[1]}"

        if kind == 0xB0:
            cc = gm.CONTROLLERS.get(e.data[0], f"CC {e.data[0]}")
            return f"{cc} = {e.data[1]}"

        if kind == 0xC0:
            program = gm.programName(e.data[0])
            return program

        if kind == 0xD0:
            return f"pressure {e.data[0]}"

        if kind == 0xE0:
            return f"bend {((e.data[1] << 7) | e.data[0]) - 8192:+d}"

        raw = e.data.hex(" ")
        return raw


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point: open the optional file argument and run.

    Args:
        argv: arguments without the program name; None means `sys.argv[1:]`.
    """
    import sys
    args = sys.argv[1:] if argv is None else argv
    app = MidiEditorApp(args[0] if args else None)
    app.mainloop()
