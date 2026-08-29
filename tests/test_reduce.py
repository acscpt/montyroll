# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Voice-reduction tests for `reduce.py`.

Most cases build a tiny song from note tuples so each transform and the
allocator can be checked against an answer worked out by hand; the
synthetic fixture and, when present, JSBD-1 cover the whole pipeline.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import ROOT
from montyroll import analysis, model, reduce, smf

LOCAL = ROOT / "resources" / "local"


def _song(*notes: tuple[int, int, int, int, int], division: int = 480) -> model.Song:
    """Build a one-track song from (channel, pitch, velocity, start, end) tuples.

    Args:
        *notes: the notes.
        division: ticks per quarter.

    Returns:
        model.Song: the song, tempo 120 bpm.
    """
    track = [smf.Event(0, smf.META, bytearray((500_000).to_bytes(3, "big")), smf.META_TEMPO)]

    for ch, pitch, vel, start, end in notes:
        track.append(smf.Event(start, 0x90 | ch, bytearray([pitch, vel])))
        track.append(smf.Event(end, 0x80 | ch, bytearray([pitch, 64])))

    song = model.Song(smf.MidiFile(0, division, [track]))
    return song


def _placed(song: model.Song, plan: reduce.Plan | None = None) -> list[reduce.Placed]:
    """Wrap a song's notes as placed notes with their weights.

    Args:
        song: the song.
        plan: the plan for priorities; a neutral one by default.

    Returns:
        list[reduce.Placed]: the placed notes.
    """
    plan = plan or reduce.Plan()
    placed = reduce._place(song.notes, reduce.weights(song, plan))
    return placed


def _note(song: model.Song, channel: int, pitch: int, start: int) -> model.Note:
    """Find a note by channel, pitch and start.

    Args:
        song: the song.
        channel: 0-based channel.
        pitch: MIDI pitch.
        start: start tick.

    Returns:
        model.Note: the note.
    """
    found = next(n for n in song.notes
                 if n.channel == channel and n.pitch == pitch and n.start == start)
    return found


class TestPlan:
    """Plans and pools."""

    def testSinglePoolTakesChannelsWithNotes(self, song: model.Song) -> None:
        """The one-pool plan lists every channel that has notes, and only those."""
        plan = reduce.Plan.single(song, 4, dedupOctave=True)
        assert plan.pools[0].channels == [0, 1, 9]
        assert plan.pools[0].voices == 4
        assert plan.pools[0].dedupOctave

    def testPriorityDefaultsToOne(self) -> None:
        """An unset channel priority is neutral."""
        plan = reduce.Plan(priority={3: 2.0})
        assert plan.priorityOf(3) == 2.0
        assert plan.priorityOf(0) == 1.0

    def testTierVoicesAreCapacityLessReserve(self) -> None:
        """A tier offers its channels less those reserved for something else."""
        tier = reduce.Tier("psg", 16, reserved=4)
        assert tier.voices == 12
        assert reduce.Tier("opm", 8).voices == 8

    def testValidateRejectsAnOverfullTier(self) -> None:
        """Pools wanting more voices than their tier has after the reserve are refused."""
        plan = reduce.Plan([reduce.Pool("a", [0], 5, tier="opm"),
                            reduce.Pool("b", [1], 4, tier="opm")],
                           tiers=[reduce.Tier("opm", 8)])

        with pytest.raises(ValueError, match="opm"):
            plan.validate()

        plan.pools[1].voices = 3
        plan.validate()

    def testValidateRejectsAnUnknownTierAndABadReserve(self) -> None:
        """A pool naming no known tier, or a reserve above the capacity, is refused."""
        plan = reduce.Plan([reduce.Pool("a", [0], 2, tier="sid")], tiers=[reduce.Tier("opm", 8)])

        with pytest.raises(ValueError, match="sid"):
            plan.validate()

        with pytest.raises(ValueError, match="reserves"):
            reduce.Plan(tiers=[reduce.Tier("psg", 16, reserved=20)]).validate()

        with pytest.raises(ValueError, match="unique"):
            reduce.Plan(tiers=[reduce.Tier("opm", 8), reduce.Tier("opm", 8)]).validate()

    def testReleaseOfPrefersTheChannelSetting(self) -> None:
        """A channel's own release overrides its tier's default; no tier means none."""
        pool = reduce.Pool("strings", [0, 1], 4, tier="opm")
        plan = reduce.Plan([pool], tiers=[reduce.Tier("opm", 8, release=300.0)],
                           release={1: 20.0})
        assert plan.releaseOf(0, pool) == 300.0
        assert plan.releaseOf(1, pool) == 20.0
        assert plan.releaseOf(0, reduce.Pool("loose", [0], 1)) == 0.0


