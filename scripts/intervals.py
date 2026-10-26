"""Exact interval arithmetic on the line of positions x + kε.

A position is x + kε: x an exact rational, k an integer, ε a positive infinitesimal.
Positions are compared by x, then by k, so 1 < 1+ε < 1+2ε < 1.0001: x + ε is the
position right after x, and x - ε the one right before. A real number x is the
position x + 0ε. Lengths (measures) are real: ε has no length.

Intervals are closed in this order: an open end of a real interval is the next
position inside, (a, b] = [a+ε, b]. Infinite ends (math.inf) are always open. An
`IntervalSet` is a finite union of intervals kept in a canonical form (sorted,
disjoint, not touching), so two sets are equal iff they are equal as Python objects.
"""

from __future__ import annotations

import math
from fractions import Fraction
from typing import Iterable

INF = math.inf


class Pos:
    """The position x + kε."""

    __slots__ = ("x", "k")

    def __init__(self, x, k: int = 0) -> None:
        if isinstance(x, Pos):
            x, k = x.x, x.k + k
        self.x = x if type(x) is Fraction else Fraction(x)
        self.k = k

    @staticmethod
    def _new(x: Fraction, k: int) -> Pos:
        p = object.__new__(Pos)
        p.x, p.k = x, k
        return p

    # arithmetic: with positions, rationals, and +-inf (which absorbs)
    def __add__(self, o):
        t = type(o)
        if t is Pos:
            return Pos._new(self.x + o.x, self.k + o.k)
        if t is float:
            return o
        return Pos._new(self.x + o, self.k)

    __radd__ = __add__

    def __sub__(self, o):
        t = type(o)
        if t is Pos:
            return Pos._new(self.x - o.x, self.k - o.k)
        if t is float:
            return -o
        return Pos._new(self.x - o, self.k)

    def __rsub__(self, o):
        if type(o) is float:
            return o
        return Pos._new(o - self.x, -self.k)

    def __neg__(self) -> Pos:
        return Pos._new(-self.x, -self.k)

    def __mul__(self, n: int) -> Pos:
        if type(n) is not int:
            raise TypeError("a position is multiplied by an integer only")
        return Pos._new(self.x * n, self.k * n)

    __rmul__ = __mul__

    def __floordiv__(self, p) -> int:
        """The largest integer n with n * p <= self (p > 0)."""
        p = p if type(p) is Pos else Pos(p)
        n = math.floor(self.x / p.x)
        if n * p.x == self.x and n * p.k > self.k:
            n -= 1
        return n

    def __mod__(self, p) -> Pos:
        return self - (self // p) * p

    def __abs__(self) -> Pos:
        return self if self >= 0 else -self

    def __bool__(self) -> bool:
        return self.x != 0 or self.k != 0

    # order: (x, k) lexicographic; rationals compared by cross-multiplying (fast)
    def _cmp(self, o):
        """(left, right, k of o) with x < o.x iff left < right; None for +-inf."""
        t = type(o)
        a = self.x
        if t is Pos:
            b = o.x
            return a._numerator * b._denominator, b._numerator * a._denominator, o.k
        if t is int:
            return a._numerator, o * a._denominator, 0
        if t is float:
            return None
        b = o if t is Fraction else Fraction(o)
        return a._numerator * b._denominator, b._numerator * a._denominator, 0

    def __lt__(self, o) -> bool:
        c = self._cmp(o)
        if c is None:
            return o > 0
        return c[0] < c[1] or (c[0] == c[1] and self.k < c[2])

    def __le__(self, o) -> bool:
        c = self._cmp(o)
        if c is None:
            return o > 0
        return c[0] < c[1] or (c[0] == c[1] and self.k <= c[2])

    def __gt__(self, o) -> bool:
        c = self._cmp(o)
        if c is None:
            return o < 0
        return c[0] > c[1] or (c[0] == c[1] and self.k > c[2])

    def __ge__(self, o) -> bool:
        c = self._cmp(o)
        if c is None:
            return o < 0
        return c[0] > c[1] or (c[0] == c[1] and self.k >= c[2])

    def __eq__(self, o) -> bool:
        t = type(o)
        if t is Pos:
            return self.k == o.k and self.x == o.x
        if t is float:
            return False
        try:
            return self.k == 0 and self.x == o
        except TypeError:
            return NotImplemented

    def __ne__(self, o) -> bool:
        r = self.__eq__(o)
        return r if r is NotImplemented else not r

    def __hash__(self) -> int:
        return hash(self.x) if self.k == 0 else hash((self.x, self.k))

    def __str__(self) -> str:
        if self.k == 0:
            return _fmt(self.x)
        e = "ε" if abs(self.k) == 1 else f"{abs(self.k)}ε"
        if self.x == 0:
            return e if self.k > 0 else "-" + e
        return _fmt(self.x) + ("+" if self.k > 0 else "-") + e

    __repr__ = __str__


Num = Pos | float  # float only for +-inf
EPS = Pos(0, 1)


def num(x) -> Num:
    """A position: ints, strings, Fractions and positions become Pos, +-inf stays."""
    if type(x) is Pos:
        return x
    if isinstance(x, float):
        if math.isinf(x):
            return x
        raise TypeError("finite floats are not exact; use Fraction or str")
    return Pos(x)


def _fmt(x) -> str:
    if x == INF:
        return "+inf"
    if x == -INF:
        return "-inf"
    if type(x) is Pos:
        return str(x)
    x = Fraction(x)
    return str(x.numerator) if x.denominator == 1 else str(x)


def _valid(lo, hi) -> bool:
    if type(lo) is float:
        return lo < 0 and (type(hi) is not float or hi > 0)
    if type(hi) is float:
        return hi > 0
    return lo <= hi


class Interval:
    """[lo, hi] in the order of positions; an infinite end is open."""

    __slots__ = ("lo", "hi")

    def __init__(self, lo, hi, lo_closed: bool = True, hi_closed: bool = True) -> None:
        lo, hi = num(lo), num(hi)
        if (type(lo) is float and lo_closed) or (type(hi) is float and hi_closed):
            raise ValueError("an infinite end is open")
        if type(lo) is Pos and not lo_closed:
            lo = lo + EPS
        if type(hi) is Pos and not hi_closed:
            hi = hi - EPS
        if not _valid(lo, hi):
            raise ValueError(f"empty or invalid interval {lo} {hi}")
        self.lo, self.hi = lo, hi

    @staticmethod
    def _mk(lo, hi) -> Interval:
        iv = object.__new__(Interval)
        iv.lo, iv.hi = lo, hi
        return iv

    @staticmethod
    def make(lo, hi, lc: bool = True, hc: bool = True) -> Interval | None:
        try:
            return Interval(lo, hi, lc, hc)
        except ValueError:
            return None

    @staticmethod
    def closed(a, b) -> Interval:
        return Interval(a, b, True, True)

    @staticmethod
    def open(a, b) -> Interval:
        return Interval(a, b, False, False)

    @staticmethod
    def closed_open(a, b) -> Interval:
        return Interval(a, b, a != -INF, False)

    @staticmethod
    def open_closed(a, b) -> Interval:
        return Interval(a, b, False, b != INF)

    @staticmethod
    def point(a) -> Interval:
        a = num(a)
        return Interval._mk(a, a)

    @property
    def lo_closed(self) -> bool:
        return type(self.lo) is Pos

    @property
    def hi_closed(self) -> bool:
        return type(self.hi) is Pos

    @property
    def is_point(self) -> bool:
        return self.lo == self.hi

    @property
    def length(self):
        """The real length (ε has none)."""
        if type(self.lo) is float or type(self.hi) is float:
            return INF
        return self.hi.x - self.lo.x

    def contains(self, x) -> bool:
        return self.lo <= x <= self.hi

    def __add__(self, other: Interval) -> Interval:
        """Minkowski sum {a + b : a in self, b in other}."""
        return Interval._mk(self.lo + other.lo, self.hi + other.hi)

    def __neg__(self) -> Interval:
        return Interval._mk(-self.hi, -self.lo)

    def __eq__(self, other) -> bool:
        return isinstance(other, Interval) and self.lo == other.lo and self.hi == other.hi

    def __hash__(self) -> int:
        return hash((self.lo, self.hi))

    def __str__(self) -> str:
        if self.is_point:
            return f"{{{_fmt(self.lo)}}}"
        lo, hi = self.lo, self.hi
        if type(lo) is float:
            a = "(-inf"
        elif lo.k == 1:
            a = "(" + _fmt(lo.x)
        else:
            a = "[" + _fmt(lo)
        if type(hi) is float:
            b = "+inf)"
        elif hi.k == -1:
            b = _fmt(hi.x) + ")"
        else:
            b = _fmt(hi) + "]"
        return f"{a}, {b}"

    __repr__ = __str__


class IntervalSet:
    __slots__ = ("intervals",)

    def __init__(self, intervals: Iterable[Interval] = ()) -> None:
        self.intervals: tuple[Interval, ...] = self._canonical(list(intervals))

    @staticmethod
    def _canonical(ivs: list[Interval]) -> tuple[Interval, ...]:
        ivs.sort(key=lambda iv: iv.lo)
        out: list[Interval] = []
        for iv in ivs:
            if out:
                cur = out[-1]
                if iv.lo <= cur.hi + EPS:  # overlapping or touching
                    if iv.hi > cur.hi:
                        out[-1] = Interval._mk(cur.lo, iv.hi)
                    continue
            out.append(iv)
        return tuple(out)

    @staticmethod
    def _sorted(ivs: list[Interval]) -> IntervalSet:
        s = object.__new__(IntervalSet)
        s.intervals = tuple(ivs)
        return s

    # constructors
    @staticmethod
    def of(*ivs: Interval) -> IntervalSet:
        return IntervalSet(ivs)

    @staticmethod
    def universe() -> IntervalSet:
        return IntervalSet([Interval._mk(-INF, INF)])

    @staticmethod
    def after(x) -> IntervalSet:
        """(x, +inf)"""
        return IntervalSet([Interval(x, INF, False, False)])

    @staticmethod
    def up_to(x) -> IntervalSet:
        """(-inf, x]"""
        return IntervalSet([Interval(-INF, x, False, True)])

    # predicates
    def is_empty(self) -> bool:
        return not self.intervals

    def __bool__(self) -> bool:
        return bool(self.intervals)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, IntervalSet) and self.intervals == other.intervals

    def __hash__(self) -> int:
        return hash(self.intervals)

    def contains(self, x) -> bool:
        return any(iv.lo <= x <= iv.hi for iv in self.intervals)

    def issubset(self, other: IntervalSet) -> bool:
        return (self - other).is_empty()

    # algebra
    def __or__(self, other: IntervalSet) -> IntervalSet:
        if not other.intervals:
            return self
        if not self.intervals:
            return other
        return IntervalSet(self.intervals + other.intervals)

    def complement(self) -> IntervalSet:
        out: list[Interval] = []
        lo = -INF
        for iv in self.intervals:
            hi = iv.lo - EPS
            if _valid(lo, hi):
                out.append(Interval._mk(lo, hi))
            lo = iv.hi + EPS
        if _valid(lo, INF):
            out.append(Interval._mk(lo, INF))
        return IntervalSet._sorted(out)

    def __and__(self, other: IntervalSet) -> IntervalSet:
        out: list[Interval] = []
        a, b = self.intervals, other.intervals
        i = j = 0
        while i < len(a) and j < len(b):
            x, y = a[i], b[j]
            lo = x.lo if x.lo >= y.lo else y.lo
            hi = x.hi if x.hi <= y.hi else y.hi
            if _valid(lo, hi):
                out.append(Interval._mk(lo, hi))
            if x.hi <= y.hi:
                i += 1
            else:
                j += 1
        return IntervalSet._sorted(out)

    def __sub__(self, other: IntervalSet) -> IntervalSet:
        if not other.intervals or not self.intervals:
            return self
        return self & other.complement()

    def __add__(self, other: IntervalSet) -> IntervalSet:
        """Minkowski sum."""
        return IntervalSet(x + y for x in self.intervals for y in other.intervals)

    def __neg__(self) -> IntervalSet:
        return IntervalSet(-iv for iv in self.intervals)

    def shift(self, d) -> IntervalSet:
        d = num(d)
        return IntervalSet._sorted([Interval._mk(iv.lo + d, iv.hi + d) for iv in self.intervals])

    # measures
    def measure(self):
        return sum((iv.length for iv in self.intervals), Fraction(0))

    def points(self) -> int:
        """Number of components of length 0."""
        return sum(1 for iv in self.intervals if iv.length == 0)

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
            if iv.length == 0:
                continue
            if iv.length <= left:
                out.append(iv)
                left -= iv.length
            elif from_right:
                out.append(Interval(iv.hi.x - left, iv.hi))
                left = Fraction(0)
            else:
                out.append(Interval(iv.lo, iv.lo.x + left, True, False))
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
