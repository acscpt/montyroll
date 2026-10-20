# Usage

MontyRoll works on one MIDI file at a time. The channel strip on the left holds a card for each channel, and the Reduce panel sits under the cards. The right side holds three tabs, **Piano Roll**, **Events**, and **Report**, with the voice-demand strip under the roll. The status bar along the bottom describes whatever is under the pointer and reports the result of each command.

## Open a file

A file is opened either from the shell or from inside the program. From the shell, the path of the file goes on the command line:

```bash
python3 -m montyroll path/to/file.mid
```

Inside the program, **File > Open...** brings up a file dialog, and `Ctrl+O` does the same from the keyboard.

Once the file is loaded, the channel strip fills with one card for each channel that has notes. The **Piano Roll** tab shows the notes themselves, and the strip under the roll shows how many of them sound at once.

## Read the file

The roll and the ruler above it display the musical detail of the MIDI file. The ruler above the roll has four rows: seconds, bar numbers, tempo changes, and the chord in each beat. The harmony of a passage can therefore be read straight off the fourth row, beat by beat.

Move the mouse over any note to get more information about that note. The pitch of the note, its velocity, its channel and instrument, and its ticks are shown on the status bar, after the bar, the time, and the chord at that point.

The two tabs beside the roll show the same file in other forms. The **Events** tab lists every event in the file, decoded into a table. The **Report** tab gives the analysis as text, and **Save report...** on that tab writes the text to a file.

The mouse wheel moves the view around the roll. On its own the wheel scrolls up and down through the pitches. With `Shift` held it scrolls sideways through the bars, and with `Ctrl` held it zooms in and out around the pointer.

## Edit notes

Notes are edited directly on the roll with the mouse. A new note always goes onto the active channel, which is the channel of the card that was clicked last.

Click the card of a channel to make it active, then double-click an empty part of the roll at the pitch and time where the note belongs. The note appears one grid step long, and is selected ready for the next change. **Snap** on the toolbar sets the grid, and with **Snap** at `off` a note can start on any tick.

Existing notes are changed by dragging. Drag the body of a note to move it in time or to another pitch, and drag its right edge to make it longer or shorter. Both movements follow the same grid.

Several notes can be changed together. Hold `Shift` and click each note to build up a selection, or press `Ctrl+A` to select every note in the file. The selected notes are outlined in white, and a drag on any one of them moves them all. `Del` removes the selection, and **Edit > Set Velocity...** gives every selected note one velocity. A click on an empty part of the roll, or `Esc`, clears the selection again.

`Ctrl+S` saves the edits back to the file. **File > Save As...** writes them to a new file, and the original stays as it was.

## Change an instrument or a volume

Each channel card has an instrument list, which holds the 128 General MIDI instruments, and a volume slider.

The instrument for a channel can be set from the predefined list. The instrument can be previewed by pressing the note button beside the list and it plays a short sample of the instrument. The slider sets the volume of the channel in the same direct way.

Three buttons at the head of the strip work on every channel at once. **Change All** sets all the channels to one instrument, apart from Ch 10, which keeps its drum kit. **Mute All** mutes every channel. **Clear** puts things back: it clears every mute and solo and restores the instruments that the file opened with.

A change of instrument or volume is written into the file as an event and only persisted when saved.

## Change the tempo

The third row of the ruler shows every tempo change in the file.

Double-click the tempo row at a bar to set the tempo from that bar onwards. A dialog asks for the new tempo in bpm, and the change then shows on the row with a dashed line down the roll. A right-click on a tempo change opens a menu, and **Remove tempo change at bar N** on that menu takes the change out again.

Sometimes a whole file is at the wrong speed, as when it was sequenced at half its true tempo. **Edit > Scale All Tempos...** multiplies every tempo by a percentage and keeps the shape of the tempo track, so a value of 200 brings such a file up to speed.

## Play the file

