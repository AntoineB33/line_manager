"""pace.Pace: the scheduler's steps of OmniApp's requirements (§ Two paces): step n perfects
[chip + 10min (n + 1), chip + 10min (n + 2)] within its budget, from the big picture down."""

import time
import unittest
from fractions import Fraction as F

from helpers import Interval  # noqa: F401 (the paths of scripts/)
from instant import Instant
from intervals import Interval as Iv, IntervalSet
from line import LineState, Op
from pace import Pace
from ratio_plan import cycle
from region import Region
from resolve import apply_edit
from rules import parse_rules
from score import Score, Task

MIN, HOUR, DAY = 60, 3600, 86400
TASKS = {"A": Task(4, 45 * MIN, resilience={"Sleep": 0}), "B": Task(2, 30 * MIN, resilience={"Sleep": 0}),
         "C": Task(1, 30 * MIN, resilience={"Sleep": 0}), "D": Task(1, 15 * MIN, resilience={"Sleep": 0})}
NAMES = sorted(TASKS)


def setup(chip, days=3):
    """Tasks that exclude each other, a night every day, a past put by the generator: the
    instant schedule of the days before the chip."""
    rules = parse_rules([f"{a} → " + " & ".join(f"no {b}" for b in NAMES[i + 1:]) for i, a in enumerate(NAMES[:-1])])
    st = apply_edit(rules, LineState.empty(), [Op.put("Sleep", cycle(F(4920), F(DAY), F(0), F(8 * HOUR)))], "user")
    sc = Score(TASKS)
    past = Instant(rules, sc).schedule(st, chip - days * DAY, chip)
    st = apply_edit(rules, st, [Op.put(k, Region.of(IntervalSet([Iv(a, b, False, True) for a, b in v])))
                                for k, v in past.items()], "generated")
    return rules, sc, st


class PaceTest(unittest.TestCase):
    def test_steps_perfect_their_slices(self):
        chip = F(10 * DAY + 10 * HOUR)
        rules, sc, st = setup(chip)
        pace = Pace(rules, sc)
        steps = 6
        base = st
        for n in range(steps):
            began = time.perf_counter()
            ops, info = pace.step(st, chip, n, budget=1.5)
            self.assertLess(time.perf_counter() - began, 2.5, n)  # (its budget, and what it costs to stop)
            st = apply_edit(rules, st, ops, "generated")
            a, b = pace.slice(chip, n)
            self.assertEqual(info["slice"], (a, b))
            placed = IntervalSet()
            for k in NAMES:
                placed = placed | st.line.presence(k).on(a, b)
            self.assertFalse((IntervalSet.of(Iv(a, b, False, False)) - st.line.presence("Sleep").on(a, b) - placed).intervals, n)  # (on reads [a, b))
            self.assertFalse((placed & st.line.presence("Sleep").on(a, b)).intervals, n)
        # the steps' hour against the instant schedule's (the big picture alone)
        lo, hi = pace.slice(chip, 0)[0], pace.slice(chip, steps - 1)[1]
        inst = Instant(rules, sc).schedule(base, lo, hi)
        quick = apply_edit(rules, base, [Op.put(k, Region.of(IntervalSet([Iv(a, b, False, True) for a, b in v])))
                                         for k, v in inst.items()], "generated")
        self.assertLessEqual(sc.evaluate(st.line, lo, hi).J, sc.evaluate(quick.line, lo, hi).J)

    def test_a_step_plans_past_its_slice(self):
        """Step 0 commits its whole look ahead: its slice definitive, the hours after it a plan on
        the line; step 1 starts from that plan (a seed) and replaces it from its own slice on,
        leaving step 0's slice as it was."""
        chip = F(10 * DAY + 10 * HOUR)
        rules, sc, st = setup(chip)
        pace = Pace(rules, sc)
        ops, info = pace.step(st, chip, 0, budget=1.0)
        st = apply_edit(rules, st, ops, "generated")
        a, b = info["slice"]
        planned = IntervalSet()
        for k in NAMES:
            planned = planned | st.line.presence(k).on(b, info["planned"])
        self.assertGreater(planned.measure(), 2 * HOUR)  # (hours past the slice: the plan)
        first = {k: st.line.presence(k).on(a, b) for k in NAMES}
        ops, info1 = pace.step(st, chip, 1, budget=1.0)
        st = apply_edit(rules, st, ops, "generated")
        self.assertEqual({k: st.line.presence(k).on(a, b) for k in NAMES}, first)
        self.assertEqual(info1["slice"][0], b)

    def test_the_slice_is_proved_best(self):
        """Each step proves its slice: no plan of up to 3 runs on whole minutes scores lower (what
        follows as planned). Checked against plans scored one by one, the whole score each time,
        for the plans of up to 2 runs."""
        from itertools import combinations, product
        from types import SimpleNamespace
        from score import ScoreProblem
        from score_search import Model
        chip = F(10 * DAY + 10 * HOUR)
        rules, sc, st = setup(chip)
        pace = Pace(rules, sc)
        ops, info = pace.step(st, chip, 0, budget=2.0)
        self.assertTrue(info["proved"])
        self.assertGreater(info["plans looked at"], 1000)
        # brute force, the whole score each time
        a, b = pace.slice(chip, 0)
        p = ScoreProblem(sc, window=b - a + pace.AHEAD)
        m = Model(p, SimpleNamespace(c=a, state=st, gen=SimpleNamespace(rules=rules)), st, 60)
        runs = m.stride(0.25)
        m.deadline = time.perf_counter() + 60
        S = m.u_of(b)
        got, proved, n = m.certify(runs, S, grid=120, max_runs=2)
        self.assertTrue(proved)
        rest, u = [], 0.0
        for who, h in runs:
            if u + h > S:
                rest.append((who, u + h - max(u, S)))
            u += h
        tasks = [j for j in m.names if m.R["pi"][j] > 0] + [None]
        best = None
        for k in (1, 2):
            for cut in combinations([x for x in range(120, int(S), 120)], k - 1):
                ends = (0.0,) + tuple(float(c) for c in cut) + (S,)
                for plan in product(tasks, repeat=k):
                    if any(plan[i] == plan[i + 1] and plan[i] is not None for i in range(k - 1)):
                        continue
                    if any(j is not None and m.free_until(j, x) < y - 1e-6 for j, (x, y) in zip(plan, zip(ends, ends[1:]))):
                        continue
                    cand = Model._with_slice(runs, list(zip(plan, zip(ends, ends[1:]))), rest)
                    c = m.cost(cand)
                    best = c if best is None or c < best else best
        self.assertAlmostEqual(m.cost(got)[1], min(best, m.cost(runs))[1], places=6)

    def test_a_step_through_the_night(self):
        """A slice in the night: nothing committed there, and no time spent on it."""
        chip = F(10 * DAY + 2 * HOUR)  # (02:00: asleep until 09:22)
        rules, sc, st = setup(chip)
        ops, info = Pace(rules, sc).step(st, chip, 0, budget=1.0)
        self.assertEqual([o for o in ops if o.action == "put"], [])


