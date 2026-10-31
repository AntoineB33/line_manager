"""What changed between two states, to send it to the process of the edits (edit_worker.py)
instead of the whole state: the cost of an edit must not depend on what is on the line.

`changes(old, new)` gives the stretch of the line where they differ, what `new` has there
and the edits that differ. `patched(old, change)` gives `new` from it. The states share
their unchanged intervals, so finding the stretch only compares references, at C speed.
What repeats forever has no stretch: the whole state is sent.
"""

from __future__ import annotations

from intervals import Interval
from line import Claims, LineState
from region import Region
from resolve import _on, _outside, _spliced
from solver import EMPTY, Line

WHOLE = "whole"


def _common(a: tuple, b: tuple) -> int:
    """The length of what a and b start with in common."""
    lo, hi = 0, min(len(a), len(b))
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[lo:mid] == b[lo:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _stretch(a: Region, b: Region):
    """(first, last) position where the regions differ, None if nowhere, WHOLE if that is
    not a finite stretch."""
    if a is b:
        return None
    if a.left or a.right or b.left or b.right:
        return None if a == b else WHOLE
    x, y = a.core.intervals, b.core.intervals
    start = _common(x, y)
    x, y = x[start:], y[start:]
    end = _common(x[::-1], y[::-1])
    x, y = x[:len(x) - end], y[:len(y) - end]
    if not x and not y:
        return None
    ends = (x[0], x[-1]) if x else ()
    ends += (y[0], y[-1]) if y else ()
    return min(iv.lo for iv in ends), max(iv.hi for iv in ends)


def _pairs(old: LineState, new: LineState):
    """The two regions of each thing a state holds."""
    for a, b in ((old.line.vals, new.line.vals), (old.claims.pos, new.claims.pos), (old.claims.neg, new.claims.neg),
                 (old.claims.taken, new.claims.taken)):
        for k in set(a) | set(b):
            x, y = a.get(k, {}), b.get(k, {})
            for key in set(x) | set(y):
                yield x.get(key, EMPTY), y.get(key, EMPTY)


def changes(old: LineState, new: LineState) -> tuple:
    kept = _common(old.history, new.history)
    edits = (kept, new.history[kept:])
    lo, hi = None, None
    for a, b in _pairs(old, new):
        s = _stretch(a, b)
        if s == WHOLE:
            return (WHOLE, new)
        if s is not None:
            lo = s[0] if lo is None or s[0] < lo else lo
            hi = s[1] if hi is None or s[1] > hi else hi
    if lo is None:
        return (None, edits, new.claims.clean)
    where = Region.of(Interval._mk(lo, hi))
    return (where, edits, new.claims.clean, Line(_on(new.line, where)), new.claims.on(where))


def patched(old: LineState, change: tuple) -> LineState:
    if change[0] == WHOLE:
        return change[1]
    where, (kept, more), clean = change[:3]
    history = old.history[:kept] + more
    if where is None:
        claims, line = old.claims.copy(), old.line
    else:
        claims: Claims = _outside(old.claims, where).merged(change[4])
        line = _spliced(old.line, change[3], where)
    claims.clean = clean
    return LineState(claims, line, history)
