"""Exact 1D region algebra.

A Region is a union of intervals of the real line whose endpoints are rational
(or +-infinity), each endpoint independently open or closed: finitely many
intervals (the *core*), and on each side, optionally, a *tail* that repeats a
pattern forever with a rational period.  Points are degenerate closed
intervals.  This family is closed under union, intersection, difference and
translation, which is what makes the engine exact: removing [0, 1] from [0, 2]
yields (1, 2], not [1, 2]; and an ordered disjunction over a ray can yield
B on [0, 1) u [2, 3) u [4, 5) u ... (see SEMANTICS.md, sec. 1).

Endpoints are fractions.Fraction, never floats, so that a translation by d
followed by a translation by -d is the identity (it is not with floats).
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from fractions import Fraction

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


def lcm(a: Fraction, b: Fraction) -> Fraction:
    """The least common multiple of two positive rationals."""
    d = math.lcm(a.denominator, b.denominator)
    return Fraction(math.lcm(int(a * d), int(b * d)), d)


def _part_has(p: Part, x) -> bool:
    lo, lc, hi, hc = p
    return (lo < x or (lc and lo == x)) and (x < hi or (hc and x == hi))


# A cell is (is_point, a, b, representative).  Cells partition the real line.
Cell = tuple


def cells_of(regions: Iterable[Region]) -> list[Cell]:
    """Partition of R on which every given region is constant (regions without tails)."""
    regions = list(regions)
    assert all(r.left is None and r.right is None for r in regions), "cells of a periodic region"
    ends = sorted({e for r in regions for p in r.parts for e in (p[0], p[2]) if not math.isinf(e)})
    if not ends:
        return [(False, -INF, INF, Fraction(0))]
    cells: list[Cell] = [(False, -INF, ends[0], ends[0] - 1)]
    for i, e in enumerate(ends):
        cells.append((True, e, e, e))
        nxt = ends[i + 1] if i + 1 < len(ends) else INF
        cells.append((False, e, nxt, e + 1 if math.isinf(nxt) else (e + nxt) / 2))
    return cells


def _flat(parts: Sequence[Part]) -> Region:
    """A region without tails from canonical parts."""
    return Region(tuple(parts))


@dataclass(frozen=True)
class Tail:
    """Where x is beyond `at` (above it for a right tail, below it for a left
    one), x is in the region iff (x - at) mod period is in `pattern`, the
    canonical parts of a region within [0, period)."""
    at: Fraction
    period: Fraction
    pattern: tuple[Part, ...]

    def has(self, x) -> bool:
        y = (x - self.at) % self.period
        return any(_part_has(p, y) for p in self.pattern)


@dataclass(frozen=True)
class Region:
    """Canonical form: the core is sorted, pairwise disjoint, maximal parts;
    a tail starts where the region starts to repeat (the least such position)
    with its least period, and is never constant (a constant tail is a core
    part to infinity, or nothing).

    Two regions are equal as sets iff their canonical forms are equal, so the
    dataclass equality is set equality.  Use the classmethod constructors;
    the raw constructor does not normalise."""
    parts: tuple[Part, ...] = ()
    left: Tail | None = None   # x < left.at
    right: Tail | None = None  # x > right.at

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
        if all(r.left is None and r.right is None for r in regions):
            cells = cells_of(regions)
            return cls.from_cells(cells, [fn([rep in r for r in regions]) for *_, rep in cells])
        return _canonical(_combine_periodic(regions, fn))

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
        if not regions:
            return EMPTY
        if any(r.left is not None or r.right is not None for r in regions):
            out = EMPTY
            for r in regions:
                out |= r
            return out
        return cls._combine(list(regions), any)

    @classmethod
    def tiled(cls, cell: Region, step, first: int | None, last: int | None) -> Region:
        """The union of cell + k*step for first <= k <= last (None: without bound).
        `cell` is a bounded region without tails."""
        step = num(step)
        if not cell:
            return EMPTY
        assert cell.left is None and cell.right is None and step > 0
        lo, hi = cell.parts[0][0], cell.parts[-1][2]
        assert not (math.isinf(lo) or math.isinf(hi)), "a tiled cell must be bounded"
        span = math.ceil((hi - lo) / step) + 1
        if first is not None and last is not None:
            return cls.union_of(*(cell.shift(k * step) for k in range(first, last + 1)))
        if first is None and last is None:
            first = 0  # both sides: tiled from 0 on the right, and the rest on the left
            return cls.tiled(cell, step, 0, None) | cls.tiled(cell, step, None, -1)
        if last is None:
            assert first is not None
            # Above `at`, only copies first + i, i >= 0, can meet x, and all of them do as x grows.
            at = hi + (first - 1) * step
            core = cls.union_of(*(cell.shift(k * step) for k in range(first, first + span + 1)))
            window = cls.union_of(*(cell.shift(k * step) for k in range(first, first + 2 * span + 2)))
            pattern = _pattern(window, at, step, right=True)
            return _canonical(Region((core & at_or_below(at)).parts, None, Tail(at, step, pattern)))
        at = lo + (last + 1) * step
        core = cls.union_of(*(cell.shift(k * step) for k in range(last - span, last + 1)))
        window = cls.union_of(*(cell.shift(k * step) for k in range(last - 2 * span - 1, last + 1)))
        pattern = _pattern(window, at, step, right=False)
        return _canonical(Region((core & at_or_above(at)).parts, Tail(at, step, pattern), None))

    # ---- queries ------------------------------------------------------
    def __contains__(self, x) -> bool:
        if self.right is not None and x > self.right.at:
            return self.right.has(x)
        if self.left is not None and x < self.left.at:
            return self.left.has(x)
        return any(_part_has(p, x) for p in self.parts)

    def is_empty(self) -> bool:
        return not self.parts and self.left is None and self.right is None

    def __bool__(self) -> bool:
        return not self.is_empty()

    def is_finite(self) -> bool:
        """Finitely many parts (no tail)."""
        return self.left is None and self.right is None

    @property
    def start(self):
        """Infimum of the region."""
        if self.is_empty():
            raise ValueError("empty region has no start")
        if self.left is not None:
            return -INF
        if self.parts:
            return self.parts[0][0]
        assert self.right is not None
        return self.right.at + self.right.pattern[0][0]

    @property
    def end(self):
        """Supremum of the region."""
        if self.is_empty():
            raise ValueError("empty region has no end")
        if self.right is not None:
            return INF
        if self.parts:
            return self.parts[-1][2]
        assert self.left is not None
        return self.left.at - self.left.period + self.left.pattern[-1][2]

    def within(self, lo, hi) -> Region:
        """The region on [lo, hi], a region without tails (lo, hi may be infinite
        on a side without tail)."""
        out = [_flat(self.parts) & _closed_window(lo, hi)]
        for tail, right in ((self.right, True), (self.left, False)):
            if tail is None:
                continue
            a, b = (max(lo, tail.at), hi) if right else (lo, min(hi, tail.at))
            if a > b:
                continue
            assert not (math.isinf(a) or math.isinf(b)), "an unbounded window of a periodic tail"
            pat = _flat(tail.pattern)
            side = above(tail.at) if right else below(tail.at)
            for k in range(math.floor((a - tail.at) / tail.period) - 1, math.ceil((b - tail.at) / tail.period) + 1):
                out.append(pat.shift(tail.at + k * tail.period) & side & _closed_window(a, b))
        return Region.union_of(*out)

    def ends(self, lo, hi) -> list[Fraction]:
        """The finite ends of the parts of the region within [lo, hi]."""
        return sorted({e for p in self.within(lo, hi).parts for e in (p[0], p[2])
                       if not math.isinf(e) and lo <= e <= hi})

    def periods(self) -> list[Fraction]:
        return [t.period for t in (self.left, self.right) if t is not None]

    def measure(self) -> Fraction:
        """The total length (the region must be bounded)."""
        assert self.is_finite()
        return sum((b - a for a, _, b, _ in self.parts), Fraction(0))

    # ---- algebra ------------------------------------------------------
    def __or__(self, other: Region) -> Region:
        if other.is_empty():
            return self
        if self.is_empty():
            return other
        return Region._combine([self, other], lambda m: m[0] or m[1])

    def __and__(self, other: Region) -> Region:
        if self.is_empty() or other.is_empty():
            return EMPTY
        return Region._combine([self, other], lambda m: m[0] and m[1])

    def __sub__(self, other: Region) -> Region:
        if self.is_empty() or other.is_empty():
            return self
        return Region._combine([self, other], lambda m: m[0] and not m[1])

    def shift(self, delta) -> Region:
        d = num(delta)
        if d == 0:
            return self
        return Region(tuple((lo + d, lc, hi + d, hc) for lo, lc, hi, hc in self.parts),
                      None if self.left is None else Tail(self.left.at + d, self.left.period, self.left.pattern),
                      None if self.right is None else Tail(self.right.at + d, self.right.period, self.right.pattern))

    def __repr__(self) -> str:
        out = []
        if self.left is not None:
            out.append(f"(…{_show(self.left.pattern)} every {self.left.period} below {self.left.at})")
        out += [_show((p,)) for p in self.parts]
        if self.right is not None:
            out.append(f"({_show(self.right.pattern)} every {self.right.period} above {self.right.at}…)")
        return " ∪ ".join(out) if out else "∅"


def _show(parts) -> str:
    out = []
    for lo, lc, hi, hc in parts:
        if lo == hi:
            out.append(f"{{{lo}}}")
        else:
            out.append(f"{'[' if lc else '('}{lo}, {hi}{']' if hc else ')'}")
    return " ∪ ".join(out) if out else "∅"


EMPTY = Region()
FULL = Region(((-INF, False, INF, False),))


def above(x) -> Region:
    return Region(((x, False, INF, False),))


def below(x) -> Region:
    return Region(((-INF, False, x, False),))


def at_or_below(x) -> Region:
    return Region(((-INF, False, x, True),))


def at_or_above(x) -> Region:
    return Region(((x, True, INF, False),))


def _closed_window(lo, hi) -> Region:
    return Region(((lo, not math.isinf(lo), hi, not math.isinf(hi)),))


def _pattern(region: Region, at: Fraction, period: Fraction, right: bool) -> tuple[Part, ...]:
    """The pattern, within [0, period), of `region` just beyond `at`: on
    (at, at + period] for a right tail, [at - period, at) for a left one."""
    if right:
        piece = region.within(at, at + period) & above(at)
        return (piece.shift(-at) & below(period)
                | (Region.point(0) if (at + period) in piece else EMPTY)).parts
    piece = region.within(at - period, at) & below(at)
    return piece.shift(period - at).parts


def _threshold(r: Region, right: bool):
    """Beyond it (above for right, below for left), r is periodic or constant."""
    tail, other = (r.right, r.left) if right else (r.left, r.right)
    if tail is not None:
        return tail.at
    ends = [e for p in r.parts for e in (p[0], p[2]) if not math.isinf(e)]
    if other is not None:
        ends.append(other.at)
    return (max(ends) if right else min(ends)) if ends else None


def _combine_periodic(regions: Sequence[Region], fn) -> Region:
    """fn applied pointwise, for regions with tails (not canonical)."""
    sides = {}
    for right in (True, False):
        periods = [t.period for r in regions for t in ((r.right if right else r.left),) if t is not None]
        if not periods:
            continue
        p = periods[0]
        for q in periods[1:]:
            p = lcm(p, q)
        ts = [t for r in regions for t in (_threshold(r, right),) if t is not None]
        sides[right] = (max(ts) if right else min(ts), p)
    lo = sides[False][0] if False in sides else -INF
    hi = sides[True][0] if True in sides else INF
    core = Region._combine([r.within(lo, hi) for r in regions], fn)
    tails = {}
    for right, (at, period) in sides.items():
        window = (at, at + period) if right else (at - period, at)
        piece = Region._combine([r.within(*window) for r in regions], fn)
        tails[right] = Tail(at, period, _pattern(piece, at, period, right))
    return Region(core.parts, tails.get(False), tails.get(True))


def _rotate(pattern: Region, d: Fraction, period: Fraction) -> Region:
    """The pattern shifted by d, modulo period."""
    s = pattern.shift(d)
    return (s & below(period)) | (s & at_or_above(period)).shift(-period)


def _least_period(pattern: tuple[Part, ...], period: Fraction) -> tuple[tuple[Part, ...], Fraction]:
    """The least period of the pattern (a divisor of `period`), and the pattern for it."""
    pat = _flat(pattern)
    ends = {e for q in pattern for e in (q[0], q[2])}
    for n in range(len(ends) + 1, 1, -1):
        d = period / n
        if _rotate(pat, d, period) == pat:
            return (pat & below(d)).parts, d
    return pattern, period


def _sup(r: Region):
    """The supremum of a region, canonical or not (-inf if empty)."""
    out = -INF
    if r.right is not None and r.right.pattern:
        return INF
    if r.parts:
        out = max(out, r.parts[-1][2])
    if r.left is not None and r.left.pattern:
        out = max(out, r.left.at - r.left.period + r.left.pattern[-1][2])
    return out


def _inf(r: Region):
    """The infimum of a region, canonical or not (+inf if empty)."""
    out = INF
    if r.left is not None and r.left.pattern:
        return -INF
    if r.parts:
        out = min(out, r.parts[0][0])
    if r.right is not None and r.right.pattern:
        out = min(out, r.right.at + r.right.pattern[0][0])
    return out


def _canonical(r: Region) -> Region:
    """The canonical form: tails with their least period, starting where the
    region starts to repeat; constant tails become core parts.

    The right tail of a set S with period P starts at sup{x : x in S differs from
    x + P in S} (0 if S is P-periodic everywhere); the left tail at the
    corresponding infimum, but not after the right tail's start.  Both are
    functions of S, so equal sets have equal canonical forms."""
    core, tails = _flat(r.parts), {}
    for right, tail in ((True, r.right), (False, r.left)):
        if tail is None:
            continue
        pat = _flat(tail.pattern)
        if pat.is_empty():
            continue
        if pat == Region.interval(0, tail.period, True, False):
            core |= above(tail.at) if right else below(tail.at)
            continue
        tails[right] = Tail(tail.at, *reversed(_least_period(tail.pattern, tail.period)))
    if not tails:
        return core
    whole = Region(core.parts, tails.get(False), tails.get(True))
    starts = {}
    if True in tails:
        period = tails[True].period
        diff = _combine_periodic([whole, whole.shift(-period)], lambda m: m[0] != m[1])
        starts[True] = _sup(diff) if not diff.is_empty() and _sup(diff) > -INF else Fraction(0)
    if False in tails:
        period = tails[False].period
        diff = _combine_periodic([whole, whole.shift(period)], lambda m: m[0] != m[1])
        at = _inf(diff) if not diff.is_empty() and _inf(diff) < INF else Fraction(0)
        starts[False] = min(at, starts[True]) if True in starts else at
    lo, hi = starts.get(False, -INF), starts.get(True, INF)
    new_core = whole.within(lo, hi)
    new_tails = {right: Tail(starts[right], t.period, _pattern(whole, starts[right], t.period, right))
                 for right, t in tails.items()}
    return Region(new_core.parts, new_tails.get(False), new_tails.get(True))


def _xor(a: Region, b: Region) -> Region:
    return Region._combine([a, b], lambda m: m[0] != m[1])


Region.__xor__ = _xor  # type: ignore[attr-defined]
