"""The chip: exact position, mode, and the minimum gap between mode transitions."""
from __future__ import annotations

import sys
from collections.abc import Hashable, Iterable
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

# "reverse logic" has a space in its name, so it cannot be imported as a package.
# Every module of this folder imports chip first, directly or through track.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "reverse logic"))

from region import num  # noqa: E402

Mode = Hashable
Number = Fraction | int | float | str  # anything region.num accepts


@dataclass(frozen=True)
class ChipState:
    position: Fraction
    mode: Mode
    last_transition: Fraction | None  # position of the latest mode transition


class Chip:
    def __init__(self, modes: Iterable[Mode], initial_mode: Mode,
                 initial_position: Number = 0, min_transition_gap: Number = 1):
        self.modes = frozenset(modes)
        if not self.modes:
            raise ValueError("there must be at least one mode")
        if initial_mode not in self.modes:
            raise ValueError(f"unknown mode {initial_mode!r}")
        self.min_transition_gap = num(min_transition_gap)
        if self.min_transition_gap <= 0:
            raise ValueError("min_transition_gap must be strictly positive")
        # The initial mode is not a transition.
        self.state = ChipState(num(initial_position), initial_mode, None)

    @property
    def position(self) -> Fraction:
        return self.state.position

    @property
    def mode(self) -> Mode:
        return self.state.mode

    def next_state(self, new_position: Number, new_mode: Mode | None = None) -> ChipState:
        """The state after advancing to `new_position`, then switching to
        `new_mode` there.  The chip itself is not changed."""
        s = self.state
        p = num(new_position)
        if p < s.position:
            raise ValueError(f"the chip only advances: {p} < {s.position}")
        mode = s.mode if new_mode is None else new_mode
        if mode not in self.modes:
            raise ValueError(f"unknown mode {mode!r}")
        if mode == s.mode:
            return ChipState(p, mode, s.last_transition)
        if s.last_transition is not None and p - s.last_transition < self.min_transition_gap:
            raise ValueError(
                f"mode transition at {p} is only {p - s.last_transition} after the previous one "
                f"at {s.last_transition}; the minimum gap is {self.min_transition_gap}")
        return ChipState(p, mode, p)
