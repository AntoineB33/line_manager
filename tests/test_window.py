"""What the window and a run do in real time: requirement.md's first script, re-runs, limits
on the clock, sleep, and the slow edit, in the real window."""

import gc
import os
import sys
import time
import unittest
from fractions import Fraction as F

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "UI"))

from helpers import Interval, Region  # noqa: E402

import app as A  # noqa: E402
from app import FifteenMinuteBreaks, PresenceRatioProblem, Session  # noqa: E402
from generator import RulesGenerator  # noqa: E402
from line import LineState, Op  # noqa: E402
from modes import ModeTimeline  # noqa: E402
from rules import parse_rules  # noqa: E402
from script import ChipIn, EditAction, RerunAction  # noqa: E402
from simulation import EDIT_LIMIT, Chip, Simulation  # noqa: E402
from solver import checkpoint  # noqa: E402

try:
    import gui
    import tkinter
    tkinter.Tk().destroy()
except Exception:  # no display
    gui = None

BREAKS = ["Break15 → NoScreen", "mode 1 → no NoScreen", "mode 2 → NoScreen"]


def background(rules, problem, constraints=(), q=F(1, 2)):
    g = RulesGenerator(parse_rules(rules), problem, q, constraints=constraints)
    g.paced = True
    chip = Chip([(0, 0)])
    sim = Simulation(LineState.empty(), g, chip, ModeTimeline(0, "1"))
    sim.background = True
    sim.start()
    return sim, chip


def go(sim, chip, t):
    chip.extend(t, t)
    sim.advance(t)


class FirstScriptTest(unittest.TestCase):
    def test_the_biggest_ratio_target(self):
        sim, _ = background(["A → B"], PresenceRatioProblem({"X": F(1, 4), "Y": F(1, 2)}, 120))
        self.assertEqual(sim.state.line.presence("Y"), Region.of(Interval(0, 10, False, True)))
        self.assertFalse(sim.state.line.presence("X"))

    def test_only_what_is_not_reached(self):
        sim, _ = background(["A → B"], PresenceRatioProblem({"X": F(1, 4), "Y": F(1, 2)}, 120))
        y = sim.state.line.presence("Y")
        sim.state = LineState.empty()
        rest = sim.gen.first_edit(sim.state, F(0))
        self.assertEqual(rest[0].kind, "Y")
        state = sim.state
        for kind in ("Y", "X"):
            from resolve import apply_edit
            state = apply_edit(sim.rules, state, [Op.put(kind, Region.of(Interval(0, 120, False, True)))], "user")
        self.assertEqual(sim.gen.first_edit(state, F(0)), [])
        self.assertTrue(y)

    def test_the_constraints_win(self):
        """The kind rules out "no screen": the constraint's breaks stay, and the
        first script does not put what they forbid."""
        rules = BREAKS + ["X → no NoScreen"]
        sim, chip = background(rules, PresenceRatioProblem({"X": F(1, 2)}, 240), [FifteenMinuteBreaks()])
        self.assertIn("keep Break15", sim.script.text())
        self.assertTrue(sim.state.line.presence("Break15").contains(5))
        self.assertFalse(sim.state.line.presence("X").contains(5))

    def test_it_stays_in_the_next_scripts(self):
        sim, _ = background(["A → B"], PresenceRatioProblem({"Y": F(1, 2)}, 120))
        req = sim.request
        line = "put Y 100% on (0, 10]"
        self.assertIn(line, sim.script.text())
        for script, stats in req.gen.stages(req.state, req.chip, req.known, req.previous):
            self.assertIn(line, script.text())


class ReRunTest(unittest.TestCase):
    def test_a_run_is_not_cancelled_by_its_own_script(self):
        sim, chip = background(["mode 1 → D"], PresenceRatioProblem({}, 90))
        req = sim.request
        out = list(req.gen.stages(req.state, req.chip, req.known, req.previous))
        sim.publish(req.id, *out[0])
        at = next(e.trigger.lo for e in sim.script.events if isinstance(e.trigger, ChipIn) and isinstance(e.actions[0], RerunAction))
        go(sim, chip, at + 1)
        self.assertIs(sim.request, req)
        sim.complete(req.id, *out[-1])
        self.assertIsNot(sim.request, req)  # now it may
        self.assertIsNotNone(sim.request)
        self.assertEqual(sim.request.why, "script")

    def test_the_next_run_comes_after_this_one(self):
        """The first stage measures: at twice what the run takes, not D/18."""
        g = RulesGenerator(parse_rules(["mode 1 → D"]), PresenceRatioProblem({"A": F(1, 4)}, 180), F(1, 2))
        g.paced, g.speed = True, F(1000)
        real = g._stage

        def slow(*a):
            time.sleep(0.05)
            return real(*a)

        g._stage = slow
        script, _ = next(g.stages(LineState.empty(), F(0), ModeTimeline(0, "1").known(0)))
        at = next(e.trigger.lo for e in script.events if isinstance(e.trigger, ChipIn) and isinstance(e.actions[0], RerunAction))
        self.assertGreaterEqual(at, 100)  # 0.05 s * 2 * at least one stage * 1000 units per second
        self.assertGreater(at, 135 / 18)


