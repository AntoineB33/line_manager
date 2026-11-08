"""instant.Instant: the instant schedule of OmniApp's requirements (§ Two paces), on its three
examples, and what it must never do: put a task where it is forbidden, leave idle time a task
could have, or take long."""

import time
import unittest
from fractions import Fraction as F

from helpers import Interval  # noqa: F401 (the paths of scripts/)
from instant import Instant
from intervals import Interval as Iv, IntervalSet
from line import LineState, Op
from ratio_plan import cycle
from region import Region
from resolve import apply_edit
from rules import parse_rules
from score import Score, Task

MIN, HOUR, DAY = 60, 3600, 86400
YEARS_1000 = 1000 * 365 * DAY


def night_state(rules, extra=()):
    """A night every day, 01:22 to 09:22, put by the user."""
    rs = parse_rules(rules)
    st = apply_edit(rs, LineState.empty(), [Op.put("Sleep", cycle(F(4920), F(DAY), F(0), F(8 * HOUR)))] + list(extra), "user")
    return rs, st


def total(panels, k, a, b):
    return sum(min(y, b) - max(x, a) for x, y in panels.get(k, ()) if y > a and x < b)


class InstantTest(unittest.TestCase):
    TASKS = {"A": Task(4, 45 * MIN, resilience={"Sleep": 0}), "B": Task(2, 30 * MIN, resilience={"Sleep": 0}),
             "C": Task(1, 30 * MIN, resilience={"Sleep": 0}), "D": Task(1, 15 * MIN, resilience={"Sleep": 0})}

    def test_thousands_of_years_ahead(self):
        """Two days 1000 years ahead, at once: no task in the night, every awake moment given to
        a task, each task's share roughly its target, its panels of its minimum where they fit."""
        rs, st = night_state([])
        sc = Score(self.TASKS)
        a = F(YEARS_1000 + 12 * HOUR)
        b = a + 2 * DAY
        began = time.perf_counter()
        got = Instant(rs, sc).schedule(st, a, b)
        took = time.perf_counter() - began
        self.assertLess(took, 0.5)
        night = st.line.presence("Sleep").on(a, b)  # (read in the window, as the schedule reads it)
        placed = IntervalSet()
        for k, v in got.items():
            r = IntervalSet([Iv(x, y, False, True) for x, y in v])
            self.assertFalse((r & night).intervals, k)
            self.assertFalse((r & placed).intervals, k)  # (one task at a time)
            placed = placed | r
        awake = IntervalSet.of(Iv(a, b, False, True)) - night
        self.assertFalse((awake - placed).intervals)  # (no idle time a task could have)
        served = {k: total(got, k, a, b) for k in self.TASKS}
        allt = sum(served.values())
        for k, p in (("A", 4), ("B", 2), ("C", 1), ("D", 1)):
            self.assertAlmostEqual(float(served[k] / allt), p / 8, delta=0.06, msg=k)

    def test_far_ahead_regions_stay_cheap(self):
        """An element 1000 years ahead with a night every day: combined lazily, not written out
        night by night (365000 of them), and read in a window as fast as near the chip."""
        rs, st = night_state([])
        a = F(YEARS_1000)
        began = time.perf_counter()
        far = st.line.presence("Sleep") | Region.of(Iv(a, a + HOUR, False, True))
        self.assertTrue(far.contains(a + F(HOUR, 2)) and far.contains(F(5000)))
        self.assertEqual(len(far.on(a - DAY, a + DAY).intervals), 3)
        self.assertLess(time.perf_counter() - began, 0.5)

    def test_a_dragged_block_leaves_less_around_it(self):
        """The user's 4h of A: over the 8h after it, A has less than without the block, and the
        block itself is left as it is (not in the instant schedule)."""
        a = F(10 * DAY + 10 * HOUR)
        block = [Op.put("A", Region.of(Iv(a, a + 4 * HOUR, False, True)))]
        rs, plain = night_state([])
        _, dragged = night_state([], block)
        sc = Score(self.TASKS)
        lone = Instant(rs, sc).schedule(plain, a, a + 12 * HOUR)
        near = Instant(rs, sc).schedule(dragged, a, a + 12 * HOUR)
        self.assertEqual(total(near, "A", a, a + 4 * HOUR), 0)  # (the user's: not placed again)
        self.assertLess(total(near, "A", a + 4 * HOUR, a + 12 * HOUR), total(lone, "A", a + 4 * HOUR, a + 12 * HOUR))

    def test_new_rules_at_once(self):
        """The priorities changed (D now the largest): the next schedule follows them."""
        rs, st = night_state([])
        a = F(20 * DAY + 10 * HOUR)
        before = Instant(rs, Score(self.TASKS)).schedule(st, a, a + DAY)
        changed = dict(self.TASKS, D=Task(8, 15 * MIN, resilience={"Sleep": 0}))
        after = Instant(rs, Score(changed)).schedule(st, a, a + DAY)
        self.assertGreater(total(after, "D", a, a + DAY), 2 * total(before, "D", a, a + DAY))

    def test_a_rule_that_forbids_a_task(self):
        """"Meeting → no A": no A in the user's meeting; the others fill it."""
        a = F(30 * DAY + 10 * HOUR)
        meeting = [Op.put("Meeting", Region.of(Iv(a + HOUR, a + 3 * HOUR, False, True)))]
        rs, st = night_state(["Meeting → no A"], meeting)
        got = Instant(rs, Score(self.TASKS)).schedule(st, a, a + 6 * HOUR)
        self.assertEqual(total(got, "A", a + HOUR, a + 3 * HOUR), 0)
        self.assertEqual(sum(total(got, k, a + HOUR, a + 3 * HOUR) for k in "BCD"), 2 * HOUR)

    def test_the_breaks_far_ahead(self):
        """The 15min breaks the constraints placed forever with the night: 1000 years ahead, no
        task in a break (resilience 0 to "no screen"), read where they are."""
        from constraints import AsEarlyAsPossible, MaximumGap, MinimumGap
        from generator import RulesGenerator
        from modes import ModeTimeline
        rules = ["Break15 → NoScreen", "Sleep → NoScreen", "Break15 is always 100%", "mode 1 → no NoScreen"]
        rs, st = night_state(rules)
        cons = [AsEarlyAsPossible("Break15", length=15 * MIN),
                MinimumGap("Break15", 2 * HOUR, after="NoScreen", at_least=15 * MIN, pull="NoScreen"),
                MaximumGap("Break15", 2 * HOUR, after="NoScreen", at_least=15 * MIN)]
        g = RulesGenerator(rs, [], 0.5, constraints=cons)
        script, _ = g.run(st, F(DAY + 12 * HOUR), ModeTimeline(0, "1").known(F(DAY + 12 * HOUR)))
        line_state = script.base
        tasks = {k: Task(t.priority, t.minimum, resilience={"NoScreen": 0}) for k, t in self.TASKS.items()}
        a = F(YEARS_1000 + 10 * HOUR)
        breaks = line_state.line.presence("Break15").on(a, a + DAY)
        self.assertTrue(breaks.intervals)  # (they are there, 1000 years ahead)
        got = Instant(rs, Score(tasks)).schedule(line_state, a, a + DAY)
        ns = line_state.line.presence("NoScreen").on(a, a + DAY)
        for k, v in got.items():
            r = IntervalSet([Iv(x, y, False, True) for x, y in v])
            self.assertFalse((r & ns).intervals, k)


if __name__ == "__main__":
    unittest.main()
