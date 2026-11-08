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

import bisect
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


def _group(s: IntervalSet) -> str:
    """What repeats, as text: in parentheses if it is several intervals (what repeats is
    then told from what is before it)."""
    return f"({s})" if len(s.intervals) > 1 else str(s)


def _hi(iv):
    return iv.hi


def _clip(s: IntervalSet, a, b) -> IntervalSet:
    """s on [a, b): the intervals that can meet it found by bisection (the core of a region
    two patterns share can hold thousands), then cut as `s & [a, b)` would."""
    ivs = s.intervals
    if len(ivs) < 32:
        return s & _window(a, b)
    i = bisect.bisect_left(ivs, a, key=_hi)
    j = i
    while j < len(ivs) and ivs[j].lo < b:
        j += 1
    if i >= j:
        return _NONE
    return IntervalSet._sorted(list(ivs[i:j])) & _window(a, b)


def _factors(n: int) -> list:
    """The prime factors of n > 0, with their multiplicity."""
    out, d = [], 2
    while d * d <= n and d < 100000:
        while n % d == 0:
            out.append(d)
            n //= d
        d += 1
    if n > 1:
        out.append(n)
    return out


def _probably_repeats(pattern: IntervalSet, q: Pos, period: Pos) -> bool:
    """A cheap test before the whole one: a few pieces, each found again q further (within the
    period), and as many pieces as p copies can make."""
    ivs = pattern.intervals
    p = int(period.x / q.x)
    if len(ivs) % p not in (0, 1, p - 1) and p > 2:
        return False
    for iv in ivs[:3] + ivs[len(ivs) // 2:len(ivs) // 2 + 2]:
        lo, hi = iv.lo + q, iv.hi + q
        if hi >= period:
            lo, hi = lo - period, hi - period
            if lo < ZERO:
                continue
        got = _clip(pattern, lo, hi + EPS).intervals
        if not got or got[0].lo > lo or got[0].hi < hi:
            return False
    return True


LONG_PERIOD = 86400  # periods up to it are kept as they are (`_shortest`)


def _shortest(pattern: IntervalSet, period: Pos) -> tuple:
    """(the pattern, its period) with the shortest period it repeats with, among the
    period's divisions by a prime factor of its real part's numerator (and of its ε part):
    two regions repeating every 1220 and every 86400 combine every 61 days, and what is left
    of them after an operation may repeat every day again. Only a period longer than
    LONG_PERIOD: a short one shortened folds a piece that moves with the chip into the repeating
    part, and the mode rules' formula of a run no longer follows it (the 20s example's crossing
    solved at each frame). A constant pattern is left as it is."""
    if _constant(pattern, period) or period.x <= LONG_PERIOD:
        return pattern, period
    changed = True
    while changed:
        changed = False
        for p in sorted(set(_factors(period.x.numerator))):
            if period.k % p:
                continue
            q = Pos(period.x / p, period.k // p)
            if not _probably_repeats(pattern, q, period):
                continue
            part = pattern & _window(ZERO, q)
            again = IntervalSet([iv for i in range(p) for iv in part.shift(q * i).intervals])
            if again == pattern:
                pattern, period, changed = part, q, True
                break
    return pattern, period


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
        return _EMPTY

    @staticmethod
    def universe() -> Region:
        return _UNIVERSE

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
        parts = [_clip(self.core, a, b)]
        if b > self.hi and self.right:
            start = a if a > self.hi else self.hi
            if _constant(self.right, self.pr):  # all of [hi, inf): one interval, not one per period
                parts.append(_window(start, b))
            else:
                k = (start - self.hi) // self.pr
                while self.hi + k * self.pr < b:
                    base = self.hi + k * self.pr  # (the window cut out of the pattern first, then shifted)
                    parts.append(_clip(self.right, a - base, b - base).shift(base))
                    k += 1
        if a < self.lo and self.left:
            end = b if b < self.lo else self.lo
            if _constant(self.left, self.pl):  # all of (-inf, lo)
                parts.append(_window(a, end))
            else:
                k = (self.lo - end) // self.pl
                while self.lo - k * self.pl > a:
                    base = self.lo - (k + 1) * self.pl
                    parts.append(_clip(self.left, a - base, b - base).shift(base))
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
    def _combine(self, other: Region, op, name: str | None = None, lazy: bool = True) -> Region:
        if lazy and name is not None:
            if isinstance(self, Lazy) or isinstance(other, Lazy) or _long_together(self, other):
                return Lazy(name, self, other)
        if not (self.left or self.right or other.left or other.right):  # both bounded: the cores only
            core = op(self.core, other.core)
            if not core:
                return _EMPTY
            r = object.__new__(Region)  # already compact: from the first to the last position of the core
            r.lo, r.hi, r.core = core.intervals[0].lo, core.intervals[-1].hi + EPS, core
            r.pl, r.pr, r.left, r.right = ONE, ONE, _NONE, _NONE
            return r
        lo = self.lo if self.lo <= other.lo else other.lo
        hi = self.hi if self.hi >= other.hi else other.hi
        pl = _period(self.left, self.pl, other.left, other.pl)
        pr = _period(self.right, self.pr, other.right, other.pr)
        core = op(self.on(lo, hi), other.on(lo, hi))
        right = op(self.on(hi, hi + pr), other.on(hi, hi + pr)).shift(-hi)
        left = op(self.on(lo - pl, lo), other.on(lo - pl, lo)).shift(-(lo - pl))
        return Region(lo, hi, core, pl, left, pr, right)._compact()

    def __or__(self, other: Region) -> Region:
        if isinstance(other, Lazy):
            return self._combine(other, _OPS["or"], "or")
        if other.is_empty():
            return self
        if self.is_empty():
            return other
        return self._combine(other, _OPS["or"], "or")

    def __and__(self, other: Region) -> Region:
        if self.is_empty():
            return self
        if isinstance(other, Lazy):
            return self._combine(other, _OPS["and"], "and")
        if other.is_empty():
            return other
        return self._combine(other, _OPS["and"], "and")

    def __sub__(self, other: Region) -> Region:
        if self.is_empty():
            return self
        if isinstance(other, Lazy):
            return self._combine(other, _OPS["sub"], "sub")
        if other.is_empty():
            return self
        return self._combine(other, _OPS["sub"], "sub")

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

    def is_everything(self) -> bool:
        """Every position of the line."""
        return self.left == _window(ZERO, self.pl) and self.right == _window(ZERO, self.pr) and self.core == _window(self.lo, self.hi)

    def _compact(self) -> Region:
        """Shrink the core where it is empty next to an empty tail, and where its ends
        repeat the tails (keeps sizes small); each tail with the shortest period it repeats
        with (`_shortest`)."""
        if getattr(self, "src", None) is not None:  # (a region built by a call keeps it: ratio_plan.Built)
            right, pr, left, pl = self.right, self.pr, self.left, self.pl
        else:
            right, pr = _shortest(self.right, self.pr)
            left, pl = _shortest(self.left, self.pl)
        if pr != self.pr or pl != self.pl:
            return Region(self.lo, self.hi, self.core, pl, left, pr, right)._compact()
        lo, hi = self.lo, self.hi
        if not self.right:
            h = _last(self.core, self.lo)
            hi = h if h > lo else lo
        else:
            if self.core and self.right == _window(ZERO, self.pr) and self.core.intervals[-1].hi + EPS >= hi:
                # a full tail after a full end of the core: at once, however long it is
                hi = hi - ((hi - max(self.core.intervals[-1].lo, lo)) // self.pr) * self.pr
            while hi - self.pr >= lo and (self.core & _window(hi - self.pr, hi)).shift(self.pr - hi) == self.right:
                hi = hi - self.pr
        if not self.left:
            lo = (self.core.inf() if self.core.inf() < hi else hi) if self.core else hi
        else:
            if self.core and self.left == _window(ZERO, self.pl) and self.core.intervals[0].lo <= lo:
                lo = lo + ((min(self.core.intervals[0].hi + EPS, hi) - lo) // self.pl) * self.pl
            while lo + self.pl <= hi and (self.core & _window(lo, lo + self.pl)).shift(-lo) == self.left:
                lo = lo + self.pl
        if lo == self.lo and hi == self.hi:
            return self
        core = self.core
        if self.right and hi != self.hi or self.left and lo != self.lo:
            core = core & _window(lo, hi)  # what is folded into a tail
        return Region(lo, hi, core, self.pl, self.left, self.pr, self.right)

    # ------------------------------------------------------------ show
    def __str__(self) -> str:
        shown = self.core
        tails = []
        if self.left == _window(ZERO, self.pl):
            shown = shown | IntervalSet.of(Interval(-INF, self.lo, False, False))
        elif self.left:
            tails.append(f"... {_group(self.left.shift(self.lo - self.pl))} repeating every {self.pl} to the left")
        if self.right == _window(ZERO, self.pr):
            shown = shown | IntervalSet.of(Interval(self.hi, INF, True, False))
        elif self.right:
            tails.append(f"{_group(self.right.shift(self.hi))} repeating every {self.pr} to the right ...")
        parts = [t for t in tails if t.startswith("...")] + ([str(shown)] if shown else []) + [t for t in tails if not t.startswith("...")]
        return " U ".join(parts) if parts else "{}"

    __repr__ = __str__



# regions are never modified once built: these are shared
_EMPTY = Region(ZERO, ZERO, IntervalSet())
_UNIVERSE = Region.of(IntervalSet.universe())

_OPS = {"or": lambda a, b: a | b, "and": lambda a, b: a & b, "sub": lambda a, b: a - b}
LAZY_PERIOD = 7 * 86400  # two tails that repeat together only after more than this: a lazy region
LAZY_DEPTH = 24  # a lazy region built on this many others is computed (its reads would chain too far)


def _long_together(a: Region, b: Region) -> bool:
    """Whether combining a and b would give a tail repeating only after LAZY_PERIOD (the 20s
    breaks every 1220 and the night every 86400: 61 days)."""
    for pa, ka, pb, kb in ((a.right, a.pr, b.right, b.pr), (a.left, a.pl, b.left, b.pl)):
        if pa and pb and ka != kb and not _constant(pa, ka) and not _constant(pb, kb):
            try:
                if plcm(ka, kb).x > LAZY_PERIOD:
                    return True
            except IncompatiblePeriods:
                return False
    return False


class Lazy(Region):
    """A region kept as the operation that makes it, computed only where it is read.

    Two regions whose tails repeat together only after a long period (a night every 86400 and
    20s breaks every 1220: 61 days, thousands of pieces) are combined lazily: reading a window
    (`on`) or a position (`contains`) reads the operands there, and further unions,
    intersections, differences, complements and shifts stay lazy. What needs the whole of it
    (its tails and bounds, emptiness, equality, its text) computes it once, exactly as a Region
    is computed, and keeps it. So nothing it answers differs from the Region it stands for:
    only when the work is done does."""

    __slots__ = ("_op", "_a", "_b", "_d", "_full", "_depth")

    def __init__(self, op: str, a, b=None, d=None) -> None:
        self._op, self._a, self._b, self._d = op, a, b, d
        self._full = None
        self._depth = 1 + max(a._depth if isinstance(a, Lazy) else 0, b._depth if isinstance(b, Lazy) else 0)
        if self._depth > LAZY_DEPTH:
            self.full()

    def full(self) -> Region:
        """The region itself, computed once."""
        if self._full is None:
            op, a = self._op, _eager(self._a)
            if op in _OPS:
                b = _eager(self._b)
                if op == "or" and (a.is_empty() or b.is_empty()):
                    out = b if a.is_empty() else a
                elif op != "or" and (a.is_empty() or b.is_empty()):
                    out = a if op == "sub" else _EMPTY
                else:  # (the plain operation of Region: a subclass's would make it lazy again)
                    out = Region._combine(a, b, _OPS[op], op, lazy=False)
            elif op == "comp":
                out = a.complement()
            elif op == "shift":
                out = a.shift(self._d)
            elif op == "dilate":
                out = a.dilate(self._d)
            elif op == "edges":
                out = a.left_edges()
            else:
                raise ValueError(op)
            self._full = out
            self._a = self._b = None
        return self._full

    def __getattr__(self, name):
        if name in ("lo", "hi", "core", "pl", "left", "pr", "right"):
            return getattr(self.full(), name)
        raise AttributeError(name)

    def on(self, a, b) -> IntervalSet:
        if self._full is not None:
            return self._full.on(a, b)
        a, b = num(a), num(b)
        op = self._op
        if op == "shift":
            return self._a.on(a - self._d, b - self._d).shift(self._d)
        if op == "dilate":  # (what reaches [a, b) comes from [a - hi - 1, b - lo + 1))
            iv = self._d
            got = self._a.on(a - iv.hi - 1, b - iv.lo + 1)
            return IntervalSet(x + iv for x in got.intervals) & _window(a, b)
        if op == "edges":  # (a start in [a, b) is the start of an interval read from a - 1)
            got = self._a.on(a - 1, b)
            return IntervalSet(Interval.point(iv.lo) for iv in got.intervals if a <= iv.lo and iv.lo != -INF)
        x = self._a.on(a, b)
        if op == "comp":
            return _window(a, b) - x
        return _OPS[op](x, self._b.on(a, b))

    def contains(self, x) -> bool:
        if self._full is not None:
            return self._full.contains(x)
        op = self._op
        if op == "shift":
            return self._a.contains(x - self._d)
        if op in ("dilate", "edges"):
            x = num(x)
            return any(iv.lo <= x <= iv.hi for iv in self.on(x, x + EPS).intervals)
        if op == "comp":
            return not self._a.contains(x)
        ia, ib = self._a.contains(x), self._b.contains(x)
        return ia or ib if op == "or" else ia and ib if op == "and" else ia and not ib

    def __or__(self, other: Region) -> Region:
        return Lazy("or", self, other)

    def __and__(self, other: Region) -> Region:
        return Lazy("and", self, other)

    def __sub__(self, other: Region) -> Region:
        return Lazy("sub", self, other)

    def complement(self) -> Region:
        return Lazy("comp", self)

    def shift(self, d) -> Region:
        return Lazy("shift", self, None, num(d))

    def dilate(self, iv: Interval) -> Region:
        if self._full is not None or iv.lo == -INF or iv.hi == INF:
            return self.full().dilate(iv)
        return Lazy("dilate", self, None, iv)

    def left_edges(self) -> Region:
        if self._full is not None:
            return self._full.left_edges()
        return Lazy("edges", self)

    PROBE = 3 * 86400  # how wide each window read to find a piece is (`is_empty`)

    def is_empty(self) -> bool:
        """Empty or not, exactly: a piece found in a window near where the operands start says
        no at once; only when none is found is the whole of it computed."""
        if self._full is not None:
            return self._full.is_empty()
        for h in self._hints():
            if self.on(h - self.PROBE, h + self.PROBE).intervals:
                return False
        return self.full().is_empty()

    def _hints(self) -> list:
        """Positions near which the operands start: where to look for a piece first."""
        out, todo = [], [self]
        while todo and len(out) < 6:
            r = todo.pop()
            if isinstance(r, Lazy) and r._full is None:
                todo += [x for x in (r._a, r._b) if isinstance(x, Region)]
            else:
                r = _eager(r)
                if not r.is_empty():
                    out.append(r.lo if r.lo != -INF else r.hi)
        return out or [ZERO]

    def __bool__(self) -> bool:
        return not self.is_empty()

    def __eq__(self, other) -> bool:
        return self.full() == _eager(other)

    def __hash__(self) -> int:
        return hash(self.full())

    def __str__(self) -> str:
        return str(self.full())

    def __reduce__(self):
        return (_same_region, (self.full(),))


def _same_region(r: Region) -> Region:
    return r


def _eager(r):
    return r.full() if isinstance(r, Lazy) else r


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
