"""Consequences of edits: "or" choices from left to right, positions x + kε, infinite
elements, and the resolution of conflicts (requirement.md)."""

import itertools
import random
import unittest
from fractions import Fraction as F

from helpers import rand_interval, rules_hold
from intervals import EPS, INF, Interval, IntervalSet, Pos
from line import USER, LineState, Op
from region import Region
from resolve import Refused, apply_edit, initial_state
from rules import RuleError, parse_rules
from solver import Conflict, Problem, Undetermined, lhs_region, region_stage, solve
from sweep import Sweep

# kinds only act on later kinds (A < B < ... < E): no cycle
POOL = ["A → B or C", "B → no D 1 unit on the right", "C → D 1 unit on the left", "A & D → no E",
        "B → E or D 2 units on the right", "A → no C 1 unit on the left", "C → E", "D → no E 1 unit on the right",
        "A & B → C", "A → B", "B → C or E 1 unit on the left", "B → D", "C → no D 1 unit on the right",
        "A 100% & not C → E 1 unit on the right", "not B & D → no E"]
ALTERNATE = ["A → B or C", "B → D", "B → no D 1 unit on the right"]  # no B right after a B


def sample_rules(rng, n):
    """Rules of POOL; the sets the restrictions forbid are drawn again."""
    while True:
        try:
            return parse_rules(rng.sample(POOL, rng.randint(1, n)))
        except RuleError:
            continue


# ------------------------------------------------------------------- brute force

def bf_line(rs, claims: set, neg: set):
    """Points only, integer offsets. claims, neg: sets of (kind, x). The line, or None.
    By definition: the first assignment of operands, in the order (x, "or"), of all the
    anchors some assignment could meet, whose closure breaks no rule."""
    ors = [(rs.element[i].lhs, rs.element[i].rhs[j].options) for i, j in rs.or_items]
    values = {}  # (kind, x) -> percentage, for the left sides

    def met(lhs, atoms):
        return {x for (_, x) in atoms if lhs.holds(lambda k: values.get((k, x), 100) if (k, x) in atoms else 0)}

    def closure(choice):
        atoms = set(claims)
        while True:
            new = set(atoms)
            for r in rs.element:
                xs = met(r.lhs, atoms)
                for it in r.rhs:
                    o = it.options[0]
                    if not it.is_or and not o.negated:
                        new |= {(o.kind, x + o.rel) for x in xs}
            for oi, (lhs, opts) in enumerate(ors):
                xs = met(lhs, atoms)
                for x in xs:
                    if choice == "all":
                        new |= {(o.kind, x + o.rel) for o in opts}
                    elif (x, oi) in choice:
                        o = opts[choice[(x, oi)]]
                        new.add((o.kind, x + o.rel))
            if new == atoms:
                return atoms
            atoms = new

    def broken(atoms):
        if atoms & neg:
            return True
        for r in rs.element:
            xs = met(r.lhs, atoms)
            for it in r.rhs:
                o = it.options[0]
                if not it.is_or and o.negated and any((o.kind, x + o.rel) in atoms for x in xs):
                    return True
        return False

    opt = closure("all")
    vars_ = sorted({(x, oi) for oi, (lhs, _) in enumerate(ors) for x in met(lhs, opt)})
    for combo in itertools.product(*[range(len(ors[oi][1])) for _, oi in vars_]):
        atoms = closure(dict(zip(vars_, combo)))
        if not broken(atoms):
            return atoms
    return None


def as_points(line, kinds, lo=-12, hi=12):
    return {(k, F(x)) for k in kinds for x in range(lo, hi + 1) if line.presence(k).contains(F(x))}


def problem_of(claims, neg):
    pos, ng = {}, {}
    for k, x in claims:
        pos.setdefault(k, {})[F(100)] = pos.get(k, {}).get(F(100), Region.empty()) | Region.point(x)
    for k, x in neg:
        ng[k] = ng.get(k, Region.empty()) | Region.point(x)
    return Problem(pos, ng)


