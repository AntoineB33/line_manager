"""What the window and a run do in real time: requirement.md's first script, re-runs, limits
on the clock, sleep, and the slow edit, in the real window."""

import gc
import os
import sys
import tempfile
import time
import unittest
from fractions import Fraction as F
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "UI"))

from helpers import Interval, Region  # noqa: E402
from helpers import summing_to_1  # noqa: E402

import app as A  # noqa: E402
from app import Breaks, MinimumLengthProblem, PresenceRatioProblem, Session  # noqa: E402
from generator import RulesGenerator  # noqa: E402
from line import LineState, Op  # noqa: E402
from modes import ModeTimeline  # noqa: E402
from resolve import apply_edit  # noqa: E402
from rules import parse_rules  # noqa: E402
from session_log import SessionLog  # noqa: E402
from script import ChipIn, RerunAction  # noqa: E402
from simulation import EDIT_LIMIT, Chip, Simulation  # noqa: E402
from solver import STOP, Interrupted, checkpoint  # noqa: E402

try:
    import gui
    import tkinter
    probe = tkinter.Tk()
    probe.withdraw()
    probe.destroy()
except Exception:  # no display
    gui = None

BREAKS = ["Break15 → NoScreen", "mode 1 → no NoScreen", "mode 2 → NoScreen"]


def background(rules, problem, constraints=(), q=F(1, 2)):
    g = RulesGenerator(parse_rules(rules), problem, q, constraints=constraints, shortcuts=False)  # the search's pace
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
        sim, _ = background(["A → B"], PresenceRatioProblem(summing_to_1({"X": F(1, 4), "Y": F(1, 2)})).with_window(120))
        self.assertEqual(sim.state.line.presence("Y"), Region.of(Interval(0, 10, False, True)))
        self.assertFalse(sim.state.line.presence("X"))

    def test_only_what_is_not_reached(self):
        sim, _ = background(["A → B"], PresenceRatioProblem(summing_to_1({"X": F(1, 4), "Y": F(1, 2)})).with_window(120))
        y = sim.state.line.presence("Y")
        sim.state = LineState.empty()
        rest = sim.gen.first_edits(sim.state, F(0))
        self.assertEqual(rest[0][0].kind, "Y")
        state = sim.state
        for kind in ("Y", "X", "Rest"):
            state = apply_edit(sim.rules, state, [Op.put(kind, Region.of(Interval(0, 120, False, True)))], "user")
        self.assertEqual(sim.gen.first_edits(state, F(0)), [])
        self.assertTrue(y)

    def test_the_constraints_win(self):
        """The kind rules out "no screen": the constraint's breaks stay, and the
        first script does not put what they forbid."""
        rules = BREAKS + ["X → no NoScreen"]
        sim, chip = background(rules, PresenceRatioProblem(summing_to_1({"X": F(1, 2)})).with_window(240), [Breaks()])
        self.assertIn("keep Break15", sim.script.text())
        self.assertTrue(sim.state.line.presence("Break15").contains(5))
        self.assertFalse(sim.state.line.presence("X").contains(5))

    def test_a_kind_short_of_its_minimum_length_goes_on(self):
        rules = parse_rules(["A → B"])
        g = RulesGenerator(rules, MinimumLengthProblem({"X": 5, "Y": 5}).with_window(60), F(1, 2))
        state = apply_edit(rules, LineState.empty(), [Op.put("X", Interval.closed(18, 20)), Op.put("Y", Interval.closed(10, 20)),
                                                      Op.put("Z", Interval.closed(18, 20))], "user")
        self.assertEqual([[o.kind for o in ops] for ops in g.first_edits(state, F(20))], [["X"]])  # Y is long enough
        self.assertEqual(g.first_edits(state, F(40)), [])  # nothing at the chip
        chip = Chip([(0, 20)])
        g.paced = True
        sim = Simulation(state, g, chip, ModeTimeline(20, "1"))
        sim.background = True
        sim.start()
        self.assertTrue(sim.state.line.presence("X").contains(25))
        self.assertFalse(sim.state.line.presence("Y").contains(25))

    def test_it_stays_in_the_next_scripts(self):
        sim, _ = background(["A → B"], PresenceRatioProblem(summing_to_1({"Y": F(1, 2), "X": F(1, 4)})).with_window(120))
        req = sim.request
        line = "put Y 100% on (0, 10]"
        self.assertIn(line, sim.script.text())
        for script, stats in req.gen.stages(req.state, req.chip, req.known, req.previous):
            self.assertIn(line, script.text())


