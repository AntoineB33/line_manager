"""The chip: exact position, and the mode transitions it goes through.  See SEMANTICS.md."""
from __future__ import annotations

import sys
from collections.abc import Hashable, Iterable
from fractions import Fraction
from pathlib import Path

# "reverse_logic" is a sibling folder, not an installed package.
# Every module of this folder imports chip first, directly or through track.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reverse_logic"))

from region import num

Mode = Hashable
Number = Fraction | int | float | str  # anything region.num accepts


class Chip:
    """Position and modes.  The mode at q is the mode of the last transition at
    or before q (the new mode applies from the transition position on), or the
    initial mode if there is none.  Transitions are not known in advance: they
    are told with the moves of the chip, as they happen, so they are never
    ahead of it.  A move tells finitely many, so every bounded stretch the chip
    has gone through contains finitely many of them."""

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

    def check(self, new_position: Number, transitions: Iterable[tuple[Number, Mode]] = ()
              ) -> tuple[Fraction, list[tuple[Fraction, Mode]]]:
        """Validate a move to `new_position` through `transitions`: the chip
        never moves left, and the transitions are in (position, new_position],
        strictly increasing, with known modes."""
        p = num(new_position)
        if p < self.position:
            raise ValueError(f"the chip never moves to the left: {p} < {self.position}")
        new = [(num(q), m) for q, m in transitions]
        last = self.position
        for q, m in new:
            if not last < q <= p:
                raise ValueError(f"mode transitions must be after the chip ({self.position}), at most at its "
                                 f"new position ({p}) and in increasing order, got {q} after {last}")
            if m not in self.modes:
                raise ValueError(f"unknown mode {m!r}")
            last = q
        return p, new
