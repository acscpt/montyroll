# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""General MIDI name tables and note-name helpers.

Pure data: the program, family, drum and controller names from the General
MIDI specification, plus the small helpers that turn MIDI numbers into the
text the UI shows.  Nothing here knows about songs, files or tkinter.
"""

# Instrument names indexed by GM program number 0-127, in families of eight.
GM_PROGRAMS = [
    # Piano
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano",
    "Honky-tonk Piano", "Electric Piano 1", "Electric Piano 2", "Harpsichord",
    "Clavinet",
    # Chromatic Percussion
    "Celesta", "Glockenspiel", "Music Box", "Vibraphone", "Marimba",
    "Xylophone", "Tubular Bells", "Dulcimer",
    # Organ
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ",
    "Reed Organ", "Accordion", "Harmonica", "Tango Accordion",
    # Guitar
    "Acoustic Guitar (nylon)", "Acoustic Guitar (steel)",
    "Electric Guitar (jazz)", "Electric Guitar (clean)",
    "Electric Guitar (muted)", "Overdriven Guitar", "Distortion Guitar",
    "Guitar Harmonics",
    # Bass
    "Acoustic Bass", "Electric Bass (finger)", "Electric Bass (pick)",
    "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1",
    "Synth Bass 2",
    # Strings
    "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings",
    "Pizzicato Strings", "Orchestral Harp", "Timpani",
    # Ensemble
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1",
    "Synth Strings 2", "Choir Aahs", "Voice Oohs", "Synth Voice",
    "Orchestra Hit",
    # Brass
    "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn",
    "Brass Section", "Synth Brass 1", "Synth Brass 2",
    # Reed
    "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe",
    "English Horn", "Bassoon", "Clarinet",
    # Pipe
    "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle",
    "Shakuhachi", "Whistle", "Ocarina",
    # Synth Lead
    "Lead 1 (square)", "Lead 2 (sawtooth)", "Lead 3 (calliope)",
    "Lead 4 (chiff)", "Lead 5 (charang)", "Lead 6 (voice)",
    "Lead 7 (fifths)", "Lead 8 (bass + lead)",
    # Synth Pad
    "Pad 1 (new age)", "Pad 2 (warm)", "Pad 3 (polysynth)", "Pad 4 (choir)",
    "Pad 5 (bowed)", "Pad 6 (metallic)", "Pad 7 (halo)", "Pad 8 (sweep)",
    # Synth Effects
    "FX 1 (rain)", "FX 2 (soundtrack)", "FX 3 (crystal)", "FX 4 (atmosphere)",
    "FX 5 (brightness)", "FX 6 (goblins)", "FX 7 (echoes)", "FX 8 (sci-fi)",
    # Ethnic
    "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bagpipe", "Fiddle",
    "Shanai",
    # Percussive
    "Tinkle Bell", "Agogo", "Steel Drums", "Woodblock", "Taiko Drum",
    "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    # Sound Effects
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet",
    "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]

# Family names indexed by program number // 8.
GM_FAMILIES = [
    "Piano", "Chromatic Percussion", "Organ", "Guitar", "Bass", "Strings",
    "Ensemble", "Brass", "Reed", "Pipe", "Synth Lead", "Synth Pad",
    "Synth Effects", "Ethnic", "Percussive", "Sound Effects",
]

# Drum sound names keyed by note number on the percussion channel (35-81).
GM_DRUMS = {
    35: "Acoustic Bass Drum", 36: "Bass Drum 1", 37: "Side Stick",
    38: "Acoustic Snare", 39: "Hand Clap", 40: "Electric Snare",
    41: "Low Floor Tom", 42: "Closed Hi-Hat", 43: "High Floor Tom",
    44: "Pedal Hi-Hat", 45: "Low Tom", 46: "Open Hi-Hat", 47: "Low-Mid Tom",
    48: "Hi-Mid Tom", 49: "Crash Cymbal 1", 50: "High Tom",
    51: "Ride Cymbal 1", 52: "Chinese Cymbal", 53: "Ride Bell",
    54: "Tambourine", 55: "Splash Cymbal", 56: "Cowbell",
    57: "Crash Cymbal 2", 58: "Vibraslap", 59: "Ride Cymbal 2",
    60: "Hi Bongo", 61: "Low Bongo", 62: "Mute Hi Conga", 63: "Open Hi Conga",
    64: "Low Conga", 65: "High Timbale", 66: "Low Timbale", 67: "High Agogo",
    68: "Low Agogo", 69: "Cabasa", 70: "Maracas", 71: "Short Whistle",
    72: "Long Whistle", 73: "Short Guiro", 74: "Long Guiro", 75: "Claves",
    76: "Hi Wood Block", 77: "Low Wood Block", 78: "Mute Cuica",
    79: "Open Cuica", 80: "Mute Triangle", 81: "Open Triangle",
}

# Controller names keyed by CC number; the commonly encountered ones only.
CONTROLLERS = {
    0: "Bank Select", 1: "Modulation", 2: "Breath", 4: "Foot Pedal",
    5: "Portamento Time", 6: "Data Entry", 7: "Volume", 8: "Balance",
    10: "Pan", 11: "Expression", 32: "Bank Select LSB", 64: "Sustain Pedal",
    65: "Portamento", 66: "Sostenuto", 67: "Soft Pedal", 68: "Legato",
    71: "Resonance", 72: "Release Time", 73: "Attack Time", 74: "Brightness",
    84: "Portamento Control", 91: "Reverb", 92: "Tremolo", 93: "Chorus",
    94: "Detune", 95: "Phaser", 98: "NRPN LSB", 99: "NRPN MSB",
    100: "RPN LSB", 101: "RPN MSB", 120: "All Sound Off",
    121: "Reset Controllers", 123: "All Notes Off",
}

# Pitch-class names indexed by pitch % 12, spelled with sharps throughout.
KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def noteName(pitch: int) -> str:
    """Spell a MIDI pitch as a note name, with middle C (60) as C4.

    Args:
        pitch: MIDI note number 0-127.

    Returns:
        str: pitch class and octave, e.g. "C#4" for 61.
    """

    # Octave -1 holds MIDI 0-11, which is what puts 60 in octave 4.
    return f"{KEY_NAMES[pitch % 12]}{pitch // 12 - 1}"


def programName(program: int) -> str:
    """Look up the GM instrument name for a program number.

    Args:
        program: program number; only the low seven bits are used.

    Returns:
        str: the instrument name, e.g. "Acoustic Grand Piano" for 0.
    """

    return GM_PROGRAMS[program & 0x7F]


def familyName(program: int) -> str:
    """Look up the GM family a program number belongs to.

    Args:
        program: program number; only the low seven bits are used.

    Returns:
        str: the family name, e.g. "Piano" for programs 0-7.
    """

    # GM families are blocks of eight consecutive programs.
    return GM_FAMILIES[(program & 0x7F) // 8]


def drumName(pitch: int) -> str:
    """Name a note played on the drum channel.

    Args:
        pitch: MIDI note number.

    Returns:
        str: the GM drum sound name, or the plain note name for a pitch the
        GM drum map leaves unnamed.
    """

    # Outside the GM drum map (35-81) the ordinary note name stands in.
    name = GM_DRUMS.get(pitch, noteName(pitch))
    return name
