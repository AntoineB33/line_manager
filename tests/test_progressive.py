"""A run of the generator is progressive (requirement.md): scripts published one after the
other, definitive up to a position that moves on at the pace."""

import os
import sys
import unittest
from fractions import Fraction as F
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from helpers import Interval, Region  # noqa: E402

import generator  # noqa: E402
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

    def test_the_speed_of_the_chip(self):
        g = gen(H=120)
        g.speed = F(3)
        self.assertEqual(stages(g)[0][1].definitive, 30)

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

    def test_out_of_time_it_publishes_what_it_has(self):
        """An approximation: a lower score, the constraints as they are."""
        g = gen(H=120, rules=["Break15 → NoScreen", "mode 1 → no NoScreen"], constraints=[FifteenMinuteBreaks()])
        g.paced = True
        clock = iter(range(0, 10 ** 6, 11))
        with mock.patch.object(generator.time, "monotonic", lambda: next(clock)):
            out = stages(g)
        self.assertEqual(len(out), len(stages(gen(H=120))))
        self.assertTrue(all(not edits(s) for s, _ in out))
        self.assertTrue(all(any("keep Break15" in ln for ln in s.text().splitlines()) for s, _ in out))


class PublishTest(unittest.TestCase):
    def simulation(self):
        g = gen(q=F(9, 10), H=120)
        chip = Chip([(0, 0)])
        sim = Simulation(LineState.empty(), g, chip, ModeTimeline(0, "1"))
        sim.background = True
        sim.start()
        return sim, chip

    def test_published_as_they_come(self):
        sim, chip = self.simulation()
        req = sim.request
        out = list(req.gen.stages(req.state, req.chip, req.known))
        for k, (script, stats) in enumerate(out[:-1]):
            self.assertTrue(sim.publish(req.id, script, stats))
            self.assertIs(sim.script, script)
            self.assertIs(sim.request, req)  # the run goes on
            self.assertEqual(sim.definitive, stats.definitive)
            self.assertEqual(len(sim.log.runs), 0)
        self.assertTrue(sim.complete(req.id, *out[-1]))
        self.assertIsNone(sim.request)
        self.assertEqual(len(sim.log.runs), 1)

    def test_a_cancelled_run_publishes_nothing(self):
        sim, chip = self.simulation()
        old = sim.request
        out = list(old.gen.stages(old.state, old.chip, old.known))
        sim._install(sim.t, "script")
        self.assertIsNot(sim.request, old)
        before = sim.script
        self.assertFalse(sim.publish(old.id, *out[0]))
        self.assertIs(sim.script, before)

    def test_what_a_stage_did_is_not_done_again(self):
        sim, chip = self.simulation()
        req = sim.request
        out = list(req.gen.stages(req.state, req.chip, req.known))
        made = [s for s, _ in out if edits(s)]
        self.assertGreater(len(made), 1)
        for script, stats in out[:-1]:
            sim.publish(req.id, script, stats)
        chip.extend(1, 1)
        sim.advance(1)
        n = sum(1 for ch in sim.log.changes if ch.source == "script")
        self.assertTrue(sim.complete(req.id, *out[-1]))
        added = len([e for e in out[-1][0].events if str(e) not in map(str, out[-2][0].events)])
        self.assertLessEqual(sum(1 for ch in sim.log.changes if ch.source == "script") - n, added)
        self.assertEqual(sum(1 for r in sim.log.refused if r[1] == "script"), 0)


if __name__ == "__main__":
    unittest.main()