class TestPlanFile:
    """The sidecar JSON."""

    def testSidecarPath(self) -> None:
        """The plan file takes the MIDI file's name with `.monty` for the extension."""
        assert reduce.sidecarPath("/x/JSBD-1.mid") == "/x/JSBD-1.monty"
        assert reduce.sidecarPath("song.midi") == "song.monty"
        assert reduce.sidecarPath("a.b.mid") == "a.b.monty"

    def testMidiFileIsResolvedAgainstThePlan(self, tmp_path: Path) -> None:
        """A bare name or a relative path is beside the plan file; an absolute one stands."""
        plan = reduce.Plan([reduce.Pool("all", [0], 4)])
        path = tmp_path / "sub" / "song.monty"
        path.parent.mkdir()
        reduce.savePlan(plan, str(path), midiFile="song.mid")
        read = reduce.readPlanFile(str(path))
        assert read.midiFile == str(tmp_path / "sub" / "song.mid")
        assert read.midiData is None
        assert read.plan.pools == plan.pools
        reduce.savePlan(plan, str(path), midiFile="../song.mid")
        assert reduce.readPlanFile(str(path)).midiFile == str(tmp_path / "sub" / ".." / "song.mid")
        reduce.savePlan(plan, str(path), midiFile="/elsewhere/song.mid")
        assert reduce.readPlanFile(str(path)).midiFile == "/elsewhere/song.mid"
        reduce.savePlan(plan, str(path))
        assert reduce.readPlanFile(str(path)).midiFile is None

    def testTargetsAndRangeRoundTrip(self, tmp_path: Path) -> None:
        """A target's section and the export's bars survive a round trip; bad bars are refused."""
        plan = reduce.Plan([reduce.Pool("all", [0], 4)])
        path = tmp_path / "x.monty"
        reduce.savePlan(plan, str(path), targets={"zsm": {"sounds": {"all": "brass"}}},
                        exportRange={"start": 45, "end": 108, "loop": 46})
        read = reduce.readPlanFile(str(path))
        assert read.targets == {"zsm": {"sounds": {"all": "brass"}}}
        assert read.range == {"start": 45, "end": 108, "loop": 46}
        reduce.savePlan(plan, str(path))
        read = reduce.readPlanFile(str(path))
        assert read.targets == {} and read.range == {}
        path.write_text('{"pools": [], "range": {"start": "forty"}}')

        with pytest.raises(ValueError, match="range"):
            reduce.readPlanFile(str(path))

    def testEmbeddedMidiIsCheckedAndReturned(self, tmp_path: Path) -> None:
        """A bundle carries the MIDI as base64 with its SHA-256, refused when it does not match."""
        plan = reduce.Plan([reduce.Pool("all", [0], 4)])
        raw = b"MThd" + bytes(range(40))
        path = tmp_path / "bundle.monty"
        reduce.savePlan(plan, str(path), midiFile="song.mid", midiData=raw)
        data = reduce.planToDict(plan, "song.mid", raw)
        assert data["midiBlob"]["sha256"] == __import__("hashlib").sha256(raw).hexdigest()
        read = reduce.readPlanFile(str(path))
        assert read.midiData == raw
        assert read.midiFile == str(tmp_path / "song.mid")
        text = path.read_text().replace(data["midiBlob"]["sha256"][:8], "00000000")
        path.write_text(text)

        with pytest.raises(ValueError, match="checksum"):
            reduce.readPlanFile(str(path))

        path.write_text('{"pools": [], "midiBlob": {"sha256": "x", "base64": "@@"}}')

        with pytest.raises(ValueError, match="malformed"):
            reduce.readPlanFile(str(path))

    def testRoundTrip(self, tmp_path: Path) -> None:
        """Tiers, pools with every switch, priorities and releases survive a save and load."""
        plan = reduce.Plan([reduce.Pool("melody", [0, 1], 3, dedupOctave=True, tremoloGap=8,
                                        legatoGap=120, monoTop=True, maxChord=2, tier="opm"),
                            reduce.Pool("rest", [9], 1)],
                           priority={0: 2.0, 1: 1.0},
                           tiers=[reduce.Tier("opm", 8, reserved=1, release=300.0, chip="opm")],
                           release={1: 20.0})
        path = tmp_path / "x.monty"
        reduce.savePlan(plan, str(path))
        back = reduce.loadPlan(str(path))
        assert back.tiers == plan.tiers
        assert back.pools == plan.pools
        assert back.priority == {0: 2.0}       # a neutral priority is not written
        assert back.release == {1: 20.0}

    def testChannelsAreOneBasedInTheFile(self) -> None:
        """The file names channels as the strip does, Ch 1 to Ch 16."""
        plan = reduce.Plan([reduce.Pool("all", [0, 9], 4)], priority={9: 2.0}, release={0: 5.0})
        data = reduce.planToDict(plan)
        assert data["version"] == reduce.PLAN_VERSION
        assert data["pools"][0]["channels"] == [1, 10]
        assert data["priority"] == {"10": 2.0}
        assert data["release"] == {"1": 5.0}

    def testUnknownKeysAreIgnoredAndDefaultsFilled(self) -> None:
        """A file may carry other sections, and a missing switch takes the pool default."""
        data = {"pools": [{"name": "a", "channels": [1], "voices": 2, "later": 1}],
                "export": {"loop": 45}}
        plan = reduce.planFromDict(data)
        pool = plan.pools[0]
        assert (pool.channels, pool.voices, pool.tier) == ([0], 2, "")
        assert pool.dedupUnison and not pool.dedupOctave and not pool.monoTop
        assert plan.tiers == [] and plan.priority == {} and plan.release == {}

    def testMalformedIsRefusedWithThePath(self, tmp_path: Path) -> None:
        """Anything that is not a fitting plan is a ValueError naming the file."""
        path = tmp_path / "bad.monty"
        cases = [
            ("[1, 2]", "bad.monty"),
            ("{not json", "not JSON"),
            ('{"pools": [{"name": "a", "voices": 1}]}', "not a plan"),
            ('{"pools": [{"name": "a", "channels": [17], "voices": 1}]}', "channel 17"),
            ('{"pools": [{"name": "a", "channels": [1], "voices": 9, "tier": "opm"}],'
             ' "tiers": [{"name": "opm", "capacity": 8}]}', "opm"),
        ]

        for text, match in cases:
            path.write_text(text)

            with pytest.raises(ValueError, match=match):
                reduce.loadPlan(str(path))


