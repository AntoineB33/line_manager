"""When the memory limit is reached, the far left of the chip is flattened, then removed."""

import os
import sys
import unittest
from fractions import Fraction as F

sys.path.insert(0, os.path.dirname(__file__))

from helpers import Interval  # noqa: E402

from generator import RulesGenerator  # noqa: E402
from line import FLAT, USER, LineState, Op  # noqa: E402
from modes import ModeTimeline  # noqa: E402
from problems import PresenceRatioProblem  # noqa: E402
from region import Region  # noqa: E402
from resolve import apply_edit, flattened  # noqa: E402
from rules import parse_rules  # noqa: E402
from simulation import Chip, Simulation  # noqa: E402
from solver import solve  # noqa: E402

RULES = ["A → B or C", "C → no B 1 unit on the right"]


def crowded(rs, n=20):
    state = LineState.empty()
    for k in range(n):
        state = apply_edit(rs, state, [Op.put("A", Interval.closed(4 * k, 4 * k + 1))], USER)
    return state


class FlattenedTest(unittest.TestCase):
    def test_the_line_does_not_change(self):
        rs = parse_rules(RULES)
        state = crowded(rs)
        flat = flattened(rs, state, F(40))
        self.assertEqual(flat.line, state.line)
        self.assertEqual(solve(rs, flat.claims.problem()), state.line)

    def test_no_layer_is_left(self):
        rs = parse_rules(RULES)
        flat = flattened(rs, crowded(rs), F(40))
        self.assertEqual(len(flat.history), 10)
        for r in flat.history:
            self.assertFalse(r.footprint & Region.of(Interval.closed(-100, 39)))
        self.assertLess(flat.memory(), crowded(rs).memory() + 1000)
        self.assertTrue(all(o == FLAT for k in flat.claims.pos for (_, o) in flat.claims.pos[k]
                            if flat.claims.pos[k][(_, o)] & Region.of(Interval.closed(-100, 39))))

    def test_the_rules_do_not_choose_again(self):
        """C was at 3 because of a "no B": its reason is gone, but the line stays."""
        rs = parse_rules(["A → B or C"])
        state = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 5))], USER)
        state = apply_edit(rs, state, [Op.put_no("B", Interval.point(3))], USER)
        flat = flattened(rs, state, F(10))
        after = apply_edit(rs, flat, [Op.put("A", Interval.closed(20, 21))], USER)
        self.assertTrue(after.line.presence("C").contains(F(3)))
        self.assertFalse(after.line.presence("B").contains(F(3)))

    def test_nothing_comes_back_there(self):
        """The reverse logic is degraded: on the flat stretch, what gives way is gone."""
        rs = parse_rules(["A → B"])
        state = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 5))], USER)
        flat = flattened(rs, state, F(10))
        after = apply_edit(rs, flat, [Op.put_no("A", Interval.closed(1, 2))], USER)
        self.assertFalse(after.line.presence("A").contains(F(3, 2)))
        back = apply_edit(rs, after, [Op.take_no("A", Interval.closed(1, 2))], USER)
        self.assertFalse(back.line.presence("A").contains(F(3, 2)))
        # on what is not flat, it still comes back
        layered = apply_edit(rs, state, [Op.put_no("A", Interval.closed(1, 2))], USER)
        layered = apply_edit(rs, layered, [Op.take_no("A", Interval.closed(1, 2))], USER)
        self.assertTrue(layered.line.presence("A").contains(F(3, 2)))

    def test_what_is_right_of_it_works_as_before(self):
        rs = parse_rules(RULES)
        state = crowded(rs)
        flat = flattened(rs, state, F(40))
        for s in (state, flat):
            s = apply_edit(rs, s, [Op.put_no("B", Interval.point(F(60)))], USER)
            self.assertTrue(s.line.presence("C").contains(F(60)))
            s = apply_edit(rs, s, [Op.take_no("B", Interval.point(F(60)))], USER)
            self.assertTrue(s.line.presence("B").contains(F(60)))

    def test_rules_across_the_boundary(self):
        rs = parse_rules(["A → B 3 units on the right"])
        state = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 8))], USER)
        flat = flattened(rs, state, F(5))
        self.assertEqual(flat.line, state.line)
        after = apply_edit(rs, flat, [Op.put("A", Interval.closed(30, 31))], USER)
        self.assertEqual(after.line.presence("B"), Region.of(Interval.closed(3, 11)) | Region.of(Interval.closed(33, 34)))

    def test_removed(self):
        rs = parse_rules(RULES)
        state = crowded(rs)
        flat = flattened(rs, state, F(40), F(20))
        self.assertFalse(flat.line.presence("A").contains(F(16)))
        self.assertTrue(flat.line.presence("A").contains(F(24)))
        self.assertTrue(flat.line.presence("A").contains(F(60)))
        after = apply_edit(rs, flat, [Op.put("A", Interval.closed(100, 101))], USER)
        self.assertFalse(after.line.presence("A").contains(F(16)))


class SimulationTest(unittest.TestCase):
    def sim(self, rs, limit):
        s = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 4), 0.5), Chip([(0, 0), (1000, 1000)]), ModeTimeline(0, "1"))
        s.memory_limit = limit
        return s

    def test_it_stays_under_the_limit_left_of_the_chip_only(self):
        rs = parse_rules(RULES)
        s = self.sim(rs, 60000)
        s.advance(F(500))
        peak = 0
        for k in range(120):
            self.assertIsNone(s.edit_now([Op.put("A", Interval.closed(4 * k, 4 * k + 1))]))
            peak = max(peak, s.state.memory())
        self.assertLessEqual(s.state.memory(), 60000)
        self.assertLessEqual(peak, 60000)
        self.assertIsNotNone(s.flat_to)
        self.assertLess(s.flat_to, 500)
        self.assertTrue(s.state.line.presence("A").contains(F(4 * 119)))

    def test_it_removes_when_flattening_is_not_enough(self):
        rs = parse_rules(RULES)
        s = self.sim(rs, 12000)
        s.advance(F(500))
        for k in range(100):
            self.assertIsNone(s.edit_now([Op.put("A", Interval.closed(4 * k, 4 * k + 1))]))
        self.assertIsNotNone(s.gone_to)
        self.assertFalse(s.state.line.presence("A").contains(F(0)))

    def test_nothing_right_of_the_chip_goes(self):
        rs = parse_rules(RULES)
        s = self.sim(rs, 3000)
        for k in range(30):
            self.assertIsNone(s.edit_now([Op.put("A", Interval.closed(4 * k + 1, 4 * k + 2))]))
        self.assertIsNone(s.flat_to)
        self.assertTrue(s.state.line.presence("A").contains(F(1)))

    def test_under_the_limit_nothing_happens(self):
        rs = parse_rules(RULES)
        s = self.sim(rs, 10 ** 9)
        s.advance(F(500))
        s.edit_now([Op.put("A", Interval.closed(0, 1))])
        self.assertIsNone(s.flat_to)
        self.assertEqual(len(s.state.history), 1)


if __name__ == "__main__":
    unittest.main()
