"""The generated script as the Python that builds it (scripts/script_python.py): what the
window shows, and reads back when the user changes it."""

import os
import runpy
import sys
import unittest
from fractions import Fraction as F
from unittest import mock

from helpers import Interval, Region  # noqa: F401 (the paths of scripts/)

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


class WrittenOutTest(unittest.TestCase):
    """The constraints' edits are written in the script, not called while it runs: the
    breaks of screen_break_15_rules.py, and what the chip does when it is on one in mode 1
    (it pushes it: the breaks again from the chip), which the user can change."""

    def breaks(self, s):
        return [(iv.lo.x, iv.hi.x) for iv in s.sim.state.line.presence("Break15").on(-100, 20000).intervals]

    def test_the_breaks_and_the_push_are_in_the_script(self):
        s = example("screen_break_15_rules")
        text = s.script_text()
        self.assertIn("R1 = cycle(F(0), F(8100), F(0), F(900))", text)
        self.assertIn("EditAction(ops=(Op.put('Break15', R1),))", text)
        self.assertIn("ChipOn(kind='Break15', mode='1')", text)
        self.assertIn("from_chip=True", text)
        self.assertNotIn("ConstraintEditAction()),", text)  # (nothing called while it runs)

    def test_the_chip_pushes_a_break_as_the_hand_written_ones(self):
        out = []
        for name in ("screen_break_15", "screen_break_15_rules"):
            s = example(name)
            s.tick(F(20))  # mode 1: no "no screen" at the chip
            out.append(self.breaks(s)[:3])
        self.assertEqual(out[0], out[1])
        self.assertEqual(out[1][0], (20, 920))

    def test_a_changed_push_moves_it(self):
        """(10, 910] from the chip instead of (0, 900]: a pushed break is 10 further."""
        s = example("screen_break_15_rules")
        text = s.script_text()
        edited = text.replace("cycle(F(0), F(8100), F(0), F(900))", "cycle(F(0), F(8100), F(10), F(910))")
        self.assertNotEqual(edited, text)
        s.edit_script(edited, s.sim.script)
        s.tick(F(20))
        self.assertEqual(self.breaks(s)[0], (30, 930))

    def test_the_user_s_example_a_gap_of_10_kept(self):
        """The user's example, as the user does it: the generator off, then in the generated
        script (chip, chip + 900] replaced by (chip + 10, chip + 910], nothing else: the chip
        in mode 1 pushes the break 10 ahead of it, as it goes."""
        s = example("screen_break_15_rules")
        s.command("generator off")
        text = s.script_text()
        edited = text.replace("Op.put('Break15', R1)), from_chip=True)",
                              "Op.put('Break15', cycle(F(0), F(8100), F(10), F(910)))), from_chip=True)")
        self.assertNotEqual(edited, text)
        s.edit_script(edited, s.sim.script)
        for _ in range(5):
            s.tick(F(5))
            a, b = self.breaks(s)[0]
            self.assertEqual((a - s.position, b - s.position), (10, 910), s.position)


