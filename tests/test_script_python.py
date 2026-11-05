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
from app import Breaks, PresenceRatioProblem, Session  # noqa: E402
from script_python import from_python, to_python  # noqa: E402
from script_text import ScriptError  # noqa: E402

RULES = ["Break15 → NoScreen", "mode 1 → no NoScreen", "mode 2 → NoScreen"]


def session() -> Session:
    s = Session(RULES, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2), [Breaks(length=15)])
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

    def test_the_whole_script_comes_back_at_once(self):
        """Every part of the script in place, its state too (the line the run compiled from,
        what was true and fired), as values: reading it back computes nothing, so a saved
        script applies at once (screen_break_15_task.py). The mode rules the run compiled
        ahead, a cache, are kept from the script it replaces."""
        import time
        from unittest import mock
        import mode_rules
        import solver
        s = example("screen_break_15_task")
        a = s.sim.script
        text = s.script_text()
        for part in ("BASE = line_state(claims(", "Line({", "origin=", "true=", "fired=", "since=", "frame=",
                     "on_edit=", "on_view=", "loops="):
            self.assertIn(part, text)
        began = time.perf_counter()
        with mock.patch.object(solver, "solve", side_effect=AssertionError("solved")),                 mock.patch.object(mode_rules, "compile_runs", side_effect=AssertionError("compiled")):
            b = from_python(text, a, s.rs)
        self.assertLess(time.perf_counter() - began, 1)
        self.assertEqual((b.loops, b.on_edit, b.on_view, b.covered, b.live, b.origin, b.true, b.fired, b.since),
                         (a.loops, a.on_edit, a.on_view, a.covered, a.live, a.origin, a.true, a.fired, a.since))
        self.assertEqual(list(map(str, b.constraints)), list(map(str, a.constraints)))
        self.assertEqual((b.base.line.vals, b.base.history), (a.base.line.vals, a.base.history))
        self.assertEqual(b.stretches, a.stretches)  # (kept: the same covered stretches, the same constraints)
        self.assertNotIn("[900+2ε", text)  # what was true or fired names its events, not their intervals

    def test_the_file_is_the_script(self):
        """The button's file is a .py file that runs on its own: it builds SCRIPT, the script
        in place, and nothing else."""
        import subprocess
        import tempfile
        s = session()
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".py", delete=False) as f:
            f.write(s.script_text() + "\nprint(type(SCRIPT).__name__, len(SCRIPT.events))\n")
            path = f.name
        try:
            out = subprocess.run([sys.executable, path], capture_output=True, text=True, timeout=120)
        finally:
            os.unlink(path)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), ["Script", str(len(s.sim.script.events))])

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
        edit = next(line for line in text.splitlines() if "Op.put('A', TURNS1['A'])" in line)
        self.assertNotIn("Interval(", edit)  # the turns are their call
        self.assertLess(text.count("Interval("), 40)  # (the line the run compiled from too: not 120 pieces a period)
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
        rs = parse_rules(["B → no A"])
        g = RulesGenerator(rs, [(PresenceRatioProblem({"A": F(1, 2), "B": F(1, 2)}), 1),
                           (MinimumLengthProblem({"A": 1, "B": 1}), 1)], 0.5, seed=0)
        script, _ = g.run(LineState.empty(), F(3), ModeTimeline(0, "1").known(3))
        text = to_python(script, rules=rs)
        self.assertEqual(text.count("cycle("), 2, text)  # once each, at the top: the line and the edits name it
        back = from_python(text, script, rs)
        self.assertEqual(back.loops, script.loops)
        self.assertEqual(back.base.line.vals, script.base.line.vals)  # (the line as values)


class FrameTest(unittest.TestCase):
    """s_f (Script.frame) is part of the script: in its text, and what the user writes there
    is drawn until s_v makes it again (when the view moves or is resized)."""

    def test_s_f_is_in_the_text_and_read_back(self):
        from script import Place
        s = session()
        s.sim.script.frame = [Place("world", "line"), Place(17, "rectangle", (1.5, 2.0, 3.0, 4.0), (0.0, 1.0))]
        text = s.script_text()
        self.assertIn("frame=[", text)
        self.assertIn("Place(item='world', shape='line'),", text)
        self.assertIn("Place(item=17, shape='rectangle', coords=(1.5, 2.0, 3.0, 4.0), rates=(0.0, 1.0)),", text)
        edited = text.replace("coords=(1.5, 2.0, 3.0, 4.0)", "coords=(1.5, 2.0, 30.0, 4.0)")
        s.edit_script(edited, s.sim.script)
        self.assertEqual(s.sim.script.frame[1].coords, (1.5, 2.0, 30.0, 4.0))
        self.assertEqual(s.sim.script.frame_given, s.sim.shown)

    def test_the_window_keeps_it_until_s_v(self):
        try:
            from gui import keeps_frame
        except ImportError:
            self.skipTest("no tkinter")
        from script import Place, Script
        given = Script(frame=[Place("world", "line")], frame_given=3)
        self.assertTrue(keeps_frame(given, 3))  # the window redraws: it stays
        self.assertFalse(keeps_frame(given, 4))  # s_v showed the line again: made again
        self.assertFalse(keeps_frame(Script(frame=[Place("world", "line")]), 3))  # s_v's own


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
                    [Breaks(length=15)])
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
        # published before the run's last one, it calls for a new run where it stops being definitive
        self.assertIn("Event(trigger=ChipIn(lo=F(10), hi=INF), actions=(RerunAction(),))", s.script_text())
        s.sim.edit_now([Op.put("NoScreen", Region.of(Interval.closed(50, 60)))])
        self.assertIsNot(s.sim.request, first)  # the run from the line before the edit: replaced
        self.assertEqual(s.sim.request.why, "user edit")
        self.assertIn("asked by the script then in place, at your edit", s.script_text())
        s.close()


class PerfectTest(unittest.TestCase):
    """The user's rule: a script that calls for no new run but at the user's edits is the
    perfect one, and the run that published it ends there."""

    def test_the_run_ends_with_it(self):
        from generator import RulesGenerator
        from line import LineState
        from modes import ModeTimeline
        from rules import parse_rules
        from script import Edited
        g = RulesGenerator(parse_rules(["A → B"]), [(PresenceRatioProblem({}), 1)], 0.5)
        out = list(g.stages(LineState.empty(), F(0), ModeTimeline(0, "1").known(0)))
        self.assertEqual(len(out), 1)
        script, stats = out[0]
        self.assertTrue(all(isinstance(tr, Edited) for tr in script.rerun_triggers()))
        self.assertTrue(stats.final)
        self.assertEqual(stats.definitive, float("inf"))

    def test_a_late_run_moves_the_call(self):
        """The script in place gets definitive further when the run is late (the pace):
        its call for a new run goes with where it stops being definitive."""
        from script import ChipIn, RerunAction
        from simulation import LAST_RESORT
        s = Session(RULES, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2),
                    [Breaks(length=15)])
        s.sim.background = True
        s.begin()
        req = s.sim.request
        s.sim.keep_pace(req.pace_at)

        def calls():
            return [e.trigger.lo for e in s.sim.script.events if isinstance(e.trigger, ChipIn) and e.trigger.mode is None
                    and e.actions == (RerunAction(),)]
        self.assertEqual(s.sim.definitive, 20)
        self.assertEqual(calls(), [20])
        s.close()


if __name__ == "__main__":
    unittest.main()
