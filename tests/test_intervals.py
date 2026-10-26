import random
import unittest
from fractions import Fraction as F

from helpers import GRID, POSITIONS, rand_set
from intervals import EPS, INF, Interval, IntervalSet, Pos, parse_interval


def members(s):
    return {x for x in GRID if s.contains(x)}


class PositionTest(unittest.TestCase):
    """Positions x + kε: ε is positive and smaller than any positive real."""

    def test_order(self):
        self.assertTrue(Pos(1) < Pos(1, 1) < Pos(1, 2) < F(10001, 10000))
        self.assertTrue(Pos(1, -1) < 1 < Pos(1, 1))
        self.assertTrue(-INF < Pos(-10 ** 9, -5) and Pos(10 ** 9, 5) < INF)
        self.assertEqual(Pos(3), F(3))
        self.assertEqual(hash(Pos(3)), hash(F(3)))
        self.assertNotEqual(Pos(3, 1), F(3))

    def test_arithmetic(self):
        self.assertEqual(Pos(1, 1) + 1, Pos(2, 1))
        self.assertEqual(Pos(1, 1) + Pos(1, 1), Pos(2, 2))
        self.assertEqual(-Pos(1, 1), Pos(-1, -1))
        self.assertEqual(3 * Pos(1, 1), Pos(3, 3))
        self.assertEqual(Pos(2, 2) // Pos(1, 1), 2)
        self.assertEqual(Pos(2, 1) // Pos(1, 1), 1)  # 2+ε < 2+2ε
        self.assertEqual(Pos(2, 1) % Pos(1, 1), Pos(1, 0))
        self.assertEqual(Pos(F(5, 2)) % 1, Pos(F(1, 2)))
        self.assertEqual(Pos(2, -1) % 1, Pos(1, -1))
        for x in POSITIONS[::7]:
            for p in (Pos(1), Pos(F(3, 2), 1), Pos(2, -1)):
                n = x // p
                self.assertTrue(n * p <= x < (n + 1) * p, (x, p))

    def test_open_ends_are_the_next_position(self):
        self.assertEqual(Interval.open_closed(0, 1), Interval.closed(EPS, 1))
        self.assertEqual(str(Interval.closed(EPS, 1)), "(0, 1]")
        self.assertEqual(str(Interval.closed(Pos(1, 2), Pos(2, 1))), "[1+2ε, 2+ε]")
        self.assertEqual(str(Interval.point(Pos(1, 1))), "{1+ε}")
        self.assertFalse(Interval.closed_open(0, 1).contains(1))
        self.assertTrue(Interval.closed_open(0, 1).contains(Pos(1, -1)))  # 1-ε is the last position before 1
        self.assertEqual(Interval.open(0, 1).length, 1)
        self.assertEqual(Interval.closed(Pos(1), Pos(1, 3)).length, 0)  # four positions, no length
        # touching intervals merge: nothing lies between 1 and 1+ε
        self.assertEqual(IntervalSet.of(Interval.closed(0, 1), Interval.closed(Pos(1, 1), 2)), IntervalSet.of(Interval.closed(0, 2)))
        self.assertEqual(len(IntervalSet.of(Interval.closed(0, 1), Interval.closed(Pos(1, 2), 2)).intervals), 2)
        self.assertEqual(IntervalSet.of(Interval.point(1)).complement(),
                         IntervalSet.of(Interval(-INF, Pos(1, -1), False, True), Interval(Pos(1, 1), INF, True, False)))


class IntervalSetTest(unittest.TestCase):
    def test_boolean_algebra_pointwise(self):
        rng = random.Random(1)
        for _ in range(400):
            a, b = rand_set(rng), rand_set(rng)
            for x in POSITIONS[::5]:
                self.assertEqual((a | b).contains(x), a.contains(x) or b.contains(x))
                self.assertEqual((a & b).contains(x), a.contains(x) and b.contains(x))
                self.assertEqual((a - b).contains(x), a.contains(x) and not b.contains(x))
                self.assertEqual(a.complement().contains(x), not a.contains(x))
                self.assertEqual((-a).contains(-x), a.contains(x))

    def test_canonical_form(self):
        rng = random.Random(2)
        for _ in range(200):
            ivs = list(rand_set(rng, 5).intervals) + list(rand_set(rng, 5).intervals)
            s1 = IntervalSet(ivs)
            rng.shuffle(ivs)
            self.assertEqual(s1, IntervalSet(ivs))
            for x, y in zip(s1.intervals, s1.intervals[1:]):  # disjoint, not touching
                self.assertTrue(x.hi + EPS < y.lo)
        self.assertEqual(IntervalSet.of(Interval.closed_open(0, 1), Interval.closed(1, 2)), IntervalSet.of(Interval.closed(0, 2)))
        self.assertEqual(len(IntervalSet.of(Interval.open(0, 1), Interval.open(1, 2)).intervals), 2)

    def test_minkowski_sum_exact(self):
        # endpoints are multiples of 1/2, so A ∩ (x - B) has endpoints on the 1/16 grid when x is:
        # if it is not empty it contains a point of the 1/32 grid
        rng = random.Random(3)
        fine = [Pos(F(i, 32), k) for i in range(-32 * 4, 32 * 4 + 1) for k in (-1, 0, 1)]
        for _ in range(60):
            a, b = rand_set(rng, 2, -2, 2), rand_set(rng, 2, -2, 2)
            s = a + b
            for x in POSITIONS[::6]:
                brute = any(a.contains(y) and b.contains(x - y) for y in fine)
                self.assertEqual(s.contains(x), brute, (a, b, x))

    def test_measure_points_take(self):
        s = IntervalSet.of(Interval.closed(0, 1), Interval.point(3), Interval.open(4, 6))
        self.assertEqual(s.measure(), 3)
        self.assertEqual(s.points(), 1)
        self.assertEqual(s.take(F(2)).measure(), 2)
        self.assertEqual(s.take(F(2), from_right=True), IntervalSet.of(Interval.open(4, 6)))
        self.assertEqual(s.take(F(1), from_right=True), IntervalSet.of(Interval.closed_open(5, 6)))
        self.assertEqual(IntervalSet.after(0).measure(), INF)

    def test_infinite_and_parse(self):
        self.assertEqual(parse_interval("[0, inf)"), Interval(F(0), INF, True, False))
        self.assertTrue(IntervalSet.universe().complement().is_empty())
        with self.assertRaises(ValueError):
            Interval(F(0), INF, True, True)
        with self.assertRaises(ValueError):
            Interval(F(1), F(1), True, False)


if __name__ == "__main__":
    unittest.main()
