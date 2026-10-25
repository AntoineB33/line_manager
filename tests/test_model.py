"""The line, the modes and the element rules (requirement.md, first part)."""

import random
import unittest
from fractions import Fraction as F

from helpers import GRID, check_state, rand_interval, rand_set
from intervals import Interval, IntervalSet
from line import GEN, USER, LineState
from modes import ModeTimeline
from rules import parse_rule


class LineTest(unittest.TestCase):
    def test_requirement_state_example(self):
        x1, x2, x3 = F(0), F(2), F(5)
        s = LineState.from_entries([("A", Interval.closed_open(x1, x2), 100), ("A", Interval.point(x2), 50),
                                    ("B", Interval.point(x3), 100), ("C", Interval.point(x1), 100)])
        self.assertEqual(s.value_at("A", x1), 100)
        self.assertEqual(s.value_at("A", F(1)), 100)
        self.assertEqual(s.value_at("A", x2), 50)
        self.assertEqual(s.value_at("B", x3), 100)
        self.assertEqual(s.value_at("C", x1), 100)
        check_state(s)
        # the same type twice at one position is refused
        with self.assertRaises(ValueError):
            LineState.from_entries([("A", Interval.closed(x1, x2), 100), ("A", Interval.point(x2), 50)])

    def test_percentages(self):
        for bad in (0, -5, 101):
            with self.assertRaises(ValueError):
                LineState.from_entries([("A", Interval.closed(0, 1), bad)])

    def test_assign_keeps_uniqueness(self):
        rng = random.Random(5)
        s = LineState()
        for _ in range(300):
            t = rng.choice("AB")
            s.assign(t, rand_set(rng), F(rng.choice([0, 25, 50, 100])), rng.choice([USER, GEN]))
            check_state(s)

    def test_integral(self):
        s = LineState.from_entries([("A", Interval.closed(0, 2), 50), ("A", Interval.open(3, 4), 100)])
        self.assertEqual(s.integral("A", IntervalSet.of(Interval.closed(1, 10))), F(1, 2) + 1)


class ModesTest(unittest.TestCase):
    def test_one_mode_per_position(self):
        tl = ModeTimeline(0, "1", [(2, "2"), (F(5, 2), "3"), (7, "1")])
        known = tl.known(F(9))
        modes = {"1", "2", "3"}
        for x in GRID:
            if 0 <= x <= 9:
                holding = [m for m in modes if known.region(m).contains(x)]
                self.assertEqual(holding, [tl.mode_at(x)])
            else:
                self.assertFalse(any(known.region(m).contains(x) for m in modes))
        self.assertEqual(tl.mode_at(2), "2")  # the new mode applies from the transition on

    def test_transitions_validated(self):
        with self.assertRaises(ValueError):
            ModeTimeline(0, "1", [(2, "2"), (2, "1")])  # not strictly increasing
        with self.assertRaises(ValueError):
            ModeTimeline(0, "1", [(2, "1")])  # not a change
        with self.assertRaises(ValueError):
            ModeTimeline(0, "1", [(0, "2")])  # not after the start

    def test_knowledge_is_a_prefix(self):
        tl = ModeTimeline(0, "1", [(2, "2"), (4, "1")])
        k = tl.known(3)
        self.assertEqual(k.transitions, [(2, "2")])
        self.assertEqual(k.unknown(), IntervalSet.after(3))


def oracle_violated(rule, state, mode_of, x):
    """Brute force: is the rule violated at x? (offsets and pieces have endpoints on the
    1/2 grid, x on the 1/16 grid: a bad y in x + I exists iff one exists on the 1/32 grid)."""
    if rule.lhs_mode is not None and mode_of(x) != rule.lhs_mode:
        return False
    if any(state.value_at(t, x) == 0 for t in rule.lhs_types):
        return False
    for it in rule.rhs:
        lo, hi = x + it.rel.inf(), x + it.rel.sup()
        n = int((hi - lo) * 32)
        for k in range(n + 1):
            y = lo + F(k, 32)
            if it.rel.contains(y - x) and not it.allowed.ok(state.value_at(it.etype, y)):
                return True
    return False


class RulesTest(unittest.TestCase):
    def test_requirement_examples_parse(self):
        r = parse_rule("A → B")
        self.assertEqual((r.lhs_types, r.lhs_mode, r.rhs[0].etype, r.rhs[0].allowed.kind), ({"A"}, None, "B", "present"))
        self.assertEqual(parse_rule("A → no B").rhs[0].allowed.kind, "absent")
        r = parse_rule("A & B → C 3 units on the left")
        self.assertEqual((r.lhs_types, r.rhs[0].rel), ({"A", "B"}, IntervalSet.of(Interval.point(-3))))
        r = parse_rule("mode 1 → D & E")
        self.assertEqual((r.lhs_types, r.lhs_mode, [i.etype for i in r.rhs]), (frozenset(), "1", ["D", "E"]))
        self.assertEqual(parse_rule("A → A at -1").rhs[0].rel, IntervalSet.of(Interval.point(-1)))
        self.assertEqual(parse_rule("A -> B 50% on [0, 2)").rhs[0].allowed.value, 50)

    def test_violations_match_brute_force(self):
        rng = random.Random(7)
        texts = ["A → B", "A → no B", "A & B → C 3 units on the left", "mode 1 → D & E", "A → A at -1",
                 "A & mode 2 → B 50% on [0, 1)", "B → no A on (-1, 1]"]
        tl = ModeTimeline(-8, "1", [(-3, "2"), (F(1, 2), "1"), (4, "2")])
        known = tl.known(8)
        for _ in range(40):
            state = LineState()
            for t in "ABCDE":
                for _ in range(rng.randint(0, 2)):
                    iv = rand_interval(rng)
                    if not (state.support(t) & IntervalSet.of(iv)):
                        state.assign(t, IntervalSet.of(iv), F(rng.choice([50, 100])), USER)
            for text in texts:
                r = parse_rule(text)
                v = r.violations(state, known.region(r.lhs_mode) if r.lhs_mode else IntervalSet.universe())
                for x in GRID[::2]:
                    if -8 <= x <= 8:
                        self.assertEqual(v.contains(x), oracle_violated(r, state, tl.mode_at, x), (text, x, str(state)))


if __name__ == "__main__":
    unittest.main()
