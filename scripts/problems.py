"""The optimization problems of the rules generator.

The generator is given problems, each with a weight, and minimises the weighted sum
of their errors (generator.py). A problem has a horizon H: it looks at the window
W = (c, c + H], c the chip's position. It provides:
- error(ctx, state): how far the state is from its targets on W, >= 0, and 0 when
  they are reached;
- proposals(ctx, prediction, strategy): an edit of the generated script that brings
  the state closer, for a prediction of the mode rules and a placement strategy
  (generator.PREDICTIONS, generator.STRATEGIES);
- extra(ctx): other edits worth trying, as (label, ops);
- has_targets(): whether there is anything to reach.
ctx is the generator's context: the state, the chip c, and what the mode rules are
expected to do in W (`ctx.expected`).

PresenceRatioProblem. For each kind K a target r_K in [0, 1] for the presence ratio
(1/H) ∫_W s_K(x)/100 dx. The error is the sum over the kinds of |ratio - r_K|, the
ratio expected with what the mode rules will add or remove in W.

MinimumLengthProblem. For each kind K a minimum length L_K: ideally, each interval of
K is at least L long. Intervals near one another count for each other, by how close
they are: an interval i of length l_i is supported by

    S_i = sum over the intervals j of K of  l_j * max(0, 1 - d_ij / L)

where d_ij is the gap between i and j (0 for i itself), and it falls short by
max(0, L - S_i). The error is the sum of the shortfalls of the intervals that meet W,
divided by L (one interval missing entirely counts 1). So:
- one interval of length L: no shortfall;
- cut by a small hole of size h: the two pieces support each other almost fully, and
  each falls short by a bit more than h (the hole, and the pieces count a little
  less for each other), so making up for it takes more than the hole;
- a piece further than L from every other interval of K needs the full L on its own.
The positions of the line are x + kε; lengths and gaps are those of the real parts.
The mode rules are not predicted for this problem: it reads the line as it is.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from intervals import EMPTY as IEMPTY, Interval, IntervalSet, Pos
from line import GEN, Op
from region import Region


def _window(c: Fraction, H: Fraction) -> IntervalSet:
    return IntervalSet.of(Interval(c, c + H, False, True))


@dataclass
class PresenceRatioProblem:
    """Targets r_K in [0, 1] for the presence ratio (1/H) ∫_W s_K(x)/100 dx of each kind."""
    targets: dict[str, Fraction]
    horizon: Fraction

    def __post_init__(self) -> None:
        self.targets = {t: Fraction(r) for t, r in self.targets.items()}
        self.horizon = Fraction(self.horizon)
        if self.horizon <= 0 or any(not 0 <= r <= 1 for r in self.targets.values()):
            raise ValueError("horizon > 0 and targets in [0, 1]")

    name = "presence ratio"

    def has_targets(self) -> bool:
        return bool(self.targets)

    def error(self, ctx, s) -> Fraction:
        H, Wset = self.horizon, _window(ctx.c, self.horizon)
        return sum((abs(ctx.expected(s, k, ctx.weights_full, Wset) / H - r) for k, r in self.targets.items()), Fraction(0))

    def proposals(self, ctx, pred: str, strategy: str) -> tuple[Op, ...]:
        weights = {"scenario": ctx.weights_best, "uniform": ctx.weights_uniform, "none": None}[pred]
        H, c, s = self.horizon, ctx.c, ctx.state
        Wset = _window(c, H)
        W = Region.of(Wset)
        ops = []
        for kind, target in sorted(self.targets.items()):
            need = target * H - ctx.expected(s, kind, weights, Wset)
            if need > 0:
                free = (W - s.line.presence(kind)).on(c, c + H + 1) & Wset
                place = _place(free, need, strategy, c, H)
                if place:
                    ops.append(Op.put(kind, Region.of(place)))
            elif need < 0:
                mine = (s.claims.owned(kind, GEN) & W).on(c, c + H + 1) & Wset
                cut = mine.take(-need, from_right=strategy != "right")
                if cut:
                    ops.append(Op.take(kind, Region.of(cut)))
        return tuple(ops)

    def extra(self, ctx) -> list:
        return []


def _place(free: IntervalSet, amount: Fraction, strategy: str, c: Fraction, H: Fraction) -> IntervalSet:
    if strategy == "left":
        return free.take(amount)
    if strategy == "right":
        return free.take(amount, from_right=True)
    k = int(strategy.removeprefix("spread"))
    out = IEMPTY
    carry = Fraction(0)
    for i in range(k):
        part = IntervalSet.of(Interval(c + H * i / k, c + H * (i + 1) / k, False, True)) & free
        got = part.take(amount / k + carry)
        carry = amount / k + carry - got.measure()
        out = out | got
    return out


@dataclass
class MinimumLengthProblem:
    """A minimum length L_K > 0 for the intervals of each kind (see the top of this file)."""
    lengths: dict[str, Fraction]
    horizon: Fraction

    def __post_init__(self) -> None:
        self.lengths = {k: Fraction(v) for k, v in self.lengths.items()}
        self.horizon = Fraction(self.horizon)
        if self.horizon <= 0 or any(v <= 0 for v in self.lengths.values()):
            raise ValueError("horizon > 0 and minimum lengths > 0")

    name = "minimum length"

    def has_targets(self) -> bool:
        return bool(self.lengths)

    def error(self, ctx, s) -> Fraction:
        out = Fraction(0)
        for k, L in self.lengths.items():
            out += sum((d for _, d in shortfalls(s.line.presence(k), L, ctx.c, self.horizon)), Fraction(0)) / L
        return out

    def proposals(self, ctx, pred: str, strategy: str) -> tuple[Op, ...]:
        """Each interval that falls short grows by its shortfall: to the left first
        ("left"), to the right ("right"), or half on each side, on the free positions
        after the chip (not where K is, nor where a claim removes or forbids it), the
        free positions further on when the nearest are not."""
        c, s = ctx.c, ctx.state
        ops = []
        for k, L in sorted(self.lengths.items()):
            pres = s.line.presence(k)
            blocked = pres | s.claims.off(k) | s.claims.negative(k)
            free = (Region.universe() - blocked).on(c, c + self.horizon + 3 * L) & IntervalSet.after(c)
            grow = IEMPTY
            for iv, d in shortfalls(pres, L, c, self.horizon):
                if not d:
                    continue
                want = d if strategy == "left" else Fraction(0) if strategy == "right" else d / 2
                before = Interval.make(max(Pos(c), iv.lo - 2 * L), iv.lo, False, False)
                got = (free & IntervalSet.of(before)).take(want, from_right=True) if before and want else IEMPTY
                after = Interval.make(iv.hi, iv.hi + 3 * L, False, True)
                more = (free & IntervalSet.of(after)).take(d - got.measure()) if after and d > got.measure() else IEMPTY
                grow = grow | got | more
                free = free - got - more
            if grow:
                ops.append(Op.put(k, Region.of(grow)))
        return tuple(ops)

    def extra(self, ctx) -> list:
        """Remove the generated intervals that fall short, after the chip."""
        s, c = ctx.state, Pos(ctx.c)
        ops = []
        for k, L in sorted(self.lengths.items()):
            mine = s.claims.owned(k, GEN)
            drop = [iv for iv, d in shortfalls(s.line.presence(k), L, ctx.c, self.horizon)
                    if d and iv.lo > c and Region.of(IntervalSet.of(iv)).issubset(mine)]
            if drop:
                ops.append(Op.take(k, Region.of(IntervalSet(drop))))
        return [("minimum length: remove the short generated intervals", tuple(ops))] if ops else []


def shortfalls(pres: Region, L: Fraction, c: Fraction, H: Fraction) -> list[tuple[Interval, Fraction]]:
    """(interval, shortfall) for the intervals of `pres` that meet (c, c + H]."""
    ivs = pres.on(c - 2 * L, c + H + 2 * L).intervals  # an interval cut there is longer than 2L
    W = _window(c, H)
    out = []
    for i, a in enumerate(ivs):
        if not IntervalSet.of(a) & W:
            continue
        support = Fraction(0)
        for j, b in enumerate(ivs):
            gap = Fraction(0) if i == j else max(Fraction(0), (b.lo.x - a.hi.x) if j > i else (a.lo.x - b.hi.x))
            if gap < L:
                support += b.length * (1 - gap / L)
        out.append((a, max(Fraction(0), L - support)))
    return out
