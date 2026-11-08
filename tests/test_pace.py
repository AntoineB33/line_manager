"""pace.Pace: the scheduler's steps of OmniApp's requirements (§ Two paces): step n perfects
[chip + 10min (n + 1), chip + 10min (n + 2)] within its budget, from the big picture down."""

import time
import unittest
from fractions import Fraction as F

from helpers import Interval  # noqa: F401 (the paths of scripts/)
from instant import Instant
from intervals import Interval as Iv, IntervalSet
from line import LineState, Op
from pace import Pace
from ratio_plan import cycle
from region import Region
from resolve import apply_edit
from rules import parse_rules
from score import Score, Task

MIN, HOUR, DAY = 60, 3600, 86400
TASKS = {"A": Task(4, 45 * MIN, resilience={"Sleep": 0}), "B": Task(2, 30 * MIN, resilience={"Sleep": 0}),
         "C": Task(1, 30 * MIN, resilience={"Sleep": 0}), "D": Task(1, 15 * MIN, resilience={"Sleep": 0})}
NAMES = sorted(TASKS)


def setup(chip, days=3):
    """Tasks that exclude each other, a night every day, a past put by the generator: the
    instant schedule of the days before the chip."""
    rules = parse_rules([f"{a} → " + " & ".join(f"no {b}" for b in NAMES[i + 1:]) for i, a in enumerate(NAMES[:-1])])
    st = apply_edit(rules, LineState.empty(), [Op.put("Sleep", cycle(F(4920), F(DAY), F(0), F(8 * HOUR)))], "user")
    sc = Score(TASKS)
    past = Instant(rules, sc).schedule(st, chip - days * DAY, chip)
    st = apply_edit(rules, st, [Op.put(k, Region.of(IntervalSet([Iv(a, b, False, True) for a, b in v])))
                                for k, v in past.items()], "generated")
    return rules, sc, st


class PaceTest(unittest.TestCase):
    def test_steps_perfect_their_slices(self):
        chip = F(10 * DAY + 10 * HOUR)
        rules, sc, st = setup(chip)
        pace = Pace(rules, sc)
        steps = 6
        base = st
        for n in range(steps):
            began = time.perf_counter()
            ops, info = pace.step(st, chip, n, budget=1.5)
            self.assertLess(time.perf_counter() - began, 2.5, n)  # (its budget, and what it costs to stop)
            st = apply_edit(rules, st, ops, "generated")
            a, b = pace.slice(chip, n)
            self.assertEqual(info["slice"], (a, b))
            placed = IntervalSet()
            for k in NAMES:
                placed = placed | st.line.presence(k).on(a, b)
            self.assertFalse((IntervalSet.of(Iv(a, b, False, False)) - st.line.presence("Sleep").on(a, b) - placed).intervals, n)  # (on reads [a, b))
            self.assertFalse((placed & st.line.presence("Sleep").on(a, b)).intervals, n)
        # the steps' hour against the instant schedule's (the big picture alone)
        lo, hi = pace.slice(chip, 0)[0], pace.slice(chip, steps - 1)[1]
        inst = Instant(rules, sc).schedule(base, lo, hi)
        quick = apply_edit(rules, base, [Op.put(k, Region.of(IntervalSet([Iv(a, b, False, True) for a, b in v])))
                                         for k, v in inst.items()], "generated")
        self.assertLessEqual(sc.evaluate(st.line, lo, hi).J, sc.evaluate(quick.line, lo, hi).J)

    def test_a_step_through_the_night(self):
        """A slice in the night: nothing committed there, and no time spent on it."""
        chip = F(10 * DAY + 2 * HOUR)  # (02:00: asleep until 09:22)
        rules, sc, st = setup(chip)
        ops, info = Pace(rules, sc).step(st, chip, 0, budget=1.0)
        self.assertEqual([o for o in ops if o.action == "put"], [])


if __name__ == "__main__":
    unittest.main()
