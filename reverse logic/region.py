"""Exact 1D region algebra.

A Region is a finite union of intervals of the real line whose endpoints are
rational (or +-infinity) and each endpoint is independently open or closed.
Points are degenerate closed intervals.  This family is closed under union,
intersection, difference and translation, which is what makes the engine
exact: removing [0, 1] from [0, 2] yields (1, 2], not [1, 2].

Endpoints are fractions.Fraction, never floats, so that a translation by d
followed by a translation by -d is the identity (it is not with floats).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Callable, Iterable, Sequence

INF = math.inf

# (lo, lo_closed, hi, hi_closed); lo/hi are Fraction or +-INF
Part = tuple


def num(x) -> Fraction:
    """Convert a user number to an exact Fraction.

    Floats are read through their shortest repr, so 0.1 means 1/10.
    """
    if isinstance(x, bool):
        raise TypeError("booleans are not coordinates")
    if isinstance(x, Fraction):
        return x
    if isinstance(x, int):
        return Fraction(x)
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            raise ValueError(f"coordinate must be finite, got {x}")
        return Fraction(repr(x))
    if isinstance(x, str):
        return Fraction(x)
    raise TypeError(f"unsupported coordinate type {type(x).__name__}")


def _part_has(p: Part, x) -> bool:
    lo, lc, hi, hc = p
    return (lo < x or (lc and lo == x)) and (x < hi or (hc and x == hi))


# A cell is (is_point, a, b, representative).  Cells partition the real line.
Cell = tuple


def cells_of(regions: Iterable[Region]) -> list[Cell]:
    """Partition of R on which every given region is constant."""
    ends = sorted({e for r in regions for p in r.parts for e in (p[0], p[2]) if not math.isinf(e)})
    if not ends:
        return [(False, -INF, INF, Fraction(0))]
    cells: list[Cell] = [(False, -INF, ends[0], ends[0] - 1)]
    for i, e in enumerate(ends):
        cells.append((True, e, e, e))
        nxt = ends[i + 1] if i + 1 < len(ends) else INF
        cells.append((False, e, nxt, e + 1 if math.isinf(nxt) else (e + nxt) / 2))
    return cells


@dataclass(frozen=True)
class Region:
    """Canonical form: sorted, pairwise disjoint, maximal (non-adjacent) parts.

    Two regions are equal as sets iff their canonical forms are equal, so the
    dataclass equality is set equality.  Use the classmethod constructors;
    the raw constructor does not normalise.
    """
    parts: tuple[Part, ...] = ()

    # ---- construction -------------------------------------------------
    @classmethod
    def from_cells(cls, cells: Sequence[Cell], flags: Sequence[bool]) -> Region:
        parts: list[Part] = []
        cur: list | None = None
        for (is_pt, a, b, _), f in zip(cells, flags):
            if f:
                if cur is None:
                    cur = [a, is_pt, b, is_pt]
                else:
                    cur[2], cur[3] = b, is_pt
            elif cur is not None:
                parts.append(tuple(cur))
                cur = None
        if cur is not None:
            parts.append(tuple(cur))
        return cls(tuple(parts))

    @classmethod
    def _combine(cls, regions: Sequence[Region], fn: Callable[[list[bool]], bool]) -> Region:
        cells = cells_of(regions)
        return cls.from_cells(cells, [fn([rep in r for r in regions]) for *_, rep in cells])

    @classmethod
    def interval(cls, a, b, left_closed: bool = True, right_closed: bool = True) -> Region:
        a, b = num(a), num(b)
        if a > b:
            raise ValueError(f"interval start {a} is after its end {b}")
        if a == b and not (left_closed and right_closed):
            return EMPTY
        return cls._combine([cls(((a, left_closed, b, right_closed),))], lambda m: m[0])

    @classmethod
    def closed(cls, a, b) -> Region:
        return cls.interval(a, b, True, True)

    @classmethod
    def open(cls, a, b) -> Region:
        return cls.interval(a, b, False, False)

    @classmethod
    def point(cls, x) -> Region:
        return cls.interval(x, x)

    @classmethod
    def points(cls, *xs) -> Region:
        return cls.union_of(*(cls.point(x) for x in xs))

    @classmethod
    def union_of(cls, *regions: Region) -> Region:
        return cls._combine(list(regions), any) if regions else EMPTY

    # ---- queries ------------------------------------------------------
    def __contains__(self, x) -> bool:
        return any(_part_has(p, x) for p in self.parts)

    def is_empty(self) -> bool:
        return not self.parts

    def __bool__(self) -> bool:
        return bool(self.parts)

    @property
    def start(self):
        """Infimum of the region."""
        if not self.parts:
            raise ValueError("empty region has no start")
        return self.parts[0][0]

    # ---- algebra ------------------------------------------------------
    def __or__(self, other: Region) -> Region:
        if not other.parts:
            return self
        if not self.parts:
            return other
        return Region._combine([self, other], lambda m: m[0] or m[1])

    def __and__(self, other: Region) -> Region:
        if not self.parts or not other.parts:
            return EMPTY
        return Region._combine([self, other], lambda m: m[0] and m[1])

    def __sub__(self, other: Region) -> Region:
        if not self.parts or not other.parts:
            return self
        return Region._combine([self, other], lambda m: m[0] and not m[1])

    def shift(self, delta) -> Region:
        d = num(delta)
        if d == 0:
            return self
        return Region(tuple((lo + d, lc, hi + d, hc) for lo, lc, hi, hc in self.parts))

    def __repr__(self) -> str:
        if not self.parts:
            return "∅"
        out = []
        for lo, lc, hi, hc in self.parts:
            if lo == hi:
                out.append(f"{{{lo}}}")
            else:
                out.append(f"{'[' if lc else '('}{lo}, {hi}{']' if hc else ')'}")
        return " ∪ ".join(out)


EMPTY = Region()
FULL = Region(((-INF, False, INF, False),))
