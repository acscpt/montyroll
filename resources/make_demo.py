# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Generate the demo file `chopsticks.mid` next to this script.

The tune is "The Celebrated Chop Waltz" (Euphemia Allen, 1877), long out of
copyright; the arrangement here is original and built entirely through
`montyroll.smf`, so the file has a known provenance and can be published.

It is written to show the application off: the first sixteen bars are the
two-finger piano part on its own, the repeat adds a bass, chord stabs and a
light drum part, and the tempo lifts at the repeat and eases off at the end
so the ruler has tempo flags to show. Markers label the two passes.

Run:  python resources/make_demo.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from montyroll import smf  # noqa: E402

DIVISION = 480
BEAT = DIVISION                 # a quarter note
BAR = 3 * BEAT                  # the piece is in 3/4

PIANO, CHORDS, BASS, DRUMS = 0, 1, 2, 9
KICK, SNARE, HAT, RIDE = 36, 38, 42, 51

# Each two-bar group is one interval played six times, the intervals widening
# from a second to an octave; a phrase ends with a short cadence figure.
# Pitches are MIDI numbers: C4 = 60.
GROUPS = [(65, 67), (64, 67), (62, 71), (60, 72)]
CADENCE = [(64, 72), (62, 71), (60, 72)]

# Harmony for those groups: G7, C, G7, C. Bass root and the chord above it.
HARMONY = [(43, [67, 71, 74]), (48, [67, 72, 76]), (43, [67, 71, 74]), (48, [67, 72, 76])]


def meta(tick: int, metaType: int, data: bytes) -> smf.Event:
    """Build a meta event.

    Args:
        tick: absolute tick.
        metaType: the meta type byte.
        data: the payload.

    Returns:
        smf.Event: the event.
    """
    event = smf.Event(tick, smf.META, bytearray(data), metaType)
    return event


def tempo(tick: int, bpm: float) -> smf.Event:
    """Build a tempo event.

    Args:
        tick: absolute tick.
        bpm: quarter notes per minute.

    Returns:
        smf.Event: the event.
    """
    uspb = round(60_000_000 / bpm)
    event = meta(tick, smf.META_TEMPO, uspb.to_bytes(3, "big"))
    return event


def note(track: list[smf.Event], ch: int, pitch: int, start: int, length: int,
         velocity: int) -> None:
    """Append a note-on and note-off pair to a track.

    Args:
        track: the track to extend.
        ch: 0-based channel.
        pitch: MIDI note number.
        start: absolute start tick.
        length: duration in ticks.
        velocity: note-on velocity.
    """
    track.append(smf.Event(start, 0x90 | ch, bytearray([pitch, velocity])))
    track.append(smf.Event(start + length, 0x80 | ch, bytearray([pitch, 64])))


def pianoPhrase(track: list[smf.Event], start: int, final: bool, hold: bool) -> None:
    """Write one eight-bar phrase of the two-finger part.

    Args:
        track: the piano track.
        start: tick of the phrase's first bar.
        final: True for the second phrase, which ends with the cadence figure.
        hold: True at the end of the piece, adding a held chord in a bar of its own.
    """
    tick = start

    for index, (low, high) in enumerate(GROUPS):
        lastGroup = index == len(GROUPS) - 1

        # The last group of the final phrase is shortened to make room for
        # the cadence; every other group is six even quarter notes.
        count = 3 if final and lastGroup else 6

        for beat in range(count):
            accent = 96 if beat % 3 == 0 else 80
            note(track, PIANO, low, tick, BEAT - 40, accent)
            note(track, PIANO, high, tick, BEAT - 40, accent)
            tick += BEAT

        if final and lastGroup:
            for low, high in CADENCE:
                note(track, PIANO, low, tick, BEAT - 40, 88)
                note(track, PIANO, high, tick, BEAT - 40, 88)
                tick += BEAT

        # A held chord in a bar of its own closes the piece.
        if hold and lastGroup:
            for pitch in (48, 60, 64, 67, 72):
                note(track, PIANO, pitch, tick, BAR, 100)


