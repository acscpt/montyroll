# Changelog

All notable changes to MontyRoll. The format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

This project is alpha. Behaviour, file handling and the user interface may change without warning until v1.0.

## [Unreleased]

### Added

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
