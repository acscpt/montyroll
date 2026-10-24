# Changelog

All notable changes to MontyRoll. The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

This project is alpha. Behaviour, file handling and the user interface may change without warning until v1.0.

## [Unreleased]

### Added

- **Channel meters.** During playback a meter under each channel's name shows its level, the velocity of its loudest sounding note times its volume, rising at each note, easing while it is held and falling once it ends, with a mark that holds the peak for a second. The number swatch is lit by the level and dims while the channel is silent. The rows are a little taller to hold the meter, and stopping clears it.

- **Folding the active row.** A double click on a channel row's bar folds the active row's figures and reduction controls away, and they stay folded as other rows are clicked until another double click opens them.

## [0.5.0] - 2026-10-24

### Added

- **Keys light as they play.** During playback the keyboard beside the roll lights each key whose note sounds under the cursor, in its channel's colour, a darker shade on a black key. Where two channels sound one pitch the note that started last lights it, a muted channel lights nothing, and with the reduction on only the notes it keeps light up. Stopping puts every key back.

## [0.4.0] - 2026-10-22

### Added

- **A new look.** A flat dark theme of blue-grey panels with etched lines between them, pastel channel colours, a cyan accent, and a painted switch and sliders. It is built on the ttk clam theme and needs nothing beyond the standard library. The colours, the fonts and the painting live in the new module `theme.py`.

- **The inspector.** A right-hand pane describes the active channel, with its instrument picker and preview, and the selected note, with its reduction verdict and its weight. Its burger button folds it to a narrow strip and gives the roll the width.

- **Lanes under the roll.** A switch above the strip chooses Demand, as before, Velocity, with a bar for each note, or Weight, with each note's reduction weight and the notes the reduction drops drawn as outlines.

- **Adding and deleting channels.** **+** and **-** at the head of the channel strip, and **Edit > Add Channel**, **Add Drum Channel** and **Delete Channel...**, start a channel playing Acoustic Grand Piano on a track of its own, or Ch 10 with the standard kit, and delete the active channel with every event on it after asking. A new channel joins the selected pool and becomes active, ready for notes. New `Song.addChannel` and `Song.removeChannel`.

- **A transport readout.** The toolbar shows the time, the bar and beat, and the tempo in a large readout, which shows the opening position while stopped.

### Changed

- **Compact channel rows.** Each channel is one line on a grey bar: its number on a swatch of its colour, its instrument, then mute and solo as speaker and headphones icons and its volume. The active channel's bar is lit with its name in bold, and its row also shows its figures and its pool, release and priority. A name too long for a narrow strip ends in an ellipsis. The instrument picker and the preview button moved to the inspector.

- **Events and Report in a drawer.** The two pages open under the roll from buttons along the bottom, beside the count of events, so the roll stays in view.

- **The file summary on a line of its own.** The summary moved from the end of the toolbar to a header line under it, so it shows in full on a narrow window.

- **The Reduce panel.** The reduction is a switch, with **Shade**, the roll's shading by weight, beside it. Under the pools table, a display in the strip's dark shows the selected pool: its name on a blue tag, then **Tier**, **Sound** and **Overlap**, the transforms as toggles, and the three gaps as values that drag, step and take typing, reading `off` or `all` at 0.

- **The roll.** A file opens scrolled to the middle of its pitches, notes have an edge a little darker than their fill, and the playback cursor is drawn in the accent colour. The bar under the pointer is lit faintly through the ruler and the roll, the beat within it a step more, and the slot a double click would fill a step more again, as is the note under the pointer.

- **Quicker edits on a large file.** The roll draws only the notes, the grid and the ruler in and around its view, and after an edit only the beats and the stretch of voice demand it touched are worked out again. On a file of 22,000 notes, selecting a note takes 15 ms in place of 135 ms, adding or deleting one about 50 ms in place of 700 ms, a zoom 30 ms in place of 300 ms, a mute 13 ms in place of 280 ms, and the file opens in a window drawn after about 265 ms in place of 950 ms, its chord row named straight after in idle moments.

- **The reduction off the main thread.** A change of budget, pool, transform, tier or priority works the reduction out on a thread of its own, and the roll and the strip follow when it lands, about 200 ms later on that file, where the window used to stop for 760 ms. A note edit still reduces at once, so an edited note is never drawn from the reduction before it.

- **Faster analysis and reduction.** The voice demand is counted in a faster sweep, chords are named from templates prepared once, the unison and octave folds no longer search a list for each note they drop, the notes' weights are kept until a priority changes, a file's events are read and its notes built with less work per event, and the Events page is filled when it is on view. The inspector's weight bar is now out of the most a note can weigh under the plan. `reduce.reduce` takes the weights when the caller holds them, and there are new `reduce.noteWeight`, `analysis.resweep`, and `analysis.stepIndex`, `chordAt` and `chordRuns`, which `chordTrack` is built from.