class CompiledPushTest(unittest.TestCase):
    """The chip pushing a break, "if the chip is on Break15 and mode is 1, then from the
    chip, take Break15 off (0, inf), put Break15 on R", is the same text, but the mode
    rules keep it as the chip goes (constraints.ChipPush), compiled ahead: the line is a
    function of the chip's position. No edit and nothing solved at each step of the chip
    (an edit and a solve at each step cost about a second each); and the same line as an
    edit at each instant."""

    def breaks(self, s):
        return [(iv.lo.x, iv.hi.x) for iv in s.sim.state.line.presence("Break15").on(s.position - 100, s.position + 20000).intervals]

    def start(self):
        s = example("screen_break_15_rules")
        s.sim.background = False
        s.tick(F(1, 20))  # (the chip is on the break: the run starts)
        return s

    def test_the_push_is_read_as_one(self):
        pushes = example("screen_break_15_rules").sim.script.pushes()
        self.assertEqual([(k.kind, k.mode, k.within, k.on_kind) for k in pushes.values()],
                         [("Break15", "1", 0, "Break15"),  # the chip in a break in mode 1
                          ("Break15", "2", 0, "NoScreen"), ("Break15", "3", 0, "NoScreen")])  # locked 15 minutes

    def test_no_edit_and_nothing_solved_as_the_chip_pushes(self):
        import mode_rules
        import resolve
        s = self.start()
        n, count = len(s.sim.log.changes), [0]

        def counted(real):
            def f(*a, **k):
                count[0] += 1
                return real(*a, **k)
            return f
        with mock.patch.object(mode_rules, "solve", counted(mode_rules.solve)), \
                mock.patch.object(resolve, "solve", counted(resolve.solve)):
            for _ in range(40):  # two seconds at 20 frames a second
                s.tick(F(1, 20))
                a, b = self.breaks(s)[0]
                self.assertEqual((a - s.position, b - s.position), (0, 900), s.position)
        self.assertEqual(count[0], 0)
        self.assertEqual([c for c in s.sim.log.changes[n:] if c.source == "script"], [])

    def test_nothing_solved_as_mode_2_keeps_the_next_break_2_hours_ahead(self):
        """Locked (mode 2 from 12000), out of the break since 12900: "no screen" grows with
        the chip, so the next break is 2 hours after it at each position, the script's push
        from "no screen" (generator.constraint_events), with nothing solved and no edit of
        the script in the frames (as the window runs it: the runs in their own process)."""
        import mode_rules
        import resolve
        s = example("screen_break_15_rules")
        s.sim.background = False
        s.tick(F(13400) - s.position)
        s.sim.background = True
        n, count = len(s.sim.log.changes), [0]

        def counted(real):
            def f(*a, **k):
                count[0] += 1
                return real(*a, **k)
            return f
        with mock.patch.object(mode_rules, "solve", counted(mode_rules.solve)), \
                mock.patch.object(resolve, "solve", counted(resolve.solve)):
            for _ in range(40):
                s.tick(F(1, 20))
                a, b = self.breaks(s)[0]
                self.assertEqual((a - s.position, b - s.position), (7200, 8100), s.position)
        self.assertEqual(count[0], 0)
        self.assertEqual([c for c in s.sim.log.changes[n:] if c.source == "script"], [])

    def test_nothing_solved_as_the_line_crosses_or_drags_a_20s_break(self):
        """screen_break_20s_rules.py: the line crossing the break of (1220, 1240] in mode 1
        (as in mode 3: the break stays), then reaching and dragging the one of 3660 in mode 2
        (the push "if the chip is on Break20s and mode is 2"): 4 seconds of frames each,
        nothing solved, no edit of the script."""
        import mode_rules
        import resolve
        count = [0]

        def counted(real):
            def f(*a, **k):
                count[0] += 1
                return real(*a, **k)
            return f
        for start, mode, breaks in ((1218, "1", lambda p: [(1220, 1240)]), (3658, "2", lambda p: [(max(p, 3660), max(p, 3660) + 20)])):
            with self.subTest(start=start):
                s = example("screen_break_20s_rules")
                s.sim.background = False
                s.tick(F(start) - s.position)
                s.sim.background = True
                n, count[0] = len(s.sim.log.changes), 0
                with mock.patch.object(mode_rules, "solve", counted(mode_rules.solve)), \
                        mock.patch.object(resolve, "solve", counted(resolve.solve)):
                    for _ in range(80):
                        s.tick(F(1, 20))
                        self.assertEqual(s.sim.modes.mode_at(s.position), mode)
                        got = [(iv.lo.x, iv.hi.x) for iv in s.sim.state.line.presence("Break20s").on(s.position - 30, s.position + 30).intervals]
                        self.assertEqual(got, breaks(s.position), s.position)
                self.assertEqual(count[0], 0)
                self.assertEqual([c for c in s.sim.log.changes[n:] if c.source == "script"], [])

    def test_the_line_meets_a_break_no_run_compiled_with_nothing_to_solve(self):
        """The generator off, the line far past what its last run compiled: the mode rules
        the chip goes through are checked ahead of it in the edit worker's process
        (app.Preparer, mode_rules.prepare_ahead), the run where the chip starts to push the
        break included. The frames around the meeting solve nothing (1.45 s solved in the
        frame without it), and the break is the same as solved in the frame."""
        import time
        import mode_rules
        import resolve
        from app import Preparer
        from intervals import Pos

        def first(s):
            return s.sim.state.line.presence("Break15").on(s.position - 1, s.position + 20000).intervals[0]

        def near_the_meeting():
            s = example("screen_break_15_rules")
            s.sim.background = False
            s.tick(F(14500))
            s.command("generator off")
            while first(s).lo.x - s.position > 30:
                s.tick(first(s).lo.x - s.position - 30)
            return s, first(s).lo.x
        s, meet = near_the_meeting()
        alone, _ = near_the_meeting()  # (the same, solved in the frames)
        s.sim.ahead_jobs = Preparer()
        try:
            s.tick(F(1, 20))
            mr = s.sim._live_rules["1"]
            waited = time.monotonic()  # (the process starts, then answers: the chip waits here, a window would not)
            while mr.frontier() <= Pos(meet) and time.monotonic() - waited < 60:
                time.sleep(0.05)
                s.sim._ahead_of_chip()  # (an answer taken, what follows asked for)
            self.assertGreater(mr.frontier(), Pos(meet))  # known past the meeting
            s.tick(meet - s.position - 2)
            alone.tick(meet - alone.position - 2)
            count = [0]

            def counted(real):
                def f(*a, **k):
                    count[0] += 1
                    return real(*a, **k)
                return f
            with mock.patch.object(mode_rules, "solve", counted(mode_rules.solve)), \
                    mock.patch.object(resolve, "solve", counted(resolve.solve)):
                for _ in range(80):
                    s.tick(F(1, 20))
                    iv = first(s)
                    if s.position > meet:
                        self.assertEqual((iv.lo.x, iv.hi.x), (s.position, s.position + 900), s.position)
            self.assertEqual(count[0], 0)
            for _ in range(80):
                alone.tick(F(1, 20))
            self.assertEqual(s.sim.state.line.presence("Break15").on(0, 40000),
                             alone.sim.state.line.presence("Break15").on(0, 40000))
            self.assertEqual(s.sim.log.refused, alone.sim.log.refused)
        finally:
            s.sim.ahead_jobs.close()

    def test_the_line_meets_a_break_with_nothing_to_solve(self):
        """Back at the screen after a lock (mode 2, then mode 1 at 14400), the line meets the
        next break while working, as the window runs it (the generator's runs in their own
        process, `background`): the run the generator compiled ahead takes the break from
        the first frame inside it, with nothing solved in the frames around it. (A rerun
        falls due at that same instant, 21600: a request, not a calculation of the frame.)"""
        import mode_rules
        import resolve
        s = example("screen_break_15_rules")
        s.sim.background = False

        def first():
            return s.sim.state.line.presence("Break15").on(s.position - 1, s.position + 20000).intervals[0]
        s.tick(F(14500))
        while first().lo.x - s.position > 2:  # (the runs during the jump can move it)
            s.tick(first().lo.x - s.position - 2)
        meet = first().lo.x
        s.sim.background = True
        count = [0]

        def counted(real):
            def f(*a, **k):
                count[0] += 1
                return real(*a, **k)
            return f
        with mock.patch.object(mode_rules, "solve", counted(mode_rules.solve)), \
                mock.patch.object(resolve, "solve", counted(resolve.solve)):
            for _ in range(80):  # 4 s at 20 frames a second, the meeting in the middle
                s.tick(F(1, 20))
                iv = first()
                if s.position > meet:
                    self.assertEqual((iv.lo.x, iv.hi.x), (s.position, s.position + 900), s.position)
                else:
                    self.assertEqual(iv.lo.x, meet, s.position)
        self.assertEqual(count[0], 0)
        self.assertEqual(s.sim.log.refused, [])

    def test_nothing_solved_as_the_chip_pushes_a_break_10_ahead(self):
        """(10, 910] from the chip: pushed from the position before, the break starts right
        at chip + 10, which is still "less than 10 after" the chip: one run, not a run (and
        a solve) at each position."""
        import mode_rules
        s = example("screen_break_15_rules")
        s.command("generator off")
        text = s.script_text()
        s.edit_script(text.replace("Op.put('Break15', R1)), from_chip=True)",
                                   "Op.put('Break15', cycle(F(0), F(8100), F(10), F(910)))), from_chip=True)"), s.sim.script)
        s.sim.background = False
        s.tick(F(1, 20))
        count = [0]
        real = mode_rules.solve

        def counted(*a, **k):
            count[0] += 1
            return real(*a, **k)
        with mock.patch.object(mode_rules, "solve", counted):
            for _ in range(40):
                s.tick(F(1, 20))
                a, b = self.breaks(s)[0]
                self.assertEqual((a - s.position, b - s.position), (10, 910), s.position)
        self.assertEqual(count[0], 0)

    def test_a_jump_lands_where_the_steps_land(self):
        """The program put to sleep: the chip 50 further at once, as if it went step by step."""
        stepped, jumped = self.start(), self.start()
        for _ in range(100):
            stepped.tick(F(1, 2))
        jumped.tick(F(50))
        self.assertEqual(stepped.position, jumped.position)
        self.assertEqual(self.breaks(stepped)[:3], self.breaks(jumped)[:3])
        self.assertEqual(self.breaks(jumped)[0], (jumped.position, jumped.position + 900))
        self.assertEqual(jumped.sim.log.refused, [])

    def test_another_edit_from_the_chip_is_an_edit_at_each_instant(self):
        """Only "K is R after the chip" is read as a push: anything else in the event
        (another kind, a value under 100, a take that leaves some of K after the chip) and
        it fires at each instant, as written."""
        from constraints import push_of
        from intervals import INF
        from line import Op
        from script import ChipOn, EditAction, Event
        R = Region.of(Interval.open_closed(0, 900))
        after = Region.of(Interval(0, INF, False, False))

        def ev(*ops):
            return Event(ChipOn("Break15", "1"), (EditAction(ops, from_chip=True),))
        self.assertIsNotNone(push_of(ev(Op.take("Break15", after), Op.put("Break15", R))))
        self.assertIsNone(push_of(ev(Op.take("Break15", after), Op.put("Break15", R, 50))))
        self.assertIsNone(push_of(ev(Op.take("Break15", Region.of(Interval.open_closed(0, 10))), Op.put("Break15", R))))
        self.assertIsNone(push_of(ev(Op.take("Break15", after), Op.put("Break5", R))))
        self.assertIsNone(push_of(Event(ChipOn("Break15", "1"), (EditAction((Op.take("Break15", after), Op.put("Break15", R))),))))

    def test_a_push_once_the_trigger_has_lasted(self):
        """"If the chip has been on NoScreen for 900 and is not on Break15, and mode is 2,
        then from the chip, take Break15 off (0, inf), put it on (7200, 8100]": a push of
        Break15 read from another kind, only with `lasted` (else that kind at the chip would
        push K at once). Its trigger reads what is behind the chip, and not inside K."""
        from constraints import push_of
        from intervals import INF
        from intervals import Interval as Iv2
        from line import LineState, Op
        from rules import parse_rules
        from resolve import apply_edit
        from script import ChipOn, EditAction, Event
        R = Region.of(Interval.open_closed(7200, 8100))
        after = Region.of(Interval(0, INF, False, False))
        ops = (Op.take("Break15", after), Op.put("Break15", R))
        trig = ChipOn("NoScreen", "2", lasted=900, unless="Break15")
        push = push_of(Event(trig, (EditAction(ops, from_chip=True),)))
        self.assertEqual((push.kind, push.on_kind, push.lasted, push.unless, push.within), ("Break15", "NoScreen", 900, "Break15", 0))
        self.assertIsNone(push_of(Event(ChipOn("NoScreen", "2"), (EditAction(ops, from_chip=True),))))
        rs = parse_rules(["Break15 → NoScreen"])
        s = apply_edit(rs, LineState.empty(), [Op.put("NoScreen", Iv2(1000, 2000))], "user")
        s = apply_edit(rs, s, [Op.put("Break15", Iv2(1940, 1970, False, True))], "generated")
        for c, on in ((1899, False), (1900, True), (1960, False), (2000, True)):  # (in the break at 1960)
            with self.subTest(chip=c):
                self.assertEqual(trig.holds(s, F(c), "2"), on)
                self.assertEqual(push.on(s, F(c)), on)
        self.assertFalse(trig.holds(s, F(1900), "1"))
        self.assertEqual(str(trig), "the chip has been on NoScreen for 900 and is not on Break15 and mode is 2")
        from script_python import py
        self.assertEqual(py(trig), "ChipOn(kind='NoScreen', mode='2', lasted=900, unless='Break15')")


