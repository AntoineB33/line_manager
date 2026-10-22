"""Tests: python -m unittest test_reverse_logic   (REVLOGIC_SEEDS=n for more random runs)."""
from __future__ import annotations

import itertools
import math
import os
import random
import unittest
from fractions import Fraction

from reverse_logic import (
    EMPTY,
    FULL,
    Add,
    ContradictionError,
    DependencyError,
    Extend,
    LineSystem,
    MissingElementError,
    Move,
    Region,
    Remove,
    Retract,
    RuleCycleError,
    forbids,
    implies,
    implies_any,
    inverse,
)
from rules import Forbids, Implies, ImpliesAny, RuleSet

SEEDS = int(os.environ.get("REVLOGIC_SEEDS", "60"))
F = Fraction


def rand_region(rng: random.Random, lo=0, hi=8, den=2) -> Region:
    parts = []
    for _ in range(rng.randint(1, 2)):
        a = F(rng.randint(lo * den, hi * den), den)
        if rng.random() < 0.3:
            parts.append(Region.point(a))
        else:
            b = a + F(rng.randint(1, 3 * den), den)
            parts.append(Region.interval(a, b, rng.random() < 0.7, rng.random() < 0.7))
    return Region.union_of(*parts)


def samples(*regions: Region):
    ends = sorted({e for r in regions for p in r.parts for e in (p[0], p[2])} | {F(0)})
    pts = set(ends)
    for a, b in itertools.pairwise(ends):
        pts.add((a + b) / 2)
    pts.add(ends[0] - 1)
    pts.add(ends[-1] + 1)
    return pts


class RegionTests(unittest.TestCase):
    def test_algebra_matches_pointwise_semantics(self):
        rng = random.Random(1)
        for _ in range(300):
            a, b = rand_region(rng), rand_region(rng)
            d = F(rng.randint(-6, 6), 2)
            for x in samples(a, b, a.shift(d)):
                self.assertEqual(x in (a | b), x in a or x in b)
                self.assertEqual(x in (a & b), x in a and x in b)
                self.assertEqual(x in (a - b), x in a and x not in b)
                self.assertEqual(x in a.shift(d), (x - d) in a)
            self.assertEqual(a.shift(d).shift(-d), a)

    def test_periodic_algebra_matches_pointwise_semantics(self):
        """Regions that repeat forever: every operation is right at every point
        (tested on a dense set of points), and equal sets are equal regions."""
        rng = random.Random(2)

        def rand_periodic():
            out = rand_region(rng, -4, 4)
            for _ in range(rng.randint(1, 2)):
                step = F(rng.randint(1, 4), rng.randint(1, 2))
                cell = rand_region(rng, 0, 2) & Region.interval(0, step * rng.randint(1, 2), True, False)
                k = rng.randint(-3, 3)
                out |= Region.tiled(cell, step, *rng.choice([(k, None), (None, k), (None, None)]))
            return out

        def points(*regions):
            pts = set(range(-30, 31))
            for r in regions:
                for e in r.ends(-30, 30):
                    pts |= {e, e + F(1, 97), e - F(1, 97)}
            return sorted(pts) + [F(x, 7) for x in range(-200, 200)]

        for _ in range(60):
            a, b = rand_periodic(), rand_periodic()
            d = F(rng.randint(-6, 6), 2)
            union, inter, diff, moved = a | b, a & b, a - b, a.shift(d)
            for x in points(a, b, moved):
                self.assertEqual(x in union, x in a or x in b)
                self.assertEqual(x in inter, x in a and x in b)
                self.assertEqual(x in diff, x in a and x not in b)
                self.assertEqual(x in moved, (x - d) in a)
            self.assertEqual(moved.shift(-d), a)
            self.assertEqual((a - b) | (a & b), a)  # the same set, built differently
            self.assertEqual((a | b) - b, a - b)
            self.assertEqual(a & a, a)
        c = Region.interval(0, 1, True, False)
        self.assertEqual(Region.tiled(c, 2, 0, None), Region.tiled(c, 4, 0, None) | Region.tiled(c, 4, 0, None).shift(2))
        self.assertEqual(Region.tiled(c, 1, 0, None), Region(((F(0), True, math.inf, False),)))  # constant: a ray

    def test_open_endpoints_after_removal(self):
        r = Region.closed(0, 2) - Region.closed(0, 1)
        self.assertEqual(r, Region.interval(1, 2, left_closed=False))
        self.assertNotIn(1, r)

    def test_canonical_equality(self):
        self.assertEqual(Region.closed(0, 1) | Region.closed(1, 2), Region.closed(0, 2))
        self.assertEqual(Region.open(0, 1) | Region.point(1) | Region.open(1, 2), Region.open(0, 2))
        self.assertEqual(Region.point(0.1).start, F(1, 10))


