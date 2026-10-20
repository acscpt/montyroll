# Troubleshooting

MontyRoll reports a problem in the place where it happens. Before the window opens, a problem shows in the terminal as a Python error. Once the window is open, a dialog appears when a file, a plan, or an export is refused, and its text names the file and the reason. A plan that the reducer cannot use shows on the result label of the Reduce panel. The outcome of a save or an export shows on the status bar.

In most cases the message names the cause on its own. The entries here match each message or symptom to its cause and give the fix.

## Index

| Symptom | Likely cause |
| --- | --- |
| [tkinter is missing](#tkinter-is-missing) | Python is installed and tkinter is a separate package |
| [No display](#no-display) | `DISPLAY` is unset in a remote shell or a container |
| [pip refuses to install](#pip-refuses-to-install) | The operating system manages the system Python |
| [A file is not a Standard MIDI File](#a-file-is-not-a-standard-midi-file) | The file starts with bytes other than `MThd` |
| [A file uses SMPTE time](#a-file-uses-smpte-time) | The file counts time in frames |
| [A plan does not load](#a-plan-does-not-load) | A syntax error, a missing field, or a changed bundle |
| [No synth is found](#no-synth-is-found) | No synth on `PATH`, or FluidSynth with no soundfont |
| [Playback pauses when a control moves](#playback-pauses-when-a-control-moves) | The synth restarts to apply the change |
| [One channel is silent](#one-channel-is-silent) | A mute, a solo on another channel, or a volume of zero |
| [The plan is refused](#the-plan-is-refused) | The pools of a tier ask for more voices than it has |
| [Many notes drop with no voice](#many-notes-drop-with-no-voice) | A pool is smaller than the demand of its channels |
| [A pool has no tier](#a-pool-has-no-tier) | An export needs a chip for every pool |
| [A split finds no notes](#a-split-finds-no-notes) | The active channel is empty |
| [A split needs more free channels](#a-split-needs-more-free-channels) | More parts than unused channels |
| [The exported music distorts](#the-exported-music-distorts) | Many voices at full level |
| [Exported notes end with a click](#exported-notes-end-with-a-click) | A voice was taken during its release |
| [The toolbar is cut off](#the-toolbar-is-cut-off) | The toolbar is wider than the window at a high display scale |
| [The Events page stops at 8000 events](#the-events-page-stops-at-8000-events) | The page lists the first 8000 events of a large file |

## Start-up

### tkinter is missing

Error message: `ModuleNotFoundError: No module named 'tkinter'`.

Python is installed and tkinter is a separate package on this system. Install it. On Debian or Ubuntu:

```bash
sudo apt install python3-tk
```

[^ Index](#index)

### No display

Error message: `_tkinter.TclError: no display name and no $DISPLAY environment variable`.

The window needs an X11 display. A remote shell and a container both start with `DISPLAY` unset.

On a desktop machine, start MontyRoll from a terminal inside the desktop session, where `DISPLAY` is already set. Over SSH, connect with `ssh -X`, and the window opens on the local display. The tests are a separate case: on a machine with no screen, `xvfb-run -a .venv/bin/pytest` gives them a virtual display.

[^ Index](#index)

### pip refuses to install

Error message: `error: externally-managed-environment`.

The operating system manages the system Python, and pip protects it. Install with pipx, which gives MontyRoll an environment of its own:

```bash
pipx install montyroll
```

[^ Index](#index)

## Files

### A file is not a Standard MIDI File

Error message: an `Open failed` dialog with `not a Standard MIDI File (missing MThd)`.

A Standard MIDI File starts with the four bytes `MThd`, and this file starts with other bytes. A RIFF MIDI file with the suffix `.rmi`, for example, starts with `RIFF`.

This command prints the first four bytes of a file:

```bash
head -c 4 file.mid
```

Export the music again as a Standard MIDI File from the program that made it.

[^ Index](#index)

### A file uses SMPTE time

Error message: an `Open failed` dialog with `SMPTE time division is not supported`.

The file counts time in SMPTE frames, and MontyRoll reads files that count time in ticks per quarter note. Save the file again from its sequencer with ticks per quarter note, which sequencers also call PPQ.

[^ Index](#index)

### A plan does not load

Error message: a `Plan not loaded` dialog that names the plan file and the reason.

The reason is one of five, and each points at its own fix.

| Reason in the dialog | Cause | Fix |
| --- | --- | --- |
| `not JSON`, with a line and a column | A syntax error in the `.monty` file | Correct the syntax at that place. |
| `not a plan`, with a field name | A missing field. Each tier needs `name` and `capacity`, and each pool needs `name`, `channels`, and `voices`. | Add the field. |
| `channel N is not 1 to 16` | A channel number outside the range of the plan file | Number the channels from 1 to 16. |
| `pools want N voices, M available` | The pools of one tier ask for more than the tier has | Lower `voices` on a pool, or lower `reserved` on the tier. |
| `the embedded MIDI does not match its checksum` | The bundle changed after MontyRoll wrote it | Open the original MIDI file and save the bundle again. |

[^ Index](#index)

## Sound

### No synth is found

Error message: a `No usable MIDI synth found` dialog when **Play** is clicked.

Playback goes through a software synth on `PATH`. Install one:

```bash
sudo apt install timidity
```

If FluidSynth is installed and the dialog still appears, add a General MIDI soundfont. MontyRoll looks for one in `/usr/share/sounds/sf2`, `/usr/share/soundfonts`, and `/usr/local/share/soundfonts`.

```bash
sudo apt install fluid-soundfont-gm
```

If `aplaymidi` is the one program installed, add `timidity`. `aplaymidi` sends notes to an ALSA synth port, and `timidity` makes the sound itself.

[^ Index](#index)

### Playback pauses when a control moves

The pause is expected. A synth plays a file from its first event, so to apply a change MontyRoll starts the synth again from the cursor. A mute, a solo, a volume, and the speed all restart it. The restart comes about a third of a second after the control stops moving, which lets a dragged slider settle first.

[^ Index](#index)

### One channel is silent

Three controls can silence a channel, and all three are on the channel rows.

The first is **M** on the row of the channel itself. With **M** on, the channel is muted, and its notes are dimmed on the roll. The second is **S** on any other row. With **S** on, the soloed channels play alone, which silences every channel that is left unsoloed. **Clear** at the head of the strip resets every mute and solo in one click.

The third is the volume slider on the row. With the slider at the far left the channel plays at volume zero, and a drag to the right brings it back.

[^ Index](#index)

## Reduction and export

### The plan is refused

Error message: the result label reads `plan refused`, as in `plan refused: tier opm: pools want 9 voices, 8 available`.

The pools of one tier ask for more voices than the tier has.

There are two ways out. Select a pool on that tier and lower its **Voices** until the pools fit. Or open **Tiers...** and lower **Reserved** on the tier, which gives its pools more voices to share.

[^ Index](#index)

### Many notes drop with no voice

The result label counts many `no voice` drops. Each one is a note that found every voice of its pool busy with a note that weighs more. Five changes lower the count, in order of cost to the music.

1. Raise **Voices** on the pool, up to what its tier has.

2. Turn on **Octaves** on the pool, which folds octave doublings.

3. Move a channel to a pool with voices to spare.

4. Raise **Pri** under the row of the channel that must stay whole.

5. Lower **Release ms** on the tier, so that each voice is free sooner.

[^ Index](#index)

### A pool has no tier

Error message: an export dialog with `has no tier, so its voices have no chip`.

The export places each pool on the chip of its tier, so every pool needs one. Open **Tiers...** on the Reduce panel, click the preset for the machine, and close the dialog with **OK**. The tiers of the machine are now in the plan. Then select the pool in the **Pool** box and set its **Tier** to one of them.

[^ Index](#index)

### A split finds no notes

Error message: a `Split channel` dialog with `has no notes to split`.

The split works on the active channel, which is the row clicked last. Click the row of the channel to split, then choose **Edit > Split Channel into Parts...** again.

[^ Index](#index)

### A split needs more free channels

Error message: a `Split channel` dialog with `needs N free channels`.

Each part after the first takes a free channel, and Ch 10 stays with the drums. Enter a smaller number of parts.

[^ Index](#index)

### The exported music distorts

Loud passages distort on the target. Many voices at full level add up past the range of the output. Lower **Vol** on the toolbar and export again. A value near 60% suits a file that uses most of the voices.

[^ Index](#index)

### Exported notes end with a click

A voice was taken while its last note was still in its release, and the result label counts each case under `tails clipped`. Raise **Release ms** on the tier, or raise **Rel** under the row of the channel, so that the allocator holds each voice for longer.

[^ Index](#index)

## Display

### The toolbar is cut off

At a high display scale the toolbar can be wider than the window, and the zoom buttons at its right end are clipped. The file summary has a line of its own under the toolbar and stays whole. Widen the window or maximise it.

[^ Index](#index)

### The Events page stops at 8000 events

The page ends with a row that reads `N more events not shown`. It lists the first 8000 events of a large file. The song holds every event, and a save writes them all.

[^ Index](#index)
