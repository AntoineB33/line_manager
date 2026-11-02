"""A run of the generator is progressive (requirement.md): scripts published one after the
other, definitive up to a position that moves on at the pace."""

import itertools
import os
import sys
import unittest
from fractions import Fraction as F
from unittest import mock

sys.path.insert(0, os.path.dirname(__file__))

from helpers import Interval, Region  # noqa: E402

from constraints import FifteenMinuteBreaks  # noqa: E402
from generator import PACE, RulesGenerator  # noqa: E402
from intervals import Pos  # noqa: E402
from line import LineState, Op  # noqa: E402
from mode_rules import ModeRules  # noqa: E402
from modes import ModeTimeline  # noqa: E402
from problems import PresenceRatioProblem  # noqa: E402
from rules import parse_rules  # noqa: E402
from script import ChipIn, EditAction, RerunAction  # noqa: E402
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

    def test_the_bounds_of_q(self):
        """q is in [0, 1], and q = 0 is the lowest of them, not a run that stops early."""
        for q in (0, 1, 0.0, 1.0, F(0), F(1)):
            self.assertEqual(stages(gen(q=q, H=120))[-1][1].definitive, 120)
        self.assertLess(len(stages(gen(q=0, H=120))), len(stages(gen(q=1, H=120))))
        for q in (-0.1, 1.1):
            with self.assertRaises(ValueError):
                gen(q=q)

    def test_the_bounds_of_q_in_a_process_of_its_own(self):
        for q in (0, 1):
            g = gen(q=q, H=120)
            g.paced = True
            self.assertEqual(stages(g)[-1][1].definitive, 120)

    def test_a_lower_q_never_asks_for_its_next_run_sooner(self):
        """Whatever a stage takes: the longer runs of a higher q do not put theirs further."""
        def next_run(q, seconds):
            ticks = itertools.count(0, seconds)
            with mock.patch("generator.time.perf_counter", lambda: float(next(ticks))):
                g = gen(q=q, H=1440)
                g.paced = True
                script, _ = next(g.stages(LineState.empty(), F(0), ModeTimeline(0, "1").known(0)))
            return next(e.trigger.lo for e in script.events if isinstance(e.trigger, ChipIn) and isinstance(e.actions[0], RerunAction))
        for seconds in (0, 1, 5):
            at = [next_run(q, seconds) for q in (0, F(1, 10), F(1, 2), F(9, 10), 1)]
            self.assertEqual(at, sorted(at, reverse=True), seconds)


class ReachingBackTest(unittest.TestCase):
    def test_a_rule_that_reaches_before_the_frontier(self):
        """A stage cannot place what a rule brings back before the last frontier."""
        rs = parse_rules(["A → B 30 units on the left of p"])
        g = RulesGenerator(rs, PresenceRatioProblem({"A": F(1, 4), "B": F(1, 4)}, 120), F(9, 10))
        out = list(g.stages(LineState.empty(), F(0), ModeTimeline(0, "1").known(0)))
        for (a, st), (b, _) in zip(out, out[1:]):
            self.assertTrue(a.base.line.same_on(b.base.line, Region.of(Interval(-10 ** 6, st.definitive, True, True))), st.definitive)


    def test_what_the_mode_rules_reach_after_the_frontier(self):
        """D at the chip forbids A 5 further: a later stage cannot place A within 5 of the
        frontier, or D before it would depend on that stage."""
        rs = parse_rules(["mode 1 → D", "D → no A 5 units on the right"])
        g = RulesGenerator(rs, PresenceRatioProblem({"A": F(1, 2)}, 60), F(9, 10))
        self.assertEqual(g.margin(), 5)
        out = list(g.stages(LineState.empty(), F(0), ModeTimeline(0, "2").known(0)))

        def through(s, hi):
            for ev in ModeRules(rs, "1").act(s, Pos(F(0)), Pos(F(hi))):
                if not isinstance(ev, tuple):
                    s = ev.after
            return s
        self.assertGreater(len(out), 2)
        for (a, st), (b, _) in zip(out, out[1:]):
            x = st.definitive - 1
            self.assertEqual(through(a.base, x).line.presence("D"), through(b.base, x).line.presence("D"), x)

    def test_no_bound_on_what_the_mode_rules_reach(self):
        rs = parse_rules(["mode 1 → D", "D → D 3 units on the right"])
        self.assertIsNone(RulesGenerator(rs, PresenceRatioProblem({}, 60), F(1, 2)).margin())


class EndlessTest(unittest.TestCase):
    def endless(self, rules, problem):
        g = RulesGenerator(parse_rules(rules), problem, 1)
        g.endless = True
        with mock.patch("generator.SEARCH_TIME", F(1, 100)):
            out = list(itertools.islice(g.stages(LineState.empty(), F(0), ModeTimeline(0, "1").known(0)), 40))
        return out, len(out) < 40

    def test_a_run_that_cannot_prove_it_is_done_goes_on(self):
        out, ended = self.endless(RULES, PresenceRatioProblem({"A": F(1, 4)}, 60))
        self.assertFalse(ended)
        self.assertFalse(any(st.final for _, st in out))
        xs = [st.definitive for _, st in out]
        self.assertEqual(xs, sorted(xs))
        self.assertGreater(xs[-1], 60)  # past the horizon, at the pace

    def test_a_run_that_needs_no_other_ends(self):
        out, ended = self.endless(["A → B"], PresenceRatioProblem({}, 5))
        self.assertTrue(ended)
        self.assertTrue(out[-1][1].final)


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

    def test_a_position_trigger_cancels_the_run_going_on(self):
        sim, chip = self.simulation()
        req, out = self.run_of(sim)
        sim.publish(req.id, *out[0])
        at = next(e.trigger.lo for e in sim.script.events if isinstance(e.trigger, ChipIn) and isinstance(e.actions[0], RerunAction))
        self.go(sim, chip, at + 1)
        self.assertIsNot(sim.request, req)
        self.assertEqual(sim.request.chip, at)
        self.assertFalse(sim.publish(req.id, *out[1]))

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
        self.assertGreaterEqual(sim.definitive, 30)
        frontier = sim.definitive
        script = out[0][0]
        self.assertTrue(edits(script))
        before = Region.of(Interval(-10 ** 6, frontier, True, True))
        for o in edits(sim._fit(script)):
            if str(o) not in map(str, edits(sim.script)):
                self.assertTrue((o.region & before).is_empty(), str(o))

    def test_nothing_before_the_frontier_changes_after_the_first_script(self):
        sim, chip = self.simulation()
        first = sim.script
        req, out = self.run_of(sim)
        before = Region.of(Interval(-10 ** 6, 10, True, True))
        for script, stats in out:
            for o in edits(script):
                if str(o) not in map(str, edits(first)):
                    self.assertTrue((o.region & before).is_empty(), str(o))
            self.assertIn("if the chip is in [0, 10], then apply the mode rules.", script.text())


if __name__ == "__main__":
    unittest.main()
