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

    def test_a30_b15_c15_from_an_empty_line(self):
        """Three tasks that exclude each other, a third each, over 9 hours from an empty line: no
        panel under its minimum, and a score better than both fixed patterns of
        scheduler_score.md's example (measured 4684 against 5421 for A30 B15 C15 B15 C15 and 4910
        for B15 C15 A30)."""
        sc, g, st = self.run_generator(["A → no B", "A → no C", "B → no C"],
                                       {"A": Task(1, 30 * MIN), "B": Task(1, 15 * MIN), "C": Task(1, 15 * MIN)},
                                       window=9 * HOUR)
        r = sc.evaluate(st.line, 0, g.window)
        self.assertEqual(r.J2, 0)
        for k, n in (("A", 30), ("B", 15), ("C", 15)):
            for iv in st.line.presence(k).on(0, g.window).intervals:
                if iv.hi.x < g.window - 1:  # (one cut by the window's end: the next run goes on with it)
                    self.assertGreaterEqual(iv.hi.x - iv.lo.x, n * MIN, (k, str(iv)))
        for cyc in ([("A", 30), ("B", 15), ("C", 15), ("B", 15), ("C", 15)], [("B", 15), ("C", 15), ("A", 30)]):
            fixed = Line(**pattern([(k, n * MIN) for k, n in cyc], 0, g.window))
            self.assertLess(r.J, sc.evaluate(fixed, 0, g.window).J, cyc)

    def test_the_search_s_model_scores_as_the_score(self):
        """score_search.Model costs a continuation as Score.evaluate scores it on the line, the
        past put by the user (pre-placed: every other task deprived there, compensated after) or
        by the generator, sleep every night."""
        from generator import RulesGenerator, _Context
        from line import LineState, Op
        from modes import ModeTimeline
        from ratio_plan import cycle
        from resolve import apply_edit
        from rules import parse_rules
        from score import ScoreProblem
        from score_search import Model
        day = 24 * HOUR
        names = ["A", "B", "C", "D"]
        rules = parse_rules([f"{a} → " + " & ".join(f"no {b}" for b in names[i + 1:]) for i, a in enumerate(names[:-1])])
        chip = F(5 * day + 12 * HOUR)
        sleep = cycle(F(4920), day, 0, 8 * HOUR)
        past = pattern([("A", 45 * MIN), ("B", 45 * MIN), ("A", 45 * MIN), ("C", 45 * MIN), ("D", 30 * MIN)], 2 * day, chip)
        sc = Score({k: Task(p, 30 * MIN, resilience={"Sleep": 0}) for k, p in zip(names, (4, 2, 1, 1))})
        for owner in ("user", "generated"):
            with self.subTest(past=owner):
                st = apply_edit(rules, LineState.empty(), [Op.put("Sleep", sleep)], "user")
                st = apply_edit(rules, st, [Op.put(k, Region.of(IntervalSet([Iv(a, b, False, True) for a, b in v if b <= chip])))
                                            for k, v in past.items()], owner)
                g = RulesGenerator(rules, [(ScoreProblem(sc), 1)], 0.5, window=day)
                p = g.problems[0][0]
                ctx = _Context(g, st, chip, ModeTimeline(0, "1").known(chip))
                m = Model(p, ctx, st, 30.0)
                runs = m.stride()
                kinds = {k: st.line.presence(k) - Region.of(Iv(chip, chip + day, False, True)) for k in names}
                for k, v in m.to_wall(runs).items():
                    kinds[k] = kinds[k] | Region.of(IntervalSet([Iv(a, b, False, True) for a, b in v]))
                kinds["Sleep"] = sleep
                line = Line()
                line.kinds = kinds
                exact = sc.evaluate(line, chip, chip + day, p._preplaced(st))
                self.assertAlmostEqual(m.cost(runs)[1] / exact.J, 1, delta=0.01)

    def test_after_a_past_at_the_shares(self):
        """Account 3's shape: 22 tasks that exclude each other at its shares (26% down to 0.66%, 5
        at 0), a 45min minimum, sleep every night (resilience 0), 60 days of past at the shares
        (stride scheduling): over the next day the generator leaves little to nobody and scores
        better than going on at the shares. (Without the whole-continuation search,
        score_search.py, it left 38% to nobody.)"""
        from generator import RulesGenerator
        from line import LineState, Op
        from modes import ModeTimeline
        from ratio_plan import cycle
        from resolve import apply_edit
        from rules import parse_rules
        from score import ScoreProblem
        day = 24 * HOUR
        shares = {"T458": 2606, "T21": 1097, "T461": 1029, "T462": 1029, "T9": 1000, "T455": 769, "T463": 465,
                  "T464": 398, "T465": 398, "T459": 274, "T466": 266, "T467": 199, "T460": 137, "T469": 133,
                  "T468": 66, "T470": 66, "T471": 66, "T87": 0, "T124": 0, "T198": 0, "T250": 0, "T280": 0}
        names = sorted(shares)
        rules = parse_rules([f"{a} → " + " & ".join(f"no {b}" for b in names[i + 1:]) for i, a in enumerate(names[:-1])])
        chip, start = F(100 * day + 12 * HOUR), F(40 * day)
        sleep = cycle(start - day + 4920, day, 0, 8 * HOUR)

        def stride(upto):
            pos, panels, t = {k: 0.0 for k in names if shares[k]}, {k: [] for k in names}, start
            while t < upto:
                if sleep.contains(t + 1):
                    t = F(next(iv.hi.x for iv in sleep.on(t, t + day).intervals if iv.lo <= t + 1 <= iv.hi))
                    continue
                k = min(pos, key=lambda k: (pos[k], k))
                b = min(t + 45 * MIN, F(upto))
                panels[k].append((t, b))
                pos[k] += 45 * MIN / shares[k]
                t = b
            return panels
        past = stride(chip)
        ops = [Op.put(k, Region.of(IntervalSet([Iv(a, b, False, True) for a, b in v]))) for k, v in past.items() if v]
        st0 = apply_edit(rules, LineState.empty(), [Op.put("Sleep", sleep)], "user")
        st0 = apply_edit(rules, st0, ops, "generated")  # (a past schedule: not tasks the user put, pre-placed)
        sc = Score({k: Task(p, 45 * MIN, resilience={"Sleep": 0}) for k, p in shares.items()})
        g = RulesGenerator(rules, [(ScoreProblem(sc), 1)], 0.5, window=day)
        script, _ = g.run(st0, chip, ModeTimeline(0, "1").known(chip))
        line = script.base.line
        W = IntervalSet.of(Iv(chip, chip + day, False, True))
        awake = float(day - line.integral("Sleep", W))
        served = sum(float(line.integral(k, W)) for k in names)
        self.assertLess(1 - served / awake, 0.1)
        on = stride(chip + day)
        going_on = Line(Sleep=[], **on)
        going_on.kinds["Sleep"] = sleep
        self.assertLess(sc.evaluate(line, chip, chip + day).J, sc.evaluate(going_on, chip, chip + day).J)

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
