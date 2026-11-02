"""An event is looked at when it can change: its next position, and what it reads."""

import random
import unittest
from fractions import Fraction as F
from unittest import mock

from helpers import Interval  # noqa: F401 (the paths of scripts/)
from generator import PresenceRatioProblem, RulesGenerator
from intervals import INF, IntervalSet
from line import GEN, LineState, Op
from modes import ModeTimeline
from region import Region
from resolve import apply_edit
from rules import parse_rules
from script import ChipIn, Event, PresenceAbove, RerunAction, ScoreBelow, score
from simulation import Chip, Simulation
import watch


def line_with(ivs, kind="A"):
    return apply_edit(parse_rules([]), LineState.empty(), [Op.put(kind, Region.of(IntervalSet.of(*ivs)))], GEN)


def random_intervals(rng):
    edges = sorted(rng.sample(range(0, 200), 2 * rng.randint(1, 5)))
    return [Interval.closed(edges[i], edges[i + 1]) for i in range(0, len(edges), 2)]


class CrossingTest(unittest.TestCase):
    def test_a_presence_above(self):
        """A on [40, 60], ratio over the next 40: above 1/4 once more than 10 of it is in."""
        s = line_with([Interval.closed(40, 60)])
        t = PresenceAbove("A", F(100), F(1, 4), F(40), F(0), F(100))
        at, _ = watch.look(t, s, F(0), "1", ())
        self.assertEqual(at, F(10) + watch.TINY)
        self.assertTrue(t.holds(s, at, "1"))
        self.assertFalse(t.holds(s, F(10), "1"))
        at, _ = watch.look(t, s, at, "1", ())
        self.assertEqual(at, 50)  # and no more once only 10 of it is left
        self.assertFalse(t.holds(s, at, "1"))
        self.assertTrue(t.holds(s, at - watch.TINY, "1"))

    def test_presence_against_every_position(self):
        rng = random.Random(3)
        for _ in range(60):
            s = line_with(random_intervals(rng))
            H, y = F(rng.randint(5, 60)), F(rng.randint(1, 19), 20)
            t = PresenceAbove("A", F(100), y, H, F(0), F(120))
            c = F(0)
            while c < 120:
                was = t.holds(s, c, "1")
                at, _ = watch.look(t, s, c, "1", ())
                if at == INF:
                    break
                for k in range(1, 41):  # nothing changes before it
                    x = c + (at - c) * k / 41
                    if x < 120 and x < at - watch.TINY:  # (a rising one is looked at TINY after it)
                        self.assertEqual(t.holds(s, x, "1"), was, (c, at, x))
                self.assertGreater(at, c)
                c = at

    def test_score_against_every_position(self):
        rng = random.Random(5)
        for _ in range(40):
            s = line_with(random_intervals(rng))
            problems = [(PresenceRatioProblem({"A": F(rng.randint(1, 9), 10)}, rng.randint(10, 60)), F(1))]
            t = ScoreBelow(F(rng.randint(10, 19), 20))
            c = F(0)
            for _ in range(40):
                was = score(problems, s, c) < t.z
                at, _ = watch.look(t, s, c, "1", problems)
                if at == INF or at > 200:
                    break
                for k in range(1, 21):
                    x = c + (at - c) * k / 21
                    if x < at - watch.TINY:
                        self.assertEqual(score(problems, s, x) < t.z, was, (c, at, x))
                self.assertGreater(at, c)
                c = at


class SimulationTest(unittest.TestCase):
    def simulation(self, events):
        s = line_with([Interval.closed(40, 60)])
        sim = Simulation(s, RulesGenerator(parse_rules([]), PresenceRatioProblem({"A": F(1, 4)}, 40), 0.5), Chip([(0, 0), (100, 100)]), ModeTimeline(0, "1"))
        sim.start()
        sim.script.events = events
        sim._was_true = [False] * len(events)
        sim._fired = [False] * len(events)
        sim._looks = None
        sim._install = mock.Mock()
        return sim

    PRESENCE = PresenceAbove("A", F(100), F(1, 4), F(40), F(0), F(100))

    def test_fires_at_the_crossing_not_at_the_next_step(self):
        sim = self.simulation([Event(self.PRESENCE, (RerunAction(),))])
        sim.advance(F(1, 100))  # (it is looked at once, then knows its position)
        sim.advance(30)  # one step
        self.assertEqual(sim._install.call_args[0][0], F(10) + watch.TINY)

    def test_nothing_is_computed_while_nothing_can_change(self):
        sim = self.simulation([Event(self.PRESENCE, (RerunAction(),))])
        sim.advance(F(1))
        with mock.patch.object(PresenceAbove, "holds", side_effect=AssertionError), mock.patch.object(watch, "_next", side_effect=AssertionError):
            for k in range(1, 9):
                sim.advance(1 + F(k, 2))

    def test_what_it_reads_changing_looks_again(self):
        sim = self.simulation([Event(self.PRESENCE, (RerunAction(),))])
        sim.advance(F(1))
        sim.state = line_with([Interval.closed(2, 30)])
        sim.advance(F(2))
        sim._install.assert_called()  # 28 of the next 40: above 1/4 at once

    def test_a_chip_trigger_is_not_tested_before_its_position(self):
        sim = self.simulation([Event(ChipIn(F(50), INF), (RerunAction(),))])
        sim.advance(F(1))
        with mock.patch.object(ChipIn, "holds", side_effect=AssertionError):
            sim.advance(F(10))


if __name__ == "__main__":
    unittest.main()
