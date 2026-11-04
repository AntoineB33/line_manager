"""The best line of the presence ratio targets and minimum lengths
(rules_generator_requirements.md; scripts/ratio_plan.py): the consequences listed in the
requirements, found at once."""

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
    problems = [(PresenceRatioProblem(targets, 100), 1)]
    if lengths:
        problems.append((MinimumLengthProblem(lengths, 100), 1))
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


if __name__ == "__main__":
    unittest.main()
