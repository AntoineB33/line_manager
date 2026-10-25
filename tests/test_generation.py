"""The rules generator, its scripts, the hyperparameter and the probability finder
(requirement.md, second part), checked on exact simulations."""

import random
import unittest
from fractions import Fraction as F

from helpers import check_log, check_state, rand_interval
from generator import PresenceRatioProblem, RulesGenerator, budget, rerun_distance, rerun_transitions
from guards import mode_region
from intervals import INF, Interval, IntervalSet
from line import USER, LineState
from modes import ModeTimeline
from probability import ProbabilityFinder
from rules import parse_rule
from script import ChipIn, Event, FollowAction, RerunAction, Script, SetAction, TransitionCount
from simulation import Chip, Simulation, UserEdit

RULE_POOL = ["A → B", "A → no C", "A & B → C 3 units on the left", "mode 1 → D & E", "A → A at -1",
             "mode 2 → no D", "C → B at 1", "mode 1 → E on [1, 2]", "B → no A on (-1, 1]", "mode 2 → F 50% at 2"]


def random_world(rng, user_content=True):
    rules = [parse_rule(t) for t in rng.sample(RULE_POOL, rng.randint(1, 4))]
    targets = {t: F(rng.randint(0, 4), 4) for t in rng.sample("ABCDEF", rng.randint(1, 3))}
    problem = PresenceRatioProblem(targets, F(rng.choice([4, 6, 10])))
    pos, trans, mode = F(0), [], "1"
    for _ in range(rng.randint(0, 12)):
        pos += F(rng.randint(1, 8), 2)
        mode = rng.choice([m for m in "123" if m != mode])
        trans.append((pos, mode))
    modes = ModeTimeline(0, "1", trans)
    pts, t, x = [(0, 0)], F(0), F(0)
    for _ in range(rng.randint(1, 6)):
        t += F(rng.randint(1, 6))
        x += F(rng.choice([0, 1, 2, 5]))  # the chip may stop
        pts.append((t, x))
    edits = []
    if user_content:
        edits.append(UserEdit(F(0), rng.choice("ABC"), IntervalSet.of(Interval.closed(1, 3)), F(100)))
    for _ in range(rng.randint(0, 4)):
        edits.append(UserEdit(F(rng.randint(1, 20)), rng.choice("ABCDE"), IntervalSet.of(rand_interval(rng, -2, 30)),
                              F(rng.choice([0, 50, 100]))))
    return rules, problem, Chip(pts), modes, edits


class Example1Test(unittest.TestCase):
    """A → A at -1, chip at 0: the text of rules is empty; the user can add A anywhere."""

    def test_empty_script(self):
        rules = [parse_rule("A → A at -1")]
        known = ModeTimeline(0, "1").known(0)
        for targets in ({}, {"A": F(1, 2)}, {"A": F(1)}):
            for q in (0.01, 0.5, 0.99):
                script, _ = RulesGenerator(rules, PresenceRatioProblem(targets, 10), q).run(LineState(), 0, known)
                self.assertEqual(script.text(), "")

    def test_user_can_add_a_anywhere(self):
        rules = [parse_rule("A → A at -1")]
        gen = RulesGenerator(rules, PresenceRatioProblem({"A": F(1, 2)}, 10), 0.5)
        edits = [UserEdit(F(2), "A", IntervalSet.of(Interval.closed(-5, -4)), F(100)),  # left of the chip
                 UserEdit(F(3), "A", IntervalSet.of(Interval.closed(20, 21)), F(30))]
        modes = ModeTimeline(0, "1")
        sim = Simulation(LineState(), gen, Chip([(0, 0), (40, 40)]), modes, edits)
        log = sim.run(40)
        self.assertEqual(sim.state.value_at("A", F(-9, 2)), 100)
        self.assertEqual(sim.state.value_at("A", F(20)), 30)
        check_log(log, rules, modes)
        # the generator never adds A: each A needs another A 1 unit on its left, down to the chip
        self.assertTrue(all(p.owner == USER for p in sim.state.pieces("A")))


