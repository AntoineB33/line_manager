"""Tests: python -m unittest test_reverse_logic   (REVLOGIC_SEEDS=n for more random runs)."""
from __future__ import annotations

import os
import random
import unittest
from fractions import Fraction

from reverse_logic import (EMPTY, Add, ContradictionError, LineSystem, Move, Region, Remove,
                           RuleCycleError, forbids, implies, implies_any, inverse)

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
    for a, b in zip(ends, ends[1:]):
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

    def test_requirements_example(self):
        line = LineSystem([forbids("D", "C")])
        r67 = Region.closed(6, 7)
        line.apply([Add("D", r67), Add("A", Region.closed(1, 2)), Add("B", Region.point(4))])
        initial = line.state()
        a = line.apply([Move.to("A", Region.closed(1, 2), 3), Move.to("B", Region.point(4), 5), Add("C", r67)])
        after_a = line.state()
        self.assertEqual(after_a["C"], r67)
        self.assertNotIn("D", after_a)
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

    def test_implication_adds_and_contrapositive_adapts(self):
        line = LineSystem([implies("A", "B")])
        line.apply([Add("A", Region.closed(0, 2))])
        self.assertEqual(line.state()["B"], Region.closed(0, 2))
        line.apply([Remove("B", Region.closed(1, 3))])
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
        line = LineSystem([implies_any("A", ["B", "C"])])
        line.apply([Add("A", Region.point(0)), Remove("B", Region.point(0))])
        self.assertEqual(line.state(), {"A": Region.point(0), "C": Region.point(0)})
        with self.assertRaises(ContradictionError):
            line.apply([Add("A", Region.point(5)), Remove("B", Region.point(5)), Remove("C", Region.point(5))])

    def test_add_and_remove_same_place_in_one_edit_rejected(self):
        line = LineSystem()
        with self.assertRaises(ContradictionError):
            line.apply([Add("A", Region.closed(0, 2)), Remove("A", Region.closed(1, 3))])

    def test_requires(self):
        line = LineSystem([forbids("F", "E")])
        line.apply([Add("E", Region.point(0))])
        a = line.apply([Remove("E", Region.point(0))])
        b = line.apply([Add("E", Region.point(0)), Add("F", Region.point(0))])
        self.assertTrue(line.requires(b, a))
        c = line.apply([Add("G", Region.point(9))])
        self.assertFalse(line.requires(c, a))


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


def rand_edit(rng: random.Random):
    ops = []
    for _ in range(rng.randint(1, 3)):
        k, reg = rng.choice(KINDS), rand_region(rng)
        t = rng.random()
        ops.append(Add(k, reg) if t < 0.5 else Remove(k, reg) if t < 0.75 else Move(k, reg, F(rng.randint(-4, 4), 2)))
    return ops


def try_apply(line: LineSystem, ops):
    try:
        return line.apply(ops)
    except ContradictionError:
        return None


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
                    ops = rand_edit(rng)
                    before = line.state()
                    eid = try_apply(line, ops)
                    if eid is None:
                        self.assertEqual(line.state(), before)  # rejected edits change nothing
                        continue
                    ids.append(eid)
                    self.check_invariants(line, eid)

                    # R1/R2/R3: an edit followed by its inverse restores the line
                    if rng.random() < 0.5:
                        inv = line.apply(inverse(ops))  # must never be rejected
                        self.assertEqual(line.state(), before)
                        line.reverse(inv)  # keep the history going from the edit
                        self.check_invariants(line, eid)

                # R4: reversing any edit gives the line as if it had never been made
                active = list(ids)
                while active:
                    victim = rng.choice(active)
                    active.remove(victim)
                    line.reverse(victim)
                    self.check_invariants(line, None)
                    replay = LineSystem(rules)
                    replayed = [try_apply(replay, line._edits[i].ops) for i in active]
                    if all(r is not None for r in replayed):  # no remaining edit required the victim
                        self.assertEqual(line.state(), replay.state())
                    else:
                        self.assertTrue(any(line.requires(i, victim) for i in active))
                self.assertEqual(line.state(), {})

    def test_determinism(self):
        for seed in range(SEEDS // 3):
            rng = random.Random(1000 + seed)
            rules = rand_rules(rng)
            edits = [rand_edit(rng) for _ in range(4)]
            states = []
            for _ in range(2):
                line = LineSystem(rules)
                for ops in edits:
                    try_apply(line, ops)
                states.append(line.state())
            self.assertEqual(states[0], states[1])


if __name__ == "__main__":
    unittest.main()