class TestWeights:
    """The per-note score."""

    def testAloneIsTheLine(self) -> None:
        """A note alone on its channel scores the top-line factor."""
        s = _song((0, 60, 127, 0, 480))
        w = reduce.weights(s, reduce.Plan())
        assert w[id(s.notes[0])] == pytest.approx(1.5)

    def testTextureAndVelocity(self) -> None:
        """Top 1.5, bottom 1.3, inner 1.0, scaled by velocity."""
        s = _song((0, 60, 127, 0, 480), (0, 64, 127, 0, 480), (0, 67, 127, 0, 480))
        w = reduce.weights(s, reduce.Plan())
        assert w[id(_note(s, 0, 67, 0))] == pytest.approx(1.5)
        assert w[id(_note(s, 0, 60, 0))] == pytest.approx(1.3)
        assert w[id(_note(s, 0, 64, 0))] == pytest.approx(1.0)
        quiet = _song((0, 60, 1, 0, 480))
        assert reduce.weights(quiet, reduce.Plan())[id(quiet.notes[0])] == \
            pytest.approx(1.5 * (0.5 + 0.5 / 127))

    def testDoublingDiscount(self) -> None:
        """A unison already sounding elsewhere scores 0.1, an octave 0.5."""
        s = _song((0, 60, 127, 0, 480), (1, 60, 127, 0, 480), (2, 72, 127, 0, 480))
        w = reduce.weights(s, reduce.Plan())
        assert w[id(_note(s, 0, 60, 0))] == pytest.approx(1.5)
        assert w[id(_note(s, 1, 60, 0))] == pytest.approx(0.15)
        assert w[id(_note(s, 2, 72, 0))] == pytest.approx(0.75)

    def testDrumsNeverCountAsDoubling(self) -> None:
        """A kick on 36 does not discount a bass C2 and is not discounted by it."""
        s = _song((9, 36, 127, 0, 480), (0, 36, 127, 0, 480))
        w = reduce.weights(s, reduce.Plan())
        assert all(v == pytest.approx(1.5) for v in w.values())

    def testPriorityScales(self) -> None:
        """A channel priority multiplies the score."""
        s = _song((0, 60, 127, 0, 480), (1, 62, 127, 0, 480))
        w = reduce.weights(s, reduce.Plan(priority={1: 2.0}))
        assert w[id(_note(s, 1, 62, 0))] == pytest.approx(3.0)
        assert w[id(_note(s, 0, 60, 0))] == pytest.approx(1.5)


