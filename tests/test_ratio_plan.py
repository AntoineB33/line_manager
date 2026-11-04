"""The best line of the presence ratio targets and minimum lengths (scripts/ratio_plan.py),
on the whole line, found at once: the examples of the rules generator's logic."""

import time
import unittest
from fractions import Fraction as F

from helpers import Interval  # noqa: F401 (the paths of scripts/)
from generator import MinimumLengthProblem, PresenceRatioProblem, RulesGenerator
from intervals import INF, Interval as Iv
from line import LineState, Op
from modes import ModeTimeline
from region import Region
from resolve import apply_edit
from rules import parse_rules

X = F(3)  # the chip when the generator starts
HALF = F(1, 2)
FAST = 0.5  # seconds: "very quickly" (a few milliseconds here)


def generator(rules, targets, lengths=None, seed=0):
    problems = [(PresenceRatioProblem(targets), 1)]
    if lengths:
        problems.append((MinimumLengthProblem(lengths), 1))
    return RulesGenerator(parse_rules(rules), problems, 0.5, seed=seed)


def perfect(rules, targets, lengths=None, past=None, seed=0, since=-INF):
    """The line after the generator's script from X, and how long the run took; `past` is
    on (since, X]."""
    g = generator(rules, targets, lengths, seed)
    state = LineState.empty()
    if past:
        state = apply_edit(g.rules, state, [Op.put(past, Iv(since, X, False, True))], "user")
    began = time.perf_counter()
    script, stats = g.run(state, X, ModeTimeline(0, "1").known(X))
    took = time.perf_counter() - began
    return script, stats, took


def in_cycle(region, lo, hi, period=2, n=6):
    """region is (X + lo + k period, X + hi + k period] for every k >= 0, read at points:
    inside, at both ends, and between."""
    for k in range(n):
        a, b = X + lo + k * period, X + hi + k * period
        if region.contains(a) or not region.contains(F(a + b) / 2) or not region.contains(b):
            return False
        if region.contains(b + F(period - (hi - lo), 2)):
            return False
    return not region.contains(X)


def after():
    return Region.of(Iv(X, INF, False, False))