- **Volume and playback tempo.** The master volume moved beside the readout, with its figure, as the tempo has. **Speed** is renamed **Tempo**. A double click on either resets it to 100%. Both change only what is heard and exported.

- **The voice budget.** The budget beside the strip is a number between two slim arrows, in place of a spinbox. The arrows and the mouse wheel step it, a drag up or down changes it, and a double click opens it for typing. Every budget line on the strip can be dragged as well. The reduction is worked out again once the budget stops changing, so stepping through budgets stays quick on a large file.

- **The pools table.** The Reduce panel shows every pool on a line of its own, with its tier, its voices in a value box that drags, steps and takes typing, its peak, amber while it is above its voices, and its channel count, in place of the pool box and its Voices spinbox. A click selects the pool for the display below it, and a double click on the name renames it. A pool takes no more voices than its tier has left.

- **Budget lines.** The lines on the strip are solid and two pixels wide over a dark edge, with their labels on tags, so they read across the stack. Each one moves a pool when dragged, a tier's line the selected pool on that tier, and it turns blue with the pool's name on its tag while it is under the pointer or dragged.

### Fixed

- **Chord names took a bass note that had stopped.** The bass of a beat could be a short low note that ended before the beat, when a longer note had started before it, so a held chord kept the bass of a bar earlier. The bass is now the lowest note sounding in the beat. About 2% of the beats in the orchestral test files are named differently.

- **Clear after an instrument change.** **Clear** rebuilt the channel rows when it restored an instrument and left the old rows' pool boxes on the list the pools refresh, so the next pool added or renamed failed with a Tcl error. The rows' lists are now rebuilt with them.

- **The Events page after an edit.** The page and the event count beside the drawer buttons kept the events as the file was opened until a tempo edit or a channel command refilled them, so a note added or deleted did not show. Every change now marks the page out of date, and it is filled again when on view.

- **Pool controls on a channel with no notes.** A channel with events but no notes had no **Pool**, **Rel** or **Pri** under its row. Every row has them now, so a new channel can be given its pool before it has notes.

- **Raising the budget with the reduction on.** While **Reduce to the voice budget** was on, the budget beside the strip could be lowered but not raised, as its limit followed the reduced demand, which the budget itself caps. The limit is now the peak of the file as written.

- **The Sound box offers the pool's own chip.** The box listed the sounds of every chip of the target, so a YM2151 pool was offered PSG waveforms, which its export ignored. It now lists only the sounds of the chip that the pool's tier names, and `Chip` carries those names as `sounds`. For a pool with no chip the box reads `(no chip)`, greyed, and its tooltip says to choose a tier.

## [0.3.0] - 2026-10-20

### Added

- **Splitting a channel into parts.** Edit > Split Channel into Parts gives each part of a flattened channel a channel of its own, following each part from note to note without letting two parts cross. The top part keeps the source channel with its program and controllers.

- **Doubling and echo.** Edit > Double Channel and Edit > Echo Channel copy a channel onto a free channel, transposed or delayed, at a lower velocity. New module `arrange.py`.

- **Detuned doubles and downbeats.** `arrange.double` takes a detune in cents, written as a pitch bend at tick 0, so a unison copy sounds as a second desk beside its source, and `arrange.downbeats` copies the lowest note that starts each bar onto a new channel, for timpani under a bass line. The Commander X16 export plays a channel's tick-0 pitch bend as a static detune: the key fraction on the YM2151, the frequency word on the PSG.

- **Tiers.** Pools now belong to tiers, one per chip, each with a channel count, a reserve kept for sound effects, and a default release for its patches. A plan whose pools ask a tier for more voices than it has is refused.

- **Release tails.** A voice stays busy until the release of its note has died away, with the release set in milliseconds for each tier or channel. A voice taken during its release costs a fraction of the note's weight, the clipped tail counts towards the loss, and a channel that strikes the same pitch again retriggers its releasing voice.

- **Pool editing.** Each channel card names the pool its notes are reduced in, beside its release override and priority. The Reduce panel edits one pool at a time (voices, tier and transforms), with buttons to add, remove and rename pools and a Tiers dialog that has a Commander X16 preset.

- **Overlap.** Each card shows how much of the channel's playing time falls while the pool being edited is already sounding: a low figure marks a channel that could share that pool's voices. The panel shows the worst figure among the pool's own members.

- **Demand strip with tiers.** The strip stacks channels by tier and pool and draws a budget line for each tier. While the reduction is on it shows occupancy, notes plus their releases, and hovering reports each tier's count against its budget.

- **Shade by weight.** A switch on the Reduce panel colours notes by their reduction weight instead of velocity, scaled to the heaviest note in the song, so raising a channel's priority visibly brightens it and dims the rest. Hovering a note then reports its weight in the status bar.

- **ZSM export for the Commander X16.** File > Export Commander X16 (ZSM) writes the reduction as a ZSM file for ZSMKit, with each pool on the YM2151 or the VERA PSG according to its tier. The written file is decoded, checked, and replayed through a model of both chips before the status bar reports its size and the voices it uses.

