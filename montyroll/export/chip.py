# SPDX-FileCopyrightText: 2026 Heisenberg (acscpt)
# SPDX-License-Identifier: MIT

"""The record naming a chip an export target plays, shared by the targets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chip:
    """A chip a target plays, as a `Tier` names it and a preset fills it."""

    name: str
    capacity: int           # channels
    release: float = 0.0    # default milliseconds a voice stays busy after its note
    reserved: int = 0       # channels a preset keeps for something other than music
    sounds: tuple[str, ...] = ()  # names a pool on this chip may be given