class PerfectScriptTest(unittest.TestCase):
    def check(self, script, stats, took, expected):
        """The line has `expected` ({kind: {value: region}}) and nothing else after X, the
        script is the perfect one (final, definitive everywhere), found quickly."""
        line = script.base.line
        self.assertEqual(stats.chosen, "perfect script")
        self.assertTrue(stats.final)
        self.assertEqual(stats.definitive, INF)
        self.assertLess(took, FAST)
        self.assertEqual(sorted(line.kinds()), sorted(expected))
        for k, vals in expected.items():
            self.assertEqual(set(line.vals[k]), set(vals), k)
            for v, want in vals.items():
                got = line.vals[k][v] & after()
                if isinstance(want, tuple):
                    self.assertTrue(in_cycle(got, *want), f"{k}: {got}")
                else:
                    self.assertEqual(got, want, k)
        self.assertEqual({type(t).__name__ for t in script.rerun_triggers()}, {"Edited"})  # only the user's edits

    def test_one_element_at_100_percent(self):
        self.check(*perfect([], {"A": 1}), {"A": {100: after()}})

    def test_two_elements_at_half_are_both_at_50_percent(self):
        self.check(*perfect([], {"A": HALF, "B": HALF}), {"A": {50: after()}, "B": {50: after()}})

    def test_two_exclusive_elements_with_no_minimum_length_are_refused(self):
        with self.assertRaises(ValueError):
            generator(["B → no A"], {"A": HALF, "B": HALF})

    def test_two_exclusive_elements_alternate_by_their_minimum_length(self):
        first = set()
        for seed in range(8):
            with self.subTest(seed=seed):
                script, stats, took = perfect(["B → no A"], {"A": HALF, "B": HALF}, {"A": 1, "B": 1}, seed=seed)
                a, b = (0, 1), (1, 2)
                if not script.base.line.presence("A").contains(X + HALF):
                    a, b = b, a
                first.add("A" if a == (0, 1) else "B")
                self.check(script, stats, took, {"A": {100: a}, "B": {100: b}})
        self.assertEqual(first, {"A", "B"})  # drawn at random

    def test_the_element_just_before_the_chip_comes_last(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                script, stats, took = perfect(["B → no A"], {"A": HALF, "B": HALF}, {"A": 1, "B": 1}, past="A", seed=seed)
                self.check(script, stats, took, {"A": {100: (1, 2)}, "B": {100: (0, 1)}})
                self.assertTrue(script.base.line.presence("A").contains(X))  # the past stays

    def test_the_interval_at_the_chip_goes_on_to_its_minimum_length(self):
        """B at (X - 1/2, X]: B goes on to X + 1/2, then the cycle from there, B last (a run
        of the generator while the chip is in a piece must not cut it short)."""
        for seed in range(8):
            with self.subTest(seed=seed):
                script, stats, took = perfect(["B → no A"], {"A": HALF, "B": HALF}, {"A": 1, "B": 1}, past="B", seed=seed,
                                              since=X - HALF)
                line = script.base.line
                b = line.presence("B")
                self.assertTrue(b.contains(X + F(1, 4)) and b.contains(X + HALF))
                self.assertFalse(line.presence("A").contains(X + HALF))
                for k in range(5):  # A on (X + 1/2 + 2k, X + 3/2 + 2k], B on (X + 3/2 + 2k, X + 5/2 + 2k]
                    lo = X + HALF + 2 * k
                    self.assertTrue(line.presence("A").contains(lo + HALF) and line.presence("A").contains(lo + 1), k)
                    self.assertFalse(line.presence("A").contains(lo), k)
                    self.assertTrue(b.contains(lo + F(3, 2)) and b.contains(lo + 2), k)
                    self.assertFalse(b.contains(lo + HALF), k)
                self.assertLess(took, FAST)

    def test_the_same_seed_gives_the_same_script(self):
        lines = {str(perfect(["B → no A"], {"A": HALF, "B": HALF}, {"A": 1, "B": 1}, seed=5)[0].base.line.vals["A"][100])
                 for _ in range(3)}
        self.assertEqual(len(lines), 1)

    def test_a_user_element_after_the_chip_falls_back_to_the_search(self):
        g = generator([], {"A": 1})
        state = apply_edit(g.rules, LineState.empty(), [Op.put("A", Iv(5, 6))], "user")
        self.assertIsNone(g.plan.ops(state, X))
        _, stats = g.run(state, X, ModeTimeline(0, "1").known(X))
        self.assertNotEqual(stats.chosen, "perfect script")

    def test_other_rules_fall_back_to_the_search(self):
        for rules in (["A → B"], ["B → no A on [0, 1]"], ["mode 1 → A"]):
            with self.subTest(rules=rules):
                self.assertFalse(generator(rules, {"A": HALF, "B": HALF}, {"A": 1, "B": 1}).plan.ok)


class FillTest(unittest.TestCase):
    """Every target at ratio 1 (ratio_plan.Fill): wherever it can be after the chip, at
    once, whatever the other rules, the modes and the constraints."""

    def breaks(self, rules, q=0.5):
        from constraints import FifteenMinuteBreaks
        g = RulesGenerator(parse_rules(["Break15 → NoScreen", "mode 1 → no NoScreen"] + rules),
                           [(PresenceRatioProblem({"Task": 1}), 1), (MinimumLengthProblem({"Task": 1}), 1)], q,
                           constraints=[FifteenMinuteBreaks(length=2, pause=3)])
        began = time.perf_counter()
        stages = list(g.stages(LineState.empty(), F(0), ModeTimeline(0, "1").known(0)))
        return stages, time.perf_counter() - began

    def test_related_to_nothing_it_is_everywhere(self):
        stages, took = self.breaks([])
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[-1][0].base.line.presence("Task"), after_from(0))
        self.assertLess(took, 5)

    def test_it_is_everywhere_but_where_it_is_forbidden(self):
        """With "Break15 → no Task": every gap between the breaks, far past the window, at
        any q (the budget of candidates does not cut it)."""
        for q in (0, F(1, 2)):
            with self.subTest(q=q):
                stages, took = self.breaks(["Break15 → no Task"], q)
                line = stages[-1][0].base.line
                task, brk = line.presence("Task"), line.presence("Break15")
                self.assertTrue((task & brk).is_empty())
                for x in (F(5, 2), 4, F(101, 2), 1000, 10 ** 6 + F(1, 2)):  # breaks every 5: (0, 2], (5, 7], ...
                    self.assertNotEqual(task.contains(x), brk.contains(x), x)
                self.assertLess(took, 5)


