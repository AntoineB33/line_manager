"""What the window shows between two steps (Simulation.ahead) is what the simulation
makes when the chip gets there: the line is a function of the chip's position."""

import os
import sys
import unittest
from fractions import Fraction as F

sys.path.insert(0, os.path.dirname(__file__))

from helpers import Interval, Region  # noqa: E402

from constraints import FifteenMinuteBreaks  # noqa: E402
from generator import RulesGenerator  # noqa: E402
from intervals import EPS, Pos  # noqa: E402
from line import LineState  # noqa: E402
from modes import ModeTimeline  # noqa: E402
from problems import PresenceRatioProblem  # noqa: E402
from rules import parse_rules  # noqa: E402
from simulation import Chip, Simulation  # noqa: E402

RULES = ["Break15 → NoScreen",
         "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
         "NotOnComputer → no NoComputerUnlocked", "NotOnPhone → no NoPhoneUnlocked",
         "mode 1 → no NoScreen", "mode 2 → NoScreen & NoComputerUnlocked & NoPhoneUnlocked",
         "mode 3 → NoScreen & (NotOnComputer or NotOnPhone)"]


def simulation(modes) -> Simulation:
    sim = Simulation(LineState.empty(), RulesGenerator(parse_rules(RULES), PresenceRatioProblem({}, 240), 0.5, constraints=[FifteenMinuteBreaks()]),
                     Chip([(0, 0)]), modes)
    sim.start()
    return sim


class ShownLineTest(unittest.TestCase):
    def check(self, modes, end, step=F(1, 2)):
        """At each step, the line shown a third and two thirds of the way to the next
        one, and at the next one, is the line of a simulation stopped there."""
        sim, known = simulation(modes), 0
        while sim.t < end:
            ahead, t = sim.ahead(), sim.t
            if ahead is not None:
                for f in (F(1, 3), F(2, 3)):
                    p = t + step * f
                    state = ahead(Pos(p) - EPS)
                    if state is not None:
                        known += 1
                        there = simulation(modes)
                        there.chip.extend(p, p)
                        there.advance(p)
                        self.assertEqual(state.line, there.state.line, str(p))
            sim.chip.extend(t + step, t + step)
            sim.advance(t + step)
        self.assertGreater(known, 0)

    def test_dragged(self):
        self.check(ModeTimeline(0, "1"), F(14))

    def test_through_a_mode_change(self):
        self.check(ModeTimeline(0, "1", [(F(3), "2"), (F(6), "1")]), F(10))

    def test_the_left_edge_is_at_the_chip(self):
        sim = simulation(ModeTimeline(0, "1"))
        for k in range(1, 24):
            t = F(k, 2)
            sim.chip.extend(t, t)
            sim.advance(t)
            ahead = sim.ahead()
            p = t + F(1, 7)
            state = ahead(Pos(p) - EPS)
            if state is not None:
                b = state.line.presence("Break15") & Region.of(Interval.closed(0, 100))
                self.assertEqual(b.inf().x, p)


if __name__ == "__main__":
    unittest.main()