class SteppedSessionTest(unittest.TestCase):
    """pace.Stepped in a session (UI/app.py): the run's scripts are the steps; the chip goes on
    through what they committed."""

    def test_a_session_runs_the_steps(self):
        from unittest import mock
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "UI"))
        import gui
        from app import Session
        from pace import Stepped
        from score import ScoreProblem
        rules = [f"{a} → " + " & ".join(f"no {b}" for b in NAMES[i + 1:]) for i, a in enumerate(NAMES[:-1])]
        chip = F(10 * DAY + 10 * HOUR)
        with mock.patch.object(gui, "run"):
            s = Session(rules, [(0, "1")], chip, 1, ["add Sleep on (4920, 33720] every 86400"], [(ScoreProblem(Score(TASKS)), 1)], F(1, 2))
            gen = s.sim.gen if hasattr(s.sim, "gen") else s.generator(s.rs)
            self.assertIsInstance(s.generator(s.rs), Stepped)
            s.begin()
            req = s.sim.request
            req.gen.steps, req.gen.budget = 3, 0.5
            began = time.perf_counter()
            out = []
            for script, stats in req.gen.stages(req.state, req.chip, req.known, req.previous):
                out.append((time.perf_counter() - began, script, stats))
            self.assertEqual(len(out), 4)  # (the first 10 minutes, then 3 steps)
            self.assertLess(out[0][0], 5)
            self.assertEqual([o[2].definitive for o in out], [chip + 600 * k for k in range(1, 5)])
            self.assertTrue(out[-1][2].final)
            s.sim.complete(req.id, out[-1][1], out[-1][2])
            s.sim.background = False
            for _ in range(4):
                s.tick(F(600))
            c = s.position
            line = s.sim.state.line
            placed = IntervalSet()
            for k in NAMES:
                placed = placed | line.presence(k).on(chip, chip + 2400)
            night = line.presence("Sleep").on(chip, chip + 2400)
            self.assertFalse((placed & night).intervals)
            self.assertFalse((IntervalSet.of(Iv(chip, chip + 2400, False, False)) - night - placed).intervals)


    def test_the_worker_process_gets_it_whole(self):
        """The window runs a run in a process of its own (generator_worker.py): the generator is
        pickled; once read back it still runs the steps."""
        import pickle
        from pace import Stepped
        from score import ScoreProblem
        from modes import ModeTimeline
        chip = F(10 * DAY + 10 * HOUR)
        rules, sc, st = setup(chip)
        gen = Stepped(rules, [(ScoreProblem(sc), 1)], 0.5, budget=0.3, steps=1)
        back = pickle.loads(pickle.dumps(gen))
        out = list(back.stages(st, chip, ModeTimeline(0, "1").known(chip)))
        self.assertEqual(len(out), 2)
        self.assertEqual(out[-1][1].definitive, chip + 1200)


if __name__ == "__main__":
    unittest.main()
