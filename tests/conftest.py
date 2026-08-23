# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Shared fixtures and the test-report plugin for the MontyRoll test suite.

The suite needs no MIDI files on disk: `buildSongFile` writes a small format 1
file through `smf.write` itself, with tempo and time-signature changes, several
tracks sharing a channel, controllers, pitch bend, overlapping notes, a
velocity-zero note-off and a hanging note, so every parser and model path has
something to chew on. The expected values in the tests are derived from the
constants below.

Running with `--report` writes `tests/TEST_REPORT.md` after the run: outcome
badges, a per-file coverage table when pytest-cov is active, and every test
with its docstring as the description.
"""

from __future__ import annotations

import ast
import datetime
import inspect
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from montyroll import model, smf  # noqa: E402

DIVISION = 480
TEMPO_EVENTS = [(0, 500_000), (1920, 400_000), (5280, 600_000)]
TIME_SIGS = [(0, 4, 2), (3840, 3, 2)]      # (tick, numerator, denominator power)
MARKERS = [(1920, "Verse"), (5280, "Chorus")]
TRACK_NAMES = ["Conductor", "Piano", "Flute", "Flute II", "Drums", "Extras"]


# ------------------------------------------------------------ song builder
def _meta(tick: int, metaType: int, data: bytes) -> smf.Event:
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


def _noteOn(tick: int, ch: int, pitch: int, vel: int) -> smf.Event:
    """Build a note-on event.

    Args:
        tick: absolute tick.
        ch: 0-based channel.
        pitch: MIDI note number.
        vel: velocity; zero makes it a note-off in disguise.

    Returns:
        smf.Event: the event.
    """
    event = smf.Event(tick, 0x90 | ch, bytearray([pitch, vel]))
    return event


def _noteOff(tick: int, ch: int, pitch: int) -> smf.Event:
    """Build a note-off event with release velocity 64.

    Args:
        tick: absolute tick.
        ch: 0-based channel.
        pitch: MIDI note number.

    Returns:
        smf.Event: the event.
    """
    event = smf.Event(tick, 0x80 | ch, bytearray([pitch, 64]))
    return event


def _program(tick: int, ch: int, program: int) -> smf.Event:
    """Build a program-change event.

    Args:
        tick: absolute tick.
        ch: 0-based channel.
        program: GM program number.

    Returns:
        smf.Event: the event.
    """
    event = smf.Event(tick, 0xC0 | ch, bytearray([program]))
    return event


def _control(tick: int, ch: int, cc: int, value: int) -> smf.Event:
    """Build a control-change event.

    Args:
        tick: absolute tick.
        ch: 0-based channel.
        cc: controller number.
        value: controller value.

    Returns:
        smf.Event: the event.
    """
    event = smf.Event(tick, 0xB0 | ch, bytearray([cc, value]))
    return event


def buildSong() -> smf.MidiFile:
    """Build the synthetic test song in memory.

    Returns:
        smf.MidiFile: six tracks at 480 ticks per quarter note.
    """
    conductor = [
        _meta(0, smf.META_TRACK_NAME, b"Conductor"),
        _meta(0, smf.META_TIME_SIG, bytes([4, 2, 24, 8])),
        _meta(0, smf.META_KEY_SIG, bytes([0, 0])),
        _meta(1920, smf.META_MARKER, b"Verse"),
        _meta(3840, smf.META_TIME_SIG, bytes([3, 2, 24, 8])),
        _meta(5280, smf.META_MARKER, b"Chorus"),
    ]

    # Tempo events interleaved at their own ticks.
    for tick, uspb in TEMPO_EVENTS:
        conductor.append(_meta(tick, smf.META_TEMPO, uspb.to_bytes(3, "big")))

    conductor.sort(key=lambda e: e.tick)

    # Piano: a scale, two overlapping C4s to exercise FIFO pairing, a pitch
    # bend, and a note ended by a velocity-zero note-on.
    piano = [
        _meta(0, smf.META_TRACK_NAME, b"Piano"),
        _program(0, 0, 0),
        _control(0, 0, 7, 100),
        _control(0, 0, 10, 64),
        _noteOn(0, 0, 60, 100), _noteOff(480, 0, 60),
        _noteOn(480, 0, 64, 90), _noteOff(960, 0, 64),
        _noteOn(960, 0, 67, 80), _noteOff(1440, 0, 67),
        _noteOn(1920, 0, 60, 70),
        _noteOn(2160, 0, 60, 60),
        _noteOff(2400, 0, 60),
        _noteOff(2640, 0, 60),
        smf.Event(2880, 0xE0, bytearray([0, 0x50])),
        _noteOn(3840, 0, 62, 50),
        _noteOn(4320, 0, 62, 0),
    ]

    # Flute: a second program change and CC7 later in the track, and a note
    # that never receives its note-off.
    flute = [
        _meta(0, smf.META_TRACK_NAME, b"Flute"),
        _program(0, 1, 73),
        _control(0, 1, 7, 90),
        _noteOn(0, 1, 72, 80), _noteOff(960, 1, 72),
        _program(1920, 1, 73),
        _control(1920, 1, 7, 80),
        _noteOn(1920, 1, 74, 85), _noteOff(2880, 1, 74),
        _noteOn(5760, 1, 77, 90),
    ]

    # Flute II shares channel 1 with its own program change.
    flute2 = [
        _meta(0, smf.META_TRACK_NAME, b"Flute II"),
        _program(0, 1, 73),
        _noteOn(0, 1, 69, 75), _noteOff(960, 1, 69),
        _noteOn(1920, 1, 71, 75), _noteOff(2880, 1, 71),
    ]

    drums = [
        _meta(0, smf.META_TRACK_NAME, b"Drums"),
        _noteOn(0, 9, 36, 110), _noteOff(120, 9, 36),
        _noteOn(480, 9, 38, 100), _noteOff(600, 9, 38),
        _noteOn(960, 9, 42, 90), _noteOff(1080, 9, 42),
    ]

    # Extras: a sysex, a text event and a channel-pressure event, so that
    # events outside the note/controller set survive a round trip.
    extras = [
        _meta(0, smf.META_TRACK_NAME, b"Extras"),
        smf.Event(0, smf.SYSEX, bytearray([0x7E, 0x7F, 0x09, 0x01, 0xF7])),
        _meta(0, 0x01, b"hello"),
        smf.Event(480, 0xD0 | 2, bytearray([40])),
    ]

    song = smf.MidiFile(format=1, division=DIVISION,
                        tracks=[conductor, piano, flute, flute2, drums, extras])
    return song


def buildSongFile(path: Path) -> Path:
    """Write the synthetic test song to disk.

    Args:
        path: where to write it.

    Returns:
        Path: the same path, for chaining.
    """
    smf.write(buildSong(), str(path))
    return path


def eventTuples(mf: smf.MidiFile) -> list[list[tuple[int, int, bytes, int]]]:
    """Flatten a file to comparable tuples, one list per track.

    Args:
        mf: the file.

    Returns:
        list[list[tuple[int, int, bytes, int]]]: (tick, status, data, metaType)
        per event, in track order.
    """
    tuples = [[(e.tick, e.status, bytes(e.data), e.metaType) for e in track]
              for track in mf.tracks]
    return tuples


# --------------------------------------------------------------- fixtures
@pytest.fixture
def songPath(tmp_path: Path) -> Path:
    """The synthetic song written to a temporary file."""
    path = buildSongFile(tmp_path / "song.mid")
    return path


@pytest.fixture
def song(songPath: Path) -> model.Song:
    """The synthetic song loaded as a Song."""
    loaded = model.Song.load(str(songPath))
    return loaded


def _displayAvailable() -> bool:
    """Report whether a Tk root window can be created.

    Returns:
        bool: True when tkinter can open a display.
    """
    try:
        import tkinter as tk
        root = tk.Tk()
        root.destroy()
        available = True
    except Exception:  # tkinter raises TclError; guard anything else too
        available = False
    return available


needsDisplay = pytest.mark.skipif(not _displayAvailable(),
                                  reason="no display available for tkinter")


# ---------------------------------------------------------- report plugin
def pytest_addoption(parser: Any) -> None:
    """Register the --report flag.

    Args:
        parser: the pytest option parser.
    """
    parser.addoption("--report", action="store_true", default=False,
                     help="Generate tests/TEST_REPORT.md after the run.")


def pytest_configure(config: Any) -> None:
    """Attach the report collector when --report is given.

    Args:
        config: the pytest config.
    """
    if config.getoption("--report", default=False):
        plugin = ReportCollector(config)
        config.pluginmanager.register(plugin, "report_collector")


class ReportCollector:
    """Collects test outcomes and writes the markdown report at session end."""

    def __init__(self, config: Any) -> None:
        """Remember the config and start with no results.

        Args:
            config: the pytest config.
        """
        self._config = config
        self._results: list[dict[str, Any]] = []
        self._descriptions: dict[str, str] = {}

    def pytest_collection_modifyitems(self, session: Any, config: Any,
                                      items: list[Any]) -> None:
        """Capture a description for every collected test.

        Args:
            session: unused.
            config: unused.
            items: the collected test items.
        """
        del session, config

        for item in items:
            self._descriptions[item.nodeid] = _describeTestItem(item)

    def pytest_runtest_logreport(self, report: Any) -> None:
        """Record the final outcome of each test.

        Args:
            report: the phase report from pytest.
        """
        # Only the deciding phase counts: the call for pass/fail, the setup
        # for a skip, and any failed phase for an error.
        if report.when == "call":
            outcome = report.outcome
        elif report.when == "setup" and report.outcome == "skipped":
            outcome = "skipped"
        elif report.failed:
            outcome = "error"
        else:
            return

        self._results.append({
            "nodeid": report.nodeid,
            "outcome": outcome,
            "duration": report.duration,
            "longrepr": str(report.longrepr) if report.failed else "",
            "description": self._descriptions.get(report.nodeid, ""),
        })

    def pytest_sessionfinish(self, session: Any, exitstatus: int) -> None:
        """Write the report, and append it to the GitHub step summary if set.

        Args:
            session: unused.
            exitstatus: unused.
        """
        del session, exitstatus
        coverage = _extractCoverage(self._config)
        content = _buildReport(self._results, coverage)
        reportPath = ROOT / "tests" / "TEST_REPORT.md"
        reportPath.write_text(content, encoding="utf-8")

        summaryPath = os.environ.get("GITHUB_STEP_SUMMARY")

        if summaryPath:
            with open(summaryPath, "a", encoding="utf-8") as f:
                f.write(content)


def _extractCoverage(config: Any) -> list[tuple[str, int, int, int]] | None:
    """Pull per-file coverage figures from pytest-cov, if it is active.

    Args:
        config: the pytest config.

    Returns:
        list[tuple[str, int, int, int]] | None: (file, statements, missed,
        percent) per measured file, or None without coverage data.
    """
    covPlugin = config.pluginmanager.getplugin("_cov")

    if covPlugin is None:
        return None

    controller = getattr(covPlugin, "cov_controller", None)
    covObj = getattr(controller, "cov", None)

    if covObj is None:
        return None

    results = []

    try:
        data = covObj.get_data()

        for filename in sorted(data.measured_files()):
            analysis = covObj._analyze(filename)
            stmts = len(analysis.statements)
            miss = len(analysis.missing)
            cover = int(analysis.numbers.pc_covered) if stmts > 0 else 100
            short = os.path.relpath(filename, ROOT)
            results.append((short, stmts, miss, cover))
    except Exception:
        return None

    coverage = results if results else None
    return coverage


def _statusBadge(outcome: str) -> str:
    """Build a shields.io badge image for an outcome.

    Args:
        outcome: the pytest outcome name.

    Returns:
        str: markdown image syntax.
    """
    labels = {
        "passed": ("PASS", "2ea043"),
        "failed": ("FAIL", "cf222e"),
        "skipped": ("SKIP", "9a6700"),
        "error": ("ERROR", "a40e26"),
        "xfailed": ("XFAIL", "8250df"),
        "xpassed": ("XPASS", "0a7ea4"),
    }
    label, color = labels.get(outcome, (outcome.upper(), "57606a"))
    badge = f"![{label}](https://img.shields.io/badge/{label}-{color})"
    return badge


def _coverageBar(pct: int) -> str:
    """Build an inline HTML bar for a coverage percentage.

    Args:
        pct: coverage percent.

    Returns:
        str: HTML for the bar followed by the figure.
    """
    clamped = max(0, min(100, pct))

    if clamped >= 80:
        color = "#2ea043"
    elif clamped >= 60:
        color = "#d29922"
    else:
        color = "#cf222e"

    # Keep a sliver visible at low coverage so the bar is not mistaken for
    # an empty cell.
    visible = max(4, clamped) if clamped > 0 else 0
    bar = (
        "<span style=\"display:inline-block;width:98px;background:#30363d;"
        "border-radius:4px;overflow:hidden;vertical-align:middle;\">"
        f"<span style=\"display:inline-block;width:{visible}%;"
        f"background:{color};\">&nbsp;</span></span> {clamped}%"
    )
    return bar


def _badgeUrl(label: str, message: str, color: str) -> str:
    """Build a shields.io badge URL.

    Args:
        label: left-hand text.
        message: right-hand text.
        color: hex colour without the hash.

    Returns:
        str: the URL.
    """
    url = (f"https://img.shields.io/badge/{label.replace(' ', '%20')}-"
           f"{message.replace(' ', '%20')}-{color}")
    return url


def _moduleDocstring(module: str) -> str:
    """Read the module docstring of a test file.

    Args:
        module: repository-relative path of the test module.

    Returns:
        str: the docstring, or an empty string.
    """
    path = ROOT / module

    if not path.is_file():
        return ""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        doc = ast.get_docstring(tree) or ""
    except (OSError, SyntaxError):
        doc = ""

    return doc


def _normaliseWhitespace(text: str) -> str:
    """Collapse runs of whitespace to single spaces.

    Args:
        text: the text.

    Returns:
        str: one trimmed line.
    """
    collapsed = re.sub(r"\s+", " ", text).strip()
    return collapsed


def _humaniseTestName(name: str) -> str:
    """Turn a test function name into a readable sentence.

    Args:
        name: the test name, possibly with a parameter suffix.

    Returns:
        str: sentence-case words.
    """
    if "[" in name and name.endswith("]"):
        base, param = name.split("[", 1)
        suffix = f" [{param[:-1]}]"
    else:
        base, suffix = name, ""

    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", base).replace("_", " ")
    spaced = re.sub(r"(?i)^test\s+", "", spaced)
    pretty = _normaliseWhitespace(spaced)

    if not pretty:
        return name

    sentence = pretty[0].upper() + pretty[1:] + suffix
    return sentence


def _describeTestItem(item: Any) -> str:
    """Describe a test from its docstring, falling back to its name.

    Args:
        item: the collected test item.

    Returns:
        str: the description.
    """
    parts: list[str] = []
    cls = getattr(item, "cls", None)

    if cls is not None and inspect.getdoc(cls):
        first = _normaliseWhitespace(inspect.getdoc(cls)).split(". ")[0]
        parts.append(first.rstrip("."))

    obj = getattr(item, "obj", None)

    if obj is not None and inspect.getdoc(obj):
        parts.append(_normaliseWhitespace(inspect.getdoc(obj)))

    if parts:
        description = " - ".join(parts)
    else:
        description = _humaniseTestName(item.name)

    return description


def _escapeTableCell(text: str) -> str:
    """Escape pipes so a description cannot break the table.

    Args:
        text: the cell text.

    Returns:
        str: the escaped text.
    """
    escaped = text.replace("|", "\\|")
    return escaped


def _groupResults(results: list[dict[str, Any]]) -> OrderedDict:
    """Group results by module and class in first-seen order.

    Args:
        results: the recorded outcomes.

    Returns:
        OrderedDict: module -> class name ('' for functions) -> tests.
    """
    grouped: OrderedDict = OrderedDict()

    for r in results:
        parts = r["nodeid"].split("::")
        module = parts[0]
        classname = parts[1] if len(parts) == 3 else ""
        testname = parts[-1]
        grouped.setdefault(module, OrderedDict()).setdefault(classname, []).append({
            "name": testname,
            "outcome": r["outcome"],
            "duration": r["duration"],
            "description": r.get("description", ""),
        })

    return grouped


def _buildReport(results: list[dict[str, Any]],
                 coverage: list[tuple[str, int, int, int]] | None) -> str:
    """Render the markdown report.

    Args:
        results: the recorded outcomes.
        coverage: per-file coverage rows, or None.

    Returns:
        str: the report text.
    """
    lines: list[str] = []
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    lines += ["# Test Report", "", f"Generated: {timestamp}", "", "## Overview", "",
              "This report summarises the latest pytest run: outcomes, code coverage "
              "and a description of every test taken from its docstring.", ""]

    # Headline counters as badges, then the same figures as a table.
    total = len(results)
    passed = sum(1 for r in results if r["outcome"] == "passed")
    failed = sum(1 for r in results if r["outcome"] == "failed")
    skipped = sum(1 for r in results if r["outcome"] == "skipped")
    errors = sum(1 for r in results if r["outcome"] == "error")
    duration = sum(r["duration"] for r in results)
    passRate = int((passed / total) * 100) if total else 100
    lines.append(" ".join([
        f"![Total tests]({_badgeUrl('tests', str(total), '0366d6')})",
        f"![Passed]({_badgeUrl('passed', str(passed), '2ea043')})",
        f"![Failed]({_badgeUrl('failed', str(failed), 'cf222e')})",
        f"![Skipped]({_badgeUrl('skipped', str(skipped), '9a6700')})",
        f"![Pass rate]({_badgeUrl('pass%20rate', f'{passRate}%25', '2ea043')})",
    ]))
    lines += ["", "## Summary", "", "| Metric | Count |", "| --- | ---: |",
              f"| Total tests | {total} |", f"| Passed | {passed} |",
              f"| Failed | {failed} |", f"| Skipped | {skipped} |"]

    if errors:
        lines.append(f"| Errors | {errors} |")

    lines += [f"| Pass rate | {passRate}% |", f"| Duration | {duration:.2f}s |", "",
              "```mermaid", "pie title Test outcomes", f"    \"Passed\" : {passed}",
              f"    \"Failed\" : {failed}", f"    \"Skipped\" : {skipped}"]

    if errors:
        lines.append(f"    \"Errors\" : {errors}")

    lines += ["```", ""]

    # Coverage overall, per file, and the files with the most missed lines.
    if coverage:
        totalStmts = sum(s for _, s, _, _ in coverage)
        totalMiss = sum(m for _, _, m, _ in coverage)
        covered = totalStmts - totalMiss
        overall = int(covered / totalStmts * 100) if totalStmts else 100
        lines += ["## Code Coverage", "", f"**Overall: {overall}%**", "",
                  _coverageBar(overall), "", "```mermaid",
                  "pie title Covered vs missed statements",
                  f"    \"Covered\" : {covered}", f"    \"Missed\" : {totalMiss}",
                  "```", "", "### Per-file Coverage", "",
                  "| File | Stmts | Miss | Coverage |", "| --- | ---: | ---: | --- |"]

        for filename, stmts, miss, cover in coverage:
            lines.append(f"| [{filename}](../{filename}) | {stmts} | {miss} | "
                         f"{_coverageBar(cover)} |")

        worst = sorted(coverage, key=lambda row: (-row[2], row[3], row[0]))[:8]
        lines += ["", "### Coverage Priorities", "", "| File | Missing lines | Cover |",
                  "| --- | ---: | ---: |"]

        for filename, _stmts, miss, cover in worst:
            lines.append(f"| [{filename}](../{filename}) | {miss} | {cover}% |")

        lines.append("")

    # Every test, grouped by module and class, with the module docstring
    # quoted above its table.
    lines += ["## Test Results", ""]

    for module, classes in _groupResults(results).items():
        relative = module[len("tests/"):] if module.startswith("tests/") else module
        lines += [f"### [{module}](./{relative})", ""]
        doc = _moduleDocstring(module)

        if doc:
            for paragraph in doc.split("\n\n"):
                text = paragraph.replace("\n", " ").strip()

                if text:
                    lines += [f"> {text}", ">"]

            lines[-1] = ""

        for classname, tests in classes.items():
            if classname:
                lines += [f"**{classname}**", ""]

            lines += ["| Status | Test | Description | Time |", "| --- | --- | --- | ---: |"]

            for test in tests:
                lines.append(f"| {_statusBadge(test['outcome'])} | {test['name']} | "
                             f"{_escapeTableCell(test['description'])} | "
                             f"{test['duration']:.3f}s |")

            lines.append("")

    failures = [r for r in results if r["outcome"] in ("failed", "error")]

    if failures:
        lines += ["## Failures", ""]

        for f in failures:
            lines += [f"### {f['nodeid']}", "", "```", f["longrepr"], "```", ""]

    report = "\n".join(lines) + "\n"
    return report