- **Sounds and export range.** An FM part plays a patch chosen by its GM program, and a PSG part plays a waveform, with a decay for plucked instruments. A Sound box on the Reduce panel picks one sound for a whole pool, File > Export Range sets the bars to export and a bar to loop back to, and both are saved with the plan.

- **Pluggable export targets.** Each export target is a module in the new `montyroll.export` package that implements one protocol, and the File menu has an export item for each target.

- **Plan files.** File > Save Plan writes the tiers, pools, priorities, and releases as a `.monty` JSON file beside the MIDI file, and opening the MIDI file loads the plan with it. Load Plan opens a plan together with the MIDI file it names, and Save Plan Bundle As embeds the MIDI file in the plan with a checksum, so a bundle carries everything in one file.

- **Documentation.** A `docs/` section with a quick start on the demo file, installation, an overview of how a reduction works, usage, a reference for every control and the plan file, troubleshooting, the architecture, and a worked example that exports the demo for the Commander X16.

### Changed

- **README.** Rewritten for the current features: reduction with pools and tiers, plan files, the arrange commands, and the ZSM export, with a link to the documentation and current screenshots.

### Fixed

- **Notes beyond the YM2151's range.** The Commander X16 export now folds a note outside MIDI 13 to 108 by octaves instead of stopping, so a C0 bass plays as C1.

- **Stale instrument family on the cards.** Picking an instrument now updates the family on the card's summary line as well as the picker. The line kept the family of the old program before.

- **Drum unison.** A drum hit is no longer dropped as a unison when a pitched note on another channel shares its key number and start tick. The unison transform now passes the drum channel through, as the octave transform already did.

## [0.2.0] - 2026-08-23

### Added

- **Play from here**: right-click the piano roll for Play from the nearest bar or from the nearest beat. **Change All** beside the channel strip's header sets every channel's instrument to one GM program, leaving Ch 10 alone, and **Clear** now also restores the instruments the file was opened with.

- **Report tab** with the file's analysis as text: voices sounding at once in the three counting modes, how many instruments play together and for what share of the piece, per-channel figures, doubling, the channel pairs that least and most coincide, and the busiest bars; with a Save button. The playback cursor now runs on the demand strip as well as the roll.

- **Voice reduction.** A Reduce panel under the channel strip reduces the song to the voice budget: unison and octave doublings folded, optional tremolo merge, legato, top-line and chord thinning per pool, then allocation to the budget's voices with the weakest note stolen when they run out, where a note's weight comes from its channel priority, velocity, place in its part's texture and whether it doubles another part. The roll draws dropped and cut notes stippled, the demand strip follows the reduced song, Play auditions it, and File > Save Reduced As writes it out; the original is never modified. New module `reduce.py`.

- **Chord and key analysis.** A fourth ruler was added naming the chord in each beat (triads, sevenths and suspensions matched against a duration-weighted pitch-class profile, with the bass note settling fuller chords and a one-beat island absorbed between two runs of the same chord); the status bar names the chord under the pointer; the toolbar summary ends with the file's estimated key, from Krumhansl-Kessler profile correlation.

- **Voice-demand strip** under the piano roll, showing how many notes sound at once stacked per channel, the distinct-pitch and pitch-class counts over it, and a voice budget line, with the counts reported in the status bar on hover and each channel card carrying its peak, mean and share of the piece. The figures come from a new `analysis.py`, a sweep over the song's notes that also measures unison and octave doubling and how much of the piece each pair of channels sounds together.

- **Tempo editing.** Set the tempo from any bar or scale every tempo marker in the file.  Tempo changes are marked in the piano roll.

## [0.1.0] - 2026-08-23

### Added

- **Piano-roll visualiser** for Standard MIDI Files: every note drawn as a velocity-shaded block in its channel's colour, with a piano-key gutter, bar and beat grid, and horizontal and vertical zoom.

- **Channel strip** listing every channel in use with its General MIDI instrument, note count, pitch range, instrument family and whether it uses pitch bend.

- **Three-row ruler** showing tempo-aware seconds, bar numbers, and the tempo changes and markers from the file.

- **Event list** decoding every event in the file, with tick, seconds and bar:beat for each.

- **Editing**: add, move, resize and delete notes with the mouse, grid snap from whole notes to 1/32, velocity on a selection, and per-channel instrument and volume that write real program-change and `CC7` events. Saving re-serialises the original tracks, so events the editor does not understand are written back unchanged.

- **Playback** through an external synth found on `PATH` (`timidity`, `fluidsynth` with a soundfont, `wildmidi`, or `aplaymidi` to a real ALSA synth port), with mute and solo per channel, master volume, playback speed, and instrument preview. Mute, solo, volume and speed take effect during playback.

- **Test suite** covering the parser, the song model, the GM tables, the player and the user interface, with a markdown test report and coverage summary.