class OrTest(unittest.TestCase):
    def test_alternate(self):
        """No B right after a B; add A at [1, 2]: B at [1, 2), C at 2."""
        rs = parse_rules(ALTERNATE)
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(1, 2))], USER)
        self.assertEqual(s.line.presence("A"), Region.of(Interval.closed(1, 2)))
        self.assertEqual(s.line.presence("B"), Region.of(Interval.closed_open(1, 2)))
        self.assertEqual(s.line.presence("C"), Region.point(2))

    def test_first_operand(self):
        """The requirement's example: "A → B 100% or C 100%" gives B."""
        rs = parse_rules(["A → B 100% or C 100%"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(1, 2))], USER)
        self.assertEqual((s.line.presence("B"), s.line.presence("C")), (Region.of(Interval.closed(1, 2)), Region.empty()))
        rs = parse_rules(["A → B or C", "X → no B"])
        s = apply_edit(rs, LineState.empty(), [Op.put("X", Interval.closed(0, 3)), Op.put("A", Interval.closed(1, 5))], USER)
        self.assertEqual(s.line.presence("C"), Region.of(Interval.closed(1, 3)))  # B cannot be placed there
        self.assertEqual(s.line.presence("B"), Region.of(Interval.open_closed(3, 5)))

    def test_user_edits_win(self):
        """The requirement's example: A and B at [1, 2], the user removes B at 1.5."""
        rs = parse_rules(["A → B 100% or C 100%"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(1, 2)), Op.put("B", Interval.closed(1, 2))], USER)
        s = apply_edit(rs, s, [Op.take("B", Interval.point(F(3, 2)))], USER)
        self.assertEqual(s.line.presence("A"), Region.of(Interval.closed(1, 2)))
        self.assertEqual(s.line.presence("B"), Region.of(Interval.closed(1, 2)) - Region.point(F(3, 2)))
        self.assertEqual(s.line.presence("C"), Region.point(F(3, 2)))

    def test_left_to_right(self):
        rs = parse_rules(ALTERNATE)
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(-2, -1))], USER)
        self.assertEqual(s.line.presence("B"), Region.of(Interval.closed_open(-2, -1)))
        self.assertEqual(s.line.presence("C"), Region.point(-1))
        # -1 first takes B; then 0: the B at -1 forbids B there, so C; then 1 takes B
        s = apply_edit(rs, LineState.empty(), [Op.put("A", IntervalSet.of(Interval.point(-1), Interval.point(0), Interval.point(1)))], USER)
        self.assertEqual(s.line.presence("B"), Region.of(IntervalSet.of(Interval.point(-1), Interval.point(1))))
        self.assertEqual(s.line.presence("C"), Region.point(0))
        # looking ahead: B at -1 would leave nothing for 1, where C cannot be
        rs = parse_rules(["A → B or C", "B → D", "B → no D 2 units on the right", "X → no C"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", IntervalSet.of(Interval.point(-1), Interval.point(1))), Op.put("X", Interval.point(1))], USER)
        self.assertEqual((s.line.presence("B"), s.line.presence("C")), (Region.point(1), Region.point(-1)))

    def test_unbounded_on_the_left(self):
        """No first position: choices that act on each other are not decided; others are."""
        rs = parse_rules(ALTERNATE)
        with self.assertRaises(Refused):
            apply_edit(rs, LineState.empty(), [Op.put("A", Interval(-INF, F(0), False, True))], USER)
        rs = parse_rules(["A → B or C", "X → no B"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval(-INF, F(0), False, True)), Op.put("X", Interval.closed(-5, -3))], USER)
        self.assertEqual(s.line.presence("C"), Region.of(Interval.closed(-5, -3)))
        self.assertEqual(s.line.presence("B"), Region.of(Interval(-INF, F(0), False, True)) - Region.of(Interval.closed(-5, -3)))

    def test_infinite_element_repeats(self):
        rs = parse_rules(ALTERNATE)
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval(F(0), INF, True, False))], USER)
        B, C = s.line.presence("B"), s.line.presence("C")
        for i in range(-3, 40):
            x = F(i) + F(1, 3)
            self.assertEqual(B.contains(x), i >= 0 and i % 2 == 0, x)
            self.assertEqual(C.contains(x), i >= 0 and i % 2 == 1, x)
        self.assertFalse(rules_hold(rs, s.line, s.claims))

    def test_or_relative_to_l(self):
        """One choice per largest interval of A, at its left edge."""
        rs = parse_rules(["A → B 1 unit on the right of l or C 1 unit on the right of l", "X → no B"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 3)), Op.put("A", Interval.closed(5, 6)),
                                               Op.put("X", Interval.point(1))], USER)
        self.assertEqual((s.line.presence("B"), s.line.presence("C")), (Region.point(6), Region.point(1)))
        self.assertFalse(rules_hold(rs, s.line, s.claims))

    def test_matches_brute_force(self):
        rng = random.Random(3)
        checked = 0
        for _ in range(150):
            rs = sample_rules(rng, 4)
            claims = {(rng.choice("ABCD"), F(rng.randint(-4, 4))) for _ in range(rng.randint(1, 5))}
            neg = {(rng.choice("BCDE"), F(rng.randint(-4, 4))) for _ in range(rng.randint(0, 2))} - claims
            expect = bf_line(rs, claims, neg)
            try:
                line = solve(rs, problem_of(claims, neg))
            except Conflict:
                self.assertIsNone(expect, (list(map(str, rs.rules)), claims, neg))
                continue
            self.assertIsNotNone(expect, (list(map(str, rs.rules)), claims, neg))
            self.assertEqual(as_points(line, "ABCDE"), expect, ([str(r) for r in rs.rules], claims, neg))
            self.assertFalse(rules_hold(rs, line))
            checked += 1
        self.assertGreater(checked, 80)

    def test_intervals_satisfy_the_rules(self):
        rng = random.Random(4)
        for _ in range(60):
            rs = sample_rules(rng, 4)
            pos = {}
            for k in rng.sample("ABCD", 2):
                pos[k] = {F(100): Region.of(rand_interval(rng))}
            try:
                line = solve(rs, Problem(pos))
            except Conflict:
                continue
            self.assertFalse(rules_hold(rs, line), [str(r) for r in rs.rules])


