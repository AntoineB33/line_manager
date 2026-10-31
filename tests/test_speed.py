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
from solver import Line  # noqa: E402
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


def long_way(rs, history):
    with mock.patch.object(rs, "pointwise", False), mock.patch.object(resolve, "_near", lambda *a: None), \
            mock.patch.object(resolve, "_local_line", lambda *a: None):
        return result(rs, history)


class OriginTest(unittest.TestCase):
    """Where 0 is means nothing."""
    POOL = ["A → B or C", "A → C or B", "B → no C", "C → D", "D → no B", "C → no B 1 unit on the right",
            "B → no B 1 unit on the right", "A & not B → D", "X & not A → B", "A → D 2 units on the right",
            "A → B on [0, 1] of l", "C → no D 1 unit on the left", "A → C or D 1 unit on the right"]

    def test_the_same_line_elsewhere(self):
        rng = random.Random(61)
        points = [F(k, 4) for k in range(-40, 120)]
        checked = 0
        for _ in range(120):
            try:
                rs = parse_rules(rng.sample(self.POOL, rng.randint(2, 5)))
            except RuleError:
                continue
            spans = [(rng.choice([Op.put, Op.put, Op.put_no, Op.take]), rng.choice("ABCDX"), rng.randint(0, 10), rng.randint(0, 4))
                     for _ in range(rng.randint(1, 4))]

            def at(shift):
                state, refused = LineState.empty(), []
                for op, k, a, n in spans:
                    try:
                        state = apply_edit(rs, state, [op(k, Interval.closed(a + shift, a + n + shift))], USER)
                    except Refused:
                        refused.append((k, a))
                return refused, sorted((k, [x for x in points if state.line.presence(k).contains(x + shift)]) for k in state.line.vals)

            self.assertEqual(at(0), at(10 ** 9), [str(r) for r in rs.rules])
            checked += 1
        self.assertGreater(checked, 60)

    def test_the_time_does_not_depend_on_the_position(self):
        rs = parse_rules(["A → B or C", "C → no B 1 unit on the right"])
        real, sizes = solver.Lattice.__init__, []

        def counted(self, lp, r):
            real(self, lp, r)
            sizes.append(self.kmax - self.kmin)

        with mock.patch.object(solver.Lattice, "__init__", counted):
            apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(10 ** 9, 10 ** 9 + 10))], USER)
        self.assertLess(max(sizes), 100)


class NearTest(unittest.TestCase):
    """With distances, an edit that only adds is solved on a window around it."""
    POOL = ["A → B or C", "A → C or B", "C → no B 1 unit on the right", "B → no B 1 unit on the right", "B → no C", "C → D",
            "A → D 2 units on the right", "C → no D 1 unit on the left", "A → C or D 1 unit on the right", "D → no B",
            "B → D 3 units on the left", "A → B on [0, 1] of p"]

    def test_the_same_as_the_whole_line(self):
        rng = random.Random(67)
        checked = 0
        for _ in range(200):
            try:
                rs = parse_rules(rng.sample(self.POOL, rng.randint(2, 6)))
            except RuleError:
                continue
            if rs.pointwise:
                continue  # PointwiseTest
            history = []
            for _ in range(rng.randint(3, 7)):
                a = rng.randint(-20, 90)
                iv = Interval.closed(a, a + rng.randint(0, 25))
                history.append([rng.choice([Op.put, Op.put, Op.put, Op.put_no, Op.put_no, Op.take])(rng.choice("ABCD"), iv)])
            self.assertEqual(result(rs, history), long_way(rs, history), ([str(r) for r in rs.rules], [str(o) for o in map(lambda h: h[0], history)]))
            checked += 1
        self.assertGreater(checked, 100)

    def test_it_does_not_depend_on_what_is_elsewhere(self):
        rs = parse_rules(["A → B or C", "C → no B 1 unit on the right"])
        state = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(4 * k, 4 * k + 1)) for k in range(100)], USER)
        seen, real = [], resolve.solve

        def counted(rs, prob):
            seen.append(sum(len(r.core.intervals) for d in prob.pos.values() for r in d.values()))
            return real(rs, prob)

        with mock.patch.object(resolve, "solve", counted):
            after = apply_edit(rs, state, [Op.put("A", Interval.closed(2, 3))], USER)
        self.assertLess(max(seen), 30)
        self.assertEqual(after.line, solver.solve(rs, after.claims.problem()))

    def test_a_chain_that_goes_far(self):
        """The edit changes every position of A after it: the window has to give up."""
        rs = parse_rules(["A → B or C", "C → no B 1 unit on the right"])
        state = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 1000))], USER)
        after = apply_edit(rs, state, [Op.put_no("B", Interval.point(3))], USER)
        self.assertTrue(after.line.presence("C").contains(1000))

    def test_taking_away_goes_the_long_way(self):
        """A "no B" gone can change a choice far on the left, through a chain the same
        near the edit."""
        rs = parse_rules(["A → B or C", "C → no B 1 unit on the right"])
        state = LineState.empty()
        for ops in ([Op.put("A", Interval.closed(0, 200))], [Op.put_no("B", Interval.point(3))], [Op.take_no("B", Interval.point(3))]):
            state = apply_edit(rs, state, ops, USER)
        self.assertEqual(state.line.presence("B"), Region.of(Interval.closed(0, 200)))


