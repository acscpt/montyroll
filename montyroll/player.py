# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Best-effort MIDI playback via an external synth found on PATH.

There is no stdlib audio, so playback shells out to whichever synth is
installed: the song is written to a temporary .mid file (muted channels
forced to volume 0, velocities and tempo scaled as requested) and handed to
timidity / fluidsynth / wildmidi / aplaymidi.  The synth only ever plays a
file from its start; the UI animates its own playback cursor from the tempo
map, and anything that has to change mid-piece is a stop and a fresh spawn
from the cursor position.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from glob import glob

_SOUNDFONT_DIRS = ["/usr/share/sounds/sf2", "/usr/share/soundfonts",
                   "/usr/local/share/soundfonts"]


def _findSoundfont() -> str | None:
    """Locate a General MIDI soundfont for fluidsynth to load.

    Returns:
        str | None: path of the first .sf2 or .sf3 file found in the usual
        system soundfont directories, or None when there is none.
    """

    # Directories are tried in order and the hits within one are sorted, so
    # the same soundfont is chosen on every run.
    for d in _SOUNDFONT_DIRS:
        hits = sorted(glob(os.path.join(d, "*.sf2")) + glob(os.path.join(d, "*.sf3")))

        if hits:
            return hits[0]

    return None


def _alsaSynthPort() -> str | None:
    """Find an ALSA sequencer output port that leads to a real synth.

    The "Midi Through" port is always present and goes nowhere; playing to it
    produces silence that looks exactly like a broken application, so it is
    excluded and only a port backed by an actual synth qualifies.

    Returns:
        str | None: the client:port address of the first usable output port
        listed by "aplaymidi -l", or None when aplaymidi is missing, hangs,
        or lists nothing but Midi Through.
    """

    # A missing or unresponsive aplaymidi means there is no port to offer.
    try:
        out = subprocess.run(["aplaymidi", "-l"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None

    # The first line is the column header; each port line below it starts
    # with the client:port address, followed by the client and port names.
    for line in out.splitlines()[1:]:
        parts = line.split(maxsplit=2)

        if len(parts) >= 2 and "Midi Through" not in line:
            return parts[0]

    return None


def findPlayer() -> list[str] | None:
    """Choose the external synth command used for playback.

    Candidates are tried in order of preference: timidity, fluidsynth (only
    when a GM soundfont is available to load into it), wildmidi, and finally
    aplaymidi (only when a real ALSA synth port exists to send to).

    Returns:
        list[str] | None: the command prefix to which the .mid path is
        appended, or None when no usable player is installed.
    """

    if shutil.which("timidity"):
        return ["timidity", "-idqq"]  # dumb interface, fully quiet

    # fluidsynth produces nothing without a soundfont, so it only qualifies
    # when one was found on disk.
    if shutil.which("fluidsynth"):
        sf = _findSoundfont()

        if sf:
            return ["fluidsynth", "-i", "-q", sf]

    if shutil.which("wildmidi"):
        return ["wildmidi"]

    # aplaymidi needs a destination port, and the ever-present Midi Through
    # port is silent, so it only qualifies when a real synth port exists.
    if shutil.which("aplaymidi"):
        port = _alsaSynthPort()

        if port:
            return ["aplaymidi", "-p", port]

    return None


class Player:
    """Owns one external synth process and the temp file it is playing.

    The synth is only ever told to play a file from its start, so every
    change that has to be heard (mute, volume, speed, a new position) is a
    stop followed by a fresh play of a slice built by the caller.  The app
    keeps a second instance for instrument previews so that auditioning a
    sound leaves the main playback undisturbed.
    """

    def __init__(self):
        """Start with no synth running and no temp file on disk."""

        self.proc: subprocess.Popen | None = None
        self.tmpPath: str | None = None

    @property
    def playing(self) -> bool:
        """True while the synth process exists and has not yet exited."""

        alive = self.proc is not None and self.proc.poll() is None
        return alive

    def play(self, mf, audible_channels: set[int] | None = None,
             master: float = 1.0, speed: float = 1.0) -> bool:
        """Write mf to a temp file and spawn the synth on it.

        Muted channels keep their note events but are forced to CC7 volume 0:
        timidity fast-forwards over leading silence, so deleting the notes
        would desynchronise the audio from the UI cursor whenever a muted part
        opens the piece.  The notes stay in the file to anchor the timeline.
        Note velocities are scaled by master and every tempo event by speed
        (pitch is unaffected, since MIDI tempo is only event pacing).  All of
        this happens in a playback copy; the file handed in is never touched.

        Args:
            mf: the smf.MidiFile to play, typically a slice built by the song.
            audible_channels: channels to be heard; every other channel present
                in mf is muted.  None plays everything.
            master: multiplier applied to note-on velocities, 1.0 for as-is.
            speed: tempo multiplier, 1.0 for as-is.

        Returns:
            bool: True once the synth has been spawned, False when no player
            is installed.
        """

        from . import smf

        cmd = findPlayer()

        if cmd is None:
            return False

        # Any previous synth, and its temp file, goes before a new one starts.
        self.stop()

        # Mute is "every channel present that is not audible", so a channel
        # the caller has never heard of is simply left alone.
        muted: set[int] = set()

        if audible_channels is not None:
            present = {e.channel for t in mf.tracks for e in t if e.channel >= 0}
            muted = present - audible_channels

        # The playback copy is only built when something differs from the
        # file as given; otherwise the original goes straight to disk.
        if muted or master != 1.0 or speed != 1.0:
            filtered = smf.MidiFile(mf.format, mf.division, [])
            has_tempo = False

            for track in mf.tracks:
                new = []

                for e in track:
                    # A muted channel's own volume events are dropped so the
                    # CC7=0 inserted at tick 0 can never be undone later on.
                    if (e.channel in muted and e.status & 0xF0 == 0xB0
                            and e.data[0] == 7):
                        continue

                    # Master volume scales note-on velocities into 1..127 on
                    # a fresh Event, leaving the caller's events untouched.
                    if master != 1.0 and e.isNoteOn and e.channel not in muted:
                        v = max(1, min(127, round(e.data[1] * master)))
                        e = smf.Event(e.tick, e.status, bytearray([e.data[0], v]))

                    # Speed divides each tempo event's microseconds per beat
                    # (faster means fewer microseconds), clamped to the 24-bit
                    # field; pitch is unaffected because tempo only paces events.
                    if (speed != 1.0 and e.status == smf.META
                            and e.metaType == smf.META_TEMPO and len(e.data) == 3):
                        uspb = int.from_bytes(e.data, "big")
                        uspb = max(1, min(0xFFFFFF, round(uspb / speed)))
                        e = smf.Event(e.tick, smf.META,
                                      bytearray(uspb.to_bytes(3, "big")),
                                      smf.META_TEMPO)
                        has_tempo = True

                    new.append(e)

                filtered.tracks.append(new)

            # A file with no tempo event runs at the MIDI default of 120 bpm
            # (500_000 us per beat); speed is applied to such a file by
            # inserting that default tempo, scaled, at tick 0.
            if speed != 1.0 and not has_tempo:
                uspb = max(1, min(0xFFFFFF, round(500_000 / speed)))
                filtered.tracks[0].insert(0, smf.Event(
                    0, smf.META, bytearray(uspb.to_bytes(3, "big")), smf.META_TEMPO))

            # Each muted channel gets CC7=0 at tick 0 in the first track, ahead
            # of everything else, so it is silent from the very first note.
            for ch in sorted(muted):
                filtered.tracks[0].insert(0, smf.Event(0, 0xB0 | ch, bytearray([7, 0])))

            mf = filtered

        # The synth reads from a temp file that stop() removes; the descriptor
        # from mkstemp is closed at once because smf.write opens by path.
        fd, self.tmpPath = tempfile.mkstemp(suffix=".mid", prefix="montyroll_")
        os.close(fd)
        smf.write(mf, self.tmpPath)

        # Synth output is discarded; the UI animates its own cursor from the
        # tempo map rather than listening to the process.
        self.proc = subprocess.Popen(
            cmd + [self.tmpPath],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        return True

    def stop(self) -> None:
        """Terminate the synth process, if any, and remove its temp file."""

        # A running synth gets a polite terminate, then a kill if it is still
        # there a second later; a process that has already exited needs neither.
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()

            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()

        self.proc = None

        # The temp file is removed while it is still on disk; the existence
        # check keeps stop() safe when the file has already gone.
        if self.tmpPath and os.path.exists(self.tmpPath):
            os.unlink(self.tmpPath)

        self.tmpPath = None