class AnchorTest(unittest.TestCase):
    def test_relative_to_p_and_l(self):
        rs = parse_rules(["A → B 100% on [0, 1] of l", "A → C 1 unit on the right of l", "A → D 50% 1 unit on the left of p"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(3, 4)), Op.put("A", Interval.open(6, 8))], USER)
        # the left edge of (6, 8) is its first position, 6+ε
        self.assertEqual(s.line.presence("B"), Region.of(IntervalSet.of(Interval.closed(3, 4), Interval.closed(Pos(6, 1), Pos(7, 1)))))
        self.assertEqual(s.line.presence("C"), Region.of(IntervalSet.of(Interval.point(4), Interval.point(Pos(7, 1)))))
        self.assertEqual(s.line.vals["D"], {50: Region.of(IntervalSet.of(Interval.closed(2, 3), Interval.open(5, 7)))})
        self.assertFalse(rules_hold(rs, s.line, s.claims))

    def test_intervals_relative_to_p(self):
        rs = parse_rules(["A → B on [0, 1] of p", "A → no C on (0, 2) of p"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", IntervalSet.of(Interval.point(0), Interval.closed(3, 4)))], USER)
        self.assertEqual(s.line.presence("B"), Region.of(IntervalSet.of(Interval.closed(0, 1), Interval.closed(3, 5))))
        with self.assertRaises(Refused):  # C at 5 < 4 + 2
            apply_edit(rs, s, [Op.put("C", Interval.point(5)), Op.put("A", Interval.closed(3, 4))], USER)
        s = apply_edit(rs, s, [Op.put("C", Interval.point(6))], USER)
        self.assertEqual(s.line.presence("C"), Region.point(6))

    def test_no_left_edge(self):
        """An interval with no left end: l is -inf (unbound). l + [0, 1] is at -inf, not on
        the line: nothing. l + [0, +inf) is the whole line."""
        left = Interval(-INF, F(2), False, True)
        rs = parse_rules(["A → B on [0, 1] of l"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", left)], USER)
        self.assertEqual(s.line.kinds(), ["A"])
        rs = parse_rules(["A → B on [0, inf) of l"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", left)], USER)
        self.assertEqual(s.line.presence("B"), Region.universe())
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(3, 4))], USER)
        self.assertEqual(s.line.presence("B"), Region.of(Interval(3, INF, True, False)))
        # "no B" far right: every part of A would still reach it, so all of A goes
        s = apply_edit(rs, apply_edit(rs, LineState.empty(), [Op.put("A", left)], USER), [Op.put_no("B", Interval.point(100))], USER)
        self.assertEqual(s.line.kinds(), [])

    def test_once_per_largest_interval(self):
        """Relative to l, "no F on (0, 1]" acts once per largest interval of B."""
        rs = parse_rules(["A → B", "B → F", "B → no F on (0, 1] of l"])
        with self.assertRaises(Refused):  # F on [0, 2] and no F on (0, 1]
            apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 2))], USER)
        s = apply_edit(rs, LineState.empty(), [Op.put("A", IntervalSet.of(Interval.point(0), Interval.point(2)))], USER)
        self.assertEqual(s.line.presence("F"), Region.of(IntervalSet.of(Interval.point(0), Interval.point(2))))

    def test_left_sides_with_percentages_and_not(self):
        rs = parse_rules(["A >= 50% & not C → B", "A 30% → D 1 unit on the right"])
        s = initial_state(rs, [("A", Interval.closed_open(0, 2), 50), ("A", Interval.closed(2, 4), 30), ("C", Interval.closed(1, 2), 100)])
        self.assertEqual(s.line.presence("B"), Region.of(Interval.closed_open(0, 1)))
        self.assertEqual(s.line.presence("D"), Region.of(Interval.closed(3, 5)))
        self.assertFalse(rules_hold(rs, s.line, s.claims))

    def test_intervals_satisfy_the_rules(self):
        rng = random.Random(8)
        pool = ["A → B on [0, 1] of l", "A & not B → C 50% 1 unit on the right of l", "B → no D on (0, 2) of l",
                "C | A 30% → D on [-1, 0] of l", "A → E or B", "E → C 1/2 unit on the left"]
        for _ in range(60):
            try:
                rs = parse_rules(rng.sample(pool, rng.randint(1, 4)))
            except RuleError:
                continue
            pos = {k: {F(rng.choice([30, 100])): Region.of(rand_interval(rng))} for k in rng.sample("AB", rng.randint(1, 2))}
            try:
                line = solve(rs, Problem(pos))
            except Conflict:
                continue
            self.assertFalse(rules_hold(rs, line), [str(r) for r in rs.rules])


EXAMPLE = ["A → B or C", "B → E", "B → no E on (0, 1] of l"]  # each B alone, B more than 1 apart
DENSE_POOL = ["A → B or C", "B → E", "B → no E on (0, 1] of l", "C → F on [0, 1/2] of l", "B → no F on [-1, 0) of p",
              "A & C → no E 1 unit on the right", "X → no B", "X → no C on [0, 1] of p", "E → G or H", "G → no H on (0, 1]",
              "A → B 1/2 unit on the right of l or C 1/2 unit on the right of l", "B & F → no G"]


def run_sweep(rs, prob):
    """The sweep on a rule set the lattices also solve."""
    kinds_in = set(rs.choice_dependent)
    vals, fixed, forbid = region_stage(rs, prob, kinds_in)
    vals.update(Sweep(rs, prob, vals, fixed, forbid, kinds_in).solve())
    return vals