class RuleTests(unittest.TestCase):
    def test_cycle_rejected(self):
        with self.assertRaises(RuleCycleError):
            LineSystem([implies("A", ("B", 1)), implies("B", ("A", 1))])
        LineSystem([forbids("A", "B"), forbids("B", "A")])  # symmetric clause, no production cycle

    def test_requirements_example_1(self):
        line = LineSystem([forbids("D", "C")])
        r67 = Region.closed(6, 7)
        line.apply([Add("A", Region.closed(1, 2)), Add("B", Region.closed(1, 4)), Add("D", r67)])
        initial = line.state()
        a = line.apply([Move.to("A", Region.interval(1, 2, True, False), 3),
                        Move.to("B", Region.point(4), 5), Add("C", r67)])
        after_a = line.state()
        self.assertEqual(after_a["C"], r67)
        self.assertNotIn("D", after_a)
        self.assertEqual(after_a["B"] & Region.closed(1, 4), Region.interval(1, 4, True, False))
        self.assertEqual(after_a["A"] & Region.closed(1, 2), Region.point(2))
        b = line.apply([Add("D", r67)])
        self.assertEqual(line.state()["D"], r67)
        self.assertNotIn("C", line.state())
        line.reverse(b)
        self.assertEqual(line.state(), after_a)
        line.reverse(a)
        self.assertEqual(line.state(), initial)
        with self.assertRaises(ContradictionError):
            line.apply([Add("C", Region.point(8)), Add("D", Region.point(8))])
        self.assertEqual(line.state(), initial)

    def drag_states(self, rules, start, block, steps):
        line = LineSystem(rules)
        line.apply([Add(*start)])
        line.apply([Add(*block)])
        drag = line.drag([block])
        states = []
        for d in steps:
            drag.step(d)
            states.append(line.state())
        drag.drop()
        return line, states

    def test_requirements_example_2(self):
        steps = [F(i, 8) for i in range(25)]
        line, states = self.drag_states([forbids("A", "B")], ("A", Region.closed(0, 2)),
                                        ("B", Region.closed(0, 1)), steps)
        for d, st in zip(steps, states):  # A is cut by B, and comes back behind it
            b = Region.closed(d, 1 + d)
            self.assertEqual(st, {"A": Region.closed(0, 2) - b, "B": b})
        self.assertEqual(line.state(), {"A": Region.closed(0, 2), "B": Region.closed(3, 4)})

    def test_requirements_example_3(self):
        steps = [F(i, 8) for i in range(25)]
        line, states = self.drag_states([forbids("D", "C")], ("D", Region.point(2)),
                                        ("C", Region.closed(0, 1)), steps)
        for d, st in zip(steps, states):  # D is gone exactly while C covers it
            self.assertEqual("D" in st, not (d <= 2 <= 1 + d))
        direct = LineSystem([forbids("D", "C")])
        direct.apply([Add("D", Region.point(2))])
        direct.apply([Add("C", Region.closed(3, 4))])
        self.assertEqual(line.state(), direct.state())

    def test_drag_back_to_start_restores_the_line(self):
        line = LineSystem([forbids("D", "C")])
        line.apply([Add("D", Region.point(2))])
        line.apply([Add("C", Region.closed(0, 1))])
        before = line.state()
        drag = line.drag([("C", Region.closed(0, 1))])
        for d in (1, 2, F(3, 2), 0):
            drag.step(d)
        self.assertIsNone(drag.drop())
        self.assertEqual(line.state(), before)

    def test_drag_onto_same_kind_is_refused(self):
        line = LineSystem()
        line.apply([Add("C", Region.closed(0, 1)), Add("C", Region.closed(5, 6))])
        drag = line.drag([("C", Region.closed(0, 1))])
        drag.step(2)
        with self.assertRaises(MissingElementError):
            drag.step(4)  # [4, 5] meets C on [5, 6]
        self.assertEqual(line.state()["C"], Region.closed(2, 3) | Region.closed(5, 6))

    def test_extend_and_retract(self):
        line = LineSystem()
        line.apply([Add("A", Region.closed(0, 1))])
        before = line.state()
        grow = [Extend("A", Region.interval(1, 2, False, True))]
        line.apply(grow)
        self.assertEqual(line.state(), {"A": Region.closed(0, 2)})
        line.apply(inverse(grow))
        self.assertEqual(line.state(), before)
        with self.assertRaises(MissingElementError):
            line.apply([Extend("A", Region.closed(3, 4))])  # touches nothing
        with self.assertRaises(MissingElementError):
            line.apply([Retract("A", Region.closed(0, 1))])  # nothing would remain
        with self.assertRaises(MissingElementError):
            line.apply([Retract("A", Region.closed(1, 2))])  # partly absent

    def test_putting_where_the_kind_is_already_present_is_refused(self):
        line = LineSystem([implies("A", "B")])
        line.apply([Add("A", Region.closed(0, 1)), Add("C", Region.closed(5, 6))])
        for ops in ([Add("A", Region.closed(1, 2))],
                    [Add("B", Region.point(0))],  # B is there by the rule
                    [Move("A", Region.closed(0, 1), F(1, 2)), Add("A", Region.closed(1, 2))]):
            with self.assertRaises((MissingElementError, ContradictionError)):
                line.apply(ops)
        line.apply([Move("C", Region.closed(5, 6), F(1, 2))])  # onto its own place: allowed

    def test_removing_what_a_rule_needs_makes_the_premise_adapt(self):
        line = LineSystem([implies("A", "B")])
        line.apply([Add("A", Region.closed(0, 2))])
        line.apply([Remove("B", Region.point(1))])
        self.assertEqual(line.state(), {"A": Region.closed(0, 2) - Region.point(1),
                                        "B": Region.closed(0, 2) - Region.point(1)})

    def test_reverse_all_takes_the_dependent_edits(self):
        line = LineSystem()
        a = line.apply([Add("E", Region.point(0))])
        b = line.apply([Move("E", Region.point(0), 5)])
        c = line.apply([Add("G", Region.point(9))])
        self.assertEqual(line.reverse_all([a]), [a, b])
        self.assertEqual(line.state(), {"G": Region.point(9)})
        self.assertTrue(line._edits[c].active)

    def test_implication_adds_and_contrapositive_adapts(self):
        line = LineSystem([implies("A", "B")])
        line.apply([Add("A", Region.closed(0, 2))])
        self.assertEqual(line.state()["B"], Region.closed(0, 2))
        line.apply([Remove("B", Region.closed(1, 2))])
        self.assertEqual(line.state(), {"A": Region.interval(0, 1, True, False),
                                        "B": Region.interval(0, 1, True, False)})

    def test_conjunction_with_offset(self):
        line = LineSystem([implies(["A", "B"], ("C", -3))])
        line.apply([Add("A", Region.closed(0, 4)), Add("B", Region.closed(2, 6))])
        self.assertEqual(line.state()["C"], Region.closed(-1, 1))

    def test_disjunction_uses_first_operand_then_falls_back(self):
        line = LineSystem([implies_any("A", ["B", "C"])])
        line.apply([Add("A", Region.closed(0, 1))])
        self.assertEqual(line.state(), {"A": Region.closed(0, 1), "B": Region.closed(0, 1)})
        rm = [Remove("B", Region.closed(0, 1))]
        line.apply(rm)  # B is forbidden by a newer edit: A's older obligation falls back to C
        self.assertEqual(line.state(), {"A": Region.closed(0, 1), "C": Region.closed(0, 1)})
        line.apply(inverse(rm))
        self.assertEqual(line.state(), {"A": Region.closed(0, 1), "B": Region.closed(0, 1)})

    def test_disjunction_in_one_edit_picks_allowed_operand(self):
        line = LineSystem([implies_any("A", ["B", "C"]), forbids("D", "B"), forbids("E", "C")])
        line.apply([Add("A", Region.point(0)), Add("D", Region.point(0))])
        self.assertEqual(line.state(), {"A": Region.point(0), "C": Region.point(0), "D": Region.point(0)})
        with self.assertRaises(ContradictionError):
            line.apply([Add("A", Region.point(5)), Add("D", Region.point(5)), Add("E", Region.point(5))])

    def test_add_and_remove_same_place_in_one_edit_rejected(self):
        line = LineSystem()
        line.apply([Add("A", Region.closed(0, 3))])
        with self.assertRaises(ContradictionError):
            line.apply([Add("A", Region.closed(1, 2)), Remove("A", Region.closed(1, 3))])
        with self.assertRaises(ContradictionError):
            line.apply([Remove("A", Region.closed(0, 1)), Move("A", Region.closed(2, 3), -2)])

    def test_removing_or_moving_what_is_not_there_is_refused(self):
        line = LineSystem([implies("A", "B")])
        line.apply([Add("A", Region.closed(0, 1))])
        for ops in ([Remove("C", Region.point(0))],
                    [Remove("A", Region.closed(0, 2))],  # partly absent
                    [Move("A", Region.closed(1, 2), 5)]):
            with self.assertRaises(MissingElementError):
                line.apply(ops)
        self.assertEqual(line.state(), {"A": Region.closed(0, 1), "B": Region.closed(0, 1)})
        line.apply([Remove("B", Region.point(0))])  # derived elements are on the line too

    def test_disjunction_refused_only_if_no_operands_work(self):
        rules = [implies_any("A", ["B", "C"]), forbids("B", ("B", 1))]
        line = LineSystem(rules)
        line.apply([Add("A", Region.points(0, 1))])
        self.assertEqual(line.state(), {"A": Region.points(0, 1), "B": Region.point(0), "C": Region.point(1)})
        line = LineSystem(rules)
        line.apply([Add("A", Region.closed(0, 3))])
        self.assertEqual(line.state()["B"], Region.interval(0, 1, True, False) | Region.interval(2, 3, True, False))

    def test_older_positions_that_cannot_be_placed_adapt_alone(self):
        rules = [implies_any("A", ["B", "C"]), forbids("B", ("B", 1)), forbids("X", "B"), forbids("X", "C")]
        line = LineSystem(rules)
        line.apply([Add("A", Region.points(0, 1, 10))])
        before = {"A": Region.points(0, 1, 10), "B": Region.points(0, 10), "C": Region.point(1)}
        self.assertEqual(line.state(), before)
        x = line.apply([Add("X", Region.point(10))])
        self.assertEqual(line.state(), {"A": Region.points(0, 1), "B": Region.point(0), "C": Region.point(1),
                                        "X": Region.point(10)})
        line.reverse(x)
        self.assertEqual(line.state(), before)
        # Positions that cannot all stay are kept closest to 0 first.
        line = LineSystem([implies_any("A", ["B", "C"]), forbids("B", ("B", 1)), forbids("X", "C")])
        line.apply([Add("A", Region.points(1, 2))])
        line.apply([Add("X", Region.points(1, 2))])
        self.assertEqual(line.state(), {"A": Region.point(1), "B": Region.point(1), "X": Region.points(1, 2)})
        # A position that is not hit still takes the first operand that can be placed.
        line = LineSystem([implies_any("A", ["B", "C"]), forbids("B", ("B", 1)), forbids("X", "B")])
        line.apply([Add("A", Region.points(1, 2))])
        self.assertEqual(line.state(), {"A": Region.points(1, 2), "B": Region.point(1), "C": Region.point(2)})
        line.apply([Add("X", Region.point(1))])
        self.assertEqual(line.state(), {"A": Region.points(1, 2), "B": Region.point(2), "C": Region.point(1),
                                        "X": Region.point(1)})

    def test_requires(self):
        line = LineSystem([forbids("F", "E")])
        a = line.apply([Add("E", Region.point(0))])
        b = line.apply([Remove("E", Region.point(0))])  # removes what A added
        self.assertTrue(line.requires(b, a))
        self.assertFalse(line.requires(line.apply([Add("F", Region.point(1))]), a))
        c = line.apply([Add("G", Region.point(9))])
        self.assertFalse(line.requires(c, a))

    def test_reversing_a_required_edit_is_refused(self):
        line = LineSystem()
        a = line.apply([Add("E", Region.point(0))])
        b = line.apply([Move("E", Region.point(0), 5)])
        self.assertTrue(line.requires(b, a))
        with self.assertRaises(DependencyError):
            line.reverse(a)
        self.assertEqual(line.state(), {"E": Region.point(5)})
        line.reverse(b)
        line.reverse(a)
        self.assertEqual(line.state(), {})


