# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""MontyRoll: a stdlib-only MIDI visualiser and piano-roll editor (tkinter).

Run:  python -m montyroll [file.mid]
"""

from .model import Song
from .smf import MidiFile, parse, write

__version__ = "0.1.0"

__all__ = ["MidiFile", "Song", "__version__", "parse", "write"]
