"""The generated script read back from its text (scripts/script_text.py)."""

import os
import sys
import unittest
from fractions import Fraction as F

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "UI"))

from helpers import Interval, IntervalSet, Region  # noqa: E402

from app import FifteenMinuteBreaks, PresenceRatioProblem, Session  # noqa: E402
from intervals import EPS, INF, Pos  # noqa: E402
from line import Op  # noqa: E402
from region import ONE  # noqa: E402
from script import ChipIn, EditAction, Edited, Event, RerunAction, Script, Start, TransitionCount  # noqa: E402
from script_text import ScriptError, _op, parse_script, position, region  # noqa: E402

RULES = ["Break15 → NoScreen", "mode 1 → no NoScreen", "mode 2 → NoScreen"]


def session(length=15) -> Session:
    s = Session(RULES, [(0, "1")], 0, 1, [], [(PresenceRatioProblem({}, horizon=240), 1)], F(1, 2), [FifteenMinuteBreaks(length=length)])
    s.sim.background = False
    s.sim.start()
    return s


class RegionsTest(unittest.TestCase):
    def test_positions(self):
        self.assertEqual(position("19/2-5ε"), Pos(F(19, 2), -5))
        self.assertEqual(position("15+ε"), Pos(15, 1))
        self.assertEqual(position("2ε"), Pos(0, 2))
        self.assertEqual(position("-ε"), Pos(0, -1))
        self.assertEqual(position("+inf"), INF)
        for bad in ("", "a", "1/0", "2/3ε"):
            with self.assertRaises(ValueError):
                position(bad)

    def test_as_they_are_written(self):
        full = Region(0, 0, IntervalSet())
        tail = Region(135, 135, IntervalSet(), ONE, IntervalSet(), Pos(135), IntervalSet.of(Interval.closed_open(0, 15), Interval.closed(20, 30)))
        left = Region(5, 5, IntervalSet(), Pos(10), IntervalSet.of(Interval.closed(1, 2), Interval.closed(4, 5)))
        for r in (full, Region.of(Interval(0, INF, False, False)), Region.of(Interval.point(Pos(20, 1))),
                  Region.of(IntervalSet.of(Interval.open(0, 1), Interval.closed(3, 4))), tail, left, tail | left,
                  tail | Region.of(Interval.closed(1, 2)), Region.universe()):
            self.assertEqual(region(str(r)), r, str(r))

    def test_what_repeats_is_told_from_what_is_before_it(self):
        r = region("[1, 2] U ([100, 101] U [110, 111]) repeating every 135 to the right ...")
        self.assertTrue(r.contains(1) and r.contains(100) and r.contains(110) and r.contains(235) and r.contains(245))
        self.assertFalse(r.contains(136))

    def test_edits_as_they_are_written(self):
        w = Region.of(Interval.closed(1, 2))
        for op in (Op.put("A", w, F(45, 2)), Op.put_no("A", w), Op.take("A", w), Op.take_no("A", w)):
            self.assertEqual(str(_op(str(op))), str(op))