# ---------------------------------------------------------------------------
# Randomized properties
# ---------------------------------------------------------------------------

KINDS = ["A", "B", "C", "D", "E"]


def rand_rules(rng: random.Random):
    rules = []
    for _ in range(rng.randint(1, 4)):
        i, j = sorted(rng.sample(range(len(KINDS)), 2))  # production goes forward: acyclic
        off = F(rng.choice([-1, 0, 0, 1]))
        t = rng.random()
        if t < 0.35:
            rules.append(implies(KINDS[i], (KINDS[j], off)))
        elif t < 0.6:
            rules.append(forbids(KINDS[rng.randrange(5)], (KINDS[rng.randrange(5)], off)))
        elif t < 0.8 and j + 1 < len(KINDS):
            k = rng.randrange(j + 1, len(KINDS))
            rules.append(implies_any(KINDS[i], [(KINDS[j], off), KINDS[k]]))
        elif i + 1 < j:
            rules.append(implies([KINDS[i], (KINDS[i + 1], off)], KINDS[j]))
    return [r for r in rules if not (hasattr(r, "head") and r.premises == (r.head,))]


def rand_edit(rng: random.Random, state):
    """Mostly edits that fit the line: adds where the kind is absent, removes
    and moves of what is there, extensions and retractions at an end."""
    ops = []
    for _ in range(rng.randint(1, 3)):
        k, reg = rng.choice(KINDS), rand_region(rng)
        t = rng.random()
        if t >= 0.4 and state:
            k = rng.choice(sorted(state))
            have = state[k]
            lo, _, hi, hc = rng.choice(have.parts)
            d = F(rng.randint(1, 4), 2)
            if t >= 0.85:
                ops.append(Extend(k, Region.interval(hi, hi + d, not hc, True) - have) if rng.random() < 0.5
                           else Retract(k, Region.interval(max(lo, hi - d), hi, False, hc) if lo < hi else have))
                continue
            reg = rand_region(rng) & have if rng.random() < 0.7 else have
        if t < 0.4 or not reg:
            ops.append(Add(k, (reg or rand_region(rng)) - state.get(k, EMPTY) or rand_region(rng)))
        else:
            ops.append(Remove(k, reg) if t < 0.6 else Move(k, reg, F(rng.randint(-4, 4), 2)))
    return [op for op in ops if op.region]


