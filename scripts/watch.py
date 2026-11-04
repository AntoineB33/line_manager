"""What an event of the script has to be looked at for: the simulation does not test every
trigger at every step, it keeps for each one the next position of the chip where it can
change, and what it reads (`look`). A trigger is tested again when the chip gets there or
when what it reads changes, so nothing is computed while nothing can change.

A presence ratio over (chip, chip + H] changes linearly between the edges of the line and
those edges minus H, so where it crosses a level is known in advance (`crossing`); so is
the score of presence ratio problems, once the points where a ratio crosses its target are
added. The other problems' errors are not linear in the chip: their score is read at each
instant, as the triggers with nothing to compute are (`look` gives None for them).
"""

from __future__ import annotations

from fractions import Fraction

from intervals import INF, Interval, IntervalSet
from script import ChipIn, PresenceAbove, ScoreBelow, _Now

TINY = Fraction(1, 1024)  # right after a level is reached: a ratio "exceeds" it past that position
MOST = 32  # the stretches looked through at once: the search goes on from the last one


def crossing(f, points: list, level: Fraction):
    """f is linear between the sorted points: the first position after points[0] where
    f > level changes; None if it does not."""
    a, fa = points[0], f(points[0])
    above = fa > level
    for b in points[1:]:
        fb = f(b)
        if (fb > level) != above:
            x = a + (b - a) * (level - fa) / (fb - fa)
            return x if above else x + min(TINY, (b - x) / 2)
        a, fa = b, fb
    return None


def _ends(regions, a, b, H) -> set:
    """The positions of (a, b) where the measure of these regions on (x, x + H] changes slope."""
    out = set()
    for r in regions:
        for iv in r.on(a, b + H).intervals:
            for e in (iv.lo.x, iv.hi.x):
                out.update(x for x in (e, e - H) if a < x < b)
    return out


def _search(f, a, b, level, more=()):
    """`crossing` on [a, b], `more` being the other positions where f changes slope: the
    position to look at, b if nothing changes as far as it was looked."""
    points = sorted({a, b} | set(more))[:MOST]
    x = crossing(f, points, level)
    return x if x is not None else points[-1]


def _presence_regions(state, kinds) -> list:
    return [r for k in kinds for r in state.line.vals.get(k, {}).values()]


def _kinks(problems, state, points) -> set:
    """Where a presence ratio crosses its target: |ratio - target| changes slope there."""
    out = set()
    for p, _ in problems:
        H = p.window
        for k, r in p.targets.items():
            def g(x):
                return state.line.integral(k, IntervalSet.of(Interval(x, x + H, False, True))) / H - r
            for a, b in zip(points, points[1:]):
                ga, gb = g(a), g(b)
                if (ga > 0) != (gb > 0) and ga != gb:
                    out.add(a + (b - a) * -ga / (gb - ga))
    return out


def reads(trigger, state, mode: str, problems) -> tuple:
    """What the trigger reads, besides the chip."""
    if isinstance(trigger, ChipIn):
        return (mode,) if trigger.mode is not None else ()
    if isinstance(trigger, PresenceAbove):
        return (mode if trigger.mode is not None else None, state.line.vals.get(trigger.kind, {}).get(trigger.value))
    return tuple(state.line.vals.get(k) for p, _ in problems for k in p.targets)


def _next(trigger, state, c, problems):
    if isinstance(trigger, ChipIn):
        return trigger.lo if c < trigger.lo else trigger.hi if c < trigger.hi else INF
    if isinstance(trigger, PresenceAbove):
        if c < trigger.lo:
            return trigger.lo
        if c >= trigger.hi:
            return INF
        r = state.line.vals.get(trigger.kind, {}).get(trigger.value)
        if r is None:
            return INF
        H, top = trigger.H, min(trigger.hi, c + trigger.H)

        def ratio(x):
            return r.measure_on(IntervalSet.of(Interval(x, x + H, False, True))) / H
        return _search(ratio, c, top, trigger.y, _ends([r], c, top, H))
    if trigger.z <= 0:
        return INF
    H = max(p.window for p, _ in problems)
    kinds = [k for p, _ in problems for k in p.targets]
    top = c + H
    points = sorted({c, top} | _ends(_presence_regions(state, kinds), c, top, H))[:MOST]

    def error(x):
        return sum((w * p.error(_Now(x), state) for p, w in problems if w), Fraction(0))
    return _search(error, points[0], points[-1], 1 / trigger.z - 1, set(points) | _kinks(problems, state, points))


def look(trigger, state, c: Fraction, mode: str, problems):
    """(the next position of the chip where the trigger can change, what it reads then);
    None if it is to be read at each instant."""
    if isinstance(trigger, ScoreBelow):
        if not all(hasattr(p, "targets") for p, _ in problems):
            return None
    elif not isinstance(trigger, (ChipIn, PresenceAbove)):
        return None
    return _next(trigger, state, c, problems), reads(trigger, state, mode, problems)


def due(entry, trigger, state, c: Fraction, mode: str, problems) -> bool:
    """Is the trigger to be tested now? entry: its last `look`."""
    if entry is None:
        return True
    at, seen = entry
    if c >= at:
        return True
    now = reads(trigger, state, mode, problems)
    return any(a is not b and a != b for a, b in zip(seen, now)) or len(seen) != len(now)