class EpsilonTest(unittest.TestCase):
    def test_requirement_question(self):
        """B at 0 forbids E on (0, 1]: no B there; the first position after it is 1+ε."""
        rs = parse_rules(EXAMPLE)
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 2))], USER)
        self.assertEqual(s.line.presence("B"), Region.of(IntervalSet.of(Interval.point(0), Interval.point(Pos(1, 1)))))
        self.assertEqual(s.line.presence("E"), s.line.presence("B"))
        self.assertEqual(s.line.presence("C"), Region.of(IntervalSet.of(Interval.open_closed(0, 1), Interval.closed(Pos(1, 2), 2))))
        self.assertFalse(rules_hold(rs, s.line, s.claims))
        self.assertEqual(str(s.line.presence("B")), "{0} U {1+ε}")

    def test_drift(self):
        """On [0, +inf): B at n + nε for every n, a pattern repeating every 1+ε."""
        rs = parse_rules(EXAMPLE)
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval(F(0), INF, True, False))], USER)
        B = s.line.presence("B")
        for n in range(0, 40):
            self.assertTrue(B.contains(Pos(n, n)), n)
            self.assertFalse(B.contains(Pos(n, n - 1)) or B.contains(Pos(n, n + 1)) or B.contains(Pos(F(2 * n + 1, 2))), n)
        self.assertEqual(B.pr, Pos(1, 1))
        self.assertFalse(rules_hold(rs, s.line, s.claims))

    def test_operands_on_intervals_and_no(self):
        rs = parse_rules(["A → B on [0, 1] of l or C on [0, 1] of l", "X → no B"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.point(0)), Op.put("A", Interval.closed(3, 4)),
                                               Op.put("X", Interval.point(F(7, 2)))], USER)
        self.assertEqual((s.line.presence("B"), s.line.presence("C")), (Region.of(Interval.closed(0, 1)), Region.of(Interval.closed(3, 4))))
        rs = parse_rules(["A → no B or C"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 3)), Op.put("B", Interval.closed(1, 2))], USER)
        self.assertEqual(s.line.presence("C"), Region.of(Interval.closed(1, 2)))  # "no B" cannot be chosen there
        self.assertFalse(rules_hold(rs, s.line, s.claims))

    def test_looking_ahead(self):
        """X forbids C at 1/2, so B must be there, and a B on [0, 1/2) would forbid the E
        it needs: C on [0, 1/2), B at 1/2, then C until the B at 3/2+ε."""
        rs = parse_rules(EXAMPLE + ["X → no C"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 2)), Op.put("X", Interval.point(F(1, 2)))], USER)
        self.assertEqual(s.line.presence("B"), Region.of(IntervalSet.of(Interval.point(F(1, 2)), Interval.point(Pos(F(3, 2), 1)))))
        self.assertEqual(s.line.presence("C"), Region.of(Interval.closed(0, 2)) - s.line.presence("B"))
        self.assertFalse(rules_hold(rs, s.line, s.claims))
        # with intervals relative to p: a B on (-1/2, 1) would forbid the C needed at 1
        rs = parse_rules(["A → B or C", "B → no C on (0, 3/2) of p", "X → no B"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 3)), Op.put("X", Interval.point(1))], USER)
        self.assertEqual((s.line.presence("C"), s.line.presence("B")), (Region.of(Interval.closed(0, 1)), Region.of(Interval.open_closed(1, 3))))
        # when nothing can be chosen at 1/2 whatever the choices, the edit contradicts the rules
        rs = parse_rules(EXAMPLE + ["X → no C", "X → no B"])
        with self.assertRaises(Refused) as e:
            apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 2)), Op.put("X", Interval.point(F(1, 2)))], USER)
        self.assertIn("contradicts", str(e.exception))

    def test_sweep_matches_the_lattices(self):
        """Where both apply, the sweep gives the lattices' result, looking ahead included
        (X forbids an operand at a few points)."""
        rng = random.Random(21)
        compared = 0
        pool = POOL + ["X → no C", "X → no B"]
        for _ in range(160):
            try:
                rs = parse_rules(rng.sample(pool, rng.randint(2, 5)))
            except RuleError:
                continue
            if not rs.or_items or not rs.causal:
                continue
            pos = {k: {F(100): Region.of(rand_interval(rng))} for k in rng.sample("ABCD", 2)}
            if rng.random() < 0.6:
                pos["X"] = {F(100): Region.of(IntervalSet(Interval.point(F(rng.randint(-8, 8), 2)) for _ in range(rng.randint(1, 3))))}
            prob = Problem(pos, {rng.choice("BCDE"): Region.of(rand_interval(rng))} if rng.random() < 0.4 else {})
            try:
                want = solve(rs, prob)
            except Conflict:
                want = None
            except Undetermined:
                continue
            try:
                got = run_sweep(rs, prob)
            except Conflict:
                self.assertIsNone(want, [str(r) for r in rs.rules])
                continue
            self.assertIsNotNone(want, [str(r) for r in rs.rules])
            for k in rs.choice_dependent:
                self.assertEqual(want.presence(k), union_of(got.get(k, {})), (k, [str(r) for r in rs.rules], pos))
            compared += 1
        self.assertGreater(compared, 40)

    def test_sweep_matches_brute_force(self):
        """A on single points: the first assignment, from left to right, whose closure
        breaks no rule. Half the cases need looking ahead (X forbids C at some points)."""
        rng = random.Random(22)
        checked = 0
        for n in range(100):
            extra = rng.sample(DENSE_POOL[1:] + ["X → no C on [0, 1/2] of p", "Y → no B"], rng.randint(1, 4))
            try:
                rs = parse_rules(EXAMPLE + ["X → no C"] + extra[:2] if n % 2 else ["A → B or C"] + extra)
            except RuleError:
                continue
            if rs.lattice or any(set(rs.element[i].lhs.kinds()) & rs.choice_dependent for i, _ in rs.or_items):
                continue  # the brute force needs the left sides of the "or"s fixed
            pts = sorted({F(rng.randint(0, 12), 4) for _ in range(rng.randint(2, 6))})
            pos = {"A": {F(100): Region.of(IntervalSet(Interval.point(x) for x in pts))}}
            for k in "XY":
                if rng.random() < 0.6:
                    pos[k] = {F(100): Region.of(IntervalSet(Interval.point(rng.choice(pts)) for _ in range(rng.randint(1, 2))))}
            prob = Problem(pos)
            expect = bf_sweep(rs, prob)
            try:
                line = solve(rs, prob)
            except Conflict:
                self.assertIsNone(expect, [str(r) for r in rs.rules])
                continue
            self.assertIsNotNone(expect, ([str(r) for r in rs.rules], pts))
            for k in rs.choice_dependent:
                self.assertEqual(line.presence(k), union_of(expect.get(k, {})), (k, [str(r) for r in rs.rules], pts))
            self.assertFalse(rules_hold(rs, line))
            checked += 1
        self.assertGreater(checked, 25)

    def test_each_position_takes_the_first_operand(self):
        """On intervals: the rules hold, and at sampled positions (around every change,
        x - 2ε ... x + 2ε, and in between) the operand taken is the first one that the
        choices on its left allow and that leaves a completion."""
        rng = random.Random(23)
        solved = 0
        for n in range(60):
            try:
                rs = parse_rules((EXAMPLE + ["X → no C"] if n % 2 else ["A → B or C"]) + rng.sample(DENSE_POOL[1:], rng.randint(1, 3)))
            except RuleError:
                continue
            if rs.lattice:
                continue
            pos = {"A": {F(100): Region.of(rand_interval(rng, 0, 4))}}
            if rng.random() < 0.5:
                pos["X"] = {F(100): Region.of(rand_interval(rng, 0, 4))}
            elif n % 2:  # positions where only B can go: looking ahead
                pos["X"] = {F(100): Region.of(IntervalSet(Interval.point(F(rng.randint(0, 8), 2)) for _ in range(2)))}
            prob = Problem(pos)
            try:
                line = solve(rs, prob)
            except (Conflict, Undetermined):
                continue
            self.assertFalse(rules_hold(rs, line), [str(r) for r in rs.rules])
            kinds_in = set(rs.choice_dependent)
            vals, fixed, forbid = region_stage(rs, prob, kinds_in)
            sw = Sweep(rs, prob, vals, fixed, forbid, kinds_in)
            sw.solve()
            ch = sw.choices
            ends = {e for row in ch for reg in row for iv in reg.on(-10, 10).intervals for e in (iv.lo, iv.hi)}
            xs = sorted({Pos(e.x, e.k + j) for e in ends for j in (-2, -1, 0, 1, 2)})
            xs = sorted(set(xs) | {Pos((a.x + b.x) / 2) for a, b in zip(xs, xs[1:]) if a.x < b.x})
            for m in xs:
                before = [[reg & Region.of(Interval(-INF, m, False, False)) for reg in row] for row in ch]
                for ii, it in enumerate(sw.items):
                    taken = [oi for oi, reg in enumerate(ch[ii]) if reg.contains(m)]
                    self.assertLessEqual(len(taken), 1)
                    if taken:
                        for o2 in range(taken[0]):
                            c = [list(row) for row in before]
                            c[ii][o2] = c[ii][o2] | Region.point(m)
                            self.assertFalse(sw._valid(c, m) and sw.complete(m, c, ii + 1)[0] is not None,
                                             ([str(r) for r in rs.rules], str(m), it.text, o2))
                        before[ii][taken[0]] = before[ii][taken[0]] | Region.point(m)
            solved += 1
        self.assertGreater(solved, 20)