class PointwiseTest(unittest.TestCase):
    """No position acts on another one, even with "not": an edit computes its positions."""
    POOL = ["X & not A → B", "not C & D → no E", "A & not B → C", "A → B or C", "B → no C", "A 0% & X → D", "C → D",
            "X & not D → no A", "A → B", "E → no X"]

    def test_each_position_as_if_it_were_alone(self):
        """No more than necessary is lost: the long way on one position is exact, and
        with the others it can remove whole elements, which is allowed but not needed."""
        rng = random.Random(71)
        checked = 0
        for _ in range(70):
            try:
                rs = parse_rules(rng.sample(self.POOL, rng.randint(2, 5)))
            except RuleError:
                continue
            self.assertTrue(rs.pointwise)
            state, kept = LineState.empty(), []
            for _ in range(rng.randint(3, 7)):
                a = rng.randint(0, 14)
                span = (rng.choice([Op.put, Op.put, Op.put, Op.put_no, Op.take, Op.take_no]), rng.choice("ABCDEX"), a, a + rng.randint(0, 6))
                try:
                    state = apply_edit(rs, state, [span[0](span[1], Interval.closed(a, span[3]))], USER)
                    kept.append(span)
                except Refused:
                    pass
            for x in (F(k, 2) for k in range(0, 42)):
                alone = LineState.empty()
                for op, k, lo, hi in kept:
                    if lo <= x <= hi:
                        with mock.patch.object(rs, "pointwise", False), mock.patch.object(resolve, "_local_line", lambda *a: None):
                            alone = apply_edit(rs, alone, [op(k, Interval.point(x))], USER)
                self.assertEqual({k for k in state.line.vals if state.line.presence(k).contains(x)},
                                 {k for k in alone.line.vals if alone.line.presence(k).contains(x)}, ([str(r) for r in rs.rules], kept, x))
            checked += 1
        self.assertGreater(checked, 40)

    def test_one_solve_per_edit_there_only(self):
        rs = parse_rules(["X & not A → B"])
        state = LineState.empty()
        for k in range(30):
            state = apply_edit(rs, state, [Op.put("A", Interval.closed(40 * k, 40 * k + 10))], USER)
        real, count = resolve.solve, [0]

        def counted(*a, **k):
            count[0] += 1
            return real(*a, **k)

        with mock.patch.object(resolve, "solve", counted):
            apply_edit(rs, state, [Op.put("A", Interval.closed(5000, 5010))], USER)
        self.assertLessEqual(count[0], 3)


