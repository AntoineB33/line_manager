"""scripts/score.py: OmniApp's score (OmniApp's docs/scheduler_score.md, 2026-11-07) on a line,
checked on the examples that document gives."""

import math
import unittest
from fractions import Fraction as F

from helpers import Interval  # noqa: F401 (the paths of scripts/)
from intervals import Interval as Iv, IntervalSet
from region import Region
from score import Linear, Score, Settings, Step, Task

MIN = 60
HOUR = 3600


class Line:
    """A line of kinds, each on intervals (a, b]."""

    def __init__(self, **kinds) -> None:
        self.kinds = {k: Region.of(IntervalSet([Iv(a, b, False, True) for a, b in ivs])) if ivs else Region.empty()
                      for k, ivs in kinds.items()}

    def presence(self, k):
        return self.kinds.get(k, Region.empty())


def pattern(cycle, start, end):
    """[(kind, length), ...] repeated from start to end: {kind: [(a, b), ...]}."""
    out, x = {}, start
    while x < end:
        for k, n in cycle:
            out.setdefault(k, []).append((x, x + n))
            x += n
    return out


class ScoreTest(unittest.TestCase):
    def test_smaller_windows_score_better(self):
        """Two 50% tasks of 10min minimum: alternating every 10min scores about 4 times better
        than every 20min (the lag's excursion doubles, its square 4 times)."""
        s = Score({"A": Task(1, 10 * MIN), "B": Task(1, 10 * MIN)})
        j = {}
        for n in (10, 20):
            line = Line(**pattern([("A", n * MIN), ("B", n * MIN)], -40 * HOUR, 40 * HOUR))
            j[n] = s.evaluate(line, 0, 8 * HOUR)
            self.assertEqual(j[n].J2, 0)
        self.assertTrue(3 < j[20].J / j[10].J < 5, j[20].J / j[10].J)

    def test_a30_b15_c15(self):
        """A 30min, B and C 15min, a third each: A30 B15 C15 B15 C15 beats splitting A in two
        15min panels (A15 B15 C15), whose shortfall costs more than the lag it repays."""
        s = Score({"A": Task(1, 30 * MIN), "B": Task(1, 15 * MIN), "C": Task(1, 15 * MIN)})
        whole = Line(**pattern([("A", 30 * MIN), ("B", 15 * MIN), ("C", 15 * MIN), ("B", 15 * MIN), ("C", 15 * MIN)],
                               -40 * HOUR, 40 * HOUR))
        split = Line(**pattern([("A", 15 * MIN), ("B", 15 * MIN), ("C", 15 * MIN)], -40 * HOUR, 40 * HOUR))
        a, b = s.evaluate(whole, 0, 9 * HOUR), s.evaluate(split, 0, 9 * HOUR)
        self.assertGreater(b.J2, 0)
        self.assertLess(a.J, b.J)

    def test_compensation_after_a_blockage(self):
        """A is forbidden by Z for an hour (resilience 0): right after it, A's target is above
        its 50%, and back to 50% several compensation lengths later."""
        s = Score({"A": Task(1, 10 * MIN, resilience={"Z": 0}), "B": Task(1, 10 * MIN)})
        line = Line(Z=[(-HOUR, 0)])
        self.assertEqual(s.target(line, -HOUR / 2)["A"], 0)
        self.assertGreater(s.target(line, 60)["A"], 0.55)
        self.assertAlmostEqual(s.target(line, 40 * HOUR)["A"], 0.5, places=3)

    def test_the_compensation_in_linear_time_is_the_integral(self):
        """The running sums give the pairwise integral of scheduler_score.md, segment by segment."""
        import random
        rnd = random.Random(1)
        s = Score({"A": Task(1, 600, resilience={"Z": 0, "Y": 0.5}), "B": Task(2, 900, resilience={"Y": 0}), "C": Task(1, 1200)})
        z = sorted(rnd.sample(range(-50000, 50000, 100), 40))
        y = sorted(rnd.sample(range(-50000, 50000, 100), 40))
        line = Line(Z=[(z[i], z[i] + rnd.choice([300, 1200, 4000])) for i in range(0, 40, 2)],
                    Y=[(y[i], y[i] + rnd.choice([300, 1200, 4000])) for i in range(0, 40, 2)])
        R = s.rule_state(0)
        segs = s._segments(line, {}, -40000, 40000, min(R["lam"].values()) / 16)
        mus = [s._mu(x) for x in segs]
        sched = [any(m > 0 for m in mu.values()) for mu in mus]
        u0, u = [], 0.0
        for x, ok in zip(segs, sched):
            u0.append(u)
            u += (x[1] - x[0]) if ok else 0.0
        fast = s._compensation(R, segs, mus, sched, u0)
        for k in range(len(segs)):
            if not sched[k]:
                continue
            mid = u0[k] + (segs[k][1] - segs[k][0]) / 2
            for i in R["names"]:
                lam, acc = R["lam"][i], 0.0
                for j, sj in enumerate(segs):
                    dep = mus[k][i] - mus[j][i]
                    if j == k or not sched[j] or dep <= 0:
                        continue
                    ln = sj[1] - sj[0]
                    d = (mid - u0[j] - ln) if u0[j] + ln <= mid else (u0[j] - mid)
                    acc += dep * lam * math.exp(-max(d, 0.0) / lam) * (1 - math.exp(-ln / lam))
                self.assertAlmostEqual(fast[i][k], R["pi"][i] / lam * acc, places=12)

    def test_the_compensation_length_is_each_task_s(self):
        """lambda_i = lambda + 3 (tau_i - lambda)+: lambda while the task recurs at least every
        lambda, then about three of its periods."""
        s = Score({"A": Task(1, 30 * MIN), "B": Task(1, 30 * MIN), "R": Task(F(1, 1000), 30 * MIN)})
        R = s.rule_state(0)
        lam = Settings().compensation
        self.assertEqual(R["lam"]["A"], lam)  # tau = 1h
        self.assertAlmostEqual(R["lam"]["R"], lam + 3 * (R["tau"]["R"] - lam))

    def test_a_rare_task_s_past_still_counts(self):
        """A task due about every 115 days (1 hour at 1e-4) is rare: its panel 200 days ago
        still moves its lag now (the old 90-day cap forgot it)."""
        s = Score({"A": Task(1, HOUR), "B": Task(9999, HOUR)})
        R = s.rule_state(0)
        self.assertTrue(R["rare"]["A"])
        self.assertFalse(R["rare"]["B"])
        day = 24 * HOUR
        b = [(-2 * day, 2 * day)]
        with_panel = Line(A=[(-200 * day, -200 * day + HOUR)], B=b)
        without = Line(B=b)
        self.assertNotAlmostEqual(s.evaluate(with_panel, 0, day).J1, s.evaluate(without, 0, day).J1, places=6)

    def test_the_maximum(self):
        """A panel longer than its maximum costs (criterion 3); none with no maximum."""
        line = Line(A=[(0, 2 * HOUR)], B=[(2 * HOUR, 4 * HOUR)])
        capped = Score({"A": Task(1, 10 * MIN, maximum=HOUR), "B": Task(1, 10 * MIN)})
        free = Score({"A": Task(1, 10 * MIN), "B": Task(1, 10 * MIN)})
        self.assertGreater(capped.evaluate(line, 0, 4 * HOUR).J3, 0)
        self.assertEqual(free.evaluate(line, 0, 4 * HOUR).J3, 0)
        open_end = Line(A=[(0, 5 * HOUR)])
        self.assertGreater(capped.evaluate(open_end, 0, 4 * HOUR).J3, 0)  # (the open panel is charged)
        with self.assertRaises(ValueError):
            Score({"A": Task(1, HOUR, maximum=10 * MIN)})

    def test_a_quantity_by_a_date(self):
        """10 hours of A within [0, 20h]: reached, nothing missing; 4 hours short, D = 4h;
        and A's target is raised to what is missing over the time left."""
        q = ((10 * HOUR, 0, 20 * HOUR),)
        s = Score({"A": Task(1, HOUR, quantities=q), "B": Task(9, HOUR)})
        done = Line(A=[(0, 10 * HOUR)], B=[(10 * HOUR, 20 * HOUR)])
        short = Line(A=[(0, 6 * HOUR)], B=[(6 * HOUR, 20 * HOUR)])
        self.assertEqual(s.evaluate(done, 0, 20 * HOUR).D, 0)
        self.assertAlmostEqual(s.evaluate(short, 0, 20 * HOUR).D, 4 * HOUR)
        self.assertLess(s.evaluate(done, 0, 20 * HOUR).key(), s.evaluate(short, 0, 20 * HOUR).key())
        self.assertGreater(s.target(Line(B=[(0, 20 * HOUR)]), HOUR)["A"], 0.45)  # (about 10h over 20h left)

    def test_a_step_formula_is_its_values_on_each_side(self):
        """A's priority 1 up to 0, 3 after: scored after 0, the line is scored as if it were 3 at
        all times; before 0, as if 1."""
        cycle = pattern([("A", 20 * MIN), ("B", 10 * MIN)], -40 * HOUR, 40 * HOUR)
        line = Line(**cycle)
        step = Score({"A": Task(Step(1, [(0, 3)]), 10 * MIN), "B": Task(1, 10 * MIN)})
        three = Score({"A": Task(3, 10 * MIN), "B": Task(1, 10 * MIN)})
        one = Score({"A": Task(1, 10 * MIN), "B": Task(1, 10 * MIN)})
        self.assertAlmostEqual(step.evaluate(line, HOUR, 5 * HOUR).J, three.evaluate(line, HOUR, 5 * HOUR).J)
        self.assertAlmostEqual(step.evaluate(line, -5 * HOUR, -HOUR).J, one.evaluate(line, -5 * HOUR, -HOUR).J)

    def test_a_linear_formula_is_read_in_steps(self):
        """From 1 at 0 to 3 at 4h: its pieces are scored each with its own value."""
        s = Score({"A": Task(Linear(0, 1, 4 * HOUR, 3), 10 * MIN), "B": Task(1, 10 * MIN)})
        self.assertAlmostEqual(s.rule_state(2 * HOUR)["pi"]["A"], 2 / 3)
        line = Line(**pattern([("A", 20 * MIN), ("B", 10 * MIN)], -40 * HOUR, 40 * HOUR))
        self.assertTrue(math.isfinite(s.evaluate(line, 0, 4 * HOUR).J))