class TestTransforms:
    """Each transform on a hand-built case."""

    def testDedupUnisonKeepsTheHeavier(self) -> None:
        """Of two channels striking one pitch together, the heavier survives."""
        s = _song((0, 60, 127, 0, 480), (1, 60, 40, 0, 480), (1, 64, 40, 0, 480))
        r = reduce.Reduction()
        out = reduce.dedupUnison(_placed(s), r)
        assert [(p.channel, p.pitch) for p in out] == [(0, 60), (1, 64)]
        assert r.dropped[id(_note(s, 1, 60, 0))] == "unison"

    def testDedupUnisonWithinAChannel(self) -> None:
        """Two tracks on one channel striking one pitch together cost one voice."""
        s = _song((0, 60, 100, 0, 480), (0, 60, 100, 0, 480))
        r = reduce.Reduction()
        assert len(reduce.dedupUnison(_placed(s), r)) == 1
        assert len(r.dropped) == 1

    def testDedupUnisonNeedsTheSameStart(self) -> None:
        """A unison that starts later is a different note and stays."""
        s = _song((0, 60, 100, 0, 480), (1, 60, 100, 10, 480))
        assert len(reduce.dedupUnison(_placed(s), reduce.Reduction())) == 2

    def testDedupUnisonLeavesDrums(self) -> None:
        """A kick on key 36 and a bass C2 struck together are two sounds, both kept."""
        s = _song((0, 36, 100, 0, 480), (9, 36, 100, 0, 480))
        r = reduce.Reduction()
        assert len(reduce.dedupUnison(_placed(s), r)) == 2
        assert not r.dropped

    def testDedupOctaveTieKeepsTheLower(self) -> None:
        """Equal weights: the lower octave survives, as it carries the root."""
        s = _song((0, 48, 100, 0, 480), (1, 60, 100, 0, 480))
        r = reduce.Reduction()
        out = reduce.dedupOctave(_placed(s), r)
        assert [(p.channel, p.pitch) for p in out] == [(0, 48)]
        assert r.dropped[id(_note(s, 1, 60, 0))] == "octave"

    def testDedupOctaveHeavierWins(self) -> None:
        """A priority on the upper part keeps the upper octave."""
        s = _song((0, 48, 100, 0, 480), (1, 60, 100, 0, 480))
        plan = reduce.Plan(priority={1: 3.0})
        out = reduce.dedupOctave(_placed(s, plan), reduce.Reduction())
        assert [(p.channel, p.pitch) for p in out] == [(1, 60)]

    def testDedupOctaveLeavesDrums(self) -> None:
        """A drum hit an octave from a bass note is not a doubling."""
        s = _song((0, 36, 100, 0, 480), (9, 48, 100, 0, 480))
        assert len(reduce.dedupOctave(_placed(s), reduce.Reduction())) == 2

    def testMergeTremolo(self) -> None:
        """Re-strikes within the gap continue the note; a wider gap is a new note."""
        s = _song((0, 60, 100, 0, 46), (0, 60, 100, 48, 94), (0, 60, 100, 96, 142),
                  (0, 60, 100, 300, 346), (1, 60, 100, 48, 94))
        r = reduce.Reduction()
        out = reduce.mergeTremolo(_placed(s), 2, r)
        mine = [(p.start, p.end) for p in out if p.channel == 0]
        assert mine == [(0, 142), (300, 346)]
        assert len([p for p in out if p.channel == 1]) == 1
        assert sum(1 for v in r.dropped.values() if v == "tremolo") == 2

    def testLegatoBridgesShortGapsOnly(self) -> None:
        """A note reaches the next onset within the gap; a rest longer than it stays."""
        s = _song((0, 60, 100, 0, 440), (0, 62, 100, 480, 920), (0, 64, 100, 1440, 1900))
        out = reduce.legato(_placed(s), 60)
        ends = {p.pitch: p.end for p in out}
        assert ends[60] == 480
        assert ends[62] == 920
        assert ends[64] == 1900

    def testLegatoNeverShortens(self) -> None:
        """A note already past the next onset is left alone."""
        s = _song((0, 60, 100, 0, 1000), (0, 62, 100, 480, 920))
        out = reduce.legato(_placed(s), 60)
        assert {p.pitch: p.end for p in out}[60] == 1000

    def testMonoTopDropsUnderAndRestrikesAfter(self) -> None:
        """A lower note under the top is dropped; a top that ends hands back to the survivor."""
        s = _song((0, 60, 100, 0, 960), (0, 67, 100, 240, 480))
        r = reduce.Reduction()
        out = reduce.monoTop(_placed(s), r)
        segments = [(p.pitch, p.start, p.end) for p in out]
        assert segments == [(60, 0, 240), (67, 240, 480), (60, 480, 960)]
        assert not r.dropped

    def testMonoTopDropsANoteStartingUnderTheTop(self) -> None:
        """A note that starts while a higher one sounds never sounds."""
        s = _song((0, 67, 100, 0, 960), (0, 60, 100, 240, 480))
        r = reduce.Reduction()
        out = reduce.monoTop(_placed(s), r)
        assert [(p.pitch, p.start, p.end) for p in out] == [(67, 0, 960)]
        assert r.dropped[id(_note(s, 0, 60, 240))] == "under the top line"

    def testMonoTopSegmentsKeepTheRelease(self) -> None:
        """A re-struck segment carries the release of the note it came from."""
        s = _song((0, 60, 100, 0, 960), (0, 67, 100, 240, 480))
        placed = _placed(s)

        for p in placed:
            p.release = 30

        out = reduce.monoTop(placed, reduce.Reduction())
        assert [p.release for p in out] == [30, 30, 30]

    def testThinChords(self) -> None:
        """Top and bottom are kept first, then the heaviest inner notes."""
        s = _song((0, 60, 100, 0, 480), (0, 64, 50, 0, 480), (0, 67, 120, 0, 480),
                  (0, 72, 100, 0, 480))
        r = reduce.Reduction()
        out = reduce.thinChords(_placed(s), 3, r)
        assert sorted(p.pitch for p in out) == [60, 67, 72]
        assert r.dropped[id(_note(s, 0, 64, 0))] == "thinned"
        one = reduce.thinChords(_placed(s), 1, reduce.Reduction())
        assert [p.pitch for p in one] == [72]