def union_of(d):
    out = Region.empty()
    for r in d.values():
        out = out | r
    return out


def bf_sweep(rs, prob):
    """Brute force: the "or"s have fixed left sides met at finitely many points; try
    every assignment in the order (x, "or") and keep the first whose closure holds."""
    kinds_in = set(rs.choice_dependent)
    try:
        vals, fixed, forbid = region_stage(rs, prob, kinds_in)
    except Conflict:
        return None
    sw = Sweep(rs, prob, vals, fixed, forbid, kinds_in)
    anchors = []
    for ii, it in enumerate(sw.items):
        reg = lhs_region(it.lhs, vals)
        if it.anchor == "l":
            reg = reg.left_edges()
        anchors += [(iv.lo, ii) for iv in reg.bounded().intervals]
    anchors.sort()
    for combo in itertools.product(*[range(len(sw.items[ii].ops)) for _, ii in anchors]):
        ch = [list(row) for row in sw.none]
        for (x, ii), oi in zip(anchors, combo):
            ch[ii][oi] = ch[ii][oi] | Region.point(x)
        try:
            got = sw.closure(ch, INF)
        except Conflict:
            continue
        return {k: got[k] for k in sw.kin}
    return None


# kinds only act on later kinds (A < B < ... < E); "not" on the kinds an "or" changes
ABSENT_POOL = ["A & not B → D", "A & not C → E 1 unit on the right", "B → no D 1 unit on the left", "D & not B → no E",
               "C → no E 1 unit on the right", "A → C or D 1 unit on the right", "not C & B → no D", "A 100% & not D → E",
               "A & D 0% → E 1 unit on the left", "not B & A → no C 1 unit on the right",
               "A & not B → D or E 1 unit on the right", "A & not D → no E 1 unit on the left", "not C & not D & A → E",
               "A & not E → no D 1 unit on the right", "B & not D → no E 1 unit on the right"]


