"""The scripts of UI/examples/, with no window: each one starts, and its generated script
has the rerun rules its context asks for."""

import io
import os
import runpy
from contextlib import redirect_stdout
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

# examples whose definitions are refused, and why
REFUSED = {}


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
            "screen_break_15": {(ChipIn, None), Edited},
            # its tasks: the score and their ratios are watched, and each mode, which can
            # forbid them at the chip (the next run makes the turns again)
            "screen_break_15_task": {(ChipIn, None), (ChipIn, "1"), (ChipIn, "2"), (ChipIn, "3"), Edited,
                                     ScoreBelow, PresenceAbove},
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
        self.assertEqual(names, set(expected) | set(REFUSED))
        for name, rules in expected.items():
            with self.subTest(name):
                self.assertEqual(reruns(name), rules)

    def test_single_element_full_presence_fills_its_window(self):
        """A at 100% is everywhere after the chip, at 100% (its perfect script, on the whole
        line), and nothing else is on the line."""
        line = first_run("single_element_full_presence").state.line
        self.assertEqual(line.kinds(), ["A"])
        self.assertEqual(line.presence("A"), Region.of(Iv(0, INF, lo_closed=False, hi_closed=False)))
        self.assertEqual(set(line.vals["A"]), {100})

    def test_the_refused_examples_open_no_window(self):
        """Their definitions are refused (A and B at 0.5 that exclude each other, with no
        minimum length: no line is the best, whatever the other rules and the breaks): the
        window says why, does not open, and no generator runs."""
        for name, why in sorted(REFUSED.items()):
            with self.subTest(name):
                out = io.StringIO()
                with mock.patch.object(gui, "run") as run, redirect_stdout(out):
                    runpy.run_path(os.path.join(UI, "examples", f"{name}.py"), run_name="__main__")
                self.assertFalse(run.called)
                self.assertIn(why, out.getvalue())

    def test_the_tasks_take_turns_between_the_breaks(self):
        """A, B, C at 1/3, excluding each other, none during "no screen", A and B at least
        60: turns of 60 between the breaks, from one stretch to the next, forever, after
        the first run (the search put 6 minutes of A, and nothing of B and C)."""
        line = first_run("screen_break_15_task").state.line
        ns = line.presence("NoScreen")
        pres = {k: line.presence(k) for k in "ABC"}

        def at(x):
            return [k for k in "ABC" if pres[k].contains(x)]
        for x in range(0, 30000, 7):
            self.assertLessEqual(len(at(x)), 1, x)
            self.assertFalse(at(x) and ns.contains(x), x)
        for start in (900, 8100 + 900, 81 * 8100 + 900):  # the end of a break
            turns = [at(start + 30 + 60 * j) for j in range(120)]  # the 2 hours to the next break
            self.assertTrue(all(len(k) == 1 for k in turns), start)
            self.assertTrue(all(a != b for a, b in zip(turns, turns[1:])), start)
            self.assertEqual(turns[:3], turns[3:6], start)
            self.assertEqual(at(start - 30), [], start)


if __name__ == "__main__":
    unittest.main()
