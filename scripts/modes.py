"""Modes of the chip positions.

Modes are defined on [start, +inf), the positions the chip can be at. A
timeline is an initial mode and a finite, strictly increasing list of
transitions (t_i, m_i) with t_i > start and m_i != m_{i-1}; the mode at x is
the mode of the last transition at or before x (the initial mode before the
first one). So every position has exactly one mode, modes are constant on the
half-open segments [t_i, t_{i+1}), and every bounded stretch holds finitely
many transitions.

`ModeTimeline` is the external truth; `ModeKnowledge` is what has been revealed
when the chip is at a frontier F: the timeline restricted to [start, F].
"""

from __future__ import annotations

from fractions import Fraction

from intervals import INF, EMPTY, Interval, IntervalSet, Num


class ModeTimeline:
    def __init__(self, start, initial_mode: str, transitions: list[tuple[object, str]] = ()) -> None:
        self.start = Fraction(start)
        self.initial_mode = initial_mode
        self.transitions: list[tuple[Fraction, str]] = [(Fraction(t), m) for t, m in transitions]
        prev_t, prev_m = self.start, initial_mode
        for t, m in self.transitions:
            if not t > prev_t:
                raise ValueError("transition positions must be strictly increasing and after the start")
            if m == prev_m:
                raise ValueError(f"transition at {t} does not change the mode")
            prev_t, prev_m = t, m

    def mode_at(self, x: Num) -> str:
        if x < self.start:
            raise ValueError(f"no mode before the start {self.start}")
        mode = self.initial_mode
        for t, m in self.transitions:
            if t <= x:
                mode = m
            else:
                break
        return mode

    def known(self, frontier: Num) -> ModeKnowledge:
        return ModeKnowledge(self.start, Fraction(frontier), self.initial_mode,
                             [(t, m) for t, m in self.transitions if t <= frontier])


class ModeKnowledge:
    def __init__(self, start: Fraction, frontier: Fraction, initial_mode: str, transitions: list[tuple[Fraction, str]]) -> None:
        if frontier < start:
            raise ValueError("frontier before the start")
        self.start = start
        self.frontier = frontier
        self.initial_mode = initial_mode
        self.transitions = transitions

    def segments(self) -> list[tuple[Fraction, Fraction, str]]:
        """Known segments (lo, hi, mode): [lo, hi) for completed ones, [lo, F] for the current one."""
        starts = [(self.start, self.initial_mode)] + self.transitions
        segs = []
        for i, (lo, m) in enumerate(starts):
            hi = starts[i + 1][0] if i + 1 < len(starts) else self.frontier
            segs.append((lo, hi, m))
        return segs

    def current_mode(self) -> str:
        return self.transitions[-1][1] if self.transitions else self.initial_mode

    def current_start(self) -> Fraction:
        return self.transitions[-1][0] if self.transitions else self.start

    def region(self, mode: str) -> IntervalSet:
        """Known positions with this mode."""
        segs = self.segments()
        out = []
        for i, (lo, hi, m) in enumerate(segs):
            if m == mode:
                last = i == len(segs) - 1
                out.append(Interval(lo, hi, True, last) if (lo < hi or last) else None)
        return IntervalSet(iv for iv in out if iv)

    def unknown(self) -> IntervalSet:
        """Positions whose mode is not known yet: (F, +inf)."""
        return IntervalSet.after(self.frontier)

    def possible(self, mode: str) -> IntervalSet:
        """Positions that have, or may still get, this mode."""
        return self.region(mode) | self.unknown()

    def modes_seen(self) -> set[str]:
        return {self.initial_mode} | {m for _, m in self.transitions}
