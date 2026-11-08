"""region.Lazy: a region whose operands repeat together only after a long period is kept as
the operation and computed where it is read. Whatever it answers is what the Region computed
at once answers: windows, positions, emptiness, equality, after any chain of operations."""

import random
import unittest.mock
import unittest
from fractions import Fraction as F

from helpers import Interval  # noqa: F401 (the paths of scripts/)
import region
from intervals import Interval as Iv
from ratio_plan import cycle
from region import Lazy, Region

DAY = 86400


def eager(r):
    return r.full() if isinstance(r, Lazy) else r


class LazyRegionTest(unittest.TestCase):
    def regions(self, rnd):
        """A night every day, breaks of 20 every 1220, and a few pieces: repeating together only
        after 61 days."""
        night = cycle(F(rnd.randint(0, 5000)), F(DAY), F(0), F(8 * 3600))
        breaks = cycle(F(rnd.randint(0, 1200)), F(1220), F(0), F(20))
        pieces = Region.empty()
        for _ in range(rnd.randint(0, 3)):
            a = rnd.randint(-5 * DAY, 5 * DAY)
            pieces = pieces | Region.of(Iv(a, a + rnd.randint(1, 4000), False, True))
        return [night, breaks, pieces, cycle(F(rnd.randint(0, 900)), F(900), F(0), F(300))]

    def test_long_periods_are_lazy(self):
        night = cycle(F(0), F(DAY), F(0), F(8 * 3600))
        breaks = cycle(F(0), F(1220), F(0), F(20))
        self.assertIsInstance(night | breaks, Lazy)
        self.assertNotIsInstance(night | cycle(F(0), F(DAY / 2), F(0), F(10)), Lazy)  # (a day: not long)

    def test_lazy_answers_as_computed(self):
        rnd = random.Random(7)
        ops = ["or", "and", "sub", "comp", "shift", "dilate", "edges"]
        for trial in range(40):
            with self.subTest(trial=trial):
                rs = self.regions(rnd)
                lazy = rs[0]
                with unittest.mock.patch.object(region, "LAZY_DEPTH", 10 ** 6):
                    exact = eager(rs[0])
                    for _ in range(rnd.randint(1, 5)):
                        op, other = rnd.choice(ops), rnd.choice(rs)
                        if op == "comp":
                            lazy, exact = lazy.complement(), exact.complement()
                        elif op == "shift":
                            d = F(rnd.randint(-3000, 3000))
                            lazy, exact = lazy.shift(d), exact.shift(d)
                        elif op == "dilate":
                            iv = Iv(-rnd.randint(0, 600), rnd.randint(0, 600), True, False)
                            lazy, exact = lazy.dilate(iv), exact.dilate(iv)
                        elif op == "edges":
                            lazy, exact = lazy.left_edges(), exact.left_edges()
                        else:
                            f = {"or": lambda a, b: a | b, "and": lambda a, b: a & b, "sub": lambda a, b: a - b}[op]
                            lazy = f(lazy, other)
                            with unittest.mock.patch.object(region, "LAZY_PERIOD", 10 ** 12):
                                exact = eager(f(exact, eager(other)))
                for _ in range(5):
                    a = F(rnd.randint(-10 * DAY, 70 * DAY))
                    b = a + rnd.randint(1, 3 * DAY)
                    self.assertEqual(lazy.on(a, b), exact.on(a, b), (a, b))
                    x = F(rnd.randint(-10 * DAY, 70 * DAY))
                    self.assertEqual(lazy.contains(x), exact.contains(x), x)
                self.assertEqual(lazy.is_empty(), exact.is_empty())
                self.assertTrue(lazy == exact)


if __name__ == "__main__":
    unittest.main()
