# Reference

MontyRoll primary interface is through the application window. The command line takes one optional argument, the file to open, and every other setting is a control on the screen or a field in the plan file. The tables here give each one its name, the values it takes, and its effect.

The sections run from the outside in: the command line and the keys, then each area of the window, then the numbers behind a reduction, the plan file, and the export target.

## Index

| Section | Contents |
| --- | --- |
| [Conventions](#conventions) | How labels, channels, and ticks are written |
| [Command line](#command-line) | The one argument, and what an empty song holds |
| [Environment](#environment) | The two environment variables that MontyRoll reads |
| [Keyboard and mouse](#keyboard-and-mouse) | Every key and mouse action |
| [Toolbar](#toolbar) | Playback level, speed, snap, and zoom |
| [File menu](#file-menu) | Opening, saving, plans, and exports |
| [Edit menu](#edit-menu) | Selection, velocity, the arrange commands, and tempo |
| [Channel strip](#channel-strip) | The controls and the two text lines of a channel card |
| [Piano Roll tab](#piano-roll-tab) | The four rows of the ruler |
| [Events tab](#events-tab) | The columns of the event list |
| [Report tab](#report-tab) | The two reports |
| [Demand strip](#demand-strip) | What each line and colour of the strip counts |
| [Reduce panel](#reduce-panel) | The pool controls and the transform switches |
| [Reduction order and drop reasons](#reduction-order-and-drop-reasons) | The order of the transforms and the reason each one records |
| [Note weight](#note-weight) | The four factors of a weight and their values |
| [Tiers dialog](#tiers-dialog) | The fields of a tier |
| [Export range dialog](#export-range-dialog) | The start, end, and loop bars |
| [Synth discovery](#synth-discovery) | The search order for a synth and a soundfont |
| [Plan file](#plan-file) | Every field of a `.monty` file, with an example |
| [Commander X16 (ZSM) target](#commander-x16-zsm-target) | Chips, sounds, instrument mappings, and the checks on the output |
| [Sources in the code](#sources-in-the-code) | The commands that print these lists from the code |

## Conventions

- A name in bold, such as **Voices**, is a label on the window. A control with no label is named in plain text, such as the volume slider.

- Channels are numbered 1 to 16, as on the channel cards and in the plan file. Ch 10 is the General MIDI drum channel.

- Lengths and gaps are in ticks. The file sets the number of ticks in a quarter note, and the toolbar summary shows it as `tpq`.

- A range such as 0 to 127 includes both ends.

## Command line

MontyRoll starts from the `montyroll` command of the release, or from `python3 -m montyroll` in a clone.

| Command | Effect |
| --- | --- |
| `montyroll` | Starts with an empty song. |
| `montyroll FILE` | Opens the Standard MIDI File `FILE`. |
| `python3 -m montyroll FILE` | Does the same from a clone. |

An empty song has one track, a tempo of 120 bpm, a time signature of 4/4, and 480 ticks per quarter note.

A `.monty` file with the same name beside `FILE` loads with it.

## Environment

MontyRoll reads two variables from the environment.

| Variable | Use |
| --- | --- |
| `PATH` | The search path for the synth that plays the file. |
| `DISPLAY` | The X11 display on which tkinter opens the window. |

## Keyboard and mouse

The keys act on the window as a whole. The mouse actions belong to the roll, the ruler, and the channel cards.

| Action | Control |
| --- | --- |
| Play or stop | `Space` |
| New file | `Ctrl+N` |
| Open a file | `Ctrl+O` |
| Save the file | `Ctrl+S` |
| Select every note | `Ctrl+A` |
| Clear the selection | `Esc` |
| Delete the selection | `Del` |
| Add a note on the active channel | Double-click the roll |
| Move a note | Drag the note |
| Resize a note | Drag its right edge |
| Add a note to the selection, or take it out | `Shift` and click |
| Zoom horizontally | `Ctrl` and the mouse wheel |
| Scroll horizontally | `Shift` and the mouse wheel |
| Scroll vertically | The mouse wheel |
| Play from a bar or a beat | Right-click the roll |
| Delete a note or set its velocity | Right-click the note |
| Set the tempo from a bar | Double-click the tempo row of the ruler |
| Set or remove a tempo change | Right-click the tempo row of the ruler |
| Scale every tempo | Right-click the tempo row of the ruler |
| Make a channel active | Click its card |
| Resize the demand strip | Drag the bar above the strip |

## Toolbar

The toolbar runs across the top of the window, from **Open** on the left to the summary of the file on the right.

| Control | Values | Effect |
| --- | --- | --- |
| **Open** | | Opens a file. |
| **Save** | | Saves the file. |
| **Play** | | Starts or stops playback. |
| **Vol** | 0 to 150% | Scales note velocities in the playback copy. Sets the level of an export. 100% and above export at full level. |
| **Speed** | 25 to 300% | Scales the tempo of the playback copy. |
| Position | | Shows the playback position: the time, the bar and beat, and the tempo at the cursor. |
| **Snap** | `1/1`, `1/2`, `1/4`, `1/8`, `1/16`, `1/32`, `off` | Sets the grid for a note that is added or changed. |
| **H+**, **H-** | | Zoom in and out horizontally. |
| **V+**, **V-** | | Make the note rows taller and shorter. |
| Summary | | Shows the file name, the format, the number of tracks, the ticks per quarter note, the time signature, the tempo, the note count, the length, and the key. |

## File menu

The **File** menu holds everything that reads or writes a file: the song, the plan, and the exports.

| Item | Effect |
| --- | --- |
| **New** | Replaces the song with an empty one. |
| **Open...** | Opens a file, and the `.monty` file beside it. |
| **Save** | Writes the song to its file. |
| **Save As...** | Writes the song to a new file. |
| **Save Reduced As...** | Writes the reduction as a MIDI file. |
| **Export Range...** | Sets the bar range and the loop bar of an export. |
| **Export Commander X16 (ZSM)...** | Writes the reduction as a ZSM file. The menu has one export item for each target. |
| **Load Plan...** | Opens a `.monty` file and the MIDI file that it names or embeds. |
| **Save Plan** | Writes the plan beside the MIDI file. |
| **Save Plan As...** | Writes the plan to a chosen path. |
| **Save Plan Bundle As...** | Writes the plan with the MIDI file embedded. |
| **Quit** | Closes the program. |

## Edit menu

The **Edit** menu acts on the selected notes, on the active channel, or on the tempo map.

| Item | Effect |
| --- | --- |
| **Select All** | Selects every note. |
| **Delete Selection** | Deletes the selected notes. |
| **Set Velocity...** | Sets the velocity of the selected notes, 1 to 127. |
| **Split Channel into Parts...** | Splits the active channel into 2 to 8 parts, each on its own channel. |
| **Double Channel...** | Copies the active channel onto the lowest free channel at 70% velocity. The copy sits -24 to +24 semitones away. |
| **Echo Channel...** | Copies the active channel onto the lowest free channel at 50% velocity. The copy is delayed by a number of ticks. |
| **Set Tempo at Bar...** | Sets the tempo from a bar, 4 to 1000 bpm. |
| **Scale All Tempos...** | Multiplies every tempo by a percentage, 1 to 10000. |

## Channel strip

Three buttons head the strip.

| Button | Effect |
| --- | --- |
| **Clear** | Clears every mute and solo and restores the instruments the file opened with. |
| **Change All** | Sets every channel except Ch 10 to one instrument. |
| **Mute All** | Mutes every channel. |

Each channel in use has a card.

| Control | Values | Effect |
| --- | --- | --- |
| Note button | | Plays a short sample of the instrument in the channel's pitch range. |
| Instrument list | GM programs 0 to 127 | Writes a program change into the file. Ch 10 shows `Drum Kit (GM percussion)`. |
| **M** | on, off | Mutes the channel in playback and dims its notes. |
| **S** | on, off | Plays the soloed channels alone. |
| Volume slider | 0 to 127 | Writes a `CC7` volume event into the file. |
| Summary line | | Shows the note count, the pitch range, and the instrument family. |
| Demand line | | Shows the peak notes at once, the mean while the channel sounds, the share of the piece that the channel plays, and its overlap with the edited pool. |
| **Pool** | Pool names, `(none)` | Puts the channel in a pool. `(none)` leaves the channel out of the reduction. |
| **Rel** | 0 to 5000 ms, blank | Sets the release for the channel. Blank takes the release of the tier. |
| **Pri** | 0.5 to 5.0 | Sets the priority of the channel. 1.0 is neutral. |

## Piano Roll tab

The ruler has four rows.

| Row | Content |
| --- | --- |
| 1 | Seconds, from the tempo map |
| 2 | Bar numbers |
| 3 | Tempo changes and markers |
| 4 | The chord in each beat |

Each note is a block in the colour of its channel, brighter at a higher velocity. A dashed vertical line marks each tempo change.

## Events tab

The tab lists every event in the file in seven columns: `Tick`, `Time`, `Bar`, `Track`, `Ch`, `Type`, and `Detail`.

For a file of more than 8000 events, the tab lists the first 8000 and ends with a row that counts the rest.

## Report tab

The **Original** sub-tab reports on the song. The **Reduced** sub-tab reports on the reduction while **Reduce to the voice budget** is ticked. **Save report...** writes the report on view as a text file.

## Demand strip

The strip under the roll plots the voice demand against time, on the same horizontal scale as the roll.

| Element | Meaning |
| --- | --- |
| Coloured stack | The notes that sound at each moment, one layer for each channel. |
| White line | The count of distinct pitches. A unison on two channels counts once. |
| Yellow line | The count of distinct pitch classes. An octave doubling counts once. |
| Red dashed line | The voice budget. With tiers, one line for each tier, labelled with the tier and its running total. |
| Red wash | The demand above the budget. |
| **Voices** | The budget. With one pool it runs from 1 to the peak of the file. With several pools it shows the sum of their voices. |

While the reduction is on, the strip shows each voice as busy until its release ends. The status bar reports the counts under the pointer.

## Reduce panel

The Reduce panel sits under the channel cards. Its first rows switch the reduction on and report the result, and the rest edit one pool at a time.

| Control | Values | Effect |
| --- | --- | --- |
| **Reduce to the voice budget** | on, off | Draws and plays the reduction. |
| **Shade by weight** | on, off | Colours each note by its weight in place of its velocity. |
| Result label | | Shows the notes kept, the notes cut, the tails clipped, and the loss, then the drops by reason. |
| **Pool** | Pool names | Chooses the pool that the panel edits. Return renames the pool to the typed text. |
| **+**, **-** | | Add a pool and remove the edited pool. |
| **Tiers...** | | Opens the tiers dialog. |
| **Voices** | 0 to 64 | The voices that the pool owns. |
| **Tier** | Tier names, `(none)` | The tier that the pool's voices come from. |
| **Sound** | Sound names, `(by instrument)` | The sound of the pool in an export. |
| Overlap line | | Shows the highest overlap among the channels of the pool. |
| **Merge unisons** | on, off | Drops a note that starts with the same pitch on another channel. On by default. |
| **Fold octaves** | on, off | Drops a note that starts with its octave on another channel. |
| **Top line** | on, off | Keeps the highest sounding note of each channel. |
| **Tremolo** | 0 to 480 ticks | Continues a pitch that is struck again within the gap. 0 is off. |
| **Legato** | 0 to 960 ticks | Extends a note to the next onset within the gap. 0 is off. |
| **Chord** | 0 to 8 notes | Keeps that many notes of each chord, the top and the bottom first. 0 keeps every note. |

## Reduction order and drop reasons

The reducer applies the transforms of a pool in a fixed order and then gives each remaining note a voice.

| Order | Step | Reason on the result label |
| --- | --- | --- |
| 1 | Merge unisons | `unison` |
| 2 | Fold octaves | `octave` |
| 3 | Tremolo | `tremolo` |
| 4 | Legato | |
| 5 | Top line | `under the top line` |
| 6 | Chord | `thinned` |
| 7 | Voice allocation | `no voice` |

A note on Ch 10 passes the first two steps unchanged.

## Note weight

The weight of a note is the product of four factors.

| Factor | Value |
| --- | --- |
| Priority | **Pri** on the channel's card |
| Velocity | `0.5 + 0.5 * velocity / 127` |
| Texture, highest note of its channel or a note alone | 1.5 |
| Texture, lowest note of its channel | 1.3 |
| Texture, inner note | 1.0 |
| Doubling, a pitch that already sounds on another channel | 0.1 |
| Doubling, the octave of such a pitch | 0.5 |
| Doubling, any other note | 1.0 |

At a full pool the allocator multiplies each weight by the ticks the note has left and takes the voice of the smallest product. A voice in its release counts at 0.25 of the weight of its note.

## Tiers dialog

**Tiers...** on the Reduce panel opens a dialog with one row for each tier.

| Field | Meaning |
| --- | --- |
| **Tier** | The name of the tier. |
| **Channels** | The channel count of the chip. |
| **Reserved** | The channels kept for other sound. The pools of the tier share the rest. |
| **Release ms** | The time a voice stays busy after its note ends. |
| **Chip** | The chip name that an export target reads. |

**Add tier** appends a row. One preset button for each export target fills the rows for its machine.

## Export range dialog

**File > Export Range...** opens a dialog with three fields.

| Field | Blank means |
| --- | --- |
| **Start bar** | The start of the song |
| **End bar** | The end of the song |
| **Loop back to bar** | A single pass |

Bars count from 1. The end bar is the last bar that plays.

## Synth discovery

MontyRoll uses the first synth in this order.

| Order | Program | Condition |
| --- | --- | --- |
| 1 | `timidity` | On `PATH` |
| 2 | `fluidsynth` | On `PATH`, with a soundfont |
| 3 | `wildmidi` | On `PATH` |
| 4 | `aplaymidi` | On `PATH`, with an ALSA output port other than `Midi Through` |

A soundfont is a `.sf2` or `.sf3` file in `/usr/share/sounds/sf2`, `/usr/share/soundfonts`, or `/usr/local/share/soundfonts`.

## Plan file

A plan file is JSON with the suffix `.monty`. Channel numbers in the file run from 1 to 16, as on the cards.

| Field | Type | Meaning |
| --- | --- | --- |
| `version` | number | The format version, 1. |
| `tiers` | list | One object for each tier. |
| `pools` | list | One object for each pool. |
| `priority` | object | A priority for each channel number. A channel at 1.0 is left out. |
| `release` | object | A release in milliseconds for each channel number that overrides its tier. |
| `midiFile` | string | The path of the MIDI file. A relative path starts at the directory of the plan file. |
| `midiBlob` | object | The embedded MIDI file. `base64` holds the bytes and `sha256` holds their checksum. |
| `targets` | object | One object for each export target. `targets.zsm.sounds` maps a pool name to a sound name. |
| `range` | object | The export range in bars. |

### Tier fields

| Field | Type | Meaning |
| --- | --- | --- |
| `name` | string | The name of the tier. |
| `capacity` | number | The channel count of the chip. |
| `reserved` | number | The channels kept for other sound. |
| `release` | number | The release in milliseconds. |
| `chip` | string | The chip name that an export target reads. |

### Pool fields

| Field | Type | Control on the Reduce panel |
| --- | --- | --- |
| `name` | string | **Pool** |
| `tier` | string | **Tier** |
| `voices` | number | **Voices** |
| `channels` | list of numbers | **Pool** on each channel card |
| `dedupUnison` | true or false | **Merge unisons** |
| `dedupOctave` | true or false | **Fold octaves** |
| `tremoloGap` | number of ticks | **Tremolo** |
| `legatoGap` | number of ticks | **Legato** |
| `monoTop` | true or false | **Top line** |
| `maxChord` | number of notes | **Chord** |

### Range fields

| Field | Type | Meaning |
| --- | --- | --- |
| `start` | number | The first bar of the export. |
| `end` | number | The last bar of the export. |
| `loop` | number | The bar that the music loops back to. |

This plan puts two channels on the YM2151 and two on the PSG, and exports bars 9 to 40 as a loop:

```json
{
	"version": 1,
	"tiers": [
		{
			"name": "opm",
			"capacity": 8,
			"reserved": 0,
			"release": 150.0,
			"chip": "opm"
		},
		{
			"name": "psg",
			"capacity": 16,
			"reserved": 4,
			"release": 0.0,
			"chip": "psg"
		}
	],
	"pools": [
		{
			"name": "melody",
			"tier": "opm",
			"voices": 6,
			"channels": [
				1,
				2
			],
			"dedupUnison": true,
			"dedupOctave": false,
			"tremoloGap": 0,
			"legatoGap": 0,
			"monoTop": false,
			"maxChord": 0
		},
		{
			"name": "rest",
			"tier": "psg",
			"voices": 10,
			"channels": [
				3,
				4
			],
			"dedupUnison": true,
			"dedupOctave": false,
			"tremoloGap": 4,
			"legatoGap": 8,
			"monoTop": false,
			"maxChord": 0
		}
	],
	"priority": {
		"1": 2.0
	},
	"release": {
		"2": 40.0
	},
	"midiFile": "song.mid",
	"targets": {
		"zsm": {
			"sounds": {
				"melody": "strings"
			}
		}
	},
	"range": {
		"start": 9,
		"end": 40,
		"loop": 9
	}
}
```

## Commander X16 (ZSM) target

The target writes a ZSM version 1 file for the YM2151 and the VERA PSG at 60 frames per second. The ZSMKit player reads the file from banks of 8192 bytes.

| Chip name | Chip | Channels | Preset reserve | Preset release |
| --- | --- | --- | --- | --- |
| `opm` | YM2151 | 8 | 0 | 300 ms |
| `psg` | VERA PSG | 16 | 4 | 0 ms |

The **Sound** box offers six YM2151 patches and four PSG waveforms.

| Sound | Chip | Definition |
| --- | --- | --- |
| `strings` | YM2151 | A four-operator patch in `PATCHES` in `montyroll/export/zsm.py` |
| `flute` | YM2151 | A four-operator patch in `PATCHES` |
| `reed` | YM2151 | A four-operator patch in `PATCHES` |
| `brass` | YM2151 | A four-operator patch in `PATCHES` |
| `bass` | YM2151 | A four-operator patch in `PATCHES` |
| `piano` | YM2151 | A four-operator patch in `PATCHES` |
| `square` | PSG | A pulse of width 32 |
| `pulse` | PSG | A pulse of width 16 |
| `saw` | PSG | A sawtooth |
| `triangle` | PSG | A triangle |

At `(by instrument)` a YM2151 channel takes a patch from its GM program.

| GM programs | Patch |
| --- | --- |
| 0-31, 45-46 | `piano` |
| 32-39, 47 | `bass` |
| 40-44, 48-55, 80-95 | `strings` |
| 56-63 | `brass` |
| 64-71 | `reed` |
| 72-79 | `flute` |
| 96-127 | A plain single tone |

At `(by instrument)` a PSG channel takes a waveform and a decay from its GM program. A decay steps the volume to zero over the given frames.

| GM programs | Waveform | Decay |
| --- | --- | --- |
| 0-7 | Pulse, width 16 | 45 frames |
| 8-15, 24-31 | Pulse, width 16 | 40 frames |
| 16-23, 80-127 | Pulse, width 32 | Held |
| 32-39, 43, 72-79 | Triangle | Held |
| 40-42, 44, 48-63 | Sawtooth | Held |
| 45 | Pulse, width 8 | 20 frames |
| 46 | Pulse, width 8 | 40 frames |
| 47 | Triangle | 25 frames |
| 64-71 | Pulse, width 16 | Held |

The target takes five more facts from the song.

| Source | Result in the file |
| --- | --- |
| Velocity times channel volume (`CC7`) | The level of the note: up to 24 steps of 0.75 dB on the YM2151, volume 1 to 63 on the PSG |
| Pan (`CC10`) of 0-42 | The note plays on the left |
| Pan (`CC10`) of 43-85 | The note plays on both sides |
| Pan (`CC10`) of 86-127 | The note plays on the right |
| Pitch bend at tick 0 | A fixed detune for the channel, up to 200 cents either way |
| Tempo map | The frame of each note, rounded from its absolute time |
| A pitch outside MIDI 13 to 108 on the YM2151 | The same note moved by octaves into that range |

After the file is written, the target reads the file back and checks eight things.

- The header carries the right frame rate.

- The channel masks name exactly the channels that the stream writes.

- The loop point lands on the start of a command.

- Each gap uses the fewest delay bytes.

- Each write goes to a register that the target uses.

- Each key code is a valid note.

- Each YM2151 channel has its whole patch before its first key-on.

- Every channel is silent after the last event.

The status bar then shows one line:

```text
Exported song.zsm: 3 KB, 1 banks, 39 s, peak 5 OPM and 1 PSG voices, 279 notes, no problems
```

## Sources in the code

The tables are drawn from the code, and each of these commands prints one of the lists that they come from.

```bash
grep -nE 'add_command\(label=' montyroll/app.py
grep -nE 'self.bind\("<' montyroll/app.py
grep -nE 'from_=|^MAX_EVENT_ROWS|^SNAP_CHOICES' montyroll/app.py
grep -n 'def findPlayer' -A 30 montyroll/player.py
python3 -c "from montyroll import export; t = export.TARGETS['zsm']; print(t.NAME, t.SUFFIX, t.FRAME_RATE, t.CHIPS, t.SOUNDS)"
python3 -c "from montyroll.export import zsm; print([(p, zsm.patchFor(p), zsm.psgSoundFor(p)) for p in range(128)])"
python3 -c "from montyroll import reduce; print(reduce.planToDict(reduce.Plan()))"
```
