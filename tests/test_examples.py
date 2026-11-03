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
from script import ChipIn, Edited  # noqa: E402


def reruns(name):
    """The rerun rules of the example's script, once its first run has completed: their
    kinds, with the mode of those that have one."""
    with mock.patch.object(gui, "run") as run:
        runpy.run_path(os.path.join(UI, "examples", f"{name}.py"), run_name="__main__")
    s = run.call_args[0][0]
    s.begin()
    req = s.sim.request
    s.sim.complete(req.id, *list(req.gen.stages(req.state, req.chip, req.known, req.previous))[-1])
    out = set()
    for t in s.sim.script.rerun_triggers():
        out.add((ChipIn, t.mode) if isinstance(t, ChipIn) else type(t))
    return out


@unittest.skipIf(gui is None, "no tkinter")
class ExamplesTest(unittest.TestCase):
    def test_every_example_has_its_own_rules(self):
        expected = {
            "fifteen_minute_break_modes": {(ChipIn, None), Edited},
        }
        names = {f[:-3] for f in os.listdir(os.path.join(UI, "examples")) if f.endswith(".py")}
        self.assertEqual(names, set(expected))
        for name, rules in expected.items():
            with self.subTest(name):
                self.assertEqual(reruns(name), rules)


if __name__ == "__main__":
    unittest.main()