class LocalTest(unittest.TestCase):
    """A position only acts on itself: an edit solves its own positions only."""

    def test_only_when_a_position_only_acts_on_itself(self):
        for rule in ("A → B", "A → B or C", "A & B → no C"):
            self.assertTrue(parse_rules([rule]).local, rule)
        for rule in ("A → B 1 unit on the right of p", "A → B on [0, 1] of p", "A → B on [0, 1] of l", "not A → B"):
            self.assertFalse(parse_rules([rule]).local, rule)

    def test_the_same_as_the_whole_line(self):
        rng = random.Random(53)
        tail = Region(135, 135, IntervalSet(), right=IntervalSet.of(Interval.closed_open(0, 15)), pr=F(135))
        checked = 0
        for _ in range(150):
            try:
                rs = parse_rules(rng.sample(POOL, rng.randint(2, 6)))
            except RuleError:
                continue
            self.assertTrue(rs.local)
            history = edits(rng)
            first = rng.choice([[], [Op.put("A", tail)], [Op.put("C", tail)]])
            if first and rs.cyclic:
                continue  # the long way is wrong there: test_a_cycle_over_what_repeats
            def go():
                state, out = LineState.empty(), []
                if first:
                    try:
                        state = apply_edit(rs, state, first, GEN)
                    except Refused:
                        return "refused at first"
                for ops in history:
                    owner = rng2.choice([USER, USER, GEN])
                    try:
                        state = apply_edit(rs, state, ops, owner)
                        out.append(state.line)
                    except Refused:
                        out.append("refused")
                return out
            rng2 = random.Random(1)
            fast = go()
            rng2 = random.Random(1)
            with mock.patch.object(resolve, "_local_line", lambda *a: None):  # PointwiseTest checks the rest
                slow = go()
            self.assertEqual(fast, slow, ([str(r) for r in rs.rules], [[str(o) for o in ops] for ops in history]))
            checked += 1
        self.assertGreater(checked, 80)

    @unittest.expectedFailure
    def test_a_cycle_over_what_repeats(self):
        """A known bug of the sweep (older than these shortcuts): A → D or E and E → A hold
        each other up after the end of A's claim, with nothing placing them there."""
        rs = parse_rules(["C → no E", "A → D or E", "B & D → E", "E → A", "C → D", "A → B or C"])
        tail = Region(135, 135, IntervalSet(), right=IntervalSet.of(Interval.closed_open(0, 15)), pr=F(135))
        self.assertFalse(solver.solve(rs, solver.Problem({"A": {F(100): tail}}, {})).presence("A").contains(160))

    def test_an_edit_that_comes_back(self):
        """Removing the newer "no K" lets the older K apply again."""
        rs = parse_rules(["A → B"])
        state = LineState.empty()
        for op in (Op.put("A", Interval.closed(0, 5)), Op.put_no("A", Interval.closed(1, 2)), Op.take_no("A", Interval.closed(1, 2))):
            state = apply_edit(rs, state, [op], USER)
        self.assertEqual(state.line.presence("B"), Region.of(Interval.closed(0, 5)))

    def test_it_does_not_depend_on_what_is_elsewhere(self):
        """What is solved for the edit: its own positions, not the 40 intervals before."""
        rs = parse_rules(EXAMPLE)
        state = LineState.empty()
        for k in range(40):
            state = apply_edit(rs, state, [Op.put("NoScreen", Interval.closed(40 * k, 40 * k + 10))], USER)
        self.assertTrue(state.claims.clean)
        seen = []
        real = resolve.solve

        def counted(rs, prob):
            seen.append(sum(len(r.core.intervals) for d in prob.pos.values() for r in d.values()))
            return real(rs, prob)

        with mock.patch.object(resolve, "solve", counted):
            after = apply_edit(rs, state, [Op.put("NoScreen", Interval.closed(2000, 2010))], USER)
        self.assertLessEqual(max(seen), 2)
        self.assertEqual(after.line, resolve.rebuild(rs, after.history, resolve.owned_by_others(after.claims), Line({})).line)


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
        """Newer over older, percentages, "no K", removals, the script's elements."""
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