class TestAllocate:
    """Voice assignment and stealing."""

    def testFitsWithoutStealing(self) -> None:
        """Three notes on three voices: nothing dropped, voices from the base."""
        s = _song((0, 60, 100, 0, 480), (0, 64, 100, 0, 480), (0, 67, 100, 0, 480))
        r = reduce.Reduction()
        out = reduce.allocate(_placed(s), 3, 5, r)
        assert sorted(p.voice for p in out) == [5, 6, 7]
        assert not r.dropped and not r.cut

    def testVoicesAreReused(self) -> None:
        """A voice freed by a note-off serves the next onset."""
        s = _song((0, 60, 100, 0, 480), (0, 64, 100, 480, 960))
        out = reduce.allocate(_placed(s), 1, 0, reduce.Reduction())
        assert [p.voice for p in out] == [0, 0]

    def testStealsTheWeakestByRemainingTime(self) -> None:
        """Of two equal-weight notes the one nearer its end is stolen."""
        s = _song((0, 60, 100, 0, 480), (0, 64, 100, 0, 2000), (0, 67, 100, 240, 720))
        r = reduce.Reduction()
        out = reduce.allocate(_placed(s), 2, 0, r)
        assert r.cut == {id(_note(s, 0, 60, 0)): 240}
        assert {p.pitch: p.end for p in out}[60] == 240
        assert not r.dropped

    def testWeakNewcomerIsNotStarted(self) -> None:
        """A note weaker than everything playing is dropped rather than flickered in."""
        s = _song((0, 60, 127, 0, 2000), (0, 64, 127, 0, 2000), (1, 60, 10, 240, 300))
        r = reduce.Reduction()
        out = reduce.allocate(_placed(s), 2, 0, r)
        assert len(out) == 2
        assert r.dropped[id(_note(s, 1, 60, 240))] == "no voice"
        assert not r.cut

    def testStolenAtOnsetIsADrop(self) -> None:
        """A note silenced at the tick it started is recorded as dropped, not cut."""
        s = _song((0, 60, 10, 0, 100), (0, 64, 127, 0, 2000), (0, 67, 127, 0, 2000))
        r = reduce.Reduction()
        out = reduce.allocate(_placed(s), 2, 0, r)
        assert len(out) == 2
        assert r.dropped[id(_note(s, 0, 60, 0))] == "no voice"
        assert not r.cut


    def testAPartKeepsItsVoice(self) -> None:
        """A free voice that last played the note's channel is taken before a lower one."""
        s = _song((0, 60, 100, 0, 480), (1, 64, 100, 0, 480),
                  (1, 65, 100, 960, 1440), (0, 62, 100, 960, 1440))
        out = reduce.allocate(_placed(s), 2, 0, reduce.Reduction())
        voices = {(p.channel, p.start): p.voice for p in out}
        assert voices[(0, 0)] == 0 and voices[(1, 0)] == 1
        assert voices[(1, 960)] == 1 and voices[(0, 960)] == 0

    def testReleaseHoldsTheVoice(self) -> None:
        """A voice stays busy through its release; taking it then clips the tail, not the note."""
        s = _song((0, 60, 127, 0, 480), (0, 64, 127, 520, 1000))
        placed = _placed(s)
        placed[0].release = 100
        r = reduce.Reduction()
        out = reduce.allocate(placed, 1, 0, r)
        assert [p.voice for p in out] == [0, 0]
        assert r.clipped == {id(_note(s, 0, 60, 0)): 60}
        assert not r.cut and not r.dropped

    def testVoiceIsFreeAfterTheRelease(self) -> None:
        """Once the release has run out the voice is simply free: nothing clipped."""
        s = _song((0, 60, 127, 0, 480), (0, 64, 127, 600, 1000))
        placed = _placed(s)
        placed[0].release = 100
        r = reduce.Reduction()
        out = reduce.allocate(placed, 1, 0, r)
        assert [p.voice for p in out] == [0, 0]
        assert not r.clipped

    def testRestrikeKeepsTheReleasingVoice(self) -> None:
        """The same pitch struck again on the channel retriggers its voice at no cost."""
        s = _song((0, 60, 127, 0, 480), (0, 60, 127, 500, 1000), (1, 64, 127, 500, 1000))
        placed = _placed(s)

        for p in placed:
            p.release = 200

        r = reduce.Reduction()
        out = reduce.allocate(placed, 2, 0, r)
        voices = {(p.channel, p.pitch, p.start): p.voice for p in out}
        assert voices[(0, 60, 500)] == voices[(0, 60, 0)]
        assert voices[(1, 64, 500)] != voices[(0, 60, 0)]
        assert not r.clipped and not r.cut and not r.dropped

    def testCutNoteLosesItsWholeRelease(self) -> None:
        """A note stolen while sounding is cut and the whole of its tail is clipped."""
        s = _song((0, 60, 100, 0, 480), (0, 64, 100, 0, 2000), (0, 67, 100, 240, 720))
        placed = _placed(s)

        for p in placed:
            p.release = 50

        r = reduce.Reduction()
        reduce.allocate(placed, 2, 0, r)
        victim = _note(s, 0, 60, 0)
        assert r.cut == {id(victim): 240}
        assert r.clipped == {id(victim): 50}

    def testTailOutweighsATrivialNewcomer(self) -> None:
        """A note worth less than a loud note's remaining tail is not started."""
        s = _song((0, 60, 127, 0, 480), (1, 64, 1, 490, 500))
        placed = _placed(s)
        placed[0].release = 1000
        r = reduce.Reduction()
        out = reduce.allocate(placed, 1, 0, r)
        assert len(out) == 1
        assert r.dropped[id(_note(s, 1, 64, 490))] == "no voice"
        assert not r.clipped

    def testReleaseTicksFollowTheTempo(self, song: model.Song) -> None:
        """Five hundred milliseconds is 480 ticks at 120 bpm, 600 at 150 and 400 at 100."""
        assert reduce.releaseTicks(song, 0, 500) == 480
        assert reduce.releaseTicks(song, 1920, 500) == 600
        assert reduce.releaseTicks(song, 5280, 500) == 400
        assert reduce.releaseTicks(song, 0, 0) == 0