def accompaniment(chords: list[smf.Event], bass: list[smf.Event],
                  drums: list[smf.Event], start: int) -> None:
    """Write the waltz accompaniment for one sixteen-bar pass.

    Args:
        chords: the chord track.
        bass: the bass track.
        drums: the drum track.
        start: tick of the pass's first bar.
    """
    for phrase in range(2):
        for index, (root, chord) in enumerate(HARMONY):
            groupStart = start + phrase * 8 * BAR + index * 2 * BAR

            for bar in range(2):
                barStart = groupStart + bar * BAR

                # Oom on the downbeat, pah-pah on two and three.
                note(bass, BASS, root, barStart, BEAT - 60, 100)

                for beat in (1, 2):
                    for pitch in chord:
                        note(chords, CHORDS, pitch, barStart + beat * BEAT, BEAT // 2, 72)

                note(drums, DRUMS, KICK, barStart, BEAT // 4, 100)
                note(drums, DRUMS, HAT, barStart + BEAT, BEAT // 4, 70)
                note(drums, DRUMS, HAT, barStart + 2 * BEAT, BEAT // 4, 70)

                # A ride on the last beat of every second bar lifts the phrase.
                if bar == 1:
                    note(drums, DRUMS, RIDE, barStart + 2 * BEAT, BEAT // 4, 80)

    # A snare flourish into the closing bar, where the band lands with the piano.
    lastBar = start + 16 * BAR - BAR

    for beat in range(3):
        note(drums, DRUMS, SNARE, lastBar + beat * BEAT, BEAT // 4, 90 + beat * 10)

    closing = start + 16 * BAR
    note(bass, BASS, 36, closing, BAR, 110)
    note(drums, DRUMS, KICK, closing, BEAT // 4, 110)
    note(drums, DRUMS, 49, closing, BEAT // 4, 100)     # crash cymbal


def build() -> smf.MidiFile:
    """Assemble the demo file.

    Returns:
        smf.MidiFile: format 1, five tracks, 33 bars of 3/4.
    """
    passLength = 16 * BAR
    conductor = [
        meta(0, smf.META_TRACK_NAME, b"Chopsticks"),
        meta(0, 0x02, b"Arrangement (c) 2026 Heisenberg (acscpt), MIT licence: free to use with "
             b"attribution. Tune: Euphemia Allen, 1877, public domain."),
        meta(0, 0x01, b"Generated by resources/make_demo.py from "
             b"https://github.com/acscpt/montyroll"),
        meta(0, smf.META_TIME_SIG, bytes([3, 2, 24, 8])),
        meta(0, smf.META_KEY_SIG, bytes([0, 0])),
        tempo(0, 150),
        meta(0, smf.META_MARKER, b"Piano alone"),
        tempo(passLength, 165),
        meta(passLength, smf.META_MARKER, b"With the band"),
    ]

    # Ease off over the last bar: a few tempo steps down.
    lastBar = 2 * passLength - BAR

    for step in range(4):
        conductor.append(tempo(lastBar + step * (BAR // 4), 165 - step * 20))

    piano = [meta(0, smf.META_TRACK_NAME, b"Piano"),
             smf.Event(0, 0xC0 | PIANO, bytearray([0])),
             smf.Event(0, 0xB0 | PIANO, bytearray([7, 110])),
             smf.Event(0, 0xB0 | PIANO, bytearray([10, 64]))]
    chords = [meta(0, smf.META_TRACK_NAME, b"Vibes"),
              smf.Event(0, 0xC0 | CHORDS, bytearray([11])),
              smf.Event(0, 0xB0 | CHORDS, bytearray([7, 90])),
              smf.Event(0, 0xB0 | CHORDS, bytearray([10, 40]))]
    bass = [meta(0, smf.META_TRACK_NAME, b"Bass"),
            smf.Event(0, 0xC0 | BASS, bytearray([32])),
            smf.Event(0, 0xB0 | BASS, bytearray([7, 100])),
            smf.Event(0, 0xB0 | BASS, bytearray([10, 80]))]
    drums = [meta(0, smf.META_TRACK_NAME, b"Drums"),
             smf.Event(0, 0xB0 | DRUMS, bytearray([7, 95]))]

    for passIndex in range(2):
        start = passIndex * passLength
        pianoPhrase(piano, start, final=False, hold=False)
        pianoPhrase(piano, start + 8 * BAR, final=True, hold=passIndex == 1)

    accompaniment(chords, bass, drums, passLength)
    demo = smf.MidiFile(format=1, division=DIVISION,
                        tracks=[conductor, piano, chords, bass, drums])
    return demo


def main() -> None:
    """Write the demo file beside this script and report what it contains."""
    out = Path(__file__).resolve().parent / "chopsticks.mid"
    demo = build()
    smf.write(demo, str(out))
    notes = sum(1 for t in demo.tracks for e in t if e.isNoteOn)
    print(f"wrote {out} ({notes} notes, {len(demo.tracks)} tracks)")


if __name__ == "__main__":
    main()
