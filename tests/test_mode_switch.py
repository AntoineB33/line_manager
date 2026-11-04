"""A mode the script did not expect: the chip computes it, and it must not do so again and
again for what goes on the same way. Counted in solves: the time depends on the machine."""

import os
import sys
import unittest
from fractions import Fraction as F
from unittest import mock

from helpers import Interval  # noqa: F401 (the paths of scripts/)

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "UI"))
import mode_rules  # noqa: E402
from app import FifteenMinuteBreaks, PresenceRatioProblem, Session  # noqa: E402
from intervals import EPS, Pos  # noqa: E402

RULES = ["Break15 → NoScreen", "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
         "NotOnComputer → no NoComputerUnlocked", "NotOnPhone → no NoPhoneUnlocked", "mode 1 → no NoScreen",
         "mode 3 → NoScreen & (NotOnComputer or NotOnPhone)"]


def solves_after_a_switch(units):
    s = Session(RULES, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2), [FifteenMinuteBreaks()])
    s.begin()
    req = s.sim.request
    s.sim.complete(req.id, *list(req.gen.stages(req.state, req.chip, req.known, req.previous))[-1])
    s.sim.request = None
    s.tick(F(100))
    s.set_mode("3")
    real = mode_rules.ModeRules._same
    with mock.patch.object(mode_rules.ModeRules, "_same", autospec=True, side_effect=real) as same:
        for _ in range(units):
            s.tick(F(1))
        n = same.call_count
    s.close()
    return n


class UnexpectedModeTest(unittest.TestCase):
    def test_a_run_going_on_is_not_solved_again_at_each_unit(self):
        self.assertLessEqual(solves_after_a_switch(60), 30)

    def test_the_look_ahead_is_what_makes_it_so(self):
        with mock.patch.object(mode_rules, "LOOK_AHEAD", 1):
            self.assertGreater(solves_after_a_switch(60), 30)


class AtTheChipTest(unittest.TestCase):
    """The mode rules hold at the chip's position at every instant, not only once it has passed."""

    def session(self):
        s = Session(RULES, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2), [FifteenMinuteBreaks()])
        s.begin()
        req = s.sim.request
        s.sim.complete(req.id, *list(req.gen.stages(req.state, req.chip, req.known, req.previous))[-1])
        s.sim.request = None
        self.addCleanup(s.close)
        return s

    @staticmethod
    def at(s, kind, d=0):
        return s.sim.state.line.presence(kind).contains(Pos(s.position) + d * EPS)

    def test_no_break_at_the_chip_in_mode_1(self):
        s = self.session()
        for _ in range(4):
            s.tick(F(7, 3))
            self.assertFalse(self.at(s, "NoScreen"))
            self.assertFalse(self.at(s, "Break15"))
            self.assertTrue(self.at(s, "Break15", 1))  # right after it

    def test_the_other_mode_holds_at_the_chip_at_once(self):
        s = self.session()
        s.tick(F(5))
        s.set_mode("3")
        self.assertTrue(self.at(s, "NoScreen"))
        self.assertTrue(self.at(s, "NotOnComputer"))
        self.assertFalse(self.at(s, "NoScreen", -1))

    def test_a_mode_changed_at_the_position_owns_it(self):
        s = self.session()
        s.tick(F(5))
        s.set_mode("3")
        s.tick(F(1))
        s.set_mode("1")
        self.assertFalse(self.at(s, "NoScreen"))
        self.assertTrue(self.at(s, "NoScreen", -1))
        self.assertFalse(self.at(s, "NotOnComputer"))  # what mode 3 did at this position is undone

    def test_going_on_does_not_solve_again_at_each_step(self):
        s = self.session()
        s.tick(F(100))
        real = mode_rules.ModeRules.decide
        with mock.patch.object(mode_rules.ModeRules, "decide", autospec=True, side_effect=real) as decide:
            for _ in range(20):
                s.tick(F(1, 20))
            self.assertLessEqual(decide.call_count, 2)


if __name__ == "__main__":
    unittest.main()