class CalculatorTest(unittest.TestCase):
    """A process that never answers, not even at a checkpoint."""

    def session(self, worker=None):
        if worker:
            hang = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__", "busy_worker.py")
            with open(hang, "w") as f:
                f.write("import pickle, sys\npickle.dump('ready', sys.stdout.buffer)\nsys.stdout.buffer.flush()\n"
                        "sys.stdin.buffer.read(1)\nwhile True:\n    pass\n")
            self.addCleanup(os.remove, hang)
            real, A.EDIT_WORKER = A.EDIT_WORKER, hang
            self.addCleanup(setattr, A, "EDIT_WORKER", real)
        s = Session(BREAKS, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2), [Breaks()])
        s.sim.background = False
        s.sim.start()
        s.calculate_apart()
        self.addCleanup(s.calculator.close)
        return s

    def test_an_edit_that_never_ends_is_refused_at_the_limit(self):
        s = self.session(worker=True)
        s.calculator.ask  # started: its imports are not the edit's time
        t = time.monotonic()
        out = s.command("add NoScreen on [50, 51]")
        took = time.monotonic() - t
        self.assertIn(A.TOO_LONG, out)
        self.assertGreaterEqual(took, EDIT_LIMIT - 0.01)
        self.assertLess(took, EDIT_LIMIT + 2)
        self.assertFalse(s.sim.state.line.presence("NoScreen").contains(50))

    def test_a_mode_change_that_never_ends_is_refused_at_the_limit(self):
        s = self.session(worker=True)
        t = time.monotonic()
        out = s.set_mode("2")
        self.assertIn("refused", out)
        self.assertLess(time.monotonic() - t, EDIT_LIMIT + 2)
        self.assertEqual(s.mode(), "1")

    def test_it_is_replaced(self):
        s = self.session(worker=True)
        first = s.calculator.proc
        self.assertIn(A.TOO_LONG, s.command("add NoScreen on [50, 51]"))
        self.assertIsNot(s.calculator.proc, first)
        self.assertIsNotNone(first.wait(5))  # ended

    def test_the_same_as_at_once(self):
        s, plain = self.session(), Session(BREAKS, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2), [Breaks()])
        plain.sim.background = False
        plain.sim.start()
        for text in ("add NoScreen on [300, 310]", "add X on [50, 51]", "add Break15 on [40, 41]"):
            self.assertEqual(s.command(text), plain.command(text))
            self.assertEqual(s.sim.state.line, plain.sim.state.line)
        self.assertEqual(s.set_mode("2")[:6], "mode 2")
        self.assertEqual(s.mode(), "2")


def hidden_root():
    """The tests do not take the screen: nothing they open is shown."""
    root = gui.tk.Tk()
    root.withdraw()
    return root


DEVICES = ["Break15 → NoScreen",
           "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
           "NotOnComputer → no NoComputerUnlocked", "NotOnPhone → no NoPhoneUnlocked",
           "mode 1 → no NoScreen", "mode 2 → NoScreen & NoComputerUnlocked & NoPhoneUnlocked",
           "mode 3 → NoScreen & (NotOnComputer or NotOnPhone)"]
UN, FAKE, LOCK = "unlocked", "fake locked", "locked"
SELECTORS = {"computer": (UN, FAKE, LOCK), "phone": (UN, FAKE, LOCK)}
SELECTOR_MODES = {(UN, UN): "1", (UN, FAKE): "1", (UN, LOCK): "1", (FAKE, UN): "1", (LOCK, UN): "1", (LOCK, LOCK): "2",
                  (FAKE, LOCK): "3", (LOCK, FAKE): "3", (FAKE, FAKE): "3"}


