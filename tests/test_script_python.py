"""The generated script as the Python that builds it (scripts/script_python.py): what the
window shows, and reads back when the user changes it."""

import os
import runpy
import sys
import unittest
from fractions import Fraction as F
from unittest import mock

from helpers import Interval  # noqa: F401 (the paths of scripts/)

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "UI")
sys.path.insert(0, UI)
from app import FifteenMinuteBreaks, PresenceRatioProblem, Session  # noqa: E402
from script_python import from_python, to_python  # noqa: E402
from script_text import ScriptError  # noqa: E402

RULES = ["Break15 → NoScreen", "mode 1 → no NoScreen", "mode 2 → NoScreen"]


def session() -> Session:
    s = Session(RULES, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2), [FifteenMinuteBreaks(length=15)])
    s.sim.background = False
    s.sim.start()
    return s


def example(name):
    """The window's session of an example, once its first run has completed."""
    import gui
    with mock.patch.object(gui, "run") as run:
        runpy.run_path(os.path.join(UI, "examples", f"{name}.py"), run_name="__main__")
    s = run.call_args[0][0]
    s.begin()
    req = s.sim.request
    s.sim.complete(req.id, *list(req.gen.stages(req.state, req.chip, req.known, req.previous))[-1])
    return s


class PythonTest(unittest.TestCase):
    def test_the_window_shows_the_python_of_the_script(self):
        s = session()
        text = s.script_text()
        self.assertIn("SCRIPT = Script(", text)
        compile(text, "<script>", "exec")  # it is Python
        back = from_python(text, s.sim.script)
        self.assertEqual((back.loops, back.on_edit, back.on_view, back.covered, back.live),
                         (s.sim.script.loops, s.sim.script.on_edit, s.sim.script.on_view, s.sim.script.covered,
                          s.sim.script.live))

    def test_a_change_of_the_python_is_the_script(self):
        s = session()
        before = s.sim.script
        text = s.script_text().replace("length=F(15)", "length=F(16)")
        self.assertIn("replaced", s.edit_script(text, before))
        self.assertEqual(s.sim.script.constraints[0].length, 16)
        self.assertIn("Changed by the user at time 0.", s.script_text())

    def test_what_is_wrong_is_told(self):
        s = session()
        with self.assertRaises(ScriptError) as e:
            s.edit_script(s.script_text() + "SCRIPT = Script(loops=[\n", s.sim.script)
        self.assertTrue(e.exception.errors)
        with self.assertRaises(ScriptError) as e:
            s.edit_script(s.script_text().replace("'Break15'", "'Break16'"), s.sim.script)
        self.assertIn("Break16 is not in the rules", str(e.exception))

    def test_turns_are_their_call_not_their_intervals(self):
        """screen_break_15_task.py: the turns of A, B, C between the breaks are one call of
        ratio_plan.turns (the stretches between the breaks, repeating every 8100), not the
        40 pieces of each period, and the Python gives the same script back."""
        s = example("screen_break_15_task")
        text = s.script_text()
        self.assertIn("TURNS1 = turns(", text)
        self.assertEqual(text.count("turns("), 1)
        for k in "ABC":
            self.assertIn(f"Op.put('{k}', TURNS1['{k}'])", text)
        self.assertLess(text.count("Interval("), 10)
        back = from_python(text, s.sim.script)
        self.assertEqual(back.loops, s.sim.script.loops)
        line = s.sim.state.line
        for e in back.events:  # the same line from the regions the Python builds
            for a in e.actions:
                for o in getattr(a, "ops", ()):
                    self.assertEqual(o.region.on(0, 20000), line.presence(o.kind).on(0, 20000), o.kind)

    def test_a_cycle_is_its_call(self):
        """A and B at 0.5 that exclude each other, at least 1, nothing else: cycle(...)."""
        from generator import MinimumLengthProblem, RulesGenerator
        from line import LineState
        from modes import ModeTimeline
        from rules import parse_rules
        g = RulesGenerator(parse_rules(["B → no A"]), [(PresenceRatioProblem({"A": F(1, 2), "B": F(1, 2)}), 1),
                           (MinimumLengthProblem({"A": 1, "B": 1}), 1)], 0.5, seed=0)
        script, _ = g.run(LineState.empty(), F(3), ModeTimeline(0, "1").known(3))
        text = to_python(script)
        self.assertEqual(text.count("cycle("), 2, text)
        self.assertEqual(from_python(text, script).loops, script.loops)


class RunGoingOnTest(unittest.TestCase):
    """The script published at the start of a run, while the run goes on."""

    def test_an_edit_while_it_runs_runs_it_again(self):
        """The start-up script asks for one run and has no rule for the user's edits: the
        stand-in that replaces it keeps one, or an edit during the first run would be
        answered by nothing (the run computes from the line before it)."""
        from intervals import Interval
        from line import Op
        from region import Region
        s = Session(RULES, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2),
                    [FifteenMinuteBreaks(length=15)])
        s.sim.background = True
        s.begin()
        first = s.sim.request
        self.assertEqual(first.why, "start")
        self.assertIn("Event(trigger=Edited(only=True), actions=(RerunAction(),))", s.script_text())
        # this script is the run's own first one (a run is progressive): no call in it is needed
        self.assertIn("This script is the one published by the generator run going on, from chip 0, asked by the "
                      "start-up script (run the generator once) at time 0; the run goes on, and its next script "
                      "replaces this one.", s.script_text())
        self.assertIn("No generator run has completed yet.", s.script_text())
        s.sim.edit_now([Op.put("NoScreen", Region.of(Interval.closed(50, 60)))])
        self.assertIsNot(s.sim.request, first)  # the run from the line before the edit: replaced
        self.assertEqual(s.sim.request.why, "user edit")
        self.assertIn("asked by the script then in place, at your edit", s.script_text())
        s.close()


if __name__ == "__main__":
    unittest.main()