def bf_absent(rs, claims: set):
    """Points only, integer offsets, every "or" met where A is (as in ABSENT_POOL). The
    line, or None: the first assignment of operands, in the order (x, "or"), whose line
    breaks no rule. Each line is computed kind after kind (no cycle), so a kind read as
    absent is read once it is known: no assumption that choices only add consequences."""
    ors = [rs.element[i].rhs[j] for i, j in rs.or_items]
    xs = sorted({x for k, x in claims if k == "A"})
    lo, hi = int(min(x for _, x in claims)) - 3, int(max(x for _, x in claims)) + 3
    anchors = sorted(((x, n) for x in xs for n in range(len(ors))))
    for combo in itertools.product(*[range(len(ors[n].options)) for _, n in anchors]):
        took = dict(zip(anchors, combo))
        have: set = set(claims)
        holds = lambda f, x: f.holds(lambda k: 100 if (k, x) in have else 0)
        for K in rs.order:
            for x in range(lo, hi + 1):
                for i, r in enumerate(rs.element):
                    for j, it in enumerate(r.rhs):
                        for t, o in enumerate(it.options):
                            if o.kind != K or o.negated or (it.is_or and took.get((x - o.rel.x, rs.or_items.index((i, j)))) != t):
                                continue
                            if holds(r.lhs, x - o.rel.x):
                                have.add((K, F(x)))
        ok = True
        for r in rs.element:
            for it in r.rhs:
                o = it.options[0]
                if not it.is_or and o.negated:
                    for x in range(lo, hi + 1):
                        if holds(r.lhs, x) and (o.kind, F(x + o.rel.x)) in have:
                            ok = False
        if ok:
            return have
    return None


class AbsentReadTest(unittest.TestCase):
    """A left side that reads under "not" a kind an "or" changes (requirement.md: any
    boolean expression), solved exactly on lattices."""

    def test_example(self):
        rs = parse_rules(["A → B or C", "A & not B → E"])
        line = solve(rs, Problem({"A": {F(100): Region.of(Interval.closed(0, 2))}}))
        self.assertEqual((line.presence("B"), line.presence("E")), (Region.of(Interval.closed(0, 2)), Region.empty()))
        # B cannot be at 1: C there, so E there
        rs = parse_rules(["A → B or C", "A & not B → E", "X → no B"])
        line = solve(rs, Problem({"A": {F(100): Region.of(Interval.closed(0, 2))}, "X": {F(100): Region.point(1)}}))
        self.assertEqual((line.presence("C"), line.presence("E")), (Region.point(1), Region.point(1)))

    def test_matches_brute_force(self):
        rng = random.Random(29)
        checked = 0
        for _ in range(250):
            try:
                rs = parse_rules(["A → B or C"] + rng.sample(ABSENT_POOL, rng.randint(2, 5)))
            except RuleError:
                continue
            claims = {("A", F(x)) for x in rng.sample(range(0, 5), rng.randint(1, 4))}
            claims |= {(rng.choice("BCDE"), F(rng.randint(-1, 5))) for _ in range(rng.randint(0, 2))}
            expect = bf_absent(rs, claims)
            try:
                line = solve(rs, problem_of(claims, set()))
            except Conflict:
                self.assertIsNone(expect, ([str(r) for r in rs.rules], sorted(claims)))
                continue
            self.assertIsNotNone(expect, ([str(r) for r in rs.rules], sorted(claims)))
            self.assertEqual(as_points(line, "ABCDE"), expect, ([str(r) for r in rs.rules], sorted(claims)))
            self.assertFalse(rules_hold(rs, line))
            checked += 1
        self.assertGreater(checked, 60)


class RightSideTest(unittest.TestCase):
    """A right side with "and" inside "or": the leftmost operand that can be chosen, an
    operand being a conjunction (requirement.md); the hidden kinds are not on the line."""

    def line(self, rules, ops):
        rs = parse_rules(rules)
        s = apply_edit(rs, LineState.empty(), ops, USER)
        self.assertFalse(rules_hold(rs, s.line, s.claims))
        return s.line

    def test_conjunction_operand(self):
        line = self.line(["A → (B & C) or D"], [Op.put("A", Interval.closed(0, 2))])
        self.assertEqual(line.kinds(), ["A", "B", "C"])
        line = self.line(["A → (B & C) or D", "X → no C"], [Op.put("A", Interval.closed(0, 2)), Op.put("X", Interval.point(1))])
        everywhere_but_1 = Region.of(IntervalSet.of(Interval.closed_open(0, 1), Interval.open_closed(1, 2)))
        self.assertEqual((line.presence("B"), line.presence("C"), line.presence("D")), (everywhere_but_1, everywhere_but_1, Region.point(1)))

    def test_nested(self):
        line = self.line(["A → ((B or C) & E) or D", "X → no B", "Y → no E"],
                         [Op.put("A", Interval.closed(0, 3)), Op.put("X", Interval.closed(1, 3)), Op.put("Y", Interval.closed(2, 3))])
        self.assertEqual(line.presence("B"), Region.of(Interval.closed_open(0, 1)))
        self.assertEqual(line.presence("C"), Region.of(Interval.closed_open(1, 2)))
        self.assertEqual(line.presence("E"), Region.of(Interval.closed_open(0, 2)))
        self.assertEqual(line.presence("D"), Region.of(Interval.closed(2, 3)))

    def test_and_of_ors(self):
        """(B or C) & (D or E): each "or" takes its own leftmost operand."""
        line = self.line(["A → (B or C) & (D or E)", "X → no D"], [Op.put("A", Interval.closed(0, 1)), Op.put("X", Interval.point(1))])
        self.assertEqual(line.presence("B"), Region.of(Interval.closed(0, 1)))
        self.assertEqual((line.presence("D"), line.presence("E")), (Region.of(Interval.closed_open(0, 1)), Region.point(1)))


