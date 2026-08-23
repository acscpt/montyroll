# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""External-synth playback tests for `player.py`.

No synth is spawned: `subprocess.Popen`, `subprocess.run`, `shutil.which` and
the soundfont glob are monkeypatched, and the tests inspect the temporary
MIDI file the player hands to the synth. That file is where the playback
rules live: muted channels are forced to CC7 0 with their own CC7 events
stripped, master volume scales note-on velocities, speed scales every tempo
event, and the file on disk is never touched.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from conftest import DIVISION, TEMPO_EVENTS, buildSong, eventTuples
from montyroll import player, smf


class FakeProc:
    """Stands in for a Popen handle; records whether it was stopped."""

    def __init__(self) -> None:
        """Start alive and untouched."""
        self.alive = True
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        """Mimic Popen.poll: None while alive, 0 once stopped."""
        status = None if self.alive else 0
        return status

    def terminate(self) -> None:
        """Mimic Popen.terminate."""
        self.terminated = True
        self.alive = False

    def wait(self, timeout: float | None = None) -> int:
        """Mimic Popen.wait."""
        return 0

    def kill(self) -> None:
        """Mimic Popen.kill."""
        self.killed = True
        self.alive = False


@pytest.fixture
def spawned(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Capture the command and the file contents passed to Popen.

    Returns:
        dict[str, Any]: filled with 'cmd', 'mf' (parsed file) and 'proc'.
    """
    captured: dict[str, Any] = {}

    def fakePopen(cmd: list[str], **kwargs: Any) -> FakeProc:
        captured["cmd"] = cmd
        captured["path"] = cmd[-1]
        captured["mf"] = smf.parse(cmd[-1])
        captured["proc"] = FakeProc()
        return captured["proc"]

    monkeypatch.setattr(player.subprocess, "Popen", fakePopen)
    monkeypatch.setattr(player, "findPlayer", lambda: ["fakesynth"])
    return captured


def _cc7AtZero(mf: smf.MidiFile, ch: int) -> list[int]:
    """List the CC7 values on a channel at tick 0 in track 0.

    Args:
        mf: the file.
        ch: 0-based channel.

    Returns:
        list[int]: the values in event order.
    """
    values = [e.data[1] for e in mf.tracks[0]
              if e.tick == 0 and e.status == 0xB0 | ch and e.data[0] == 7]
    return values


class TestDiscovery:
    """Which synth `findPlayer` chooses."""

    def testTimidityFirst(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """timidity wins when present, with its quiet flags."""
        monkeypatch.setattr(player.shutil, "which", lambda name: "/usr/bin/" + name)
        assert player.findPlayer() == ["timidity", "-idqq"]

    def testFluidsynthNeedsASoundfont(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Without a soundfont fluidsynth is passed over for the next option."""
        present = {"fluidsynth", "wildmidi"}
        monkeypatch.setattr(player.shutil, "which",
                            lambda name: "/usr/bin/" + name if name in present else None)
        monkeypatch.setattr(player, "_findSoundfont", lambda: None)
        assert player.findPlayer() == ["wildmidi"]
        monkeypatch.setattr(player, "_findSoundfont", lambda: "/sf/gm.sf2")
        assert player.findPlayer() == ["fluidsynth", "-i", "-q", "/sf/gm.sf2"]

    def testAplaymidiNeedsARealPort(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """aplaymidi is usable only when a port other than Midi Through exists."""
        monkeypatch.setattr(player.shutil, "which",
                            lambda name: "/usr/bin/aplaymidi" if name == "aplaymidi" else None)
        monkeypatch.setattr(player, "_alsaSynthPort", lambda: None)
        assert player.findPlayer() is None
        monkeypatch.setattr(player, "_alsaSynthPort", lambda: "128:0")
        assert player.findPlayer() == ["aplaymidi", "-p", "128:0"]

    def testNothingInstalled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No synth on PATH means no player."""
        monkeypatch.setattr(player.shutil, "which", lambda name: None)
        assert player.findPlayer() is None

    def testSoundfontSearchIsDeterministic(self, monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:
        """The first directory with a soundfont wins and its files are sorted."""
        first, second = tmp_path / "a", tmp_path / "b"
        first.mkdir()
        second.mkdir()
        (first / "zz.sf2").write_bytes(b"")
        (first / "aa.sf3").write_bytes(b"")
        (second / "gm.sf2").write_bytes(b"")
        monkeypatch.setattr(player, "_SOUNDFONT_DIRS", [str(tmp_path / "none"), str(first),
                                                        str(second)])
        assert player._findSoundfont() == str(first / "aa.sf3")

    def testAlsaPortSkipsMidiThrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The always-present Midi Through port is never chosen."""
        listing = (" Port    Client name                      Port name\n"
                   " 14:0    Midi Through                     Midi Through Port-0\n"
                   "128:0    FLUID Synth (1234)               Synth input port\n")

        def fakeRun(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(cmd, 0, stdout=listing, stderr="")

        monkeypatch.setattr(player.subprocess, "run", fakeRun)
        assert player._alsaSynthPort() == "128:0"

    def testAlsaPortOnlyMidiThrough(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With only Midi Through listed there is no usable port."""
        listing = (" Port    Client name                      Port name\n"
                   " 14:0    Midi Through                     Midi Through Port-0\n")
        monkeypatch.setattr(player.subprocess, "run",
                            lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, listing, ""))
        assert player._alsaSynthPort() is None

    def testAlsaPortToleratesMissingTool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A missing or hanging aplaymidi is reported as no port, not an exception."""
        def boom(cmd: list[str], **kwargs: Any) -> None:
            raise OSError("no aplaymidi")

        monkeypatch.setattr(player.subprocess, "run", boom)
        assert player._alsaSynthPort() is None


class TestPlay:
    """What the player writes for the synth."""

    def testNoPlayerReturnsFalse(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With no synth available play reports failure and spawns nothing."""
        monkeypatch.setattr(player, "findPlayer", lambda: None)
        monkeypatch.setattr(player.subprocess, "Popen",
                            lambda *a, **k: pytest.fail("Popen should not be called"))
        assert player.Player().play(buildSong()) is False

    def testUnmodifiedPlaybackWritesTheFileAsIs(self, spawned: dict[str, Any]) -> None:
        """With nothing muted or scaled the synth gets the song unchanged."""
        p = player.Player()
        assert p.play(buildSong()) is True
        assert spawned["cmd"][0] == "fakesynth"
        assert eventTuples(spawned["mf"]) == eventTuples(buildSong())
        assert p.playing

    def testMutedChannelsAreSilencedNotDeleted(self, spawned: dict[str, Any]) -> None:
        """A muted channel keeps its notes, gets CC7 0 at tick 0 and loses its own CC7s."""
        player.Player().play(buildSong(), audible_channels={0, 9})
        mf = spawned["mf"]
        assert _cc7AtZero(mf, 1) == [0]
        assert _cc7AtZero(mf, 2) == [0]
        fluteCc7 = [e for t in mf.tracks[1:] for e in t
                    if e.status == 0xB1 and e.data[0] == 7]
        assert fluteCc7 == []
        fluteNotes = [e for t in mf.tracks for e in t if e.status == 0x91 and e.isNoteOn]
        assert len(fluteNotes) == 5

    def testAudibleChannelsKeepTheirVolume(self, spawned: dict[str, Any]) -> None:
        """An audible channel's CC7 events are left exactly as they were."""
        player.Player().play(buildSong(), audible_channels={0, 9})
        mf = spawned["mf"]
        assert _cc7AtZero(mf, 0) == []
        pianoCc7 = [e.data[1] for t in mf.tracks for e in t
                    if e.status == 0xB0 and e.data[0] == 7]
        assert pianoCc7 == [100]

    def testMasterScalesVelocities(self, spawned: dict[str, Any]) -> None:
        """Note-on velocities are scaled and clamped to 1-127; note-offs are not."""
        player.Player().play(buildSong(), master=0.5)
        velocities = [e.data[1] for e in spawned["mf"].tracks[1] if e.isNoteOn]
        assert velocities == [50, 45, 40, 35, 30, 25]
        player.Player().play(buildSong(), master=2.0)
        loud = [e.data[1] for e in spawned["mf"].tracks[4] if e.isNoteOn]
        assert loud == [127, 127, 127]

    def testMasterLeavesMutedChannelsAlone(self, spawned: dict[str, Any]) -> None:
        """Scaling a muted channel's velocities would be wasted; they are left as is."""
        player.Player().play(buildSong(), audible_channels={9}, master=0.5)
        pianoVelocities = [e.data[1] for e in spawned["mf"].tracks[1] if e.isNoteOn]
        assert pianoVelocities == [100, 90, 80, 70, 60, 50]

    def testSpeedScalesEveryTempoEvent(self, spawned: dict[str, Any]) -> None:
        """Each tempo event is divided by the speed so the whole map speeds up."""
        player.Player().play(buildSong(), speed=2.0)
        tempos = [(e.tick, int.from_bytes(e.data, "big")) for e in spawned["mf"].tracks[0]
                  if e.status == smf.META and e.metaType == smf.META_TEMPO]
        assert tempos == [(t, uspb // 2) for t, uspb in TEMPO_EVENTS]

    def testSpeedInsertsATempoWhenTheFileHasNone(self, spawned: dict[str, Any]) -> None:
        """A file with no tempo event gets a scaled 120 bpm tempo at tick 0."""
        track = [smf.Event(0, 0x90, bytearray([60, 100])),
                 smf.Event(480, 0x80, bytearray([60, 64]))]
        player.Player().play(smf.MidiFile(0, DIVISION, [track]), speed=0.5)
        tempos = [(e.tick, int.from_bytes(e.data, "big")) for e in spawned["mf"].tracks[0]
                  if e.status == smf.META and e.metaType == smf.META_TEMPO]
        assert tempos == [(0, 1_000_000)]

    def testSourceFileIsNeverModified(self, spawned: dict[str, Any]) -> None:
        """Muting, scaling and speed all act on a copy."""
        original = buildSong()
        before = eventTuples(original)
        player.Player().play(original, audible_channels={0}, master=0.3, speed=1.5)
        assert eventTuples(original) == before

    def testPlayStopsThePreviousSynth(self, spawned: dict[str, Any]) -> None:
        """Starting playback again terminates the running process first."""
        p = player.Player()
        p.play(buildSong())
        first = spawned["proc"]
        p.play(buildSong())
        assert first.terminated
        assert spawned["proc"] is not first

    def testStopRemovesTheTempFile(self, spawned: dict[str, Any]) -> None:
        """Stopping terminates the synth and deletes the temporary file."""
        p = player.Player()
        p.play(buildSong())
        path = spawned["path"]
        assert os.path.exists(path)
        p.stop()
        assert not os.path.exists(path)
        assert p.proc is None
        assert p.tmpPath is None
        assert not p.playing

    def testStopKillsAStubbornSynth(self, spawned: dict[str, Any]) -> None:
        """A process that ignores terminate is killed after the wait times out."""
        p = player.Player()
        p.play(buildSong())
        proc = spawned["proc"]

        def slowWait(timeout: float | None = None) -> int:
            raise subprocess.TimeoutExpired("fakesynth", timeout or 0)

        proc.wait = slowWait
        proc.terminate = lambda: None
        p.stop()
        assert proc.killed

    def testStopWhenNotPlayingIsHarmless(self) -> None:
        """Stopping an idle player does nothing."""
        p = player.Player()
        p.stop()
        assert not p.playing
