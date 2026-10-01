# Changelog

All notable changes to MontyRoll. The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

This project is alpha. Behaviour, file handling and the user interface may change without warning until v1.0.

## [Unreleased]

### Added

- **Splitting a channel into parts.** Edit > Split Channel into Parts separates a flattened channel's polyphony into voice streams, each on its own channel: chords assign top-down without crossing, each note to the stream whose last pitch is nearest, and the opening chord cuts at its widest pitch gaps. The top stream keeps the source channel with its program and controllers. Edit > Double Channel and Edit > Echo Channel copy a part onto a free channel, transposed or delayed, at a scaled velocity. New module `arrange.py`.

- **Detuned doubles and downbeats.** `arrange.double` takes a detune in cents, written as a pitch bend at tick 0, so a unison copy sounds as a second desk beside its source, and `arrange.downbeats` copies the lowest note that starts each bar onto a new channel, for timpani under a bass line. The Commander X16 export plays a channel's tick-0 pitch bend as a static detune: the key fraction on the YM2151, the frequency word on the PSG.


- **Tiers.** Pools now belong to tiers, one per chip, each with a channel count, a reserve kept for sound effects, and a default release for its patches. A plan whose pools ask a tier for more voices than it has is refused.

- **Release tails.** A voice now stays busy until its note's release has died away, not just until the note ends; the release is set in milliseconds per tier or per channel and converted to ticks through the tempo map. A voice stolen during its tail costs a fraction of the note's weight, the clipped tail counts towards the loss, and a channel re-striking the same pitch simply retriggers its releasing voice.

- **Pool editing.** Each channel card names the pool its notes are reduced in, beside its release override and priority. The Reduce panel edits one pool at a time (voices, tier and transforms), with buttons to add, remove and rename pools and a Tiers dialog that has a Commander X16 preset.

- **Overlap.** Each card shows how much of the channel's playing time falls while the pool being edited is already sounding: a low figure marks a channel that could share that pool's voices. The panel shows the worst figure among the pool's own members.

- **Demand strip with tiers.** The strip stacks channels by tier and pool and draws a budget line for each tier. While the reduction is on it shows occupancy, notes plus their releases, and hovering reports each tier's count against its budget.

- **Shade by weight.** A switch on the Reduce panel colours notes by their reduction weight instead of velocity, scaled to the heaviest note in the song, so raising a channel's priority visibly brightens it and dims the rest. Hovering a note then reports its weight in the status bar.

- **ZSM export for the Commander X16.** File > Export Commander X16 (ZSM) writes the reduction as a ZSM file for ZSMKit: each pool's voices go to the chip of its tier (YM2151 or VERA PSG), frames come from the tempo map, a note's velocity and channel volume set its level, its channel's pan places it, an FM part plays a patch from a small library chosen by its GM program (strings, flute, reed, brass, bass, piano) and a PSG part a waveform and, for plucked instruments, a decay likewise. A Sound box on the Reduce panel names a patch or waveform for a whole pool instead, and File > Export Range sets the bars to export and a bar to loop back to; both are saved with the plan. The toolbar's volume is the export's level. The written file is decoded, checked and replayed through a chip model before the status bar reports its size in banks and the voices used. New package `montyroll.export`.

- **Plan files.** File > Save Plan writes the tiers, pools, priorities and releases as a `.monty` file beside the MIDI file (JSON, with channels numbered as the cards show them), and opening a file loads the plan found beside it. The file names the MIDI it belongs to, so Load Plan opens that file as well; Save Plan As takes any path, and Save Plan Bundle As embeds the MIDI in the plan with a checksum, so a bundle needs no other file.

### Fixed

- **Notes beyond the YM2151's range.** The Commander X16 export now folds a note outside MIDI 13 to 108 by octaves instead of stopping; a C0 bass plays as C1.

- **Stale instrument family on the cards.** Picking an instrument now updates the card's summary line as well as the picker; it kept the old program's family before.

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