def try_apply(line: LineSystem, ops):
    try:
        return line.apply(ops)
    except (ContradictionError, MissingElementError):
        return None


def brute_force_consistent(ruleset, adds: dict[str, set[int]]) -> bool:
    """Is there any choice of operands under which the closure of `adds`
    breaks no rule?  Plain enumeration over integer points (integer offsets)."""
    order = ruleset.order(adds)
    idx = {k: i for i, k in enumerate(order)}
    xs = range(-20, 30)

    def holds(state, atoms, x):
        return all((x + int(o)) in state.get(a, set()) for a, o in atoms)

    def run(state, rules):
        if not rules:
            return True
        r, rest = rules[0], rules[1:]
        if isinstance(r, Forbids):
            return not any(holds(state, r.premises + (r.head,), x) for x in xs) and run(state, rest)
        anchors = [x for x in xs if holds(state, r.premises, x)]
        options = [r.head] if isinstance(r, Implies) else r.options
        for pick in itertools.product(range(len(options)), repeat=len(anchors)):
            st = {k: set(v) for k, v in state.items()}
            for x, j in zip(anchors, pick):
                k, o = options[j]
                st.setdefault(k, set()).add(x + int(o))
            if run(st, rest):
                return True
        return False

    # Same firing order as the solver: a rule fires once every kind it reads is final.
    def fire_at(r):
        return max(idx[a] for a, _ in r.premises + ((r.head,) if isinstance(r, Forbids) else ()))
    return run({k: set(v) for k, v in adds.items()}, sorted(ruleset.rules, key=fire_at))


