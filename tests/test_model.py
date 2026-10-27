"""The line, the regions, the modes and the rules (requirement.md, first part)."""

import random
import unittest
from fractions import Fraction as F

from helpers import GRID, rand_set
from intervals import EPS, INF, Interval, IntervalSet, Pos
from line import Op
from modes import ModeTimeline
from region import IncompatiblePeriods, Region
from resolve import initial_state
from rules import TRUE, And, Atom, RuleError, parse_rule, parse_rules
from solver import lhs_region


class LineTest(unittest.TestCase):
    def test_requirement_state_example(self):
        x1, x2, x3 = F(0), F(2), F(5)
        rs = parse_rules([])
        s = initial_state(rs, [("A", Interval.closed_open(x1, x2), 100), ("A", Interval.point(x2), 50),
                               ("B", Interval.point(x3), 100), ("C", Interval.point(x1), 100)])
        self.assertEqual(s.line.value_at("A", x1), 100)
        self.assertEqual(s.line.value_at("A", F(1)), 100)
        self.assertEqual(s.line.value_at("A", x2), 50)
        self.assertEqual(s.line.value_at("B", x3), 100)
        self.assertEqual(s.line.value_at("C", x1), 100)
        with self.assertRaises(ValueError):  # the same kind twice at one position
            initial_state(rs, [("A", Interval.closed(x1, x2), 100), ("A", Interval.point(x2), 50)])

    def test_percentages(self):
        for bad in (0, -5, 101):
            with self.assertRaises(ValueError):
                Op.put("A", Interval.closed(0, 1), bad)


class RegionTest(unittest.TestCase):
    def rand_region(self, rng):
        r = Region.of(rand_set(rng))
        if rng.random() < 0.3:
            r = r | Region.of(Interval(F(rng.randint(-4, 4)), INF, rng.random() < 0.5, False))
        if rng.random() < 0.4:
            P = F(rng.randint(1, 4), 2)
            pat = IntervalSet.of(Interval(F(0), P / 2, True, False))
            r = r | Region(F(rng.randint(-3, 3)), F(rng.randint(3, 6)), IntervalSet(), P, pat if rng.random() < 0.5 else IntervalSet(), P, pat)
        return r

    def test_algebra_pointwise(self):
        rng = random.Random(1)
        pts = [Pos(F(i, 8), k) for i in range(-30 * 8, 30 * 8, 5) for k in (-1, 0, 1)]
        for _ in range(200):
            a, b = self.rand_region(rng), self.rand_region(rng)
            d = F(rng.randint(-6, 6), 2)
            u, i, s, c, t = a | b, a & b, a - b, a.complement(), a.shift(d)
            for x in pts:
                ax, bx = a.contains(x), b.contains(x)
                self.assertEqual(u.contains(x), ax or bx)
                self.assertEqual(i.contains(x), ax and bx)
                self.assertEqual(s.contains(x), ax and not bx)
                self.assertEqual(c.contains(x), not ax)
                self.assertEqual(t.contains(x), a.contains(x - d))
            self.assertEqual(a | b, b | a)
            self.assertTrue((a & b).issubset(a))

    def test_left_edges_dilate_components(self):
        rng = random.Random(2)
        pts = [Pos(F(i, 8), k) for i in range(-12 * 8, 12 * 8, 3) for k in (-1, 0, 1)]
        for _ in range(150):
            a = self.rand_region(rng)
            e = a.left_edges()
            lo, hi = sorted(F(rng.randint(-6, 6), 2) for _ in range(2))
            iv = Interval.make(lo, hi, rng.random() < 0.5 or lo == hi, rng.random() < 0.5 or lo == hi) or Interval.point(lo)
            d = a.dilate(iv)
            ts = [t for t in (Pos(lo + F(j, 16), k) for j in range(int((hi - lo) * 16) + 1) for k in (-2, -1, 0, 1, 2)) if iv.contains(t)]
            comps = a.components()
            msg = (str(a), str(iv))
            for x in pts:
                ax = a.contains(x)
                # the left edge of a largest interval: in it, and the position just before is not
                self.assertEqual(e.contains(x), ax and not a.contains(x - EPS), (msg, x))
                self.assertEqual(d.contains(x), any(a.contains(x - t) for t in ts), (msg, x))
                self.assertEqual(sum(1 for c in comps if c.contains(x)), 1 if ax else 0, (msg, x))
        r = Region.of(IntervalSet.of(Interval.closed_open(0, 1), Interval.open(1, 3), Interval.point(5)))
        self.assertEqual(r.left_edges(), Region.of(IntervalSet.of(Interval.point(0), Interval.point(Pos(1, 1)), Interval.point(5))))
        self.assertEqual(Region.of(Interval(-INF, F(2), False, False)).left_edges(), Region.empty())  # no left edge
        self.assertEqual(r.dilate(Interval(F(0), INF, True, False)), Region.of(Interval(F(0), INF, True, False)))
        self.assertEqual(r.components_meeting(Region.of(Interval.closed(1, 5))), 2)
        self.assertEqual(r.components_meeting(Region.of(Interval.closed(0, 5))), 3)

    def test_period_with_an_epsilon_part(self):
        """B at n + nε for every n >= 0: a pattern repeating every 1+ε."""
        b = Region(Pos(0), Pos(1, 1), IntervalSet.of(Interval.point(0)), Pos(1), IntervalSet(), Pos(1, 1), IntervalSet.of(Interval.point(0)))
        for n in range(-3, 30):
            self.assertEqual(b.contains(Pos(n, n)), n >= 0)
            self.assertFalse(b.contains(Pos(n, n + 1)) or b.contains(Pos(n, n - 1)) or b.contains(Pos(F(2 * n + 1, 2))))
        c = Region.of(Interval(F(0), INF, True, False)) - b  # with a constant tail: any period goes
        self.assertTrue(c.contains(Pos(5, 4)) and not c.contains(Pos(5, 5)))
        self.assertEqual(b.shift(Pos(1, 1)), b - Region.point(0))
        alt = Region(Pos(0), Pos(1), IntervalSet(), Pos(1), IntervalSet(), Pos(2), IntervalSet.of(Interval.closed_open(0, 1)))
        with self.assertRaises(IncompatiblePeriods):  # every 1+ε and every 2: no common period
            b | alt