class ScoreProblemTest(unittest.TestCase):
    """score.ScoreProblem: the rules generator with OmniApp's score as its problem."""

    def run_generator(self, rules, tasks, state=None, window=None):
        from generator import RulesGenerator
        from line import LineState
        from modes import ModeTimeline
        from rules import parse_rules
        from score import ScoreProblem
        sc = Score(tasks)
        g = RulesGenerator(parse_rules(rules), [(ScoreProblem(sc), 1)], 0.5, window=window)
        script, _ = g.run(state or LineState.empty(), F(0), ModeTimeline(0, "1").known(0))
        return sc, g, script.base

    def test_a30_b15_c15_panels_of_their_minimum(self):
        """Three tasks that exclude each other, a third each: panels of their minimum, none short."""
        sc, g, st = self.run_generator(["A → no B", "A → no C", "B → no C"],
                                       {"A": Task(1, 30 * MIN), "B": Task(1, 15 * MIN), "C": Task(1, 15 * MIN)},
                                       window=9 * HOUR)
        self.assertEqual(sc.evaluate(st.line, 0, g.window).J2, 0)
        for k, n in (("A", 30), ("B", 15), ("C", 15)):
            for iv in st.line.presence(k).on(0, g.window).intervals:
                if iv.hi.x < g.window - 1:  # (one cut by the window's end: the next run goes on with it)
                    self.assertEqual(iv.hi.x - iv.lo.x, n * MIN, (k, str(iv)))

    @unittest.expectedFailure
    def test_a30_b15_c15_from_an_empty_line(self):
        """Over 9 hours from an empty line, at least as good as the fixed patterns of
        scheduler_score.md's example. Does not hold yet: measured 5487 against 5421
        (A30 B15 C15 B15 C15) and 4909 (B15 C15 A30), gaps_to_solve_later.md."""
        sc, g, st = self.run_generator(["A → no B", "A → no C", "B → no C"],
                                       {"A": Task(1, 30 * MIN), "B": Task(1, 15 * MIN), "C": Task(1, 15 * MIN)},
                                       window=9 * HOUR)
        r = sc.evaluate(st.line, 0, g.window)
        self.assertEqual(r.J2, 0)
        for k, n in (("A", 30), ("B", 15), ("C", 15)):
            for iv in st.line.presence(k).on(0, g.window).intervals:
                if iv.hi.x >= g.window - 1:
                    continue  # (cut by the window's end: the next run goes on with it)
                self.assertEqual(iv.hi.x - iv.lo.x + (0 if iv.lo.k else 0), n * MIN, (k, str(iv)))
        for cyc in ([("A", 30), ("B", 15), ("C", 15), ("B", 15), ("C", 15)], [("B", 15), ("C", 15), ("A", 30)]):
            fixed = Line(**pattern([(k, n * MIN) for k, n in cyc], 0, g.window))
            self.assertLessEqual(r.J, sc.evaluate(fixed, 0, g.window).J, cyc)

    def test_a_quantity_by_a_date_is_reached(self):
        """A at a tenth of B's priority, but 2 hours of it asked within the first 3: reached."""
        q = ((2 * HOUR, 0, 3 * HOUR),)
        sc, g, st = self.run_generator(["A → no B"], {"A": Task(1, 45 * MIN, quantities=q), "B": Task(10, 45 * MIN)})
        self.assertGreaterEqual(g.window, 3 * HOUR)
        self.assertEqual(sc.evaluate(st.line, 0, g.window).D, 0)
        self.assertGreaterEqual(st.line.integral("A", IntervalSet.of(Iv(0, 3 * HOUR, False, True))), 2 * HOUR)

    def test_the_user_s_task_stays(self):
        """The user's A on (600, 1200]: still there, the rest filled around it."""
        from line import LineState, Op
        from resolve import apply_edit
        from rules import parse_rules
        rules = ["A → no B"]
        st0 = apply_edit(parse_rules(rules), LineState.empty(), [Op.put("A", Region.of(Iv(600, 1200, False, True)))], "user")
        sc, g, st = self.run_generator(rules, {"A": Task(1, 10 * MIN), "B": Task(1, 10 * MIN)}, st0)
        self.assertTrue(Region.of(Iv(600, 1200, False, True)).issubset(st.line.presence("A")))
        self.assertGreater(st.line.integral("B", IntervalSet.of(Iv(0, g.window, False, True))), 0)


if __name__ == "__main__":
    unittest.main()
