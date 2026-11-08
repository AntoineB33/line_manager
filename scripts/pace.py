"""The scheduler's steps (OmniApp's docs/scheduler_requirements.md § Two paces): from the chip c
where the scheduler starts, step n has a budget (10 seconds) to deliver the best schedule it can
for the 10 minutes it focuses on, [c + 10min (n + 1), c + 10min (n + 2)]; the first 10 minutes
after the chip are the instant schedule's (instant.py), as is everything the steps have not
reached.

A step goes from a big picture down to its slice:
1. the big picture: the instant schedule of the hours ahead of the slice (milliseconds);
2. the hours ahead, seeded with that big picture and with the score search's own seeds at the
   shares (score_search.Model: the score's model of the window, the lags from the line as the
   steps before left it), improved over the whole of them while a part of the budget lasts;
3. the slice: finer moves (an eighth of the shorter run) on the runs that meet it, the whole
   still scored, while the rest of the budget lasts.
Only the slice is committed: the next step starts from the line with it, so the steps join.
"""

from __future__ import annotations

import time
from fractions import Fraction
from types import SimpleNamespace

from instant import Instant
from intervals import Interval, IntervalSet
from line import GEN, Op
from region import Region
from score import ScoreProblem
from score_search import SEEDS, Model


class Pace:
    STEP = 600  # each step's slice: 10 minutes
    BUDGET = 10.0  # each step's seconds
    AHEAD = 4 * 3600  # how far past its slice a step looks (its big picture)
    WHOLE = 0.5  # the part of the budget for the hours ahead; the rest for the slice

    def __init__(self, rules, score) -> None:
        self.rules, self.score = rules, score
        self.instant = Instant(rules, score)

    def slice(self, chip, n) -> tuple:
        a = Fraction(chip) + self.STEP * (n + 1)
        return a, a + self.STEP

    def step(self, state, chip, n, budget: float | None = None) -> tuple:
        """(the edit committing step n's slice, what the step did): the generator's tasks on the
        slice taken off and its schedule put there."""
        began = time.perf_counter()
        budget = self.BUDGET if budget is None else budget
        a, b = self.slice(chip, n)
        end = b + self.AHEAD
        big = self.instant.schedule(state, a, end)
        problem = ScoreProblem(self.score, window=end - a)
        ctx = SimpleNamespace(c=a, state=state, gen=SimpleNamespace(rules=self.rules))
        m = Model(problem, ctx, state, budget * self.WHOLE)
        info = {"slice": (a, b)}
        if m.U <= 0:
            return self._commit(state, {}, a, b), info
        seeds = [m.from_wall(big)] + [m.stride(off) for off in SEEDS[:2]]
        costs = [m.cost(r) for r in seeds]
        info["big picture"] = costs[0]
        best = min(zip(costs, range(len(seeds))))[1]
        runs = m.improve(seeds[best])
        info["hours ahead"] = m.cost(runs)
        m.deadline = began + budget  # (the rest of the budget: the slice)
        focus = (m.u_of(a), m.u_of(b))
        runs = m.improve(runs, focus=focus, fraction=8)
        info["slice done"] = m.cost(runs)
        info["seconds"] = time.perf_counter() - began
        return self._commit(state, m.to_wall(runs), a, b), info

    def _commit(self, state, wall: dict, a, b) -> list:
        """The edit: on [a, b], the generator's tasks off, the step's put."""
        W = Region.of(Interval(a, b, False, True))
        ops = []
        for i in sorted(self.score.tasks):
            mine = state.claims.owned(i, GEN) & W
            if not mine.is_empty():
                ops.append(Op.take(i, mine))
            got = [(_snap(max(x, a), a, b), _snap(min(y, b), a, b)) for x, y in wall.get(i, ()) if y > a and x < b]
            got = [(x, y) for x, y in got if y > x]
            if got:
                ops.append(Op.put(i, Region.of(IntervalSet([Interval(x, y, False, True) for x, y in got]))))
        return ops


def _snap(x, a, b) -> Fraction:
    """A position the model computed in floats, on the hundredth of a second, and on the slice's
    ends where it is that close to them: the steps' slices join without a gap."""
    x = Fraction(round(float(x) * 100), 100)
    for e in (a, b):
        if abs(x - e) <= Fraction(1, 100):
            return e
    return x