# ------------------------------------------------------------------- resolution

def bf_resolve(rs, old_claims: dict, old_neg: set, ops):
    """old_claims {(kind, x)}, ops [(action, kind, x)]. Returns the new line or None (refused):
    keep the edit, remove a minimal set of older elements losing the fewest elements of the
    line, keeping the leftmost on a tie."""
    claims, neg = set(old_claims), set(old_neg)
    mand, mneg, temp = set(), set(), set()
    for a, k, x in ops:
        if a == "put":
            neg.discard((k, x)); claims.add((k, x)); mand.add((k, x))
        elif a == "put_no":
            claims.discard((k, x)); neg.add((k, x)); mneg.add((k, x))
        elif a == "take":
            claims.discard((k, x)); temp.add((k, x))
    if bf_line(rs, mand, mneg | temp) is None:
        return None
    old_line = bf_line(rs, set(old_claims), set(old_neg))
    cands = sorted([("pos", k, x) for k, x in claims - mand] + [("neg", k, x) for k, x in neg - mneg],
                   key=lambda c: (c[2], rs.rank.get(c[1], 99)))
    best, minimal = None, []
    for n in range(len(cands) + 1):
        for sub in itertools.combinations(cands, n):
            s = set(sub)
            if any(m <= s for m in minimal):
                continue
            line = bf_line(rs, claims - {(k, x) for t, k, x in s if t == "pos"}, (neg - {(k, x) for t, k, x in s if t == "neg"}) | temp)
            if line is None:
                continue
            minimal.append(s)
            loss = len(old_line - line)
            if best is None or loss < best[0] or (loss == best[0] and _keeps_leftmost(s, best[1], rs)):
                best = (loss, s, line)
    return best[2] if best else None


def _keeps_leftmost(a, b, rs):
    diff = sorted(a ^ b, key=lambda c: (c[2], rs.rank.get(c[1], 99)))
    return bool(diff) and diff[0] in b


