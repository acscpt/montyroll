# Overview

MontyRoll is a desktop program for Standard MIDI Files. It opens a file as a piano roll, with a row for each channel beside the roll, a voice-demand strip underneath that shows how many voices play at any instant, and an inspector for the active channel and the selected note.

The main purpose of MontyRoll is to reduce those voices to a *budget* targeting a platform that has a limited number of channels. The reduction keeps as much of the music as that budget allows.

The reduction is worked out on the screen. Each note that it drops is stippled on the roll, and the strip shows the demand that remains. Playback goes through a software synth, so a change to the budget can be seen and heard immediately. Once the arrangement sounds right it can be exported to a format. Currently ZSM is supported.

## The MontyRoll workflow

The following sections detail the main workflow steps.

### Reading a file

On loading a MIDI file the piano roll draws each note in the colour of its channel, brighter at a higher velocity. The ruler above the roll carries four rows: seconds, bar numbers, tempo changes, and the chord in each beat. Each channel is displayed as a row on the left, with its colour, its instrument, its mute and solo, and its volume. The active row also shows its note count, its range, and its figures.

Two more pages open in a drawer under the roll. The **Events** page decodes every event of the file and the **Report** page gives an analysis, covering the key, the voice demand, the doubling between channels, and the busiest bars. The strip under the roll can also show the velocity or the reduction weight of every note in place of the demand.

### Editing

Notes are edited on the roll with the mouse. A note can be dragged or a double-click will add a note for the active channel. Dragging on a note's right edge changes its length.

The inspector changes the instrument of the active channel, picked from the General MIDI list, and each channel's row changes its volume. The tempo row of the ruler sets and removes tempo changes.

Edits are added directly as events of the loaded file and only persisted if saved.

### Playing

Pressing play plays the file from the first bar via the configured software synth. Channels can be muted or made to play solo. The toolbar has a master volume and a speed control. They do not affect the file, only the current playback.

### Reducing

The Reduce panel fits the file to the voice budget. The reducer gives every note a weight, merges notes that double each other, and the note with the least weight gives way when the budget is full.

With **Reduce to the voice budget** switched on, the roll, the strip, and playback all show the reduced arrangement.

### Arranging

Three commands on the **Edit** menu add to an arrangement.

A channel that carries several parts can be split so that there is one channel for each part, enabling each part to be edited individually, such as by giving it an instrument of its own.

A channel can be doubled, duplicated to a new channel and shifted in pitch. Finally a channel can be copied and delayed as an echo.

### Exporting

An export target writes the reduced arrangement for a platform. Currently ZSM format for the Commander X16 (YM2151 and the VERA PSG) is supported.

## How a reduction works

A voice is one note that sounds. A sound chip has a fixed number of channels and each channel plays one voice, so a platform has a fixed number of voices. A MIDI file is written for a synth with voices to spare, and an orchestral file can have many simultaneous voices in play.

The budget is the number of voices that the reduction may use. Wherever the demand is at or under the budget, every note plays. Where the demand rises above the budget, some notes have to give way, and the reducer picks them by weight.

### Weight

Every note has a weight, the product of four factors:

- **The priority of its channel.** This is **Pri** under the channel's row, where 1.0 is neutral.

- **Its velocity.** A loud note weighs more than a quiet one.

- **Its place in the texture.** The top line and the bass of a channel weigh more than the notes between them.

- **Doubling.** A note that doubles another channel at the unison or the octave weighs less, because the pitch is already heard.

A note that starts while every voice is busy is weighed against the notes that are playing. Each weight is multiplied by the ticks that the note has left, and the smallest product gives way. A long melody note therefore holds its voice against a short inner note, and a new note that weighs less than everything playing is the one dropped.

### Transforms

Before any note competes for a voice, the reducer removes demand that the ear barely hears. A unison between two channels plays once. An octave doubling can be folded in the same way. A fast repeated note can be held as one long note, and a chord can be thinned to its outer notes. Each of these is a switch on the Reduce panel, and each drop is counted by its reason on the result label.

### Pools

A pool is a set of channels that share a number of voices. A new file has one pool, `all`, which holds every channel and the whole budget. With several pools the competition for voices stays inside each one, so a melody in a pool of its own keeps its voices however busy the accompaniment becomes.

### Tiers

A tier describes one sound chip: the number of channels it has, the channels reserved for other sound such as effects, and the time a voice needs to fade after its note ends. Each pool draws its voices from one tier. The Commander X16 has two, the YM2151 with 8 channels and the VERA PSG with 16, and the strip then draws one budget line for each.

The fade matters to the reducer. A voice is held through its release, so the tail of one note is heard out before the next note takes the voice.

### The plan

The tiers, the pools, the priorities, and the export settings together make the plan. The plan is saved beside the MIDI file with the suffix `.monty`, and it loads with the file. The MIDI file itself stays as it was. A reduction is a view over the song, worked out again from the plan whenever either changes.

## Further reading

- [Quick start](quickstart.md) - the demo file reduced from eight voices to four.

- [Usage](usage.md) - the steps for each task, from a first look at a file to an export.

- [Reference](reference.md#note-weight) - the value of each weight factor and the order of the transforms.

- [Architecture](architecture.md) - how the code is layered and the path of a file through it.
