"""Exact subsets of the line that may repeat forever.

A Region is a core plus a periodic tail on each side:

    x < lo:        x ∈ R  iff  (x - lo) mod pl ∈ left      (left  ⊆ [0, pl))
    lo <= x < hi:  x ∈ R  iff  x ∈ core                     (core  ⊆ [lo, hi))
    x >= hi:       x ∈ R  iff  (x - hi) mod pr ∈ right     (right ⊆ [0, pr))

Positions, bounds and periods are positions x + kε (intervals.py); a period has a
positive real part and may have an ε part: B at n + nε for every n >= 0 repeats
every 1+ε. Every finite union of intervals (bounded or not) is a Region whose tails
are constant (empty or full), and the family is closed under ∪, ∩, \\, complement
and translation, except that two tails repeating with periods of different slopes
(k/x) have no common period (`IncompatiblePeriods`); a constant tail goes with any
period. All numbers are exact.
"""

from __future__ import annotations

import math
from fractions import Fraction

from intervals import EPS, INF, Interval, IntervalSet, Num, Pos, num


class IncompatiblePeriods(ValueError):
    pass


def flcm(a: Fraction, b: Fraction) -> Fraction:
    d = a.denominator * b.denominator // math.gcd(a.denominator, b.denominator)
    x, y = int(a * d), int(b * d)
    return Fraction(x * y // math.gcd(x, y), d)


def plcm(p: Pos, q: Pos) -> Pos:
    """The least common multiple of two periods."""
    L = flcm(p.x, q.x)
    n, m = int(L / p.x), int(L / q.x)
    if n * p.k != m * q.k:
        raise IncompatiblePeriods(f"no common period for {p} and {q}")
    return Pos(L, n * p.k)


ZERO = Pos(0)
ONE = Pos(1)


def _window(a, b) -> IntervalSet:
    """[a, b) for positions a, b."""
    return IntervalSet._sorted([Interval._mk(a, b - EPS)]) if a < b else _NONE


_NONE = IntervalSet()


def _constant(pattern: IntervalSet, period: Pos) -> bool:
    return not pattern or pattern == _window(ZERO, period)


def _last(core: IntervalSet, default) -> Pos:
    """A bound h with core ⊆ (-inf, h)."""
    if not core:
        return default
    return core.intervals[-1].hi + EPS


class Region:
    __slots__ = ("lo", "hi", "core", "pl", "left", "pr", "right")

    def __init__(self, lo, hi, core: IntervalSet, pl=ONE, left: IntervalSet = IntervalSet(), pr=ONE, right: IntervalSet = IntervalSet()):
        self.lo, self.hi = num(lo), num(hi)
        self.pl, self.pr = num(pl), num(pr)
        if not (self.lo <= self.hi and self.pl.x > 0 and self.pr.x > 0):
            raise ValueError("bad region bounds")
        self.core = core & _window(self.lo, self.hi)
        self.left = left & _window(ZERO, self.pl)
        self.right = right & _window(ZERO, self.pr)

    # ------------------------------------------------------------ build
    @staticmethod
    def of(s: IntervalSet | Interval) -> Region:
        if isinstance(s, Interval):
            s = IntervalSet.of(s)
        finite = [e for iv in s.intervals for e in (iv.lo, iv.hi) if type(e) is Pos]
        lo = min(finite, default=ZERO)
        hi = max(finite, default=ZERO) + 1
        full = _window(ZERO, ONE)
        left = full if s.inf() == -INF else IntervalSet()
        right = full if s.sup() == INF else IntervalSet()
        return Region(lo, hi, s, ONE, left, ONE, right)

    @staticmethod
    def empty() -> Region:
        return Region(ZERO, ZERO, IntervalSet())

    @staticmethod
    def universe() -> Region:
        return Region.of(IntervalSet.universe())

    @staticmethod
    def point(x) -> Region:
        return Region.of(Interval.point(x))

    # ------------------------------------------------------------ read
    def contains(self, x) -> bool:
        if x < self.lo:
            return self.left.contains((x - self.lo) % self.pl) if self.left else False
        if x >= self.hi:
            return self.right.contains((x - self.hi) % self.pr) if self.right else False
        return self.core.contains(x)

    def on(self, a, b) -> IntervalSet:
        """The part of the region in [a, b) (a, b finite), as a finite union of intervals."""
        a, b = num(a), num(b)
        parts = [self.core & _window(a, b)]
        if b > self.hi and self.right:
            start = a if a > self.hi else self.hi
            k = (start - self.hi) // self.pr
            while self.hi + k * self.pr < b:
                parts.append(self.right.shift(self.hi + k * self.pr) & _window(a, b))
                k += 1
        if a < self.lo and self.left:
            end = b if b < self.lo else self.lo
            k = (self.lo - end) // self.pl
            while self.lo - k * self.pl > a:
                base = self.lo - (k + 1) * self.pl
                parts.append(self.left.shift(base) & _window(a, b))
                k += 1
        if len(parts) == 1:
            return parts[0]
        return IntervalSet([iv for p in parts for iv in p.intervals])

    def is_empty(self) -> bool:
        return not self.core and not self.left and not self.right

    def __bool__(self) -> bool:
        return not self.is_empty()

    def is_bounded(self) -> bool:
        return not self.left and not self.right

    def bounded(self) -> IntervalSet:
        if not self.is_bounded():
            raise ValueError("unbounded region")
        return self.core

    def measure_on(self, window: IntervalSet) -> Fraction:
        a, b = window.inf(), window.sup()
        return (self.on(a, b + 1) & window).measure()

    # ------------------------------------------------------------ algebra
    def _combine(self, other: Region, op) -> Region:
        lo = self.lo if self.lo <= other.lo else other.lo
        hi = self.hi if self.hi >= other.hi else other.hi
        pl = _period(self.left, self.pl, other.left, other.pl)
        pr = _period(self.right, self.pr, other.right, other.pr)
        core = op(self.on(lo, hi), other.on(lo, hi))
        right = op(self.on(hi, hi + pr), other.on(hi, hi + pr)).shift(-hi)
        left = op(self.on(lo - pl, lo), other.on(lo - pl, lo)).shift(-(lo - pl))
        return Region(lo, hi, core, pl, left, pr, right)._compact()

    def __or__(self, other: Region) -> Region:
        if other.is_empty():
            return self
        if self.is_empty():
            return other
        return self._combine(other, lambda a, b: a | b)

    def __and__(self, other: Region) -> Region:
        if self.is_empty():
            return self
        if other.is_empty():
            return other
        return self._combine(other, lambda a, b: a & b)

    def __sub__(self, other: Region) -> Region:
        if self.is_empty() or other.is_empty():
            return self
        return self._combine(other, lambda a, b: a - b)

    def complement(self) -> Region:
        return Region(self.lo, self.hi, _window(self.lo, self.hi) - self.core,
                      self.pl, _window(ZERO, self.pl) - self.left, self.pr, _window(ZERO, self.pr) - self.right)

    def shift(self, d) -> Region:
        d = num(d)
        return Region(self.lo + d, self.hi + d, self.core.shift(d), self.pl, self.left, self.pr, self.right)

    def inf(self) -> Num:
        if self.left:
            return -INF
        if self.core:
            return self.core.inf()
        return self.hi + self.right.inf() if self.right else INF

    def sup(self) -> Num:
        if self.right:
            return INF
        if self.core:
            return self.core.sup()
        return self.lo - self.pl + self.left.sup() if self.left else -INF

    def left_edges(self) -> Region:
        """The left edge of each component (largest interval) of the region, as points;
        a component unbounded on the left has none. Being a left edge is a local
        property, so the tails of the result repeat with the same periods."""
        a, b = self.lo - 2 * self.pl, self.hi + 2 * self.pr
        starts = [iv.lo for iv in self.on(a, b).intervals if iv.lo > a]
        pts = lambda lo, hi, d: IntervalSet(Interval.point(x + d) for x in starts if lo <= x < hi)
        hi = self.hi + self.pr
        return Region(self.lo, hi, pts(self.lo, hi, ZERO), self.pl, pts(self.lo - self.pl, self.lo, self.pl - self.lo),
                      self.pr, pts(hi, hi + self.pr, -hi))._compact()

    def dilate(self, iv: Interval) -> Region:
        """{x + t : x in the region, t in iv}."""
        if not self:
            return self
        if iv.lo == -INF or iv.hi == INF:
            lo, hi = -INF, INF
            if iv.hi == INF and iv.lo != -INF and self.inf() != -INF:
                lo = self.inf() + iv.lo
            if iv.lo == -INF and iv.hi != INF and self.sup() != INF:
                hi = self.sup() + iv.hi
            return Region.of(Interval._mk(lo, hi))
        a, b = iv.lo, iv.hi
        s = self.on(self.lo + a - b - self.pl - 1, self.hi + b - a + self.pr + 1)
        d = IntervalSet(x + iv for x in s.intervals)
        lo, hi = self.lo + a, self.hi + b
        return Region(lo, hi, d, self.pl, d.shift(self.pl - lo), self.pr, d.shift(-hi))._compact()

    def components(self) -> list[Region]:
        """The components (largest intervals) of the region; a tail that repeats a
        pattern (infinitely many components) counts as one."""
        out, rest = [], self
        if self.left and not _constant(self.left, self.pl):
            t = self & Region.of(Interval(-INF, self.lo, False, False))
            out.append(t)
            rest = rest - t
        if self.right and not _constant(self.right, self.pr):
            t = self & Region.of(Interval(self.hi, INF, True, False))
            out.append(t)
            rest = rest - t
        s = rest.core
        if rest.left:
            s = s | IntervalSet.of(Interval(-INF, rest.lo, False, False))
        if rest.right:
            s = s | IntervalSet.of(Interval(rest.hi, INF, True, False))
        return [Region.of(iv) for iv in s.intervals] + out

    def components_meeting(self, other: Region) -> Num:
        """How many components of the region meet `other` (INF if infinitely many)."""
        x = self & other
        if not x:
            return 0
        if (x.left and not _constant(self.left, self.pl)) or (x.right and not _constant(self.right, self.pr)):
            return INF
        m = max(self.pl.x, self.pr.x, x.pl.x, x.pr.x) + 1
        lo = self.lo if self.lo <= x.lo else x.lo
        hi = self.hi if self.hi >= x.hi else x.hi
        a, b = lo - m, hi + m
        xs = x.on(a, b).intervals
        n, j = 0, 0
        for iv in self.on(a, b).intervals:  # both sorted: one pass
            while j < len(xs) and xs[j].hi < iv.lo:
                j += 1
            if j < len(xs) and xs[j].lo <= iv.hi:
                n += 1
        return n

    def issubset(self, other: Region) -> bool:
        return (self - other).is_empty()

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Region):
            return NotImplemented
        try:
            return (self - other).is_empty() and (other - self).is_empty()
        except IncompatiblePeriods:
            return False

    def __hash__(self) -> int:
        return 0

    def _compact(self) -> Region:
        """Shrink the core where it is empty next to an empty tail (keeps sizes small)."""
        lo, hi = self.lo, self.hi
        if not self.right:
            h = _last(self.core, self.lo)
            hi = h if h > lo else lo
        if not self.left:
            lo = (self.core.inf() if self.core.inf() < hi else hi) if self.core else hi
        if lo == self.lo and hi == self.hi:
            return self
        return Region(lo, hi, self.core, self.pl, self.left, self.pr, self.right)

    # ------------------------------------------------------------ show
    def __str__(self) -> str:
        shown = self.core
        tails = []
        if self.left == _window(ZERO, self.pl):
            shown = shown | IntervalSet.of(Interval(-INF, self.lo, False, False))
        elif self.left:
            tails.append(f"... {self.left.shift(self.lo - self.pl)} repeating every {self.pl} to the left")
        if self.right == _window(ZERO, self.pr):
            shown = shown | IntervalSet.of(Interval(self.hi, INF, True, False))
        elif self.right:
            tails.append(f"{self.right.shift(self.hi)} repeating every {self.pr} to the right ...")
        parts = [t for t in tails if t.startswith("...")] + ([str(shown)] if shown else []) + [t for t in tails if not t.startswith("...")]
        return " U ".join(parts) if parts else "{}"

    __repr__ = __str__


def _period(pa: IntervalSet, a: Pos, pb: IntervalSet, b: Pos) -> Pos:
    """A period for combining two tails: a constant tail goes with any period."""
    ca, cb = _constant(pa, a), _constant(pb, b)
    if ca and cb:
        return ONE
    if ca:
        return b
    if cb:
        return a
    return a if a == b else plcm(a, b)
