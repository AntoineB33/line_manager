"""What makes an edit fast gives what the long way gives."""

import os
import random
import sys
import unittest
from fractions import Fraction as F
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "UI"))

from helpers import Interval, IntervalSet, Region  # noqa: E402

import resolve  # noqa: E402
import solver  # noqa: E402
from constraints import FifteenMinuteBreaks  # noqa: E402
from line import GEN, USER, LineState, Op  # noqa: E402
from problems import PresenceRatioProblem  # noqa: E402
from resolve import Refused, apply_edit  # noqa: E402
from app import Session  # noqa: E402
from rules import RuleError, parse_rules  # noqa: E402

EXAMPLE = ["Break15 → NoScreen", "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
           "NotOnComputer → no NoComputerUnlocked", "NotOnPhone → no NoPhoneUnlocked"]
POOL = ["A → B or C", "A → C or B", "B → no C", "C → D", "D → no B", "B & D → E", "A → D or E", "C → no E", "E → A"]


def edits(rng):
    out = []
    for _ in range(rng.randint(2, 5)):
        a = rng.randint(-3, 12)
        iv = Interval.closed(a, a + rng.randint(0, 4))
        out.append([rng.choice([Op.put, Op.put, Op.put, Op.put_no, Op.take])(rng.choice("ABCDE"), iv)])
    return out


def result(rs, history):
    state, out = LineState.empty(), []
    for ops in history:
        try:
            state = apply_edit(rs, state, ops, USER)
            out.append(sorted((k, str(v), str(r)) for k, d in state.line.vals.items() for v, r in d.items()))
        except Refused:
            out.append("refused")
    return out


class FreeStepTest(unittest.TestCase):
    """No rule has a distance: any step is exact."""

    def test_the_same_as_step_1(self):
        rng = random.Random(41)
        checked = 0
        for _ in range(80):
            try:
                rs = parse_rules(rng.sample(POOL, rng.randint(2, 6)))
            except RuleError:
                continue
            history = edits(rng)
            with mock.patch.object(solver.LatticeProblem, "_free_step", staticmethod(lambda regions: F(1))):
                slow = result(rs, history)
            self.assertEqual(result(rs, history), slow, [str(r) for r in rs.rules])
            checked += 1
        self.assertGreater(checked, 40)

    def test_over_what_repeats(self):
        rs = parse_rules(EXAMPLE)
        tail = Region(135, 135, IntervalSet(), right=IntervalSet.of(Interval.closed_open(0, 15)), pr=F(135))
        ops = [Op.put("Break15", tail), Op.put("NoScreen", Interval.closed(50, 60))]
        fast = result(rs, [ops])
        with mock.patch.object(solver.LatticeProblem, "_free_step", staticmethod(lambda regions: F(1))):
            self.assertEqual(result(rs, [ops]), fast)

    def test_the_step_is_the_period(self):
        tail = Region(135, 135, IntervalSet(), right=IntervalSet.of(Interval.closed_open(0, 15)), pr=F(135))
        self.assertEqual(solver.LatticeProblem._free_step({"K": {"fixed": tail}}), 135)
        self.assertEqual(solver.LatticeProblem._free_step({"K": {"fixed": Region.of(Interval.closed(2, 5))}}), 2 * 7)


class OneSolveTest(unittest.TestCase):
    def test_the_same_as_edit_by_edit(self):
        rng = random.Random(43)
        checked = 0
        for _ in range(120):
            try:
                rs = parse_rules(rng.sample(POOL, rng.randint(2, 6)))
            except RuleError:
                continue
            self.assertTrue(rs.monotone)
            history = edits(rng)
            with mock.patch.object(resolve, "_no_contradiction", lambda *a: None):
                slow = result(rs, history)
            self.assertEqual(result(rs, history), slow, [str(r) for r in rs.rules])
            checked += 1
        self.assertGreater(checked, 60)

    def test_only_with_rules_that_only_add_contradictions(self):
        for rule in ("X & not A → no B", "A 0% → B", "A < 50% → B", "A → B on [0, 1] of l"):
            self.assertFalse(parse_rules([rule]).monotone, rule)
        for rule in ("A → B", "A & B → no C", "A → B or C", "A → B 3 units on the right of p"):
            self.assertTrue(parse_rules([rule]).monotone, rule)

    def test_the_cost_does_not_grow_with_the_edits(self):
        rs = parse_rules(EXAMPLE)
        state = LineState.empty()
        for k in range(20):
            state = apply_edit(rs, state, [Op.put("NoScreen", Interval.closed(40 * k, 40 * k + 10))], USER)
        real, count = resolve.solve, [0]

        def counted(*a, **k):
            count[0] += 1
            return real(*a, **k)

        with mock.patch.object(resolve, "solve", counted):
            apply_edit(rs, state, [Op.put("NoScreen", Interval.closed(900, 910))], USER)
        self.assertLessEqual(count[0], 2)  # the edit alone, and the line: not one per edit