class TimeLimitTest(unittest.TestCase):
    def test_an_edit_over_the_limit_is_refused(self):
        class Slow:
            def ops(self, state, c):
                while state.line.presence("X"):
                    checkpoint()
                return []

            def targets(self, state, c):
                return {}

        sim, chip = background(["mode 1 → D"], PresenceRatioProblem({}, 90), [Slow()])
        t = time.monotonic()
        why = sim.edit_now([Op.put("X", Interval.closed(50, 51))])
        took = time.monotonic() - t
        self.assertIn("too long", why)
        self.assertLess(took, EDIT_LIMIT + 1)
        self.assertGreaterEqual(took, EDIT_LIMIT)
        self.assertFalse(sim.state.line.presence("X"))

    def test_the_10_seconds_are_never_extended(self):
        """With no answer from the run: at 8 s the last resort, so that an edit of 2 s going
        on at that moment still ends before the 10 s."""
        sim, chip = background(["mode 1 → D"], PresenceRatioProblem({}, 90))
        go(sim, chip, F(79, 10))
        self.assertEqual(sim.definitive, 10)
        go(sim, chip, 8)
        self.assertEqual(sim.definitive, 20)


class SleepTest(unittest.TestCase):
    def test_the_chip_and_the_catch_up(self):
        sim, chip = background(BREAKS, PresenceRatioProblem({}, 240), [FifteenMinuteBreaks()])
        req = sim.request
        sim.complete(req.id, *list(req.gen.stages(req.state, req.chip, req.known, req.previous))[-1])
        t = time.monotonic()
        go(sim, chip, 3600)  # one hour asleep: as if it had moved
        self.assertLess(time.monotonic() - t, 30)
        self.assertEqual(chip.position(sim.t), 3600)
        self.assertGreaterEqual(sim.definitive, 3610)
        self.assertIsNotNone(sim.request)
        b = sim.state.line.presence("Break15")
        self.assertTrue(b.contains(3605))  # the constraint holds there at once
        self.assertFalse(b.contains(3599))  # and it was dragged, not left behind


@unittest.skipIf(gui is None, "no display")
class WindowTest(unittest.TestCase):
    def setUp(self):
        self.s = Session(BREAKS, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}, horizon=240), 1)], F(1, 2), [FifteenMinuteBreaks()])
        self.root = gui.tk.Tk()
        self.app = gui.App(self.root, self.s)
        self.addCleanup(self.close)

    def close(self):
        """With the window's objects freed in this thread: Tk does not stand it in another."""
        worker = self.app.worker
        self.app.close()
        worker.join(10)
        self.app = self.root = None
        gc.collect()

    def pump(self, seconds, until=None):
        end = time.time() + seconds
        while time.time() < end and not (until and until()):
            self.root.update()
            time.sleep(0.005)

    def test_a_slow_edit(self):
        """The display does not stop, the chip keeps pushing the break, and when the
        calculation is done the break has a hole from where the mode was changed."""
        app, s = self.app, self.s
        self.pump(10, lambda: s.sim.started and s.sim.script.constraints)
        app.toggle()
        self.pump(2)
        app.set_mode("2")
        self.pump(2)
        real = s.set_mode

        def slow(m):
            time.sleep(1.5)
            return real(m)

        s.set_mode = slow
        at = time.time()
        clicked = float(app.shown[0])
        app.set_mode("1")
        chips, shown = [], []
        while time.time() - at < 1.3:
            self.root.update()
            if app.shown is not None and (not chips or app.shown[0] != chips[-1]):
                chips.append(app.shown[0])
                shown.append(time.time())
            time.sleep(0.005)
        self.assertGreater(float(chips[-1]) - float(chips[0]), 1.0)  # it went on
        self.assertLess(max(b - a for a, b in zip(shown, shown[1:])), 0.25)  # with no stop
        self.assertTrue(all(a <= b for a, b in zip(chips, chips[1:])))
        self.pump(1.5)
        b = s.sim.state.line.presence("Break15")
        ivs = (b & Region.of(Interval.closed(0, F(clicked) + 2))).on(0, F(clicked) + 2).intervals
        self.assertLess(abs(float(ivs[0].hi.x) - clicked), 0.15, str(b))  # cut at the click, not at the end of the calculation
        self.assertFalse(b.contains(F(clicked) + F(1, 2)))
        self.assertTrue(b.contains(s.position + 1))  # and a new one ahead of the chip

    def test_a_hanging_worker(self):
        hang = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__", "hanging_worker.py")
        with open(hang, "w") as f:
            f.write("import sys, time\nsys.stdin.buffer.read()\ntime.sleep(3600)\n")
        self.addCleanup(os.remove, hang)
        real, A.WORKER = A.WORKER, hang
        self.addCleanup(setattr, A, "WORKER", real)
        s = self.s
        self.pump(5, lambda: s.sim.started and s.sim.definitive)
        self.assertEqual(s.sim.definitive, 10)
        self.app.toggle()
        self.pump(9)
        self.assertGreaterEqual(s.sim.definitive, 20)


if __name__ == "__main__":
    unittest.main()
