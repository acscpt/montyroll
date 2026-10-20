# Installation

MontyRoll needs Python 3.10 or later with tkinter, and a software synth for playback. It uses the standard library alone, so there are no packages to resolve and no build step, and it runs either from a clone of the repository or from the release on PyPI.

Playback is the one part that depends on another program. Python has no audio of its own, so MontyRoll sends the music to a software synth and the synth makes the sound. Without one, files still open, edit and export, and only playback is unavailable.

## Prerequisites

- Python 3.10 or later, with tkinter.

- Linux with an X11 display, the platform that MontyRoll is tested on.

- `git`, for a run from a clone.

- A software synth for playback. `timidity` is the recommended one.

## Check Python and tkinter

Many distributions package tkinter separately from Python, so a working `python3` does not mean tkinter is there as well. These two commands report the version of each:

```bash
python3 --version                                           # 3.10 or later
python3 -c "import tkinter; print(tkinter.TkVersion)"       # the Tk version, such as 8.6
```

If the second command reports `No module named 'tkinter'`, Python is installed and tkinter is not. On Debian or Ubuntu:

```bash
sudo apt install python3-tk
```

## Run from a clone

A clone gives the current code and the demo file, `resources/chopsticks.mid`, which the [Quick start](quickstart.md) and the [worked example](examples/x16-zsm.md) both use:

```bash
git clone https://github.com/acscpt/montyroll.git
cd montyroll
python3 -m montyroll resources/chopsticks.mid
```

The window opens on the demo file, with four channel cards on the left and the piano roll filling the rest. Any other MIDI file opens the same way, with its path in place of the demo. Given no path at all, MontyRoll starts on an empty song.

## Install the release from PyPI

The release on PyPI installs a `montyroll` command that runs from any directory, with no clone to keep up to date:

```bash
pip install montyroll
montyroll path/to/file.mid
```

The package contains the program alone, so the demo file does not come with it. It can be downloaded from the repository, or any MIDI file will serve instead.

> [!IMPORTANT]
> On a system where the operating system manages Python, pip reports `externally-managed-environment`. Use pipx there, which gives MontyRoll an environment of its own: `pipx install montyroll`.

## Add a synth for sound

A MIDI file holds notes rather than audio, so a synth is what turns it into sound. MontyRoll looks for four of them in a fixed [search order](reference.md#synth-discovery) and uses the first one it finds. `timidity` comes first in that order and is the simplest to install. On Debian or Ubuntu:

```bash
sudo apt install timidity
```

FluidSynth is next in the order. Its instrument sounds come from a General MIDI soundfont rather than from the program itself, so both are needed:

```bash
sudo apt install fluidsynth fluid-soundfont-gm
```

The remaining two are picked up if they happen to be installed. `wildmidi` is third, and `aplaymidi` is last.

## Verify

Start MontyRoll on the demo file and click **Play**, or press `Space`. The cursor moves across the roll, the piano sounds, and the status bar reads `Playing via timidity`.

If the window does not open, or a dialog reports `No usable MIDI synth found`, the [Troubleshooting](troubleshooting.md) page lists the cause and the fix for each.

## Set up a development environment

Running MontyRoll needs none of this. The tests and the linter come with the `dev` extra, which is the only part of the project that uses third-party packages. From the clone:

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest                          # the whole suite, with coverage
.venv/bin/ruff check montyroll tests      # the linter
```

`pytest` writes its results to `tests/TEST_REPORT.md`, and `ruff` prints `All checks passed!` on a clean tree.

The tests for the window open real Tk windows, so the suite needs a display. `xvfb-run` supplies a virtual one on a headless machine, which is how CI runs it:

```bash
xvfb-run -a .venv/bin/pytest
```
