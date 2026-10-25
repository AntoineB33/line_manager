import random
import unittest
from fractions import Fraction as F

from helpers import GRID, rand_set
from intervals import INF, Interval, IntervalSet, parse_interval


def members(s):
    return {x for x in GRID if s.contains(x)}


class IntervalSetTest(unittest.TestCase):
    def test_boolean_algebra_pointwise(self):
        rng = random.Random(1)
        for _ in range(400):
            a, b = rand_set(rng), rand_set(rng)
            for x in GRID[::3]:
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
                self.assertTrue(x.hi < y.lo or (x.hi == y.lo and not x.hi_closed and not y.lo_closed))
        self.assertEqual(IntervalSet.of(Interval.closed_open(0, 1), Interval.closed(1, 2)), IntervalSet.of(Interval.closed(0, 2)))
        self.assertEqual(len(IntervalSet.of(Interval.open(0, 1), Interval.open(1, 2)).intervals), 2)

    def test_minkowski_sum_exact(self):
        # endpoints are multiples of 1/2, so A ∩ (x - B) has endpoints on the 1/16 grid when x is:
        # if it is not empty it contains a point of the 1/32 grid
        rng = random.Random(3)
        fine = [F(i, 32) for i in range(-32 * 4, 32 * 4 + 1)]
        for _ in range(60):
            a, b = rand_set(rng, 2, -2, 2), rand_set(rng, 2, -2, 2)
            s = a + b
            for x in GRID[::2]:
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