class TestReduce:
    """The whole pipeline."""

    def testBigBudgetLosesOnlyDoublings(self, song: model.Song) -> None:
        """With voices to spare only the dedup transforms remove anything."""
        r = reduce.reduce(song, reduce.Plan.single(song, 64))
        assert not r.cut
        assert all(v == "unison" for v in r.dropped.values())
        assert r.voicesUsed == 64

    def testPoolsNumberVoicesInOrder(self, song: model.Song) -> None:
        """Each pool's voices start where the previous pool's end."""
        plan = reduce.Plan([reduce.Pool("keys", [0], 2), reduce.Pool("wind", [1], 3),
                            reduce.Pool("drums", [9], 1)])
        r = reduce.reduce(song, plan)
        byChannel = {}

        for p in r.placed:
            byChannel.setdefault(p.channel, set()).add(p.voice)

        assert byChannel[0] <= {0, 1}
        assert byChannel[1] <= {2, 3, 4}
        assert byChannel[9] == {5}
        assert r.voicesUsed == 6

    def testTiersNumberVoicesInTierOrder(self, song: model.Song) -> None:
        """A tier's pools get one contiguous range in tier order, whatever the pool order."""
        plan = reduce.Plan([reduce.Pool("wind", [1], 3, tier="psg"),
                            reduce.Pool("keys", [0], 2, tier="opm"),
                            reduce.Pool("drums", [9], 1)],
                           tiers=[reduce.Tier("opm", 8), reduce.Tier("psg", 16, reserved=4)])
        r = reduce.reduce(song, plan)
        assert r.tierBase == {"opm": 0, "psg": 2}
        byChannel = {}

        for p in r.placed:
            byChannel.setdefault(p.channel, set()).add(p.voice)

        assert byChannel[0] <= {0, 1}
        assert byChannel[1] <= {2, 3, 4}
        assert byChannel[9] == {5}
        assert r.voicesUsed == 6

    def testOverfullTierIsRefused(self, song: model.Song) -> None:
        """A plan whose pools exceed a tier is refused before anything is placed."""
        plan = reduce.Plan([reduce.Pool("all", [0, 1, 9], 9, tier="opm")],
                           tiers=[reduce.Tier("opm", 8)])

        with pytest.raises(ValueError):
            reduce.reduce(song, plan)

    def testReleaseComesFromTheTierUnlessTheChannelSaysOtherwise(self) -> None:
        """Notes carry their tier's release in ticks; a channel override replaces it."""
        s = _song((0, 60, 100, 0, 480), (1, 64, 100, 0, 480))
        plan = reduce.Plan([reduce.Pool("all", [0, 1], 4, tier="opm")],
                           tiers=[reduce.Tier("opm", 8, release=500.0)], release={1: 0.0})
        r = reduce.reduce(s, plan)
        assert {p.channel: p.release for p in r.placed} == {0: 480, 1: 0}
        assert {p.channel: p.free for p in r.placed} == {0: 960, 1: 480}

    def testClippedTailsCountTowardsTheLoss(self) -> None:
        """A clipped release costs its ticks at RELEASE_WEIGHT."""
        s = _song((0, 60, 127, 0, 480), (0, 64, 127, 520, 1000))
        plan = reduce.Plan([reduce.Pool("one", [0], 1, tier="opm")],
                           tiers=[reduce.Tier("opm", 8, release=500.0)])
        r = reduce.reduce(s, plan)

        # 500 ms is 480 ticks at 120 bpm, and 40 of them had run when the
        # second note took the voice.
        assert r.clipped == {id(_note(s, 0, 60, 0)): 440}
        assert r.loss == pytest.approx(reduce.RELEASE_WEIGHT * 1.5 * 440)
        assert not r.cut and not r.dropped

    def testChannelOutsideEveryPoolIsSilent(self, song: model.Song) -> None:
        """A channel in no pool is not placed and not counted as dropped."""
        r = reduce.reduce(song, reduce.Plan([reduce.Pool("keys", [0], 4)]))
        assert {p.channel for p in r.placed} == {0}
        assert all(n.channel == 0 for n in song.notes if id(n) in r.dropped)

    def testLossAccounting(self) -> None:
        """Loss is weight times ticks lost, per channel."""
        s = _song((0, 60, 127, 0, 480), (1, 64, 127, 0, 480), (2, 67, 127, 0, 480))
        plan = reduce.Plan.single(s, 2, dedupUnison=False)
        r = reduce.reduce(s, plan)
        assert len(r.dropped) == 1
        victim = next(n for n in s.notes if id(n) in r.dropped)
        assert r.lossByChannel == {victim.channel: pytest.approx(1.5 * 480)}
        assert r.loss == pytest.approx(720)
        assert reduce.reduce(s, reduce.Plan.single(s, 3)).loss == 0

    def testPriorityProtectsAChannel(self) -> None:
        """Raising a channel's priority moves the loss onto the others."""
        s = _song((0, 60, 100, 0, 480), (1, 64, 100, 0, 480), (2, 67, 100, 0, 480))
        plain = reduce.reduce(s, reduce.Plan.single(s, 2))
        assert 2 in plain.lossByChannel or 0 in plain.lossByChannel
        boosted = reduce.Plan.single(s, 2)
        boosted.priority[0] = 5.0
        boosted.priority[2] = 5.0
        r = reduce.reduce(s, boosted)
        assert list(r.lossByChannel) == [1]

    def testReducedFileHonoursTheBudget(self, song: model.Song) -> None:
        """The written file never sounds more notes at once than the plan allows."""
        r = reduce.reduce(song, reduce.Plan.single(song, 2))
        mf = reduce.toMidiFile(song, r)
        demand = analysis.sweep(model.Song(mf))
        assert demand.peak <= 2

    def testReducedFileKeepsMetasAndChannelEvents(self, song: model.Song) -> None:
        """The copy carries the tempo map and each channel's program and controllers."""
        r = reduce.reduce(song, reduce.Plan.single(song, 64))
        mf = reduce.toMidiFile(song, r)
        copy = model.Song(mf)
        assert copy.tempoMap == song.tempoMap
        assert copy.timeSigs == song.timeSigs
        assert copy.markers == song.markers
        assert copy.channels[0].program == song.channels[0].program
        assert copy.channels[0].volume == song.channels[0].volume
        assert copy.trackNames[1:] == ["Ch 1", "Ch 2", "Ch 3", "Ch 10"]   # Ch 3: pressure only

    def testReducedFileIsIndependent(self, song: model.Song) -> None:
        """Editing the copy's events leaves the original untouched."""
        r = reduce.reduce(song, reduce.Plan.single(song, 64))
        mf = reduce.toMidiFile(song, r)
        before = [(n.pitch, n.start, n.end) for n in song.notes]

        for track in mf.tracks:
            for e in track:
                e.tick += 1

        assert [(n.pitch, n.start, n.end) for n in song.notes] == before

    def testDanubeOnEightVoices(self) -> None:
        """JSBD-1, when present: eight voices with octaves folded loses few notes to stealing."""
        path = LOCAL / "JSBD-1.mid"

        if not path.exists():
            pytest.skip("JSBD-1.mid is not available on this machine")

        danube = model.Song.load(str(path))
        r = reduce.reduce(danube, reduce.Plan.single(danube, 8, dedupOctave=True))
        noVoice = sum(1 for v in r.dropped.values() if v == "no voice")
        assert noVoice < 300
        assert analysis.sweep(model.Song(reduce.toMidiFile(danube, r))).peak <= 8