def devices(modes=((0, "1"),)):
    s = Session(DEVICES, modes, 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2),
                [Breaks()], SELECTORS, SELECTOR_MODES)
    s.sim.background = False
    s.sim.start()
    return s


class SelectorTest(unittest.TestCase):
    def test_each_choice_gives_its_mode(self):
        for choice, mode in SELECTOR_MODES.items():
            s = devices()
            s.select(0, choice[0])
            s.select(1, choice[1])
            self.assertEqual(s.mode(), mode, choice)
            self.assertEqual(s.selection(), choice)

    def test_a_choice_that_keeps_the_mode(self):
        s = devices()
        self.assertEqual(s.select(0, LOCK), "computer locked: mode 1 stays")
        self.assertEqual(s.selection(), (LOCK, UN))
        self.assertEqual(s.select(1, LOCK)[:12], "phone locked")
        self.assertEqual(s.mode(), "2")

    def test_a_mode_they_did_not_choose(self):
        s = devices()
        s.select(0, LOCK)
        s.set_mode("3")
        self.assertEqual(s.selection(), (FAKE, LOCK))
        s.set_mode("2")
        self.assertEqual(s.selection(), (LOCK, LOCK))
        s = devices([(0, "1"), (1, "3")])
        s.tick(F(2))
        self.assertEqual(s.selection(), (FAKE, LOCK))

    def test_a_mode_they_cannot_give(self):
        s = devices()
        self.assertIn("usage", s.set_mode("4"))
        self.assertEqual(s.mode(), "1")
        with self.assertRaises(ValueError):
            Session(DEVICES, [(0, "4")], selectors=SELECTORS, selector_modes=SELECTOR_MODES)
        with self.assertRaises(ValueError):
            Session(DEVICES, [(0, "1")], selectors=SELECTORS, selector_modes={(UN, UN): "1"})


class SessionLogTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)

    def read(self, name):
        with open(os.path.join(self.folder.name, name), encoding="utf-8") as f:
            return f.read()

    def test_the_events_are_kept_over_the_sessions(self):
        for i in range(2):
            SessionLog("x", self.folder.name).event(f"event {i}")
        out = self.read("x.log")
        self.assertEqual(out.count("session started"), 2)
        self.assertIn("event 0", out)
        self.assertIn("event 1", out)

    def test_the_state_is_replaced_and_says_it_all(self):
        file = SessionLog("x", self.folder.name)
        s = devices()
        s.command("add NoScreen on [300, 310]")
        s.select(0, FAKE)
        file.state("old")
        self.assertFalse(file.state_due())
        file.state(s.state_text())
        out = self.read("x.state.txt")
        self.assertNotIn("old", out)
        self.assertIn(f"process {os.getpid()}", out)
        self.assertIn("selectors: computer fake locked, phone unlocked", out)
        self.assertIn("add NoScreen on [300, 310]", out)
        self.assertIn("--- line", out)
        self.assertIn("Generated script", out)

    def test_nothing_stops_the_window(self):
        SessionLog("x", self.folder.name)
        file = SessionLog("x", os.path.join(self.folder.name, "x.log", "no"))  # a folder that cannot be
        file.event("lost")
        file.state("lost")