class PropertyTests(unittest.TestCase):
    def check_invariants(self, line: LineSystem, newest):
        st = line.state()
        self.assertEqual(line.ruleset.violations(st), [], "rules violated")
        if newest is not None:  # R5: the newest edit's effective claims all hold
            pos, neg = line.effective(newest)
            for k, r in pos.items():
                self.assertEqual(r - st.get(k, EMPTY), EMPTY)
            for k, r in neg.items():
                self.assertEqual(r & st.get(k, EMPTY), EMPTY)

    def test_random_histories(self):
        for seed in range(SEEDS):
            rng = random.Random(seed)
            with self.subTest(seed=seed):
                rules = rand_rules(rng)
                line = LineSystem(rules)
                ids = []
                for _ in range(rng.randint(3, 6)):
                    ops = rand_edit(rng, line.state())
                    before = line.state()
                    eid = try_apply(line, ops)
                    if eid is None:
                        self.assertEqual(line.state(), before)  # rejected edits change nothing
                        continue
                    ids.append(eid)
                    self.check_invariants(line, eid)

                    # A drag step is the line of a direct move, whatever the previous steps
                    if line.state() and rng.random() < 0.3:
                        k = rng.choice(sorted(line.state()))
                        block = [(k, line.state()[k])]
                        undragged = line.state()
                        drag = line.drag(block)
                        for _ in range(3):
                            d = F(rng.randint(-6, 6), 2)
                            direct = None
                            try:
                                direct = line._validate([e.ops for e in line._active()], line._base,
                                                        [Move(k, block[0][1], d)] if d else []).state
                            except (ContradictionError, MissingElementError):
                                pass
                            shown = line.state()
                            try:
                                drag.step(d)
                            except (ContradictionError, MissingElementError):
                                self.assertIsNone(direct)
                                self.assertEqual(line.state(), shown)
                                continue
                            self.assertEqual(line.state(), direct)
                        drag.cancel()
                        self.assertEqual(line.state(), undragged)

                    # R1/R2/R3 and extend/retract: an edit followed by its inverse restores the line
                    if rng.random() < 0.5:
                        inv = line.apply(inverse(ops))  # must never be rejected
                        self.assertEqual(line.state(), before)
                        line.reverse(inv)  # keep the history going from the edit
                        self.check_invariants(line, eid)

                # R4: reversing an edit gives the line as if it had never been made.
                # It is refused, and changes nothing, iff a later edit requires it.
                active = list(ids)
                while active:
                    victim = rng.choice(active)
                    before = line.state()
                    try:
                        line.reverse(victim)
                    except DependencyError:
                        self.assertEqual(line.state(), before)
                        self.assertTrue(any(line.requires(i, victim) for i in active if i > victim))
                        victim = active[-1]  # the newest edit can always be reversed
                        line.reverse(victim)
                    active.remove(victim)
                    self.check_invariants(line, None)
                    replay = LineSystem(rules)
                    replayed = [try_apply(replay, line._edits[i].ops) for i in active]
                    self.assertTrue(all(r is not None for r in replayed))
                    self.assertEqual(line.state(), replay.state())
                self.assertEqual(line.state(), {})

    def test_determinism(self):
        for seed in range(SEEDS // 3):
            rng = random.Random(1000 + seed)
            rules = rand_rules(rng)
            edits = [rand_edit(rng, {}) for _ in range(4)]
            states = []
            for _ in range(2):
                line = LineSystem(rules)
                for ops in edits:
                    try_apply(line, ops)
                states.append(line.state())
            self.assertEqual(states[0], states[1])

    def test_refused_iff_no_operands_work(self):
        # An edit is refused only if no choice of operands satisfies the rules.
        for seed in range(SEEDS * 3):
            rng = random.Random(5000 + seed)
            rules = rand_rules(rng)
            rules.append(implies_any(KINDS[0], [KINDS[1], (KINDS[2], rng.choice([-1, 1]))]))
            rules.append(forbids(KINDS[rng.randrange(1, 5)], (KINDS[rng.randrange(1, 5)], rng.choice([-1, 0, 1]))))
            adds = {k: set(rng.sample(range(6), rng.randint(1, 3))) for k in rng.sample(KINDS, 2)}
            with self.subTest(seed=seed):
                line = LineSystem(rules)
                accepted = try_apply(line, [Add(k, Region.points(*xs)) for k, xs in adds.items()]) is not None
                self.assertEqual(accepted, brute_force_consistent(line.ruleset, adds))

    def test_older_claims_are_kept_closest_to_0_first(self):
        # A newer edit leaves some positions of an older one no operand: the
        # older claims are kept one after the other, closest to 0 first, each
        # if some choice of operands satisfies the rules with the ones before.
        tried = 0
        for seed in range(SEEDS * 3):
            rng = random.Random(8000 + seed)
            rules = rand_rules(rng)
            o = rng.choice([-1, 1])
            rules.append(implies_any(KINDS[0], [KINDS[1], (KINDS[2], o)]))
            # X often leaves an A no operand.
            rules += [forbids("X", KINDS[1]), forbids("X", (KINDS[2], rng.choice([o, o, 0]))),
                      forbids("X", (rng.choice(KINDS[1:]), rng.choice([-1, 0, 1])))]
            old = {KINDS[0]: set(rng.sample(range(-3, 4), rng.randint(2, 5))),
                   rng.choice(KINDS[1:]): set(rng.sample(range(-3, 4), rng.randint(1, 3)))}
            new = {"X": set(rng.sample(range(-3, 4), rng.randint(1, 3)))}
            line = LineSystem(rules)
            if try_apply(line, [Add(k, Region.points(*xs)) for k, xs in old.items()]) is None:
                continue
            definite = RuleSet([r for r in rules if not isinstance(r, ImpliesAny)])
            if not brute_force_consistent(definite, {**old, **new}):
                continue  # a conflict no operand avoids: the older claims adapt by their support
            tried += 1
            with self.subTest(seed=seed):
                line.apply([Add("X", Region.points(*new["X"]))])
                order = {k: i for i, k in enumerate(line.ruleset.order(KINDS + ["X"]))}
                kept: dict[str, set[int]] = {}
                for x, k in sorted(((x, k) for k, xs in old.items() for x in xs),
                                   key=lambda a: (abs(a[0]), a[0], order[a[1]])):
                    trial = {kk: set(v) for kk, v in kept.items()}
                    trial.setdefault(k, set()).add(x)
                    if brute_force_consistent(line.ruleset, {**trial, **new}):
                        kept = trial
                dropped = line._solve([e.ops for e in line._active()])[0].dropped[0][0]
                for k, xs in old.items():
                    self.assertEqual(Region.points(*xs) - dropped.get(k, EMPTY),
                                     Region.points(*kept.get(k, ())) if kept.get(k) else EMPTY)
        self.assertGreater(tried, SEEDS)


class UnboundedTests(unittest.TestCase):
    """Elements may be rays or the whole line."""
    ray = Region(((F(0), True, math.inf, False),))

    def test_definite_rules_on_rays(self):
        line = LineSystem([implies("A", ("B", -1)), forbids("B", "C")])
        line.apply([Add("C", Region.closed(-5, 5))])
        a = line.apply([Add("A", self.ray)])
        self.assertEqual(line.state()["B"], Region(((F(-1), True, math.inf, False),)))
        self.assertEqual(line.state()["C"], Region.interval(-5, -1, True, False))
        line.reverse(a)
        self.assertEqual(line.state(), {"C": Region.closed(-5, 5)})

    def test_every_anchor_takes_its_first_possible_operand(self):
        line = LineSystem([implies_any("A", ["B", "C"]), forbids(("B", 0), ("D", 0))])
        line.apply([Add("A", FULL)])
        self.assertEqual(line.state(), {"A": FULL, "B": FULL})
        line = LineSystem([implies_any("A", ["B", "C"]), forbids("B", "D")])
        line.apply([Add("A", self.ray), Add("D", Region.closed(2, 3))])
        self.assertEqual(line.state()["C"], Region.closed(2, 3))
        self.assertEqual(line.state()["B"], self.ray - Region.closed(2, 3))

    def test_competing_anchors_closest_to_0_first(self):
        """B at x forbids B at x + 1, so neighbours compete for B.  Positions are
        decided closest to 0 first (the left one first on a tie), each taking the
        first operand that still lets the others be placed; on infinitely many
        positions the result repeats forever."""
        rules = [implies_any("A", ["B", "C"]), forbids("B", ("B", 1))]
        unit = Region.interval(0, 1, True, False)

        def state(a):
            line = LineSystem(rules)
            line.apply([Add("A", a)])
            return line.state()

        got = state(self.ray)  # every lattice x + Z starts at its point in [0, 1)
        self.assertEqual(got["B"], Region.tiled(unit, 2, 0, None))
        self.assertEqual(got["C"], Region.tiled(unit, 2, 0, None).shift(1))
        self.assertEqual(state(Region.points(-2, -1))["B"], Region.point(-1))  # -1 is closer to 0
        self.assertEqual(state(Region.points(-1, 1))["B"], Region.points(-1, 1))  # not neighbours
        self.assertEqual(state(Region.points(-1, 0, 1))["B"], Region.points(-1, 1) - Region.points(-1, 1)
                         | Region.point(0))
        for a in (FULL, Region(((-math.inf, False, F(5), True),))):
            b = state(a)["B"]
            for n in range(-160, 41):
                x = F(n, 8)
                r = x - math.floor(x)
                first = r if r < F(1, 2) else r - 1  # the lattice's position closest to 0 (left on a tie)
                self.assertEqual(x in b, (x - first) % 2 == 0, (a, x))

    def test_infinite_positions_agree_with_long_finite_ones(self):
        """When the last operand can always be placed, the decisions near 0 only
        depend on the positions closer to 0, so an element on a ray or on the whole
        line gives, near 0, what the same element cut far away gives."""
        rng = random.Random(7)
        for _ in range(SEEDS // 2):
            off = rng.choice([F(1), F(2), F(1, 2), F(3, 2)])
            rules = [implies_any("A", ["B", "C", "D"]), forbids("B", ("B", off)),
                     forbids("B", ("C", rng.choice([F(-1), F(1, 2), F(1)]))), forbids("C", ("C", off * 2))]
            if rng.random() < 0.5:
                rules.append(implies("B", ("E", rng.choice([F(1), F(-1, 2)]))))
                rules.append(forbids("E", ("C", 0)))
            lo = rng.choice([-math.inf, F(rng.randint(-6, 0))])
            hi = rng.choice([math.inf, F(rng.randint(1, 6))]) if not math.isinf(lo) else math.inf
            a = Region(((lo, True, hi, True),)) if not math.isinf(lo) else Region(((lo, False, hi, not math.isinf(hi)),))
            if math.isinf(hi):
                a = Region(((lo, not math.isinf(lo), hi, False),))
            near = Region.closed(-4, 4)
            full = LineSystem(rules)
            full.apply([Add("A", a)])
            cut = LineSystem(rules)
            cut.apply([Add("A", a & Region.closed(-20, 20))])
            for kind in "BCD":
                self.assertEqual(full.state().get(kind, EMPTY) & near, cut.state().get(kind, EMPTY) & near,
                                 (rules, a, kind))

    def test_older_positions_on_a_ray_adapt_alone(self):
        rules = [implies_any("A", ["B", "C"]), forbids("B", ("B", 1)), forbids("X", "B"), forbids("X", "C")]
        line = LineSystem(rules)
        line.apply([Add("A", self.ray)])
        before = line.state()
        x = line.apply([Add("X", Region.point(10))])
        st = line.state()
        self.assertEqual(st["A"], self.ray - Region.point(10))
        for n in range(0, 40):
            self.assertEqual(n in st["B"], n < 10 and n % 2 == 0 or n > 10 and n % 2 == 1, n)
            self.assertEqual(n in st["C"], n < 10 and n % 2 == 1 or n > 10 and n % 2 == 0, n)
        self.assertEqual(st["B"] & Region.open(10, 11), Region.open(10, 11))  # other lattices: as before
        line.reverse(x)
        self.assertEqual(line.state(), before)

    def test_older_positions_kept_on_infinite_elements_agree_with_long_finite_ones(self):
        """The claims kept are decided closest to 0 first, each from the ones
        before it, so near 0 an element on a ray or on the whole line keeps what
        the same element cut far away keeps (D can always be placed far away)."""
        rng = random.Random(11)
        for _ in range(SEEDS // 2):
            o = rng.choice([F(-1), F(1)])
            rules = [implies_any("A", ["B", ("C", o), "D"]), forbids("B", ("B", rng.choice([F(1), F(2)]))),
                     forbids("C", ("C", rng.choice([F(1), F(-2)]))), forbids("X", "B"),
                     forbids("X", ("C", rng.choice([o, o, F(0)]))), forbids("X", ("D", rng.choice([F(0), F(1)])))]
            if rng.random() < 0.5:
                rules.append(implies("D", ("E", rng.choice([F(1), F(-1)]))))
                rules.append(forbids("E", "B"))
            a = rng.choice([self.ray, FULL, Region(((-math.inf, False, F(rng.randint(3, 8)), True),))])
            xs = Region.points(*rng.sample(range(-5, 6), rng.randint(1, 4)))
            near = Region.closed(-6, 6)
            states = []
            for reg in (a, a & Region.closed(-30, 30)):
                line = LineSystem(rules)
                line.apply([Add("A", reg)])
                line.apply([Add("X", xs)])
                states.append(line.state())
            for kind in "ABCDE":
                self.assertEqual(states[0].get(kind, EMPTY) & near, states[1].get(kind, EMPTY) & near,
                                 (rules, a, xs, kind))

    def test_bounded_anchors_among_unbounded_elements(self):
        for rules in ([implies_any("A", ["B", "C"]), forbids("B", ("B", 1))],
                      [implies_any("A", ["B", "C"]), forbids("B", ("Z", 1))]):
            plain, with_ray = LineSystem(rules), LineSystem(rules)
            with_ray.apply([Add("Z", Region(((F(5), True, math.inf, False),)))])
            plain.apply([Add("Z", Region.closed(5, 100))])
            for line in (plain, with_ray):
                line.apply([Add("A", Region.points(0, 1, 2, 4, Fraction(9, 2)))])
            self.assertEqual(plain.state()["B"], with_ray.state()["B"])
            self.assertEqual(plain.state().get("C"), with_ray.state().get("C"))


if __name__ == "__main__":
    unittest.main()