Playback goes through [a synth](install.md#add-a-synth-for-sound), and a cursor moves across the roll in step with the music.

**Play** on the toolbar starts the music from the first bar, and the same button stops it. `Space` does both from the keyboard. Playback can also start part way through. A right-click on the roll opens a menu with **Play from bar N** and **Play from beat N:B**, for the bar and the beat under the pointer.

Each channel card has two tick boxes for listening to part of the arrangement. **M** mutes the channel, and its notes are dimmed on the roll. **S** solos the channel, so that the soloed channels are heard alone.

**Vol** and **Speed** on the toolbar set the loudness and the speed of playback. Both change what is heard, and the file is left as it is.

All of these can be changed while the music plays. The synth restarts from the cursor to pick up the change, which is heard as a short pause.

## Reduce to a voice budget

A voice is one note that sounds, and the voice budget is the number of voices that the target can sound at once. The strip under the roll draws the demand of the file as a coloured stack and the budget as a red dashed line. Where the stack rises above the line, the file asks for more voices than the budget, and those are the bars that a reduction thins.

Start with **Voices**, in the gutter to the left of the strip, and set it to the number of voices that the target has. The budget line moves to that level, and a red wash marks each stretch where the demand is above it.

Then tick **Reduce to the voice budget** on the Reduce panel. From this point the roll, the strip, and playback all show the reduced arrangement. The roll stipples every note that the reduction drops or cuts short, so the loss can be seen bar by bar.

The result label under the tick box sums up the reduction. Its first line counts the notes kept, the notes cut short, and the tails clipped. Its second line counts the dropped notes by reason. The figure to bring down is `no voice`: each of those notes found every voice busy with a note that weighs more.

**Play** now plays the reduction. Untick the box and **Play** plays the original again, so the two can be compared by ear.

When a channel loses notes that matter to the music, raise **Pri** on its card. The notes of that channel then weigh more, and the other channels give way first.

Once the reduction sounds right, **File > Save Reduced As...** writes it out as a MIDI file of its own.

## Share voices with pools

A pool is a set of channels that share a number of voices. A new file has one pool, `all`, which holds every channel and the whole budget. Stealing happens inside one pool, so a second pool gives a group of channels voices that the rest of the file cannot take.

1. Click **+** on the Reduce panel. A new pool appears in the **Pool** box with a name such as `pool 2`, and the panel now edits that pool.

2. Type a name for the pool in the **Pool** box and press Return.

3. Set **Voices** on the panel to the number of voices that the pool owns.

4. Go to the card of each channel that belongs in the pool, and pick the pool in the **Pool** box on that card.

A melody is the usual case for a pool of its own. Its channels then keep their voices however busy the accompaniment becomes. A channel can also be left out of the reduction altogether, by setting **Pool** on its card to `(none)`.

The demand line on each card helps to decide which channels can share a pool. The line ends with `overlaps`, the share of the playing time of that channel during which the edited pool already sounds. A low figure marks a channel that rarely plays at the same time as the pool, so it can use the same voices at little cost.

## Describe the hardware with tiers

Pools say how the channels share voices. Tiers say where the voices come from. A tier describes one chip with three numbers: its channel count, the channels reserved for other sound, and the release time of its patches.

1. Click **Tiers...** on the Reduce panel. A dialog opens with one row for each tier.

2. Fill in the rows. A preset button, such as **Commander X16**, fills them for a known machine. For other hardware, **Add tier** appends an empty row to fill in by hand.

3. Click **OK** to close the dialog.

4. Select each pool in turn in the **Pool** box and set its **Tier**.

The strip then draws one budget line for each tier, and the reducer holds each voice for the release time of its tier after a note ends.

## Smooth or thin a pool

The switches at the foot of the Reduce panel belong to the pool in the **Pool** box. Each one removes demand before any note competes for a voice, at a different cost to the music.

- **Merge unisons** is ticked in a new pool. Two channels that start the same pitch together then share one voice. With the box unticked, every doubling plays for as long as there are voices free.

- **Fold octaves** goes a step further and drops a note that doubles another channel an octave away.

- **Tremolo** holds a fast repeated note as one long note. Its value is the widest gap, in ticks, between two strikes that still count as a repeat.

- **Legato** joins notes that almost touch, by extending each one to the start of the next. Its value is the widest gap, in ticks, that is closed.

- **Top line** keeps the highest sounding note of each channel and drops the notes under it.

- **Chord** thins each chord to the number of notes given, and keeps the top and the bottom note first.

## Split one channel into parts

A file can carry several musical lines on one channel, as a piano transcription does. A split gives each line, or part, a channel of its own, and each part can then take its own instrument, pool, and priority.

1. Click the card of the channel that holds the lines. This makes it the active channel.

2. Choose **Edit > Split Channel into Parts...**.

3. Enter the number of parts, from 2 to 8.

The top part stays on the original channel. Each other part moves to the lowest free channel and gets a card of its own, where its instrument can be picked.

## Double or echo a channel

Where the budget has voices to spare, a copy of a channel adds weight or space to an arrangement. Both commands work on the active channel, so the first step is a click on its card.

**Edit > Double Channel...** asks for an interval in semitones and copies the channel at that distance, so 12 gives a doubling an octave up. **Edit > Echo Channel...** asks for a delay in ticks and copies the channel that much later.

In both cases the copy lands on the lowest free channel, at a lower velocity than the original. It gets a card of its own, where its instrument can be picked.

## Save and load a plan

The tiers, the pools, the priorities, the releases, the sounds, and the export range together make the plan. The plan lives in a file of its own with the suffix `.monty`. The MIDI file stays as it was, and a later session works the reduction out again from the two.

**File > Save Plan** saves the plan beside the MIDI file, under the same name with the suffix `.monty`. From then on the plan loads by itself whenever that MIDI file is opened.

A plan can also be opened directly with **File > Load Plan...**, and MontyRoll then opens the MIDI file that the plan names. **File > Save Plan As...** writes the plan to any other path.

**File > Save Plan Bundle As...** makes a single file that carries the plan and the MIDI file together, so that an arrangement can be passed on as one file.

## Export for a sound chip

An export writes the reduction as register data for the chips that the tiers name. Each pool plays on the chip of its tier, so every pool needs [a tier](#describe-the-hardware-with-tiers) first.

1. Decide on the sound of each pool with the **Sound** box on the Reduce panel. A sound picked there plays every channel of the pool. With the box left at `(by instrument)`, each channel takes a sound that matches its own instrument.

2. Set the level of the export with **Vol** on the toolbar.

3. Choose the export item for the target on the **File** menu. For the Commander X16 the item is **Export Commander X16 (ZSM)...**.

4. Give the file a name in the dialog and save.

The status bar then reports the size of the file, its length, the peak voices on each chip, and the result of the checks that the target runs on its own output.

## Export a section and loop it

An export covers the whole song by default. A range limits it to a run of bars, and a loop bar makes the music repeat from that bar when it reaches the end.

1. Choose **File > Export Range...**. A dialog opens with three fields.

2. Enter the start bar and the end bar of the section. A blank start means the first bar of the song, and a blank end means the last.

3. Enter the bar to loop back to if the music is to repeat. With the field blank, the music plays once.

4. Click **OK**, then export as before.

The plan keeps the three bars, so the next export uses them again.

## Further reading

- [Worked example: Commander X16](examples/x16-zsm.md) - the tiers, the pools, and the export applied to the demo file from start to finish.

- [Reference](reference.md) - the range and the effect of every control named here.

- [Troubleshooting](troubleshooting.md) - what the messages on the result label and the status bar mean.