class ModesTest(unittest.TestCase):
    def test_one_mode_per_position(self):
        tl = ModeTimeline(0, "1", [(2, "2"), (F(5, 2), "3"), (7, "1")])
        known = tl.known(F(9))
        for x in GRID:
            holding = [m for m in "123" if known.region(m).contains(x)]
            self.assertEqual(holding, [tl.mode_at(x)] if 0 <= x <= 9 else [])
        self.assertEqual(tl.mode_at(2), "2")  # the new mode applies from the transition on

    def test_transitions_validated(self):
        for bad in ([(2, "2"), (2, "1")], [(2, "1")], [(0, "2")]):
            with self.assertRaises(ValueError):
                ModeTimeline(0, "1", bad)

    def test_knowledge_is_a_prefix(self):
        k = ModeTimeline(0, "1", [(2, "2"), (4, "1")]).known(3)
        self.assertEqual(k.transitions, [(2, "2")])


class RulesTest(unittest.TestCase):
    def test_requirement_examples_parse(self):
        r = parse_rule("A → B 100%")
        o = r.rhs[0].options[0]
        self.assertEqual((r.lhs, o.kind, o.rel, o.anchor, o.value), (Atom("A"), "B", 0, "p", 100))
        self.assertTrue(parse_rule("A → no B").rhs[0].options[0].negated)
        self.assertEqual(parse_rule("A → B 0%").rhs, parse_rule("A → no B").rhs)
        r = parse_rule("A & B → C 45% 3 units on the left of p")
        o = r.rhs[0].options[0]
        self.assertEqual((r.lhs, o.rel, o.anchor, o.value), (And((Atom("A"), Atom("B"))), -3, "p", 45))
        r = parse_rule("if A → B 1% 1 unit on the right of l")
        o = r.rhs[0].options[0]
        self.assertEqual((o.rel, o.anchor, o.value), (1, "l", 1))
        r = parse_rule("mode 1 → D 100% & E 100%")
        self.assertEqual((r.mode, r.lhs, [it.options[0].kind for it in r.rhs]), ("1", TRUE, ["D", "E"]))
        r = parse_rule("A → B 100% or C 100%")
        self.assertEqual([o.kind for o in r.rhs[0].options], ["B", "C"])
        self.assertEqual(parse_rule("A and B → C 100%").lhs, And((Atom("A"), Atom("B"))))
        r = parse_rule("A → B 50% on [0, 2)")  # an interval: relative to l unless "of p"
        self.assertEqual((r.rhs[0].options[0].where, r.rhs[0].options[0].anchor), (Interval.closed_open(0, 2), "l"))
        self.assertEqual(parse_rule("A → B on [0, 2) of p").rhs[0].options[0].anchor, "p")
        self.assertEqual(parse_rule("A → B on (0, 1] of l").rhs[0].options[0].where, Interval.closed(EPS, 1))
        r = parse_rule("if element A, then element B 1 unit on the right")
        self.assertEqual((r.lhs, r.rhs[0].options[0].rel), (Atom("A"), 1))

    def test_right_side_expressions(self):
        """Any expression with "and", "or" and parentheses. "&" separates the items and
        "or" binds tighter; an operand "B & C" is a hidden kind whose rule places B and C,
        and the operands are tried left first: ((B or C) & E) or D is B & E, C & E, D."""
        self.assertEqual([str(it) for it in parse_rule("A → B or C & D").rhs], ["B or C", "D"])
        r = parse_rule("A → (B & C) or D")
        self.assertEqual(([str(it) for it in r.rhs], [ir.text for ir in r.internal]), (["(B & C) or D"], ["§(B & C) → B & C"]))
        r = parse_rule("A → ((B or C) & E) or D")
        self.assertEqual([o.kind for o in r.rhs[0].options], ["§(B & E)", "§(C & E)", "D"])
        r = parse_rule("A → B or (C & no D 1 unit on the right) & (E or F on (0, 1] of p)")
        self.assertEqual([str(it) for it in r.rhs], ["B or (C & no D 1 unit on the right of p)", "E or F on (0, 1] of p"])
        self.assertEqual(r.rhs[1].options[1].where, Interval.closed(EPS, 1))  # its parentheses stay an interval
        with self.assertRaises(RuleError):
            parse_rule("A → (B & C on [0, 1] of l) or D")  # one operand relative to l, another to p
        with self.assertRaises(RuleError):
            parse_rule("A → (B or C")
        rs = parse_rules(["A → (B & C) or D", "E → (B & C) or F"])  # one hidden kind, one rule for it
        self.assertEqual(sum(1 for r in rs.rules if r.lhs == Atom("§(B & C)")), 1)

    def test_left_sides(self):
        f = parse_rule("(A >= 50% | B 30%) & not C → D").lhs
        self.assertEqual(f.kinds(), ["A", "B", "C"])
        for a, b, c, want in [(50, 0, 0, True), (40, 0, 0, False), (40, 30, 0, True), (100, 0, 1, False), (0, 30, 0, True)]:
            vals = {"A": F(a), "B": F(b), "C": F(c)}
            self.assertEqual(f.holds(lambda k: vals[k]), want)
        self.assertTrue(parse_rule("A 0% → D").lhs.holds(lambda k: F(0)))  # 0%: absent
        self.assertTrue(parse_rule("A < 20% → D").lhs.holds(lambda k: F(0)))  # an absent A counts as 0%
        # the region where a left side is met, pointwise
        rng = random.Random(6)
        for _ in range(40):
            vals = {k: {F(rng.choice([30, 50, 100])): Region.of(rand_set(rng))} for k in "ABC"}
            vals = {k: d for k, d in vals.items() if all(d.values())}
            region = lhs_region(f, vals)
            for x in GRID[::4]:
                v = lambda k: next((p for p, r in vals.get(k, {}).items() if r.contains(x)), F(0))
                self.assertEqual(region.contains(x), f.holds(v), x)

    def test_no_cycle(self):
        with self.assertRaises(RuleError):  # the requirement's example
            parse_rules(["if A → B 1% 1 unit on the right of l", "if B → A 10%"])
        with self.assertRaises(RuleError):  # A depends on itself
            parse_rules(["A → A 1 unit on the left"])
        with self.assertRaises(RuleError):  # "no B" is a consequence on B too
            parse_rules(["B → no B 1 unit on the right"])
        with self.assertRaises(RuleError):  # a kind read under "not" counts
            parse_rules(["not A & C → B", "B → A 1 unit on the right"])
        parse_rules(["A → B or C", "B → D", "B → no D 1 unit on the right"])

    def test_restrictions(self):
        for bad in (["mode 1 → D 1 unit on the right"],  # mode rules act at the chip only
                    ["A → B or C on [0, 1]"],  # C on an interval is relative to l: all of p or all of l
                    ["A → B 1 unit on the right of l or C"],
                    # read as absent: on lattices only (single positions relative to p)
                    ["A → B or C", "not B → E on [0, 1] of p"],
                    ["A → B or C", "B < 50% & A → E"],
                    # decided by the sweep: a chosen kind a left side reads is not placed before its decision
                    ["A → B or C", "B → E on [-1, 0] of l", "E → no F on (0, 1] of l"]):
            with self.assertRaises(RuleError, msg=bad):
                parse_rules(bad)
        rs = parse_rules(["A → B or C", "B & not X → E 1 unit on the left", "A → B at 1 of l or C at 1 of l", "X → no B on [0, 2] of l"])
        self.assertTrue(rs.lattice)
        for absent in (["A → B or C", "not B → E"], ["A → B or C", "B 0% → E"], ["A → B or C", "A & not B → no C 1 unit on the right"]):
            self.assertTrue(parse_rules(absent).lattice, absent)
        # a mode rule acts once, at the chip: no cycle through it
        parse_rules(["mode 1 & not X → D", "D → X 1 unit on the right"])
        with self.assertRaises(RuleError):
            parse_rules(["mode 1 → D", "D → X", "X → D"])  # the element rules alone have one
        parse_rules(["A → B on [0, 2] of p"])  # an interval relative to p
        for sweep in (["A → B or no C"], ["A → B or C", "B → E", "B → no E on (0, 1]"], ["A → B or C", "B → E at 1 of l"],
                      ["A → B or C", "B | X → E"], ["A → B or C on [0, 1] of p"], ["A → B or C", "B >= 50% → E on [0, 1)"]):
            self.assertFalse(parse_rules(sweep).lattice, sweep)
        rs = parse_rules(["A → B", "C & A → D", "A → E or F"])
        self.assertEqual([rs.rank[k] for k in "ABCDEF"], [0, 1, 2, 3, 4, 5])
        # the order of the "or"s at one position: one that reads what another places comes after it
        rs = parse_rules(["B → D or E", "A → B or C"])
        self.assertEqual(rs.or_items, [(1, 0), (0, 0)])


if __name__ == "__main__":
    unittest.main()