class ParseTest(unittest.TestCase):
    def test_the_same_text_is_the_same_script(self):
        s = session()
        before = s.sim.script
        script = parse_script(s.script_text(), before)
        self.assertEqual(script.text(), before.text())
        self.assertEqual(script.events, before.events)
        self.assertEqual(script.covered, before.covered)
        self.assertEqual(script.live, before.live)
        self.assertEqual(len(script.stretches), len(before.stretches))  # the shortcuts still apply
        self.assertIs(script.constraints[0], before.constraints[0])

    def test_the_text_is_all_there_is(self):
        """Nothing hidden: read with nothing to go from, the text says the same script."""
        s = session()
        text = s.sim.script.text()
        self.assertEqual(parse_script(text, Script()).text(), text)
        self.assertNotIn("compiled", text)

    def test_an_event_of_each_kind(self):
        text = "\n".join(["if the chip is in [1, 5/2) and mode is 2, then put A 50% on [3, 4], take B off (4, +inf).",
                          "if the chip is at or after 3, then run the generator again.",
                          "if 1 mode transition happened, then put no A on {7}; run the generator again.",
                          "if the user edits the line, then take no A off [1, 2].",
                          "if the program starts, then run the generator once, as soon as possible.",
                          "if the chip is at or after 2, then satisfy the constraints.",
                          "if the chip is at or after 2, then put A 100% on [1, 2] repeating every 10 to the right ....",
                          "if the chip is in [3+ε, 4] and mode is 7, then apply the mode rules.",
                          "elsewhere, apply the mode rules."])
        script = parse_script(text, Script())
        self.assertEqual(script.text(), text)
        self.assertEqual(script.events[0].trigger, ChipIn(F(1), F(5, 2), "2"))
        self.assertEqual(script.events[2].trigger, TransitionCount(1))
        self.assertIsInstance(script.events[3].trigger, Edited)
        self.assertIsInstance(script.events[4].trigger, Start)
        self.assertEqual(script.events[1].actions, (RerunAction(),))
        self.assertEqual(script.covered, [(Pos(3, 1), Pos(4), "7")])
        self.assertTrue(script.live)

    def test_deleted_lines_are_gone(self):
        s = session()
        before = s.sim.script
        script = parse_script("\n".join(ln for ln in before.text().splitlines() if "mode rules" not in ln and "run the generator" not in ln), before)
        self.assertEqual(script.stretches, [])
        self.assertEqual(script.covered, [])
        self.assertFalse(script.live)

    def test_nothing_is_an_empty_script(self):
        self.assertEqual(parse_script("Generated script at time 0, chip at 0 (mode 1).\n\n(empty script)\n", Script()).events, [])

    def test_not_correctly_written(self):
        for line in ("nonsense", "if the chip is somewhere, then run the generator again.", "if the chip is at or after 3, then dance.",
                     "if the chip is in [5, 3), then run the generator again.", "if 0 mode transitions happened, then run the generator again.",
                     "if the chip is at or after 3, then put A 101% on [1, 2].", "if the chip is at or after 3, then put A 100% on [2, 1].",
                     "if the chip is at or after 3, then put A 100% on [1, 2] repeating every 1 to the right ....",
                     "if the chip is in [5, 3] and mode is 1, then apply the mode rules.", "elsewhere, dance.",
                     "keep something", "keep Break15 on (chip, chip + 0] with NoScreen, as early as possible, none within 120 after as much NoScreen",
                     "keep Break15 on (chip, chip + 15] with NoScreen, as early as possible, none within 120 after as much Other"):
            with self.assertRaises(ScriptError, msg=line):
                parse_script(line, Script())

    def test_a_kind_the_rules_do_not_know(self):
        s = session()
        with self.assertRaises(ScriptError) as e:
            s.edit_script(s.script_text().replace("15", "16"), s.sim.script)
        self.assertIn("Break16 is not in the rules", str(e.exception))

    def test_every_line_is_told(self):
        with self.assertRaises(ScriptError) as e:
            parse_script("Generated script at time 0, chip at 0 (mode 1).\n\nnonsense\nif the user edits the line, then run the generator again.\nbad", Script())
        self.assertEqual([n for n, _ in e.exception.errors], [3, 5])


