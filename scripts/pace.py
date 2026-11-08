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
Each part searches until its part of the budget is spent: once the improvement stops, the best
plan is shaken a little and improved again, kept when better (Model.deepen). The last part proves
the slice: no plan of it of up to 3 runs ending on whole minutes scores lower, what follows as
planned (Model.certify, exact: the score is a sum of one term per task); a better one found is
taken. `info["proved"]` says whether the proof was finished in the budget.
The whole look ahead is committed, the slice definitive and the rest a plan: a better schedule
found from the big picture changes what is past the slice too. The next step starts from the
line with it, its plan one of its seeds (the continuation a re-plan replaces), and replaces it
from its own slice on.
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
    HOURS = 0.4  # the part of the budget for the hours ahead, ...
    SLICE = 0.7  # ... then up to this part for the slice, the rest to prove it best (Model.certify)
    PROOF = dict(grid=60, max_runs=3)  # the plans the proof looks at: up to 3 runs on whole minutes

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
        m = Model(problem, ctx, state, budget * self.HOURS)
        info = {"slice": (a, b)}
        if m.U <= 0:
            return self._commit(state, {}, a, b), info
        plan = {i: [(max(Fraction(iv.lo.x if hasattr(iv.lo, "x") else iv.lo), a), min(Fraction(iv.hi.x if hasattr(iv.hi, "x") else iv.hi), end))
                    for iv in state.claims.owned(i, GEN).on(a, end).intervals]
                for i in self.score.tasks}
        plan = {i: v for i, v in plan.items() if v}
        shown = self.instant.schedule(state, a, end, start=a)  # (what the window shows there: the step is never worse)
        seeds = [m.from_wall(big), m.from_wall(shown)] + ([m.from_wall(plan)] if plan else []) + [m.stride(off) for off in SEEDS[:2]]
        costs = [m.cost(r) for r in seeds]
        info["big picture"] = costs[0]
        # each seed improved in turn, best first, while the hours' part of the budget lasts: the
        # improvement ends in a local optimum that depends on the seed, the best one at the start
        # is not always the best at the end
        runs, key = None, None
        for _, k in sorted(zip(costs, range(len(seeds)))):
            got = m.improve(seeds[k])
            c = m.cost(got)
            if key is None or c < key:
                runs, key = got, c
            if m.out_of_time():
                break
        import random
        rnd = random.Random(n)  # (the same step decides the same: no chance in what it publishes)
        runs = m.deepen(runs, rnd)  # (the hours ahead: until their part of the budget is spent)
        info["hours ahead"] = m.cost(runs)
        m.deadline = began + budget * self.SLICE  # (then the slice)
        focus = (m.u_of(a), m.u_of(b))
        runs = m.improve(runs, focus=focus, fraction=8)
        runs = m.deepen(runs, rnd, focus=focus, fraction=8)
        m.deadline = began + budget  # (the rest: the proof that nothing on the slice is better)
        runs, proved, looked = m.certify(runs, focus[1], **self.PROOF)
        info["slice done"] = m.cost(runs)
        info["proved"], info["plans looked at"] = proved, looked
        info["seconds"] = time.perf_counter() - began
        info["planned"] = end
        return self._commit(state, m.to_wall(runs), a, end), info

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


class Stepped:
    """A rules generator whose run is the scheduler's steps (OmniApp's requirements § Two paces),
    for problems that are OmniApp's score (score.ScoreProblem): its first script is the run of
    an ordinary generator's first stage without the score (the constraints' edits: the breaks)
    with the instant schedule of the first 10 minutes after the chip; then each step adds its
    slice, the script definitive up to the slice's end, `STEPS` of them (3 hours), the last
    final. What it does not have is the ordinary generator's (it stands for one: `__getattr__`)."""

    STEPS = 18

    def __init__(self, rules, problems, q, constraints=(), budget: float | None = None, steps: int | None = None) -> None:
        from generator import RulesGenerator
        score = [p for p, w in problems if isinstance(p, ScoreProblem)]
        if not score:
            raise ValueError("the steps are those of OmniApp's score: a ScoreProblem")
        rest = [(p, w) for p, w in problems if not isinstance(p, ScoreProblem)]
        self.base = RulesGenerator(rules, rest, q, constraints=constraints)
        self.score = score[0].score
        self.problems = list(problems)
        self.budget = Pace.BUDGET if budget is None else budget
        self.steps = self.STEPS if steps is None else steps

    def __getattr__(self, name):
        if name in ("base", "score", "problems", "budget", "steps"):
            raise AttributeError(name)
        return getattr(self.base, name)

    def first_edits(self, state, c) -> list:
        """What the script at the start of a run puts at once (simulation.first_script): the
        instant schedule of the first 10 minutes after the chip."""
        pace = Pace(self.rules, self.score)
        c = Fraction(c)
        quick = pace.instant.schedule(state, c, c + Pace.STEP)
        ops = pace._commit(state, {k: [(Fraction(a), Fraction(b)) for a, b in v] for k, v in quick.items()}, c, c + Pace.STEP)
        return [ops] if ops else []

    def run(self, state, chip, known):
        out = None
        for out in self.stages(state, chip, known):
            pass
        return out

    def stages(self, state, chip, known, previous=None):
        from dataclasses import replace
        from intervals import INF
        from resolve import Refused, apply_edit
        from script import ChipIn, EditAction, Event
        c = Fraction(chip)
        first = next(iter(self.base.stages(state, c, known, previous)))
        script, stats = first
        st = script.base if script.base is not None else state
        pace = Pace(self.rules, self.score)
        events = list(script.events)

        def publish(ops, st, label, definitive, final):
            nonlocal events
            if ops:
                events = events + [Event(ChipIn(c, INF), (EditAction(tuple(ops)),))]
            return replace(script, base=st).with_events(events), replace(stats, chosen=label, definitive=definitive, final=final)
        # the first 10 minutes: the instant schedule (the steps start after them)
        quick = pace.instant.schedule(st, c, c + Pace.STEP)
        ops = pace._commit(st, {k: [(Fraction(a), Fraction(b)) for a, b in v] for k, v in quick.items()}, c, c + Pace.STEP)
        try:
            st = apply_edit(self.rules, st, ops, "generated") if ops else st
        except Refused:
            ops = []
        yield publish(ops, st, "the instant schedule of the first 10 minutes", c + Pace.STEP, self.steps == 0)
        for n in range(self.steps):
            ops, info = pace.step(st, c, n, self.budget)
            try:
                st = apply_edit(self.rules, st, ops, "generated") if ops else st
            except Refused:
                ops = []
            yield publish(ops, st, f"step {n}", info["slice"][1], n == self.steps - 1)
