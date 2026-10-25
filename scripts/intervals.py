"""Exact interval arithmetic on the real line.

Finite endpoints are `Fraction`s; infinite endpoints are `math.inf` / `-math.inf`
and are always open. An `IntervalSet` is a finite union of intervals kept in a
canonical form (sorted, pairwise disjoint, not touching), so two sets are equal
as sets of reals iff they are equal as Python objects.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable

INF = math.inf
Num = Fraction | float  # float only for +-inf


def num(x: int | str | Fraction | float) -> Num:
    """Exact number: ints, strings and Fractions become Fractions, +-inf stays."""
    if isinstance(x, float):
        if math.isinf(x):
            return x
        raise TypeError("finite floats are not exact; use Fraction or str")
    return Fraction(x)


def _fmt(x: Num) -> str:
    if x == INF:
        return "+inf"
    if x == -INF:
        return "-inf"
    return str(x)


@dataclass(frozen=True)
class Interval:
    lo: Num
    hi: Num
    lo_closed: bool
    hi_closed: bool

    def __post_init__(self) -> None:
        if not Interval.valid(self.lo, self.hi, self.lo_closed, self.hi_closed):
            raise ValueError(f"empty or invalid interval {self.lo!r} {self.hi!r}")

    @staticmethod
    def valid(lo: Num, hi: Num, lc: bool, hc: bool) -> bool:
        if (lo == INF) or (hi == -INF):
            return False
        if (lo == -INF and lc) or (hi == INF and hc):
            return False
        return lo < hi or (lo == hi and lc and hc)

    @staticmethod
    def make(lo, hi, lc: bool, hc: bool) -> Interval | None:
        lo, hi = num(lo), num(hi)
        return Interval(lo, hi, lc, hc) if Interval.valid(lo, hi, lc, hc) else None

    @staticmethod
    def closed(a, b) -> Interval:
        return Interval(num(a), num(b), True, True)

    @staticmethod
    def open(a, b) -> Interval:
        return Interval(num(a), num(b), False, False)

    @staticmethod
    def closed_open(a, b) -> Interval:
        return Interval(num(a), num(b), a != -INF, False)

    @staticmethod
    def open_closed(a, b) -> Interval:
        return Interval(num(a), num(b), False, b != INF)

    @staticmethod
    def point(a) -> Interval:
        return Interval(num(a), num(a), True, True)

    @property
    def is_point(self) -> bool:
        return self.lo == self.hi

    @property
    def length(self) -> Num:
        return self.hi - self.lo

    def contains(self, x: Num) -> bool:
        above = self.lo < x or (self.lo_closed and self.lo == x)
        below = x < self.hi or (self.hi_closed and self.hi == x)
        return above and below

    def __add__(self, other: Interval) -> Interval:
        """Minkowski sum {a + b : a in self, b in other}."""
        lo = self.lo + other.lo
        hi = self.hi + other.hi
        lc = self.lo_closed and other.lo_closed and lo != -INF
        hc = self.hi_closed and other.hi_closed and hi != INF
        return Interval(lo, hi, lc, hc)

    def __neg__(self) -> Interval:
        return Interval(-self.hi, -self.lo, self.hi_closed, self.lo_closed)

    def __str__(self) -> str:
        if self.is_point:
            return f"{{{_fmt(self.lo)}}}"
        return f"{'[' if self.lo_closed else '('}{_fmt(self.lo)}, {_fmt(self.hi)}{']' if self.hi_closed else ')'}"


def _lo_key(iv: Interval):
    # closed lower bounds start before open ones at the same value
    return (iv.lo, 0 if iv.lo_closed else 1)


def _hi_key(iv: Interval):
    # open upper bounds end before closed ones at the same value
    return (iv.hi, 1 if iv.hi_closed else 0)


class IntervalSet:
    __slots__ = ("intervals",)

    def __init__(self, intervals: Iterable[Interval] = ()) -> None:
        self.intervals: tuple[Interval, ...] = self._canonical(list(intervals))

    @staticmethod
    def _canonical(ivs: list[Interval]) -> tuple[Interval, ...]:
        ivs.sort(key=_lo_key)
        out: list[Interval] = []
        for iv in ivs:
            if out:
                cur = out[-1]
                touches = iv.lo < cur.hi or (iv.lo == cur.hi and (cur.hi_closed or iv.lo_closed))
                if touches:
                    if _hi_key(iv) > _hi_key(cur):
                        out[-1] = Interval(cur.lo, iv.hi, cur.lo_closed, iv.hi_closed)
                    continue
            out.append(iv)
        return tuple(out)

    # constructors
    @staticmethod
    def of(*ivs: Interval) -> IntervalSet:
        return IntervalSet(ivs)

    @staticmethod
    def universe() -> IntervalSet:
        return IntervalSet([Interval(-INF, INF, False, False)])

    @staticmethod
    def after(x) -> IntervalSet:
        """(x, +inf)"""
        return IntervalSet([Interval(num(x), INF, False, False)])

    @staticmethod
    def up_to(x) -> IntervalSet:
        """(-inf, x]"""
        return IntervalSet([Interval(-INF, num(x), False, True)])

    # predicates
    def is_empty(self) -> bool:
        return not self.intervals

    def __bool__(self) -> bool:
        return bool(self.intervals)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, IntervalSet) and self.intervals == other.intervals

    def __hash__(self) -> int:
        return hash(self.intervals)

    def contains(self, x: Num) -> bool:
        return any(iv.contains(x) for iv in self.intervals)

    def issubset(self, other: IntervalSet) -> bool:
        return (self - other).is_empty()

    # algebra
    def __or__(self, other: IntervalSet) -> IntervalSet:
        return IntervalSet(self.intervals + other.intervals)

    def complement(self) -> IntervalSet:
        out: list[Interval] = []
        lo, lc = -INF, False
        for iv in self.intervals:
            gap = Interval.make(lo, iv.lo, lc, not iv.lo_closed)
            if gap:
                out.append(gap)
            lo, lc = iv.hi, not iv.hi_closed
        gap = Interval.make(lo, INF, lc, False)
        if gap:
            out.append(gap)
        return IntervalSet(out)

    def __and__(self, other: IntervalSet) -> IntervalSet:
        out: list[Interval] = []
        a, b = self.intervals, other.intervals
        i = j = 0
        while i < len(a) and j < len(b):
            x, y = a[i], b[j]
            lo, lc = max((x.lo, not x.lo_closed), (y.lo, not y.lo_closed))
            hi, hc = min((x.hi, x.hi_closed), (y.hi, y.hi_closed))
            iv = Interval.make(lo, hi, not lc, hc)
            if iv:
                out.append(iv)
            if _hi_key(x) <= _hi_key(y):
                i += 1
            else:
                j += 1
        return IntervalSet(out)

    def __sub__(self, other: IntervalSet) -> IntervalSet:
        return self & other.complement()

    def __add__(self, other: IntervalSet) -> IntervalSet:
        """Minkowski sum."""
        return IntervalSet(x + y for x in self.intervals for y in other.intervals)

    def __neg__(self) -> IntervalSet:
        return IntervalSet(-iv for iv in self.intervals)

    def shift(self, d) -> IntervalSet:
        return self + IntervalSet.of(Interval.point(d))

    # measures
    def measure(self) -> Num:
        return sum((iv.length for iv in self.intervals), Fraction(0))

    def points(self) -> int:
        """Number of isolated points (components of measure 0)."""
        return sum(1 for iv in self.intervals if iv.is_point)

    def inf(self) -> Num:
        return self.intervals[0].lo if self.intervals else INF

    def sup(self) -> Num:
        return self.intervals[-1].hi if self.intervals else -INF

    def is_bounded(self) -> bool:
        return not self.intervals or (self.inf() != -INF and self.sup() != INF)

    def take(self, amount: Fraction, from_right: bool = False) -> IntervalSet:
        """A subset of measure min(amount, measure) taken from one end."""
        out: list[Interval] = []
        left = Fraction(amount)
        ivs = reversed(self.intervals) if from_right else self.intervals
        for iv in ivs:
            if left <= 0:
                break
            if iv.is_point:
                continue
            if iv.length <= left:
                out.append(iv)
                left -= iv.length
            elif from_right:
                out.append(Interval(iv.hi - left, iv.hi, True, iv.hi_closed))
                left = Fraction(0)
            else:
                out.append(Interval(iv.lo, iv.lo + left, iv.lo_closed, False))
                left = Fraction(0)
        return IntervalSet(out)

    def __repr__(self) -> str:
        return "IntervalSet(" + " U ".join(map(str, self.intervals)) + ")" if self.intervals else "IntervalSet()"

    def __str__(self) -> str:
        return " U ".join(map(str, self.intervals)) if self.intervals else "{}"


EMPTY = IntervalSet()


def parse_interval(text: str) -> Interval:
    """'[a, b)', '(a, b]', '{a}' or a bare number (a point). 'inf'/'-inf' allowed."""
    t = text.strip()
    if t.startswith("{") and t.endswith("}"):
        return Interval.point(Fraction(t[1:-1].strip()))
    if t[0] in "[(" and t[-1] in "])":
        a, b = (s.strip() for s in t[1:-1].split(","))
        conv = lambda s: -INF if s in ("-inf", "-oo") else INF if s in ("inf", "+inf", "oo") else Fraction(s)
        return Interval(conv(a), conv(b), t[0] == "[", t[-1] == "]")
    return Interval.point(Fraction(t))
