# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""Export targets: a reduction turned into what a particular chip set plays.

A target is a module that provides what `Target` lists: a name, a file
suffix, its frame rate, the chips a `Tier` may name, an `export` from a
song, a reduction and a plan to bytes, a `verify` of written bytes and a
`describe` of them. `TARGETS` maps a key to each target, and the
application offers every entry as an export and every chip in the tiers
dialog. `zsm` is the Commander X16's YM2151 and VERA PSG through the ZSM
file format.
"""

from __future__ import annotations

from types import ModuleType
from typing import Protocol

from .. import model, reduce
from . import zsm
from .chip import Chip


class Target(Protocol):
    """What an export target module provides."""

    NAME: str               # for the menu, "Commander X16 (ZSM)"
    SUFFIX: str             # the file extension, ".zsm"
    FRAME_RATE: int         # frames per second the target's player runs at
    CHIPS: tuple[Chip, ...]

    def export(self, song: model.Song, reduction: reduce.Reduction, plan: reduce.Plan, *,
               start: int = 0, end: int | None = None, loop: int | None = None,
               master: float = 1.0) -> bytes:
        """The file for a reduction; raises ValueError when a pool has no chip of this target."""

    def verify(self, data: bytes, plan: reduce.Plan) -> list[str]:
        """Problems found in written bytes, empty when they are sound."""

    def describe(self, data: bytes, plan: reduce.Plan) -> str:
        """One line about written bytes: size, length, voices used."""


def presetTiers(target: ModuleType) -> list[reduce.Tier]:
    """The tiers a target's chips make, one per chip, named after it.

    Args:
        target: a registered target module.

    Returns:
        list[reduce.Tier]: the tiers, capacity, reserve, release and chip
        from each `Chip`.
    """
    tiers = [reduce.Tier(c.name, c.capacity, c.reserved, c.release, c.name) for c in target.CHIPS]
    return tiers


TARGETS: dict[str, ModuleType] = {"zsm": zsm}
