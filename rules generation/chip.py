"""The chip: exact position, and the mode transitions decided by the texts."""
from __future__ import annotations

import sys
from collections.abc import Hashable, Iterable
from fractions import Fraction
from pathlib import Path

# "reverse logic" has a space in its name, so it cannot be imported as a package.
# Every module of this folder imports chip first, directly or through track.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reverse logic"))

from region import num  # noqa: E402

Mode = Hashable
Number = Fraction | int | float | str  # anything region.num accepts


class Chip:
    """Position and modes.  The mode at q is the mode of the last transition at
    or before q (the new mode applies from the transition position on), or the
    initial mode if there is none.  Transitions form a finite list, so every
    bounded stretch of the line contains finitely many of them."""

    def __init__(self, modes: Iterable[Mode], initial_mode: Mode, initial_position: Number = 0):
        self.modes = frozenset(modes)
        if not self.modes:
            raise ValueError("there must be at least one mode")
        if initial_mode not in self.modes:
            raise ValueError(f"unknown mode {initial_mode!r}")
        self.initial_mode = initial_mode
        self.position = num(initial_position)
        self.transitions: list[tuple[Fraction, Mode]] = []  # strictly increasing positions

    def mode_at(self, q: Number) -> Mode:
        q = num(q)
        mode = self.initial_mode
        for pos, m in self.transitions:
            if pos > q:
                break
            mode = m
        return mode

    @property
    def mode(self) -> Mode:
        return self.mode_at(self.position)

    def advance(self, new_position: Number) -> None:
        p = num(new_position)
        if p < self.position:
            raise ValueError(f"the chip never moves to the left: {p} < {self.position}")
        self.position = p

    def schedule(self, transitions: Iterable[tuple[Number, Mode]]) -> None:
        """Replace the transitions to the right of the chip by `transitions`.

        Those at or before the chip already happened and are kept.  New ones
        must be strictly to the right of the chip, in increasing order, with
        known modes."""
        new = [(num(p), m) for p, m in transitions]
        last = self.position
        for p, m in new:
            if p <= last:
                raise ValueError(f"mode transitions must be to the right of the chip ({self.position}) "
                                 f"and in increasing order, got {p} after {last}")
            if m not in self.modes:
                raise ValueError(f"unknown mode {m!r}")
            last = p
        self.transitions = [t for t in self.transitions if t[0] <= self.position] + new