class NothingReadsItTest(unittest.TestCase):
    """"Other" is in no rule, no constraint and no problem."""

    def session(self):
        s = Session(EXAMPLE + ["mode 1 → no NoScreen"], [(0, "1")], 0, 1, [], [(PresenceRatioProblem({"Wanted": F(1, 4)}, 240), 1)],
                    F(1, 2), [FifteenMinuteBreaks()])
        s.sim.background = True
        s.sim.start()
        return s

    def test_no_calculation(self):
        s = self.session()
        s.sim.calculate = lambda *a: self.fail("no process")
        kinds = s.sim.gen.kinds_read()
        for name in ("solve", "_alone", "rebuild"):
            patcher = mock.patch.object(resolve, name, lambda *a, **k: self.fail(name))
            patcher.start()
            self.addCleanup(patcher.stop)
        before = s.sim.state.line
        request = s.sim.request
        s.sim.request = None
        self.assertNotIn("Other", kinds)
        self.assertIn("done", s.command("add Other on [50, 51]"))
        self.assertTrue(s.sim.state.line.presence("Other").contains(50))
        self.assertEqual(s.sim.state.line.vals["Break15"], before.vals["Break15"])
        self.assertIsNone(s.sim.request)  # no run
        s.sim.request = request

    def test_what_is_read_still_runs(self):
        s = self.session()
        s.sim.request = None
        s.sim.script = s.sim.gen.run(s.sim.state, F(0), s.sim.known(s.sim.t))[0]
        s.sim._was_true, s.sim._fired = [False] * len(s.sim.script.events), [False] * len(s.sim.script.events)
        self.assertIn("the user edits what the rules, the constraints or the problems read", s.sim.script.text())
        s.command("add Other on [50, 51]")
        self.assertIsNone(s.sim.request)
        s.command("add Wanted on [50, 51]")  # a problem reads it
        self.assertIsNotNone(s.sim.request)
        s.sim.request = None
        s.command("add NoScreen on [70, 71]")  # a rule reads it
        self.assertIsNotNone(s.sim.request)

    def test_the_same_as_the_long_way(self):
        """Newer over older, percentages, "no K", removals, the script's elements, undone."""
        rng = random.Random(47)
        rs = parse_rules(["A → B"])
        for _ in range(60):
            fast, slow = LineState.empty(), LineState.empty()
            fast = slow = apply_edit(rs, fast, [Op.put("K", Interval.closed(2, 6), F(25))], GEN)
            for _ in range(rng.randint(1, 6)):
                a = rng.randint(0, 8)
                iv = Interval.closed(a, a + rng.randint(0, 4))
                ops = rng.choice([[Op.put("K", iv, F(rng.choice([50, 100])))], [Op.put_no("K", iv)], [Op.take("K", iv)], [Op.take_no("K", iv)],
                                  [Op.put("A", iv)]])
                try:
                    want = apply_edit(rs, slow, ops, USER) if ops[0].kind in rs.ruled else self.long_way(rs, slow, ops)
                except Refused:
                    want = None
                try:
                    got = apply_edit(rs, fast, ops, USER)
                except Refused:
                    got = None
                self.assertEqual(got is None, want is None)
                if got is not None:
                    self.assertEqual(got.line, want.line, [str(o) for o in ops])
                    self.assertIs(got.claims.same(want.claims), True)
                    fast, slow = got, want

    @staticmethod
    def long_way(rs, state, ops):
        with mock.patch.object(resolve, "_apart", side_effect=Exception):
            rec = resolve.Record.of(ops)
            resolve._alone(rs, rec.claims.problem())
            return resolve.rebuild(rs, state.history + (rec,), resolve.owned_by_others(state.claims), state.line)


if __name__ == "__main__":
    unittest.main()
