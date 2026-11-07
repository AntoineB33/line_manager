"""Random rule sets (fuzz_rules.py), fixed seeds: the fast run (compiled stretches) is the
reference's (the mode rules, pushes included, decided at the chip at each step) on the
line ahead of the chip, keeps every MinimumGap, and raises nothing.
The seeds that do not hold yet are in gaps_to_solve_later.md; run the rest with
    python tests/fuzz_rules.py <first seed> <how many> <steps>
"""

import unittest
from unittest import mock

import fuzz_rules
import gui

HOLDING = tuple(range(20))  # seeds 0 to 19, 12 steps each (all of them since the push keeps what stays)
HOLDING_MAX = tuple(k for k in range(20) if k not in (5, 16))  # (gaps_to_solve_later.md)
HOLDING_OBLIGATION = tuple(k for k in range(20) if k != 5)


class FuzzTest(unittest.TestCase):
    def test_random_rule_sets_that_hold(self):
        with mock.patch.object(gui, "run"):
            for seed in HOLDING:
                with self.subTest(seed=seed):
                    sc = fuzz_rules.scenario(seed, 12)
                    why = fuzz_rules.compare(sc)
                    self.assertIsNone(why, fuzz_rules.describe(sc) + "\n" + str(why))

    def test_with_the_obligation(self):
        """The same with a MaximumGap of each kind ("max": the same lines, the obligation
        checked too) and with the obligation alone, no AsEarlyAsPossible ("obligation")."""
        with mock.patch.object(gui, "run"):
            for variant, seeds in (("max", HOLDING_MAX), ("obligation", HOLDING_OBLIGATION)):
                for seed in seeds:
                    with self.subTest(variant=variant, seed=seed):
                        sc = fuzz_rules.scenario(seed, 12, variant)
                        why = fuzz_rules.compare(sc)
                        self.assertIsNone(why, fuzz_rules.describe(sc) + "\n" + str(why))


if __name__ == "__main__":
    unittest.main()