class ResolutionTest(unittest.TestCase):
    def test_requirement_example(self):
        """"A and B → C": A and B at x, then "no C" at x: only B and C are removed."""
        rs = parse_rules(["A and B → C"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.point(3)), Op.put("B", Interval.point(3))], USER)
        self.assertEqual(s.line.presence("C"), Region.point(3))
        s = apply_edit(rs, s, [Op.put_no("C", Interval.point(3))], USER)
        self.assertEqual(s.line.presence("A"), Region.point(3))
        self.assertEqual((s.line.presence("B"), s.line.presence("C")), (Region.empty(), Region.empty()))
        self.assertEqual(s.claims.negative("C"), Region.point(3))

    def test_part_of_an_element(self):
        """Nothing more than necessary: C must go on [5, 10]. Removing A there loses A and
        C, removing B there loses B and C: the tie keeps A, the leftmost. Only [5, 10] of
        B goes."""
        rs = parse_rules(["A and B → C"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 10)), Op.put("B", Interval.closed(2, 12))], USER)
        s = apply_edit(rs, s, [Op.put_no("C", Interval.closed(5, 20))], USER)
        self.assertEqual(s.line.presence("A"), Region.of(Interval.closed(0, 10)))
        self.assertEqual(s.line.presence("B"), Region.of(IntervalSet.of(Interval.closed_open(2, 5), Interval.open_closed(10, 12))))
        self.assertEqual(s.line.presence("C"), Region.of(Interval.closed_open(2, 5)))

    def test_a_point_is_enough(self):
        rs = parse_rules(["A → C"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 10))], USER)
        s = apply_edit(rs, s, [Op.put_no("C", Interval.point(5))], USER)
        self.assertEqual(s.line.presence("A"), Region.of(IntervalSet.of(Interval.closed_open(0, 5), Interval.open_closed(5, 10))))

    def test_fewest_lost(self):
        # removing A loses A, D, E; removing B loses B only: B goes although A is left of it
        rs = parse_rules(["A and B → C", "A → D", "D → E"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.point(0)), Op.put("B", Interval.point(0))], USER)
        s = apply_edit(rs, s, [Op.put_no("C", Interval.point(0))], USER)
        self.assertEqual(sorted(s.line.kinds()), ["A", "D", "E"])

    def test_edit_contradicting_the_rules_is_refused(self):
        rs = parse_rules(["D → no C"])
        with self.assertRaises(Refused):
            apply_edit(rs, LineState.empty(), [Op.put("C", Interval.point(8)), Op.put("D", Interval.point(8))], USER)

    def test_gone_for_good(self):
        rs = parse_rules(["A and B → C"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.point(3)), Op.put("B", Interval.point(3))], USER)
        s = apply_edit(rs, s, [Op.put_no("C", Interval.point(3))], USER)
        s = apply_edit(rs, s, [Op.take_no("C", Interval.point(3))], USER)
        self.assertEqual(s.line.kinds(), ["A"])  # B does not come back

    def test_consequence_relative_to_l(self):
        """B on [l, l + 1] reaches 7/2 from the left edge 3: A goes on [3, 7/2], the left
        edge becomes 7/2+ε and B is on (7/2, 9/2+ε]. [8, 9] is untouched."""
        rs = parse_rules(["A → B on [0, 1] of l"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(3, 6)), Op.put("A", Interval.closed(8, 9))], USER)
        s = apply_edit(rs, s, [Op.put_no("B", Interval.point(F(7, 2)))], USER)
        self.assertEqual(s.line.presence("A"), Region.of(IntervalSet.of(Interval.open_closed(F(7, 2), 6), Interval.closed(8, 9))))
        self.assertEqual(s.line.presence("B"), Region.of(IntervalSet.of(Interval(Pos(F(7, 2), 1), Pos(F(9, 2), 1)), Interval.closed(8, 9))))

    def test_fewest_elements_not_measure(self):
        """Removing A on [1, 2] and [3, 4] loses A and two C; removing the two B loses
        them and two C: A loses those parts, although it is longer."""
        rs = parse_rules(["A & B → C"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 10))], USER)
        bs = IntervalSet.of(Interval.closed(1, 2), Interval.closed(3, 4))
        s = apply_edit(rs, s, [Op.put("B", bs)], USER)
        s = apply_edit(rs, s, [Op.put_no("C", Interval.closed(0, 5))], USER)
        self.assertEqual(s.line.presence("A"), Region.of(Interval.closed(0, 10)) - Region.of(bs))
        self.assertEqual(s.line.presence("B"), Region.of(bs))
        self.assertEqual(s.line.presence("C"), Region.empty())

    def test_many_elements_in_conflict(self):
        """200 older elements in conflict at once (more than any search over removal sets
        could try): removing A at the 100 points loses A and the 100 C, removing the 100 B
        loses them and the 100 C, so A goes there. And 100 separate conflicts are decided
        one after the other."""
        rs = parse_rules(["A & B → C"])
        bs = IntervalSet(Interval.point(i) for i in range(100))
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 100))], USER)
        s = apply_edit(rs, s, [Op.put("B", bs)], USER)
        s = apply_edit(rs, s, [Op.put_no("C", Interval.closed(0, 100))], USER)
        self.assertEqual(s.line.kinds(), ["A", "B"])
        self.assertEqual(s.line.presence("A"), Region.of(Interval.closed(0, 100)) - Region.of(bs))
        self.assertEqual(s.line.presence("B"), Region.of(bs))
        rs = parse_rules(["A & B → C", "A → D"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", IntervalSet(Interval.point(3 * i) for i in range(100)))], USER)
        s = apply_edit(rs, s, [Op.put("B", IntervalSet(Interval.point(3 * i) for i in range(100)))], USER)
        s = apply_edit(rs, s, [Op.put_no("C", Interval.closed(0, 300))], USER)
        self.assertEqual(sorted(s.line.kinds()), ["A", "D"])  # removing A would also lose D
        self.assertFalse(rules_hold(rs, s.line, s.claims))

    def test_undecided_with_the_line_gives_way(self):
        """Y alone is decided, A alone too, but with both the "or" acts on an element with
        no left end, where nothing decides its choices: the edit is not refused because of
        the line, the older A gives way. The same in one edit is the edit's own problem."""
        rs = parse_rules(["A & Y → B or C", "B → D", "B → no D 1 unit on the right"])
        left = Interval(-INF, 0, False, True)
        s = apply_edit(rs, LineState.empty(), [Op.put("A", left)], USER)
        s = apply_edit(rs, s, [Op.put("Y", left)], USER)
        self.assertEqual(s.line.kinds(), ["Y"])
        with self.assertRaises(Refused):
            apply_edit(rs, LineState.empty(), [Op.put("A", left), Op.put("Y", left)], USER)

    def test_small_edit_on_a_long_element(self):
        rs = parse_rules(ALTERNATE + ["X → no C", "X → no B"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 30))], USER)
        s = apply_edit(rs, s, [Op.put("X", Interval.point(F(21, 2)))], USER)
        self.assertEqual(s.line.kinds(), ["X"])  # the A is removed whole
        self.assertFalse(rules_hold(rs, s.line, s.claims))

    def test_matches_brute_force(self):
        rng = random.Random(5)
        checked = 0
        for _ in range(120):
            rs = sample_rules(rng, 3)
            old = {(rng.choice("ABCD"), F(rng.randint(-3, 3))) for _ in range(rng.randint(1, 4))}
            if bf_line(rs, old, set()) is None:
                continue
            state = apply_edit(rs, LineState.empty(), [Op.put(k, Interval.point(x)) for k, x in sorted(old)], USER)
            ops = [(rng.choice(["put", "put_no", "take"]), rng.choice("ABCDE"), F(rng.randint(-3, 3))) for _ in range(rng.randint(1, 2))]
            if len({(k, x) for _, k, x in ops}) < len(ops):
                continue
            expect = bf_resolve(rs, old, set(), ops)
            edit = [getattr(Op, a)(k, Interval.point(x)) for a, k, x in ops]
            try:
                got = apply_edit(rs, state, edit, USER)
            except Refused:
                self.assertIsNone(expect, ([str(r) for r in rs.rules], old, ops))
                continue
            self.assertIsNotNone(expect, ([str(r) for r in rs.rules], old, ops))
            self.assertEqual(as_points(got.line, "ABCDE"), expect, ([str(r) for r in rs.rules], old, ops))
            checked += 1
        self.assertGreater(checked, 60)


if __name__ == "__main__":
    unittest.main()