class ReplaceTest(unittest.TestCase):
    def test_the_changed_script_replaces_the_previous_one(self):
        s = session()
        before = s.sim.script
        text = s.script_text() + "if the chip is at or after 1, then put A 100% on [50, 60].\n"
        self.assertIn("replaced", s.edit_script(text, before))
        self.assertIsNot(s.sim.script, before)
        self.assertIn("Changed by the user at time 0.", s.script_text())
        s.tick(F(2))
        self.assertTrue(s.sim.state.line.presence("A").contains(55))

    def test_breaks_of_16_minutes(self):
        """The number in the text is the rule: the break the chip drags, the next ones,
        the next runs', and the one the chip is in."""
        def check(s, start):
            b = s.sim.state.line.presence("Break15")
            self.assertTrue(b.contains(start + 8) and b.contains(start + 16), str(b))
            self.assertFalse(b.contains(start - 1) or b.contains(start + 17), str(b))
            self.assertTrue(b.contains(start + 136 + 16) and not b.contains(start + 136 - 1), str(b))  # one every 16 + 120

        s = session()
        s.tick(F(3))
        s.edit_script(s.script_text().replace("chip + 15]", "chip + 16]"), s.sim.script)
        self.assertIn("chip + 16]", s.sim.script.text())
        s.tick(F(1))
        check(s, 4)
        s.tick(F(20))  # through the next runs
        self.assertGreater(len(s.sim.log.runs), 1)
        self.assertIn("chip + 16]", s.sim.script.text())
        check(s, 24)
        s.set_mode("2")
        s.tick(F(1))
        s.tick(F(20))
        self.assertTrue(s.sim.state.line.presence("Break15").contains(40))  # the chip went through it, whole
        self.assertFalse(s.sim.state.line.presence("Break15").contains(41))

    def test_a_break_going_on_grows(self):
        """Mode 2, the chip in a break of 15: with 16 in the text, it lasts 16."""
        s = session()
        s.tick(F(1))
        s.set_mode("2")
        s.tick(F(1))
        s.edit_script(s.script_text().replace("chip + 15]", "chip + 16]"), s.sim.script)
        s.tick(F(1))
        b = s.sim.state.line.presence("Break15")
        self.assertTrue(b.contains(17))
        self.assertFalse(b.contains(18))

    def test_not_correctly_written_nothing_changes(self):
        s = session()
        before = s.sim.script
        with self.assertRaises(ScriptError):
            s.edit_script(s.script_text() + "nonsense\n", before)
        self.assertIs(s.sim.script, before)

    def test_deleted_elsewhere_no_mode_rules_there(self):
        """What is written is what is done: the mode rules apply on [0, 2] only."""
        s = session()
        before = s.sim.script
        text = "\n".join(ln for ln in before.text().splitlines() if "mode rules" not in ln and "run the generator" not in ln)
        s.edit_script(text + "\nif the chip is in [0, 2] and mode is 1, then apply the mode rules.", before)
        s.tick(F(4))
        b = s.sim.state.line.presence("Break15")
        self.assertFalse(b.contains(1))
        self.assertTrue(b.contains(3))  # eaten: not dragged

    def test_what_an_event_did_is_not_done_again(self):
        s = session()
        before = s.sim.script
        s.tick(F(3))
        n = len(s.sim.log.changes)
        s.edit_script(before.text(), before)
        s.tick(F(1))
        self.assertFalse(any(c.source == "script" for c in s.sim.log.changes[n:]))

    def test_the_run_going_on_is_cancelled(self):
        s = session()
        s.sim.background = True
        before = s.sim.script
        s.sim._install(s.sim.t, "script")
        req = s.sim.request
        self.assertIsNotNone(req)
        s.edit_script(before.text(), before)
        self.assertIsNone(s.sim.request)
        self.assertFalse(s.sim.complete(req.id, *req.gen.run(req.state, req.chip, req.known)))
        self.assertEqual(s.sim.script.text(), before.text())

    def test_a_file_older_than_the_script_in_place(self):
        s = session()
        before = s.sim.script
        s.sim._install(s.sim.t, "script")
        self.assertIsNot(s.sim.script, before)
        self.assertIn("newer", s.edit_script(before.text() + "\nif the chip is at or after 1, then put A 100% on [50, 60].", before))
        self.assertIn("[50, 60]", s.sim.script.text())


if __name__ == "__main__":
    unittest.main()
