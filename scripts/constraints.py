"""Constraints the rules generator satisfies (generator.py).

A constraint gives, for the line as it is and the chip at c, where its elements must be
after the chip, `targets(state, c)` ({kind: region} on (c, inf)), and the edits that
make it hold, `ops(state, c)` (on positions after c only: the past and the chip's
position never change, N1; none if it already holds). The generator makes the edits
first and scores its candidates on top of them; its script makes them again where it
is put in place (script.ConstraintEditAction). As the chip goes, the mode rules the
generator compiles keep the targets too (mode_rules.ModeRules.constrain): a break the
chip reaches in mode 1 moves on with it.

- KeepOn(op): an edit written from the chip (`Future on (0, inf)`: Future on (c, inf)).
- FifteenMinuteBreaks: the 15-minute screen breaks of OmniApp
  (docs/scheduler_requirements.md of OmniApp).
"""

from __future__ import annotations

from fractions import Fraction

from intervals import EPS, INF, Interval, IntervalSet, Pos, _fmt
from line import LineState, Op
from region import ONE, ZERO, Region
from solver import checkpoint


def _after(c) -> Region:
    """(c, inf)."""
    return Region.of(Interval(c, INF, False, False))


def _upto(c) -> Region:
    """(-inf, c]."""
    return Region.of(Interval(-INF, c, False, True))


class KeepOn:
    """The line keeps `op` (an element put on a region written from the chip)."""

    def __init__(self, op: Op) -> None:
        self.op = op

    def targets(self, state: LineState, c) -> dict:
        return {self.op.kind: self.op.region.shift(c) & _after(c)} if self.op.action == "put" else {}

    def ops(self, state: LineState, c: Fraction) -> list[Op]:
        o = Op(self.op.action, self.op.kind, self.op.region.shift(c), self.op.value)
        if o.action == "put" and o.value == 100 and (o.region - state.line.presence(o.kind)).is_empty():
            return []
        return [o]

    def __str__(self) -> str:
        return f"keep {str(self.op).replace(' on ', ' on chip + ', 1)}"


class FifteenMinuteBreaks:
    """The 15-minute screen breaks (OmniApp's rules restricted to them):
    - a break lasts `length` and comes with "no screen" (the element rule
      `Break15 → NoScreen` of the line);
    - breaks are everywhere after the chip, each as early as the rules below allow;
    - after `length` or more of "no screen" (a break included), no break starts in the
      next `pause` (it may start exactly `pause` after: one break every length + pause);
    - a break that would start inside a "no screen" period starts at the start of that
      period, or right after the chip if it began before (even against the pause);
    - a break never covers a position where "no screen" or the break is ruled out
      ("no NoScreen", e.g. the chip's positions in mode 1).
    The breaks that started at or before the chip stay as they are (the frozen past).

    Exactly, on the line's intervals: a sweep from the chip places the breaks one after
    the other; past the last "no screen" or ruled-out position ahead, they repeat every
    length + pause.
    """

    def __init__(self, brk: str = "Break15", ns: str = "NoScreen", length=15, pause=120) -> None:
        self.brk, self.ns = brk, ns
        self.length, self.pause = Fraction(length), Fraction(pause)

    def __str__(self) -> str:
        return (f"keep {self.brk} on (chip, chip + {_fmt(self.length)}] with {self.ns}, as early as possible, "
                f"none within {_fmt(self.pause)} after as much {self.ns}")

    def breaks(self, state: LineState, c: Fraction) -> Region:
        """The breaks there must be after the chip at c (those going on included)."""
        L, P = self.length, self.pause
        cp = c if isinstance(c, Pos) else Pos(c)
        now = state.line.presence(self.brk)
        # what is known of "no screen": the past as it is, and ahead what is not a break of ours
        others = Region.empty()
        for (v, owner), r in state.claims.pos.get(self.ns, {}).items():
            others = others | r
        known = (state.line.presence(self.ns) & _upto(c)) | (others & _after(c))
        ruled_out = state.claims.negative(self.ns) | state.claims.negative(self.brk) | state.claims.off(self.brk)
        ruled_out = ruled_out & _after(c)
        # the breaks going on at the chip (begun before it: the frozen past) stay whole; one
        # the chip has eaten up to its position (mode 1: no "no screen" there) moves on
        going = [iv for iv in now.on(cp - L - 1, cp + L + 1).intervals if iv.lo < cp < iv.hi]
        # (as long as a break is: the line may be from when breaks were shorter)
        ruled = state.claims.negative(self.ns) | state.claims.negative(self.brk) | state.claims.off(self.brk)
        placed = [(iv.lo, iv.lo + L if iv.hi < iv.lo + L and (Region.of(Interval._mk(iv.hi, iv.lo + L)) & ruled).is_empty() else iv.hi)
                  for iv in going]
        ahead = [r for r in (known & _after(c), ruled_out) if r]
        if any(not r.is_bounded() for r in ahead):  # "no screen" or a ban without end: no break past its start
            end = min(r.hi for r in ahead if not r.is_bounded())
        else:
            end = max([r.bounded().sup() for r in ahead if r.bounded()] + [cp])
        lo = cp - L - P - 1  # what is before it no longer matters to the pauses
        bans = [(iv.lo, iv.hi) for iv in ruled_out.on(cp, end + L + 1).intervals] if ruled_out else []

        def periods(extra):
            """The maximal "no screen" intervals with these breaks: (start, end)."""
            reg = known | Region.of(IntervalSet([Interval._mk(a, b) for a, b in extra])) if extra else known
            return [(iv.lo, iv.hi) for iv in reg.on(lo, end + 2 * (L + P) + 2).intervals]

        def allowed(t):
            """The first start from t that no pause, ban or break already there excludes."""
            changed = True
            while changed:
                checkpoint()
                changed = False
                for a, e in periods(placed):  # (a period with its break in it: no other one in it)
                    if (e - a).x >= L and (e < t < e + P or (a < t <= e and any(a <= pa and pe <= e for pa, pe in placed))):
                        t, changed = e + P, True
                for a, e in bans + placed:
                    if a <= t + L and e >= t:
                        t, changed = e + EPS, True
            return t

        t = allowed(cp + EPS)
        while t <= end:
            checkpoint()
            for a, e in periods([]):  # inside "no screen": the break starts at its start
                if a < t <= e:
                    t = a if a > cp else cp + EPS
                    break
            placed.append((t, t + L))
            t = allowed(t + L + EPS)
        # past `end`, nothing ahead: one break every L + P from t
        core = IntervalSet([Interval._mk(a, b) for a, b in placed])
        first = min([a for a, _ in placed] + [t])
        return Region(first, t, core, ONE, IntervalSet(), Pos(L + P), IntervalSet._sorted([Interval._mk(ZERO, Pos(L))]))

    def targets(self, state: LineState, c) -> dict:
        return {self.brk: self.breaks(state, c) & _after(c)}

    def ops(self, state: LineState, c: Fraction) -> list[Op]:
        want = self.breaks(state, c) & _after(c)
        now = state.line.presence(self.brk) & _after(c)
        out = []
        if not (want - now).is_empty():
            out.append(Op.put(self.brk, want - now))
        if not (now - want).is_empty():
            out.append(Op.take(self.brk, now - want))
        return out