class ReRunTest(unittest.TestCase):
    def test_a_run_is_cancelled_by_a_new_call_of_its_script(self):
        """requirement.md: called again while it is running, the previous run is cancelled."""
        sim, chip = background(["mode 1 → D"], PresenceRatioProblem({}).with_window(90))
        req = sim.request
        out = list(req.gen.stages(req.state, req.chip, req.known, req.previous))
        sim.publish(req.id, *out[0])
        at = next(e.trigger.lo for e in sim.script.events if isinstance(e.trigger, ChipIn) and isinstance(e.actions[0], RerunAction))
        go(sim, chip, at + 1)
        self.assertIsNot(sim.request, req)
        self.assertEqual(sim.request.why, "script")
        self.assertFalse(sim.complete(req.id, *out[-1]))

    def test_the_next_run_comes_after_this_one(self):
        """The first stage measures: at twice what the run takes, not D/18."""
        g = RulesGenerator(parse_rules(["mode 1 → D"]), PresenceRatioProblem(summing_to_1({"A": F(1, 4)})).with_window(180), F(1, 2))
        g.paced, g.speed = True, F(1000)
        real = g._stage

        def slow(*a, **k):
            time.sleep(0.05)
            return real(*a, **k)

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

        sim, chip = background(["mode 1 → D"], PresenceRatioProblem({}).with_window(90), [Slow()])
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
        sim, chip = background(["mode 1 → D"], PresenceRatioProblem({}).with_window(90))
        go(sim, chip, F(79, 10))
        self.assertEqual(sim.definitive, 10)
        go(sim, chip, 8)
        self.assertEqual(sim.definitive, 20)


class SleepTest(unittest.TestCase):
    def test_a_script_that_holds_by_itself_has_nothing_to_catch_up(self):
        sim, chip = background(["Break15 → NoScreen"], PresenceRatioProblem({}).with_window(240), [Breaks()])
        req = sim.request
        sim.complete(req.id, *list(req.gen.stages(req.state, req.chip, req.known, req.previous))[-1])
        go(sim, chip, 3600)
        self.assertEqual(chip.position(sim.t), 3600)
        self.assertIsNone(sim.request)
        b = sim.state.line.presence("Break15")
        self.assertTrue(b.contains(3515) and b.contains(3650))  # one every 135, past the horizon too
        self.assertFalse(b.contains(3605))

    def test_the_chip_and_the_catch_up(self):
        sim, chip = background(BREAKS, PresenceRatioProblem({}).with_window(240), [Breaks()])
        req = sim.request
        sim.complete(req.id, *list(req.gen.stages(req.state, req.chip, req.known, req.previous))[-1])
        t = time.monotonic()
        go(sim, chip, 3600)  # one hour asleep: as if it had moved
        self.assertLess(time.monotonic() - t, 30)
        self.assertEqual(chip.position(sim.t), 3600)
        self.assertGreaterEqual(sim.definitive, 3610)
        # a run catching up, or the script calls for one where it stops being definitive
        # (the user's rule: a script that calls for none is the perfect one, and this is not)
        calls = [e.trigger.lo for e in sim.script.events if isinstance(e.trigger, ChipIn) and e.trigger.mode is None
                 and any(isinstance(a, RerunAction) for a in e.actions)]
        self.assertTrue(sim.request is not None or any(x <= sim.definitive for x in calls), calls)
        b = sim.state.line.presence("Break15")
        self.assertTrue(b.contains(3605))  # the constraint holds there at once
        self.assertFalse(b.contains(3599))  # and it was dragged, not left behind


class StoppedInstallTest(unittest.TestCase):
    def test_a_script_stopped_while_installed_is_installed_again(self):
        """Pause while a script of a run is installed: undone, not lost, and no exception."""
        s = Session(BREAKS, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2), [Breaks()])
        calls = []

        def publish(rid, script, stats):
            calls.append(rid)
            s.sim.definitive = 999
            if len(calls) == 1:
                raise Interrupted("stopped")
            return True

        was = s.sim.definitive
        s._done.put((7, object(), SimpleNamespace(final=False)))
        with mock.patch.object(s.sim, "publish", side_effect=publish):
            self.assertEqual(s.runs(), [])
            self.assertEqual(s.sim.definitive, was)
            self.assertEqual(calls, [7])
            s.runs()
        self.assertEqual(calls, [7, 7])
        self.assertEqual(s.sim.definitive, 999)
        self.assertIsNone(s._stopped)


@unittest.skipIf(gui is None, "no display")
class ReplacedTest(unittest.TestCase):
    def test_a_window_a_new_one_replaces_closes(self):
        """In a window that stays hidden."""
        s = Session(["mode 1 → A"])
        root = hidden_root()
        app = gui.App(root, s)
        worker = app.worker
        app.replaced = True
        end = time.time() + 10
        while time.time() < end and not app.stopping:
            root.update()
            time.sleep(0.005)
        self.assertTrue(app.stopping)
        worker.join(10)
        self.assertFalse(worker.is_alive())
        app = root = None
        gc.collect()


