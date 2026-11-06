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
    s.sim.session = s  # (for the tests that type commands)
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
            # the breaks from rules and constraints (no target): as screen_break_15
            "screen_break_15_rules": {(ChipIn, None), Edited},
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

    def test_the_breaks_are_right_when_an_edit_or_a_mode_change_returns(self):
        """OmniApp's three screen breaks with sleep: after each kind of event, the breaks hold
        (ScreenBreaks.ops asks for nothing) as soon as the command returns, generator on or
        off, without waiting for a run. Each command took 0.07 to 0.32 s of processor time
        when this was written, 0.42 to 0.67 s before the sweep read only what is near where
        it is ("no screen" forever ahead after the 7th: the sweep must not read far into it)."""
        import time
        from fractions import Fraction
        s = first_run("screen_breaks_sleep_tasks").session
        sb = next(k for k in s.sim._constraints() if hasattr(k, "breaks"))

        def holds():
            return sb.ops(s.sim.state, s.sim.chip.position(s.sim.t)) == []
        self.assertTrue(holds())
        c = s.sim.chip.position(s.sim.t)
        b15 = s.sim.state.line.presence("Break15").on(c, c + 20000).intervals[0]
        events = [f"add NoScreen on [{c + 600}, {c + 1500})", "mode 2", "mode 1",
                  f"remove Break15 on [{b15.lo.x}, {b15.hi.x}]", f"add Sleep on [{c + 3000}, {c + 4000})",
                  "generator off", f"add NoScreen on [{c + 9000}, {c + 9900})", "mode 3", "mode 1"]
        for cmd in events:
            s.tick(Fraction(1, 2))
            t0 = time.process_time()  # (this process's time: other programs running do not count)
            out = s.command(cmd)
            took = time.process_time() - t0
            self.assertTrue(holds(), (cmd, out))
            self.assertLess(took, 0.5, (cmd, out))
        s.close()

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

    def test_the_breaks_from_rules_and_constraints_only(self):
        """screen_break_15_rules.py: no code for the breaks and no target (a break is no task),
        only rules and constraints of the generator (AsEarlyAsPossible of 15 minutes,
        MinimumGap after 15 minutes of "no screen"): a break of 900 every 8100 from the
        chip, "no screen" on each, nothing else."""
        sim = first_run("screen_break_15_rules")
        line = sim.state.line
        b = line.presence("Break15")
        for n in (0, 1, 2, 100):
            x = 8100 * n
            self.assertTrue(b.contains(x + 1) and b.contains(x + 900), n)
            self.assertFalse(b.contains(x + 901) or b.contains(x + 8099), n)
        self.assertEqual(line.presence("NoScreen").on(0, 40000), b.on(0, 40000))
        self.assertNotIn("Free", line.kinds())

    def breaks(self, name, no_screen=None):
        """The breaks of an example after its first run, and after the user adds "no screen"."""
        from intervals import Interval as Iv2
        from line import Op
        sim = first_run(name)
        if no_screen:
            sim.edit_now([Op.put("NoScreen", Region.of(Iv2(*no_screen, True, True)))])
            req = sim.request
            sim.complete(req.id, *list(req.gen.stages(req.state, req.chip, req.known, req.previous))[-1])
        return sim.state.line.presence("Break15").on(0, 60000)

    def test_the_same_breaks_as_the_hand_written_ones(self):
        """screen_break_15_rules.py (rules, targets, constraints of the generator) and
        screen_break_15.py (the breaks written in Python, constraints.Breaks): the same line,
        and the same after 30 minutes without screen (the next break 2 hours after them)."""
        def real(breaks):  # (their ends as numbers: the hand-written ones end ε after, at 900+ε)
            return [(iv.lo.x, iv.hi.x) for iv in breaks.intervals]
        for no_screen in (None, (18000, 19800)):
            with self.subTest(no_screen=no_screen):
                self.assertEqual(real(self.breaks("screen_break_15_rules", no_screen)),
                                 real(self.breaks("screen_break_15", no_screen)))
        starts = [iv.lo.x for iv in self.breaks("screen_break_15_rules", (18000, 19800)).intervals]
        self.assertEqual(starts[:4], [0, 8100, 16200, 27000])

if __name__ == "__main__":
    unittest.main()