class GeneratorOffTest(unittest.TestCase):
    """The Generator button / `generator off`: no run, whatever asks for it; on: one run."""

    def test_switched_off_nothing_runs_it(self):
        from intervals import Interval
        from line import Op
        from region import Region
        s = Session(RULES, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2),
                    [Breaks(length=15)])
        s.sim.background = True
        s.begin()
        self.assertIsNotNone(s.sim.request)
        self.assertIn("switched off", s.command("generator off"))
        self.assertIsNone(s.sim.request)  # (the run going on: dropped)
        before = s.sim.script
        s.sim.edit_now([Op.put("NoScreen", Region.of(Interval.closed(50, 60)))])  # its edit asks for a run
        self.assertIsNone(s.sim.request)
        self.assertIs(s.sim.script, before)  # the script in place goes on as it is
        self.assertTrue(any("switched off" in str(r) for r in s.sim.log.refused))
        self.assertIn("switched on", s.command("generator on"))
        self.assertIsNotNone(s.sim.request)  # once, at once
        self.assertEqual(s.sim.request.why, "generator on")
        self.assertIn("usage", s.command("generator maybe"))
        s.close()


class ScriptFileTest(unittest.TestCase):
    """The generated script is one file from the start of the program (script_file.py): at
    the start, only an instruction to run the generator once."""

    def setUp(self):
        import tempfile
        from script_file import ScriptFile
        self.dir = tempfile.mkdtemp()
        self.file = ScriptFile("example", self.dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def text(self):
        with open(self.file.path, encoding="utf-8") as f:
            return f.read()

    def test_at_the_start_it_runs_the_generator_once(self):
        s = Session(RULES, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}).with_window(240), 1)], F(1, 2),
                    [Breaks(length=15)])
        self.assertTrue(self.file.write(s.sim.script, s.script_text()))  # (before the generator runs)
        text = self.text()
        self.assertIn("Event(trigger=Start(), actions=(RerunAction(first=True),))", text)
        self.assertEqual(from_python(text, s.sim.script).events, s.sim.script.events)  # only that
        self.assertEqual(len(s.sim.script.events), 1)

    def test_written_again_for_another_script_only(self):
        s = session()
        a = s.sim.script
        self.assertTrue(self.file.write(a, s.script_text()))
        self.assertFalse(self.file.write(a, "not written"))  # the same script: as it is
        self.assertNotIn("not written", self.text())
        self.assertIsNone(self.file.saved())  # what the program wrote is no edit

    def test_a_save_of_the_user_is_read_once(self):
        s = session()
        self.file.write(s.sim.script, s.script_text())
        with open(self.file.path, "w", encoding="utf-8") as f:
            f.write(s.script_text().replace("length=F(15)", "length=F(16)"))
        text = self.file.saved()
        self.assertIn("length=F(16)", text)
        self.assertIsNone(self.file.saved())  # read: not again
        self.assertIn("replaced", s.edit_script(text, self.file.script))


class EditorTest(unittest.TestCase):
    def test_the_file_opens_in_vs_code_when_it_is_there(self):
        try:
            import gui
        except ImportError:
            self.skipTest("no tkinter")
        with mock.patch.object(gui.shutil, "which", return_value="C:/VS Code/bin/code.CMD"),                 mock.patch.object(gui.subprocess, "Popen") as popen:
            gui.open_in_editor("C:/tmp/script.py")
        self.assertEqual(popen.call_args[0][0], ["C:/VS Code/bin/code.CMD", gui.PROJECT, "C:/tmp/script.py"])  # (its settings)
        with mock.patch.object(gui.shutil, "which", return_value=None), mock.patch.object(gui.subprocess, "Popen") as popen,                 mock.patch.object(gui.os, "startfile", create=True) as startfile:
            gui.open_in_editor("C:/tmp/script.py")
        self.assertTrue(startfile.called or popen.called)  # (the system's editor)


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