def after_from(x):
    return Region.of(Iv(x, INF, False, False))


class SumTest(unittest.TestCase):
    """The presence ratio targets sum to 1, else the definitions are refused."""

    def test_targets_that_do_not_sum_to_1_are_refused(self):
        for targets in ({"A": 1, "B": 1}, {"A": 1, "B": HALF}, {"A": HALF}, {"A": F(1, 3), "B": F(1, 3)}):
            with self.subTest(targets=targets):
                with self.assertRaises(ValueError):
                    RulesGenerator(parse_rules([]), [(PresenceRatioProblem(targets), 1)], 0.5)

    def test_the_targets_of_all_the_problems_together(self):
        with self.assertRaises(ValueError):
            RulesGenerator(parse_rules([]), [(PresenceRatioProblem({"A": 1}), 1), (PresenceRatioProblem({"B": 1}), 1)], 0.5)

    def test_no_target_is_no_ratio_problem(self):
        self.assertEqual(PresenceRatioProblem({}).targets, {})

    def test_the_window_refuses_them(self):
        """The definitions of an example (start, as in screen_break_15_task.py with A and B
        at 1): refused, and no window opens."""
        import io
        import os
        import sys
        from contextlib import redirect_stdout
        from unittest import mock
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "UI"))
        import app
        out = io.StringIO()
        with mock.patch("gui.run") as run, redirect_stdout(out):
            app.start(rules=[], problems=[(PresenceRatioProblem({"Task": 1}), 1)])  # (valid: to check the mock)
            self.assertTrue(run.called)
            run.reset_mock()
            app.start(rules=[], problems=[(PresenceRatioProblem({"A": 1, "B": 1}), 1)])
        self.assertFalse(run.called)
        self.assertIn("the definitions are refused: the presence ratio targets sum to 2, not 1", out.getvalue())


class NoHorizonTest(unittest.TestCase):
    """The targets are on the whole line: a problem has no horizon, and the window of the
    generator's search (when there is no perfect script) is its own."""

    def test_a_problem_takes_no_horizon(self):
        with self.assertRaises(TypeError):
            PresenceRatioProblem({"A": 1}, 100)
        with self.assertRaises(TypeError):
            PresenceRatioProblem({"A": 1}, horizon=100)
        with self.assertRaises(TypeError):
            MinimumLengthProblem({"A": 1}, 100)

    def test_the_examples_give_no_horizon(self):
        import glob
        import os
        examples = glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "UI", "examples", "*.py"))
        self.assertTrue(examples)
        for f in examples:
            with self.subTest(os.path.basename(f)):
                with open(f, encoding="utf-8") as text:
                    self.assertNotIn("horizon=", text.read())

    def test_the_search_window_follows_the_definitions(self):
        from constraints import FifteenMinuteBreaks
        from generator import SMALLEST_WINDOW
        rules = ["C → D"]  # (no perfect script)
        self.assertEqual(generator(rules, {"A": HALF, "B": HALF}).window, SMALLEST_WINDOW)
        self.assertEqual(generator(rules, {"A": HALF, "B": HALF}, {"A": 1000}).window, 4000)  # 4 times the longest minimum length
        g = RulesGenerator(parse_rules(rules), [(PresenceRatioProblem({"A": HALF, "B": HALF}), 1)], 0.5,
                           constraints=[FifteenMinuteBreaks(length=900, pause=7200)])
        self.assertEqual(g.window, 2 * 8100)  # twice a break and its pause
        self.assertTrue(all(p.window == g.window for p, _ in g.problems))  # its problems are bound to it


if __name__ == "__main__":
    unittest.main()
