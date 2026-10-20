# MontyRoll documentation

MontyRoll reads a Standard MIDI File and shows it as a piano roll, with a voice-demand strip underneath that counts how many voices sound at each instant. It reduces that demand to a voice *budget*, usually the number of channels on a target platform, plays the reduced arrangement through a synth, and exports it as a sequence of register writes for the platform's sound chips.

These documents go deeper than the project [README](../README.md): they walk through a session, describe every control in the window, and explain how the program is put together. The [Quick start](quickstart.md) is the place to begin. It opens the demo file that ships with MontyRoll and reduces it from eight voices to four, which covers the piano roll, the strip, and the Reduce panel.

## Getting started

- [Installation](install.md) - Python and tkinter, running from a clone or installing the release from PyPI, and adding a synth for sound.

- [Quick start](quickstart.md) - open the demo file, read its voice demand, reduce it from eight voices to four, and hear the result.

- [Overview](overview.md) - what MontyRoll does and how a reduction works: voices, the budget, note weights, pools, and tiers.

- [Usage](usage.md) - a session from start to finish: reading a file, editing it, playing it, reducing it, and exporting the result.

- [Worked example: Commander X16](examples/x16-zsm.md) - the demo file taken from MIDI to a ZSM file, and played on the two sound chips of the X16 in its emulator.

- [Troubleshooting](troubleshooting.md) - the messages and symptoms that come up, with the cause and the fix for each.

## Reference and internals

- [Reference](reference.md) - every control, menu item, and key, the fields of the plan file, and the tables of the Commander X16 export.

- [Architecture](architecture.md) - the four layers of the code, the path of a file from disk to an export, and the design decisions behind them.