@unittest.skipIf(gui is None, "no display")
class CaptionTest(unittest.TestCase):
    def test_a_long_caption_is_shown_whole(self):
        """In a window that stays hidden."""
        s = Session(["Break15 → NoComputerUnlockedForAVeryLongTime", "mode 1 → A"])
        root = hidden_root()
        app = gui.App(root, s)

        def close():
            worker = app.worker
            app.close()
            worker.join(10)

        self.addCleanup(close)
        end = time.time() + 10
        while app.snap is None and time.time() < end:
            root.update()
            time.sleep(0.005)
        app.draw(app.snap)
        c = app.canvas
        caption = next(i for i in c.find_all() if c.type(i) == "text" and c.itemcget(i, "text").startswith("NoComputer"))
        self.assertGreaterEqual(c.bbox(caption)[0], 0)
        self.assertEqual(app._edge(["A"]), gui.MIN_LEFT)


@unittest.skipIf(gui is None, "no display")
class WindowTest(unittest.TestCase):
    def setUp(self):
        self.s = Session(BREAKS, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2), [Breaks()])
        self.root = hidden_root()
        self.app = gui.App(self.root, self.s)
        self.addCleanup(self.close)

    def close(self):
        """With the window's objects freed in this thread: Tk does not stand it in another."""
        worker = self.app.worker
        shown = self.root.state()
        self.app.close()
        worker.join(10)
        self.app = self.root = None
        gc.collect()
        self.assertEqual(shown, "withdrawn")

    def pump(self, seconds, until=None):
        end = time.time() + seconds
        while time.time() < end and not (until and until()):
            self.root.update()
            time.sleep(0.005)

    def test_the_wheel_scrolls_over_the_line_and_zooms_with_ctrl(self):
        app = self.app
        for wheel in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.assertTrue(self.root.bind(wheel))
        w = max(app.canvas.winfo_width(), 300)
        over_chip = app.edge[1] + F(w - app.edge[1] - gui.RIGHT, 4)  # (the mouse over the chip: the scroll stays)
        with mock.patch.object(self.root, "winfo_containing", return_value=app.canvas), \
                mock.patch.object(app, "_pointer_x", return_value=over_chip):
            app.on_wheel(SimpleNamespace(delta=-120, num="??", state=0))
            self.assertEqual((app.span, app.scrolled), (24, 3))
            app.on_wheel(SimpleNamespace(delta=120, num="??", state=0))
            app.on_wheel(SimpleNamespace(delta=0, num=4, state=0))
            self.assertEqual((app.span, app.scrolled), (24, -3))
            app.on_wheel(SimpleNamespace(delta=0, num=5, state=0))
            self.assertEqual((app.span, app.scrolled), (24, 0))
            app.on_wheel(SimpleNamespace(delta=-120, num="??", state=gui.CONTROL))
            self.assertEqual((app.span, app.scrolled), (48, 0))
            app.on_wheel(SimpleNamespace(delta=120, num="??", state=gui.CONTROL))
            app.on_wheel(SimpleNamespace(delta=0, num=4, state=gui.CONTROL))
            self.assertEqual((app.span, app.scrolled), (12, 0))
            app.on_wheel(SimpleNamespace(delta=0, num=5, state=gui.CONTROL))
            self.assertEqual((app.span, app.scrolled), (24, 0))
        with mock.patch.object(self.root, "winfo_containing", return_value=app.text):
            app.on_wheel(SimpleNamespace(delta=-120, num="??", state=gui.CONTROL))  # the log keeps its own scroll
            app.on_wheel(SimpleNamespace(delta=-120, num="??", state=0))
            self.assertEqual((app.span, app.scrolled), (24, 0))
        for _ in range(30):
            app.zoom(F(2))
        self.assertEqual(app.span, gui.MAX_SPAN)
        for _ in range(30):
            app.zoom(F(1, 2))
        self.assertEqual(app.span, gui.MIN_SPAN)

    def test_zooming_keeps_the_position_under_the_mouse_under_it(self):
        """Ctrl + wheel with the mouse over the chip, the chip not at its usual place: the
        chip is drawn at the same x after the zoom, in and out."""
        app, c = self.app, self.app.canvas
        self.pump(10, lambda: app.snap is not None)
        frozen = dict(app.snap, wall=None)  # (the chip stays where it is)

        def chip_x():
            app.draw(frozen)
            chip = next(i for i in c.find_all() if c.type(i) == "polygon" and c.itemcget(i, "fill") == "#d63031")
            xs = c.coords(chip)[::2]
            return (min(xs) + max(xs)) / 2  # (the chip's position: the middle of its triangle)

        app.scrolled = -app.span / 4  # (the chip in the middle of the line)
        x = chip_x()
        under = app.at_x(round(x))
        for up in (True, True, False, False, False):
            with mock.patch.object(self.root, "winfo_containing", return_value=app.canvas), \
                    mock.patch.object(app, "_pointer_x", return_value=round(x)):
                app.on_wheel(SimpleNamespace(delta=120 if up else -120, num="??", state=gui.CONTROL))
            self.assertEqual(app.at_x(round(x)), under)
            self.assertAlmostEqual(chip_x(), x, delta=1)
        self.assertEqual(app.span, 48)

    def test_a_chip_scrolled_out_of_view_is_not_drawn(self):
        app, c = self.app, self.app.canvas
        self.pump(10, lambda: app.snap is not None)

        def chips():
            return [i for i in c.find_all() if c.type(i) == "polygon" and c.itemcget(i, "fill") == "#d63031"]

        for scrolled, shown in ((F(0), 1), (F(1000), 0), (F(-1000), 0)):
            app.scrolled = scrolled
            app.draw(app.snap)
            self.assertEqual(len(chips()), shown)
            self.assertTrue(all(c.coords(i)[0] >= app.edge[1] for i in c.find_all() if c.type(i) == "rectangle" and c.itemcget(i, "stipple")))

    def test_a_failure_of_a_step_does_not_end_the_thread(self):
        """Pause does not stop what a run installs, and whatever fails, Play still works."""
        app, stops = self.app, []
        self.pump(10, lambda: app.snap is not None)
        STOP.set()

        def fails():
            stops.append(STOP.is_set())
            if len(stops) == 1:
                raise ValueError("it failed")
            return []

        with mock.patch.object(self.s, "runs", side_effect=fails):
            app.paused = False
            self.pump(10, lambda: len(stops) > 2 and "it failed" in app.text.get("1.0", "end"))
        self.assertEqual(stops[0], False)
        self.assertTrue(app.worker.is_alive())
        self.assertTrue(app.paused)
        self.assertEqual(app.play.cget("text"), "▶ Play")
        at = self.s.position
        app.toggle()
        self.pump(10, lambda: self.s.position > at)
        self.assertGreater(self.s.position, at)

    def drawn(self):
        """What can be seen on the canvas, with the positions in pixels."""
        c, edge = self.app.canvas, self.app.edge[1]
        width = max(c.winfo_width(), 300) - gui.RIGHT
        fixed = set(c.find_withtag("fixed"))
        out = []
        for i in c.find_all():
            xs = c.coords(i)[::2]
            if i not in fixed and max(xs) > edge + 3 and min(xs) < width - 3:
                out.append((c.type(i), tuple(round(v) for v in c.coords(i)), c.itemcget(i, "fill")))
        return sorted(out)

    def test_a_frame_moves_what_is_there(self):
        """Frames with no list built again give what a list built at that moment gives."""
        app, s = self.app, self.s
        self.pump(10, lambda: s.sim.started and s.sim.script.constraints and app.snap is not None)
        app.zoom(F(1, 4))
        app.toggle()
        self.pump(2)
        sn = app.snap
        app.sign = None
        app.draw(sn)
        fresh = []
        real = gui.App._rebuild
        built = []

        def rebuild(*args):
            built.append(1)
            real(app, *args[1:] if args[0] is app else args)

        with mock.patch.object(app, "_rebuild", side_effect=lambda *a: rebuild(*a)):
            when = sn["wall"]
            for dt in (0.05, 0.1, 0.15):
                with mock.patch.object(gui.time, "time", return_value=when + dt + 2):
                    app.draw(sn)
            moved = self.drawn()
        if built:
            self.skipTest("the formula stopped")
        with mock.patch.object(gui.time, "time", return_value=when + 2.15):
            app.sign = None
            app.draw(sn)
        fresh = self.drawn()
        self.assertEqual(len(moved), len(fresh))
        for a, b in zip(moved, fresh):
            self.assertEqual(a[0], b[0])
            self.assertEqual(a[2], b[2])
            for u, v in zip(a[1], b[1]):
                self.assertLessEqual(abs(u - v), 2, (a, b))

    def test_a_frame_calls_the_canvas_little(self):
        app, s = self.app, self.s
        self.pump(10, lambda: s.sim.started and s.sim.script.constraints and app.snap is not None)
        app.toggle()
        self.pump(2)
        sn = app.snap
        app.sign = None
        app.draw(sn)
        spy = mock.Mock(wraps=app.canvas)
        app.view.c = spy
        with mock.patch.object(gui.time, "time", return_value=sn["wall"] + 0.03):
            app.draw(sn)
        app.view.c = app.canvas
        self.assertLessEqual(len(spy.method_calls), 6, spy.method_calls)

    def test_zooming_in_takes_the_far_break_off_the_list(self):
        app, s = self.app, self.s
        self.pump(10, lambda: app.snap is not None and app.snap["line"].presence("Break15"))
        app.zoom(F(1024))  # (as wide as it goes)
        app.draw(app.snap)
        wide = sum(k[0] == "bar" for k in app.view.ids)
        self.assertGreater(wide, 0)
        for _ in range(6):
            app.zoom(F(1, 2))
        app.draw(app.snap)
        self.assertLess(sum(k[0] == "bar" for k in app.view.ids), wide)

    def test_the_axis_has_a_dozen_labels_at_every_zoom(self):
        span = gui.MIN_SPAN
        while span <= gui.MAX_SPAN:
            self.assertTrue(5 <= span / gui._tick(span) <= 14, span)
            span *= 2

    def test_selectors_replace_the_mode_buttons(self):
        self.close()
        self.s = s = Session(DEVICES, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2),
                             [Breaks()], SELECTORS, SELECTOR_MODES)
        self.root = hidden_root()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        file = SessionLog("x", folder.name)
        self.app = app = gui.App(self.root, s, file)
        self.pump(10, lambda: app.selector_buttons[0, UN].cget("relief") == "sunken")
        self.assertEqual(len(app.selector_buttons), 6)
        self.assertFalse(app.mode_buttons)
        self.assertEqual(app.selector_buttons[0, LOCK].cget("state"), "disabled")  # paused
        self.pump(10, lambda: s.sim.started)
        app.toggle()
        self.pump(1)
        app.selector_buttons[0, LOCK].invoke()
        self.pump(5, lambda: s.selection() == (LOCK, UN))
        self.assertEqual(s.mode(), "1")
        app.selector_buttons[1, FAKE].invoke()
        self.pump(5, lambda: s.mode() == "3")
        self.pump(2)
        self.assertEqual(app.selector_buttons[0, LOCK].cget("relief"), "sunken")
        self.assertEqual(app.selector_buttons[0, UN].cget("relief"), "raised")
        self.assertEqual(app.selector_buttons[1, FAKE].cget("relief"), "sunken")
        with open(file.journal, encoding="utf-8") as f:
            journal = f.read()
        self.assertIn("play", journal)
        self.assertIn("phone fake locked: mode 3 from", journal)
        with open(file.state_file, encoding="utf-8") as f:
            self.assertIn("selectors: computer locked, phone fake locked", f.read())

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