class GuaranteesTest(unittest.TestCase):
    """N1: a script never changes (-inf, chip]; N2: never removes or changes the user's
    elements; N3: never adds a rule violation, for any future of the modes."""

    def test_random_simulations(self):
        rng = random.Random(11)
        script_changes = 0
        for _ in range(40):
            rules, problem, chip, modes, edits = random_world(rng)
            gen = RulesGenerator(rules, problem, rng.choice([0.05, 0.3, 0.7, 0.95]), rng.random() < 0.7)
            sim = Simulation(LineState(), gen, chip, modes, edits)
            log = sim.run(chip.points[-1][0] + 5)
            script_changes += check_log(log, rules, modes)
            check_state(sim.state)
        self.assertGreater(script_changes, 40)  # the scripts did edit the line

    def test_follow_is_exact(self):
        rules = [parse_rule("mode 1 → D at 2")]
        modes = ModeTimeline(0, "1", [(5, "2"), (7, "1"), (12, "2")])
        gen = RulesGenerator(rules, PresenceRatioProblem({}, 10), 0.5)
        sim = Simulation(LineState(), gen, Chip([(0, 0), (40, 40)]), modes)
        log = sim.run(40)
        # mode 1 on [0, 5) and [7, 12): D exactly on [2, 7) and [9, 14)
        self.assertEqual(sim.state.support("D"), IntervalSet.of(Interval.closed_open(2, 7), Interval.closed_open(9, 14)))
        check_log(log, rules, modes)
        self.assertIn("while the chip is in [0, 10) and mode is 1, then keep D at 100% on chip + {2}.", log.runs[0][3].text())

    def test_chip_with_stops(self):
        rules = [parse_rule("mode 1 → D on [1, 2]")]
        modes = ModeTimeline(0, "1", [(3, "2")])
        gen = RulesGenerator(rules, PresenceRatioProblem({}, 10), 0.5)
        sim = Simulation(LineState(), gen, Chip([(0, 0), (2, 1), (5, 1), (7, 3), (9, 6)]), modes)
        log = sim.run(12)
        self.assertEqual(sim.state.support("D"), IntervalSet.of(Interval.closed_open(1, 5)))
        check_log(log, rules, modes)


class HyperparameterTest(unittest.TestCase):
    QS = [0.001, 0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95, 0.999]

    def test_schedules(self):
        H = F(10)
        for q1, q2 in zip(self.QS, self.QS[1:]):
            self.assertLessEqual(budget(q1), budget(q2))
            self.assertGreater(rerun_distance(q1, H), rerun_distance(q2, H))
            self.assertGreaterEqual(rerun_transitions(q1), rerun_transitions(q2))
        for bad in (0, 1, -0.1, 1.5):
            with self.assertRaises(ValueError):
                RulesGenerator([], PresenceRatioProblem({}, 1), bad)

    def test_per_run(self):
        """Lower q: a run does less work, finds a solution no better, and its script asks for a
        new run no sooner, whatever the chip and the modes do next."""
        rng = random.Random(13)
        for _ in range(25):
            rules, problem, chip, modes, edits = random_world(rng)
            sim = Simulation(LineState(), RulesGenerator(rules, problem, 0.5), chip, modes, edits)
            sim.run(rng.randint(0, 15))  # some state reached by a run
            t = F(rng.randint(0, 15))
            c = chip.position(t)
            known = modes.known(c)
            prev = None
            for q in self.QS:
                script, stats = RulesGenerator(rules, problem, q).run(sim.state.copy(), c, known)
                first = self.first_rerun(script, chip, modes, t)
                if prev:
                    self.assertGreaterEqual(stats.work, prev[0])
                    self.assertLessEqual(stats.key, prev[1])
                    self.assertLessEqual(first, prev[2])
                prev = (stats.work, stats.key, first)

    @staticmethod
    def first_rerun(script, chip, modes, t):
        times = [INF]
        c = chip.position(t)
        for trig in script.rerun_triggers():
            if isinstance(trig, ChipIn):
                times.append(chip.first_time(max(trig.lo, c)))
            else:
                later = [p for p, _ in modes.transitions if p > c]
                if len(later) >= trig.k:
                    times.append(chip.first_time(later[trig.k - 1]))
        return min(times)

    def test_whole_runs(self):
        """Over a whole simulation, a lower q never triggers more runs from scripts."""
        rng = random.Random(17)
        for _ in range(15):
            rules, problem, chip, modes, edits = random_world(rng)
            end = chip.points[-1][0] + 5
            counts = []
            for q in self.QS[::2]:
                sim = Simulation(LineState(), RulesGenerator(rules, problem, q), chip, modes, edits)
                log = sim.run(end)
                counts.append(sum(1 for r in log.runs if r[2] == "script"))
            self.assertEqual(counts, sorted(counts), counts)


