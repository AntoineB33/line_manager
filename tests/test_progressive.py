"""A run of the generator is progressive (requirement.md): scripts published one after the
other, definitive up to a position that moves on at the pace."""

import os
import sys
import unittest
from fractions import Fraction as F

sys.path.insert(0, os.path.dirname(__file__))

from helpers import Interval, Region  # noqa: E402

from constraints import FifteenMinuteBreaks  # noqa: E402
from generator import PACE, RulesGenerator  # noqa: E402
from line import LineState, Op  # noqa: E402
from modes import ModeTimeline  # noqa: E402
from problems import PresenceRatioProblem  # noqa: E402
from rules import parse_rules  # noqa: E402
from script import EditAction  # noqa: E402
from simulation import Chip, Simulation  # noqa: E402

RULES = ["mode 1 → D"]


def gen(q=F(1, 2), H=60, rules=RULES, **k):
    return RulesGenerator(parse_rules(rules), PresenceRatioProblem({"A": F(1, 4)}, H), q, **k)


def stages(g, c=0):
    return list(g.stages(LineState.empty(), F(c), ModeTimeline(c, "1").known(c)))


def edits(script):
    return [o for e in script.events for a in e.actions if isinstance(a, EditAction) for o in a.ops]


class StagesTest(unittest.TestCase):
    def test_a_short_horizon_is_one_stage(self):
        out = stages(gen(H=10))
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0][1].final)
        self.assertIsNone(out[0][1].definitive)

    def test_the_lower_q_the_shorter_the_run(self):
        counts = [len(stages(gen(q=q, H=120))) for q in (F(1, 10), F(1, 2), F(9, 10))]
        self.assertEqual(counts, sorted(counts))
        self.assertLess(counts[0], counts[-1])

    def test_the_pace(self):
        for q in (F(1, 10), F(1, 2), F(9, 10)):
            xs = [st.definitive for _, st in stages(gen(q=q, H=120), 7)]
            self.assertEqual(xs[0], 17)  # the next 10 seconds of the chip, at its speed
            self.assertEqual(xs[-1], 127)  # the horizon
            for a, b in zip(xs, xs[1:]):
                self.assertGreaterEqual(b - a, PACE)

    def test_definitive(self):
        """Every next script gives the same instructions before the frontier, whatever
        the modes: its lines stay, and what it adds is after the frontier."""
        out = stages(gen(q=F(9, 10), H=120))
        self.assertGreater(len(out), 2)
        self.assertTrue(any(edits(s) for s, _ in out))
        for (a, st), (b, _) in zip(out, out[1:]):
            lines = b.text().splitlines()
            self.assertTrue(all(ln in lines for ln in a.text().splitlines()), st.definitive)
            before = Region.of(Interval(-10 ** 6, st.definitive, True, True))
            for o in edits(b):
                if str(o) not in map(str, edits(a)):
                    self.assertTrue((o.region & before).is_empty(), str(o))

    def test_only_the_last_is_final(self):
        out = stages(gen(H=120))
        self.assertEqual([st.final for _, st in out], [False] * (len(out) - 1) + [True])
        self.assertTrue(all(st.work <= st.budget for _, st in out))

    def test_run_is_the_last_stage(self):
        g = gen(H=120)
        self.assertEqual(g.run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))[0].text(), stages(g)[-1][0].text())

    def test_the_shortcut_comes_last(self):
        out = stages(gen(H=120))
        self.assertTrue(all(not s.stretches for s, _ in out[:-1]))
        self.assertTrue(out[-1][0].stretches)


