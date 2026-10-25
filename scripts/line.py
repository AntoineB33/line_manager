"""The state of the line.

For every element type T the state is a finite set of pieces (interval,
percentage, owner) whose intervals are pairwise disjoint: a type is present at
most once at a position. s_T(x) is the percentage of the piece containing x,
or 0 when there is none. Owners are USER (added by the user) and GEN (added by
a generated script).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Callable, Iterable

from intervals import EMPTY, Interval, IntervalSet, Num

USER = "user"
GEN = "generated"


@dataclass(frozen=True)
class Piece:
    interval: Interval
    value: Fraction  # percentage in (0, 100]
    owner: str

    def __str__(self) -> str:
        return f"{self.interval} {self.value}%{' (gen)' if self.owner == GEN else ''}"


def _percent(p) -> Fraction:
    p = Fraction(p)
    if not 0 < p <= 100:
        raise ValueError(f"percentage {p} not in (0, 100]")
    return p


class LineState:
    def __init__(self) -> None:
        self._layers: dict[str, tuple[Piece, ...]] = {}

    @staticmethod
    def from_entries(entries: Iterable[tuple[str, Interval | IntervalSet, object]], owner: str = USER) -> LineState:
        """Build a state from (type, interval, percentage) entries.

        Raises ValueError if two entries put the same type at the same position.
        """
        state = LineState()
        for etype, where, pct in entries:
            x = where if isinstance(where, IntervalSet) else IntervalSet.of(where)
            overlap = state.support(etype) & x
            if overlap:
                raise ValueError(f"{etype} present several times on {overlap}")
            state.assign(etype, x, _percent(pct), owner)
        return state

    def copy(self) -> LineState:
        s = LineState()
        s._layers = dict(self._layers)
        return s

    def types(self) -> list[str]:
        return sorted(t for t, ps in self._layers.items() if ps)

    def pieces(self, etype: str) -> tuple[Piece, ...]:
        return self._layers.get(etype, ())

    def is_empty(self) -> bool:
        return not any(self._layers.values())

    def snapshot(self) -> dict[str, tuple[Piece, ...]]:
        return {t: ps for t, ps in self._layers.items() if ps}

    def __eq__(self, other: object) -> bool:
        return isinstance(other, LineState) and self.snapshot() == other.snapshot()

    # reading
    def where(self, etype: str, pred: Callable[[Piece], bool]) -> IntervalSet:
        return IntervalSet(p.interval for p in self.pieces(etype) if pred(p))

    def support(self, etype: str) -> IntervalSet:
        return self.where(etype, lambda p: True)

    def owned(self, etype: str, owner: str) -> IntervalSet:
        return self.where(etype, lambda p: p.owner == owner)

    def value_at(self, etype: str, x: Num) -> Fraction:
        for p in self.pieces(etype):
            if p.interval.contains(x):
                return p.value
        return Fraction(0)

    def integral(self, etype: str, window: IntervalSet) -> Fraction:
        """Integral of s_T / 100 over a bounded window."""
        total = Fraction(0)
        for p in self.pieces(etype):
            total += p.value / 100 * (IntervalSet.of(p.interval) & window).measure()
        return total

    def restricted(self, etype: str, region: IntervalSet) -> tuple[Piece, ...]:
        """The pieces of a type cut to a region (canonical, comparable)."""
        out = []
        for p in self.pieces(etype):
            for iv in (IntervalSet.of(p.interval) & region).intervals:
                out.append(Piece(iv, p.value, p.owner))
        return tuple(out)

    # writing
    def assign(self, etype: str, region: IntervalSet, value: Fraction, owner: str) -> None:
        """Set s_T = value with the given owner on region (value 0 removes)."""
        kept = [
            Piece(iv, p.value, p.owner)
            for p in self.pieces(etype)
            for iv in (IntervalSet.of(p.interval) - region).intervals
        ]
        if value:
            kept += [Piece(iv, _percent(value), owner) for iv in region.intervals]
        self._layers[etype] = self._canonical(kept)

    def remove_owned(self, etype: str, region: IntervalSet, owner: str) -> None:
        self.assign(etype, region & self.owned(etype, owner), Fraction(0), owner)

    @staticmethod
    def _canonical(pieces: list[Piece]) -> tuple[Piece, ...]:
        groups: dict[tuple[Fraction, str], list[Interval]] = {}
        for p in pieces:
            groups.setdefault((p.value, p.owner), []).append(p.interval)
        out = [Piece(iv, v, o) for (v, o), ivs in groups.items() for iv in IntervalSet(ivs).intervals]
        out.sort(key=lambda p: (p.interval.lo, not p.interval.lo_closed))
        return tuple(out)

    def __str__(self) -> str:
        lines = []
        for t in self.types():
            lines += [f"{t} at {p}" for p in self.pieces(t)]
        return "\n".join(lines) if lines else "(empty line)"


def changed_region(a: LineState, b: LineState, etype: str) -> IntervalSet:
    """Positions where (value, owner) of a type differs between two states."""
    keys = {(p.value, p.owner) for p in a.pieces(etype)} | {(p.value, p.owner) for p in b.pieces(etype)}
    out = EMPTY
    for v, o in keys:
        ga = a.where(etype, lambda p: (p.value, p.owner) == (v, o))
        gb = b.where(etype, lambda p: (p.value, p.owner) == (v, o))
        out = out | (ga - gb) | (gb - ga)
    return out