class ProbabilityFinderTest(unittest.TestCase):
    def periodic(self, n=40):
        trans = [(F(2 * i), "2" if i % 2 else "1") for i in range(1, n)]
        return ModeTimeline(0, "1", trans)

    def test_rules_of_a_periodic_pattern(self):
        model = ProbabilityFinder().fit(self.periodic().known(F(21, 2)))
        self.assertEqual(model.rules(), ["after mode 1, mode 2 follows with probability 1",
                                         "after mode 2, mode 1 follows with probability 1",
                                         "mode 1 lasts 2 with probability 1",
                                         "mode 2 lasts 2 with probability 1"])
        scen, residual = model.scenarios(self.periodic().known(F(21, 2)), F(16))
        self.assertEqual((len(scen), residual), (1, 0))
        self.assertEqual(scen[0].mode_region("1"), IntervalSet.of(Interval.closed_open(12, 14)))
        self.assertEqual(scen[0].mode_region("2"), IntervalSet.of(Interval.open(F(21, 2), 12), Interval.closed_open(14, 16)))
        self.assertEqual(scen[0].uncovered, IntervalSet.of(Interval.point(16)))  # the next segment starts at the horizon

    def test_probabilities(self):
        tl = ModeTimeline(0, "1", [(1, "2"), (2, "1"), (4, "3"), (5, "1"), (6, "2")])
        model = ProbabilityFinder().fit(tl.known(7))
        self.assertIn("after mode 1, mode 2 follows with probability 2/3", model.rules())
        self.assertIn("mode 1 lasts 2 with probability 1/2", model.rules())
        scen, residual = model.scenarios(tl.known(7), F(9))
        self.assertEqual(sum(s.probability for s in scen) + residual, 1)

    def test_helps_with_low_resources(self):
        """mode 1 → D must be satisfied before the mode is known. With the probability rules a
        run with few resources puts D where mode 1 will be, and only there."""
        rules = [parse_rule("mode 1 → D")]
        problem = PresenceRatioProblem({"D": F(0)}, F(8))
        modes = self.periodic()
        chip = Chip([(0, 0), (70, 70)])
        out = {}
        for use in (True, False):
            sim = Simulation(LineState(), RulesGenerator(rules, problem, 0.01, use), chip, modes)
            log = sim.run(70)
            check_log(log, rules, modes)
            final = modes.known(F(70))
            violations = rules[0].violations(sim.state, mode_region(rules[0], final, certain=True))
            out[use] = (violations.measure(), sim.state.integral("D", IntervalSet.of(Interval.closed(0, 70))))
        self.assertEqual(out[True][0], 0)  # no violation with the probability finder
        self.assertGreater(out[False][0], 0)
        self.assertLess(out[True][1], 50)  # D only about where mode 1 is (35 of 70, plus the start)


if __name__ == "__main__":
    unittest.main()


class HandWrittenScriptsTest(unittest.TestCase):
    """The guarantees hold for any script, not only for generated ones: the line refuses
    what a script may not do."""

    def run_script(self, script, rules, state, end=10):
        class Fixed(RulesGenerator):
            def run(self, s, chip, known):
                from generator import RunStats
                return (script if chip == 0 else Script()), RunStats()
        gen = Fixed(rules, PresenceRatioProblem({}, 10), 0.5)
        modes = ModeTimeline(0, "1", [(5, "2")])
        sim = Simulation(state, gen, Chip([(0, 0), (10, 10)]), modes)
        log = sim.run(end)
        check_log(log, rules, modes)
        return sim, log

    def test_refusals(self):
        user_b = LineState.from_entries([("B", Interval.closed(2, 3), 100)])
        A = lambda lo, hi: IntervalSet.of(Interval.closed(lo, hi))
        cases = [
            ([], LineState(), SetAction((("A", A(-1, 1), F(100)),)), "N1"),  # left of the chip
            ([], LineState(), SetAction((("A", A(0, 1), F(100)),)), "N1"),  # the chip's position
            ([], user_b, SetAction((("B", A(2, 3), F(0)),)), None),  # "remove generated B": the user's B stays
            ([], user_b, SetAction((("B", A(2, 3), F(50)),)), "N2"),  # change it
            ([parse_rule("A → B")], LineState(), SetAction((("A", A(4, 6), F(100)),)), "N3"),
            ([parse_rule("mode 2 → no D")], LineState(), SetAction((("D", A(7, 8), F(100)),)), "N3"),  # mode unknown there
        ]
        for rules, state, action, why in cases:
            sim, log = self.run_script(Script([Event(ChipIn(0, 10), (action,))]), rules, state)
            self.assertEqual(sim.state, state)
            if why:
                self.assertTrue(log.refused and log.refused[0][1].startswith(why), (why, log.refused))
        # a continuous edit of a type in a left side is refused
        rules = [parse_rule("A → B")]
        sim, log = self.run_script(Script([Event(ChipIn(0, 10), (FollowAction("A", F(100), IntervalSet.of(Interval.point(1))),))]), rules, LineState())
        self.assertEqual(sim.state, LineState())
        # a continuous edit skips the user's elements and the forbidden zone
        rules = [parse_rule("C → no D at 1")]
        state = LineState.from_entries([("D", Interval.closed(3, 4), 40), ("C", Interval.closed(6, 7), 100)])
        sim, log = self.run_script(Script([Event(ChipIn(0, 10), (FollowAction("D", F(100), IntervalSet.of(Interval.point(1))),))]), rules, state)
        self.assertEqual(sim.state.value_at("D", F(7, 2)), 40)
        self.assertEqual(sim.state.support("D") & IntervalSet.of(Interval.closed(7, 8)), IntervalSet())
        self.assertEqual(sim.state.value_at("D", F(9)), 100)
