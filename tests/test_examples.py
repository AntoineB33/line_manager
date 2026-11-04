"""The scripts of UI/examples/, with no window: each one starts, and its generated script
has the rerun rules its context asks for."""

import os
import runpy
import sys
import unittest
from unittest import mock

from helpers import Interval  # noqa: F401 (the paths of scripts/)

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "UI")
sys.path.insert(0, UI)
try:
    import gui
except ImportError:  # (no tkinter)
    gui = None
from intervals import INF, Interval as Iv  # noqa: E402
from region import Region  # noqa: E402
from script import ChipIn, Edited, PresenceAbove, ScoreBelow  # noqa: E402


def first_run(name):
    """The example's simulation, once its first run has completed."""
    with mock.patch.object(gui, "run") as run:
        runpy.run_path(os.path.join(UI, "examples", f"{name}.py"), run_name="__main__")
    s = run.call_args[0][0]
    s.begin()
    req = s.sim.request
    s.sim.complete(req.id, *list(req.gen.stages(req.state, req.chip, req.known, req.previous))[-1])
    return s.sim


def reruns(name):
    """The rerun rules of the example's script, once its first run has completed: their
    kinds, with the mode of those that have one."""
    out = set()
    for t in first_run(name).script.rerun_triggers():
        out.add((ChipIn, t.mode) if isinstance(t, ChipIn) else type(t))
    return out


@unittest.skipIf(gui is None, "no tkinter")
class ExamplesTest(unittest.TestCase):
    def test_every_example_has_its_own_rules(self):
        expected = {
            "fifteen_minute_break_modes": {(ChipIn, None), Edited},
            "screen_breaks_5_15": {(ChipIn, None), Edited},
            "screen_breaks": {(ChipIn, None), Edited},
            "screen_breaks_sleep": {(ChipIn, None), Edited},
            # the tasks: their targets are watched, in each mode (all reach VideoGames)
            "screen_breaks_sleep_tasks": {(ChipIn, None), (ChipIn, "1"), (ChipIn, "2"), (ChipIn, "3"), Edited,
                                          ScoreBelow, PresenceAbove},
            # its perfect script: only the user's edits change it
            "single_element_full_presence": {Edited},
        }
        names = {f[:-3] for f in os.listdir(os.path.join(UI, "examples")) if f.endswith(".py")}
        self.assertEqual(names, set(expected))
        for name, rules in expected.items():
            with self.subTest(name):
                self.assertEqual(reruns(name), rules)

    def test_single_element_full_presence_fills_its_window(self):
        """A at 100% is everywhere after the chip, at 100% (its perfect script, not limited
        by the horizon), and nothing else is on the line."""
        line = first_run("single_element_full_presence").state.line
        self.assertEqual(line.kinds(), ["A"])
        self.assertEqual(line.presence("A"), Region.of(Iv(0, INF, lo_closed=False, hi_closed=False)))
        self.assertEqual(set(line.vals["A"]), {100})


if __name__ == "__main__":
    unittest.main()