class PublishTest(unittest.TestCase):
    def simulation(self, **k):
        g = gen(q=F(9, 10), H=120, **k)
        chip = Chip([(0, 0)])
        sim = Simulation(LineState.empty(), g, chip, ModeTimeline(0, "1"))
        sim.background = True
        g.paced = True
        sim.start()
        return sim, chip

    def run_of(self, sim):
        r = sim.request
        return r, list(r.gen.stages(r.state, r.chip, r.known, r.previous))

    def go(self, sim, chip, t):
        chip.extend(t, t)
        sim.advance(t)

    def test_published_as_they_come(self):
        sim, chip = self.simulation()
        req, out = self.run_of(sim)
        for script, stats in out[:-1]:
            self.assertTrue(sim.publish(req.id, script, stats))
            self.assertIs(sim.request, req)  # the run goes on
            self.assertEqual(sim.definitive, stats.definitive)
            self.assertEqual(len(sim.log.runs), 0)
        self.assertTrue(sim.complete(req.id, *out[-1]))
        self.assertIsNone(sim.request)
        self.assertEqual(len(sim.log.runs), 1)

    def test_a_cancelled_run_publishes_nothing(self):
        sim, chip = self.simulation()
        old, out = self.run_of(sim)
        sim._install(sim.t, "script")
        self.assertIsNot(sim.request, old)
        before = sim.script
        self.assertFalse(sim.publish(old.id, *out[0]))
        self.assertIs(sim.script, before)

    def test_what_a_stage_did_is_not_done_again(self):
        sim, chip = self.simulation()
        req, out = self.run_of(sim)
        self.assertGreater(len([s for s, _ in out if edits(s)]), 1)
        for script, stats in out[:-1]:
            sim.publish(req.id, script, stats)
        self.go(sim, chip, 1)
        n = sum(1 for ch in sim.log.changes if ch.source == "script")
        self.assertTrue(sim.complete(req.id, *out[-1]))
        self.assertLessEqual(sum(1 for ch in sim.log.changes if ch.source == "script") - n, 1)
        self.assertEqual(sum(1 for r in sim.log.refused if r[1] == "script"), 0)

    def test_the_script_at_the_start_needs_no_run(self):
        """It is there at the instant of the call, with nothing from the run."""
        sim, chip = self.simulation(rules=["Break15 → NoScreen", "mode 1 → no NoScreen"], constraints=[FifteenMinuteBreaks()])
        self.assertIsNotNone(sim.request)
        self.assertEqual(sim.definitive, 10)
        text = sim.script.text()
        self.assertIn("keep Break15", text)
        self.assertIn("if the chip is in [0, 10], then apply the mode rules.", text)
        self.assertNotIn("the program starts", text)
        self.assertTrue(sim.state.line.presence("Break15").contains(5))  # the constraint holds at once

    def test_the_run_that_never_answers(self):
        """The pace and the constraints hold, and the run ends, with nothing from the run."""
        sim, chip = self.simulation(rules=["Break15 → NoScreen", "mode 1 → no NoScreen"], constraints=[FifteenMinuteBreaks()])
        req = sim.request
        seen = []
        for t in range(1, 125):
            self.go(sim, chip, t)
            if sim.request is req:
                seen.append(sim.definitive)
                self.assertGreaterEqual(sim.definitive, 10 + (t // 10) * PACE)
        self.assertIsNot(sim.request, req)
        self.assertEqual(sim.log.runs[0][4].chosen, "the script in place, as a last resort")
        b = sim.state.line.presence("Break15")
        self.assertTrue(b.contains(130) and not b.contains(120))  # dragged all along

    def test_a_late_script_counts_after_the_frontier(self):
        sim, chip = self.simulation()
        req, out = self.run_of(sim)
        self.go(sim, chip, 25)
        self.assertEqual(sim.definitive, 30)
        script = out[0][0]
        self.assertTrue(edits(script))
        sim.publish(req.id, script, out[0][1])
        self.assertGreaterEqual(sim.definitive, 30)  # (its triggers may have asked for a new run at once)
        before = Region.of(Interval(-10 ** 6, 30, True, True))
        for o in edits(sim.script):
            self.assertTrue((o.region & before).is_empty(), str(o))

    def test_nothing_before_the_frontier_changes_after_the_first_script(self):
        sim, chip = self.simulation()
        first = sim.script
        req, out = self.run_of(sim)
        before = Region.of(Interval(-10 ** 6, 10, True, True))
        for script, stats in out:
            for o in edits(script):
                self.assertTrue((o.region & before).is_empty(), str(o))
            self.assertIn("if the chip is in [0, 10], then apply the mode rules.", script.text())


if __name__ == "__main__":
    unittest.main()
