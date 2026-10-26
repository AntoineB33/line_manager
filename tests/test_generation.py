"""The rules generator, its scripts, mode rules, the hyperparameter and the probability
finder (requirement.md, second part), on exact simulations."""

import random
import unittest
from fractions import Fraction as F

from helpers import check_log, rand_interval, rules_hold
from generator import PresenceRatioProblem, RulesGenerator, budget, rerun_distance, rerun_transitions
from intervals import INF, Interval, IntervalSet
from line import GEN, MODE, USER, LineState, Op
from modes import ModeTimeline
from probability import ProbabilityFinder
from region import Region
from resolve import apply_edit
from rules import parse_rules
from script import ChipIn, EditAction, Event, ModeRulesAction, RerunAction, Script
from simulation import Chip, Simulation, UserEdit

POOL = ["A → B or C", "B → D", "B → no D 1 unit on the right", "A → no C 1 unit on the left", "C → D",
        "A & B → D", "mode 1 → E", "mode 2 → no E", "mode 1 & A → D", "A → E 50% on [0, 1] of l", "not C & D → no E"]


def random_world(rng):
    while True:
        try:
            rs = parse_rules(rng.sample(POOL, rng.randint(1, 4)))
            break
        except ValueError:
            continue
    targets = {k: F(rng.randint(0, 4), 4) for k in rng.sample("ABCE", rng.randint(1, 2))}
    problem = PresenceRatioProblem(targets, F(rng.choice([4, 6])))
    pos, trans, mode = F(0), [], "1"
    for _ in range(rng.randint(0, 5)):
        pos += F(rng.randint(1, 6))
        mode = rng.choice([m for m in "12" if m != mode])
        trans.append((pos, mode))
    modes = ModeTimeline(0, "1", trans)
    pts, t, x = [(0, 0)], F(0), F(0)
    for _ in range(rng.randint(1, 3)):
        t += F(rng.randint(2, 5))
        x += F(rng.choice([0, 2, 4]))  # the chip may stop
        pts.append((t, x))
    edits = [UserEdit(F(0), [Op.put(rng.choice("ABC"), Interval.closed(1, 3))])]
    for _ in range(rng.randint(0, 2)):
        k = rng.choice("ABCDE")
        iv = rand_interval(rng, -2, 12)
        edits.append(UserEdit(F(rng.randint(1, 10)), [rng.choice([Op.put(k, iv), Op.put_no(k, iv), Op.take(k, iv)])]))
    return rs, problem, Chip(pts), modes, edits


class ScriptTest(unittest.TestCase):
    def run_script(self, script, rs, state):
        class Fixed(RulesGenerator):
            def run(self, s, chip, known):
                from generator import RunStats
                return (script if chip == 0 else Script()), RunStats()
        sim = Simulation(state, Fixed(rs, PresenceRatioProblem({}, 10), 0.5), Chip([(0, 0), (10, 10)]), ModeTimeline(0, "1"))
        return sim, sim.run(10)

    def test_never_left_of_the_chip_nor_the_users_elements(self):
        rs = parse_rules(["A → B 1 unit on the left", "C → no B"])
        user = apply_edit(rs, LineState.empty(), [Op.put("C", Interval.closed(4, 5))], USER)
        cases = [
            (LineState.empty(), Op.put("B", Interval.closed(-1, 1)), "N1"),  # left of the chip
            (LineState.empty(), Op.put("B", Interval.point(0)), "N1"),  # the chip's position
            (LineState.empty(), Op.put("A", Interval.closed(1, 2)), "N1"),  # its consequence B on [0, 1]
            (user, Op.take("C", Interval.closed(4, 5)), "N2"),  # remove the user's C
            (user, Op.put("C", Interval.closed(4, 5), 50), "N2"),  # change it
            (user, Op.put("B", Interval.point(4)), "N2"),  # newest wins: the user's C would go
        ]
        for state, op, why in cases:
            sim, log = self.run_script(Script([Event(ChipIn(0, 10), (EditAction((op,)),))]), rs, state)
            self.assertEqual(sim.state.line, state.line, op)
            self.assertTrue(log.refused and log.refused[0][2].startswith(why), (why, log.refused))
        sim, log = self.run_script(Script([Event(ChipIn(0, 10), (EditAction((Op.put("A", Interval.closed(2, 3)),)),))]), rs, LineState.empty())
        self.assertEqual(sim.state.line.presence("B"), Region.of(Interval.closed(1, 2)))
        check_log(log, rs)


class ModeRulesTest(unittest.TestCase):
    RULES = ["mode 1 → D", "mode 2 → no D"]
    MODES = [(5, "2"), (7, "1"), (12, "2")]

    def simulate(self, rs, state, edits=(), script=None):
        gen = RulesGenerator(rs, PresenceRatioProblem({}, 10), 0.5)
        if script is not None:
            class Fixed(RulesGenerator):
                def run(self, s, chip, known):
                    from generator import RunStats
                    return script, RunStats()
            gen = Fixed(rs, PresenceRatioProblem({}, 10), 0.5)
        sim = Simulation(state, gen, Chip([(0, 0), (10, 10), (20, 15)]), ModeTimeline(0, "1", self.MODES), list(edits))
        log = sim.run(20)
        check_log(log, rs)
        return sim, log

    def test_act_at_the_chip_only(self):
        """Mode 1 adds D where the chip went in mode 1, mode 2 removes the generated D where
        the chip went in mode 2, and nothing changes beyond the chip (15)."""
        rs = parse_rules(self.RULES)
        state = apply_edit(rs, LineState.empty(), [Op.put("D", Interval.open_closed(0, 20))], GEN)
        sim, log = self.simulate(rs, state)
        self.assertEqual(sim.state.line.presence("D"), Region.of(IntervalSet.of(Interval.closed_open(0, 5), Interval.closed_open(7, 12), Interval.closed(15, 20))))
        self.assertTrue(any(ch.source == "mode" for ch in log.changes))

    def test_users_elements_win(self):
        """Edits from the user always win: mode 2 does not remove the user's D."""
        rs = parse_rules(self.RULES)
        sim, log = self.simulate(rs, LineState.empty(), [UserEdit(F(1), [Op.put("D", Interval.closed(0, 20))])])
        self.assertEqual(sim.state.line.presence("D"), Region.of(Interval.closed(0, 20)))
        self.assertTrue(any(src == "mode" and "N2:" in why for _, src, why in log.refused))

    def test_nothing_removed_away_from_the_chip(self):
        """D at the chip conflicts with the user's A on [0, 1] through C on [0, 10] of l:
        the mode rule is refused rather than removing A, away from the chip. Each position
        is decided on its own: D goes where mode 1 meets no C, (10, 12)."""
        rs = parse_rules(["A → C on [0, 10] of l", "D → no C", "mode 1 → D"])
        state = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 1))], USER)
        sim, log = self.simulate(rs, state)
        self.assertEqual(sim.state.line.presence("A"), Region.of(Interval.closed(0, 1)))
        self.assertEqual(sim.state.line.presence("C"), Region.of(Interval.closed(0, 10)))
        self.assertEqual(sim.state.line.presence("D"), Region.of(Interval.open(10, 12)))
        self.assertTrue(any(src == "mode" and "M1: would change elements away from the chip" in why for _, src, why in log.refused))

    def test_each_position_takes_the_first_operand(self):
        """One stretch of the chip, [0, 5) in mode 1, and the operand changes inside it: D
        cannot go where the generated X is (removing X would change it away from the chip)."""
        rs = parse_rules(["mode 1 → D or E", "D → no X"])
        state = apply_edit(rs, LineState.empty(), [Op.put("X", Interval.closed(2, 3))], GEN)
        sim, log = self.simulate(rs, state)
        self.assertEqual(sim.state.line.presence("X"), Region.of(Interval.closed(2, 3)))
        self.assertEqual(sim.state.line.presence("E"), Region.of(IntervalSet.of(Interval.closed(2, 3))))
        self.assertEqual(sim.state.line.presence("D") & Region.of(Interval.closed_open(0, 5)),
                         Region.of(IntervalSet.of(Interval.closed_open(0, 2), Interval.open(3, 5))))
        for ch in log.changes:
            if ch.source == "mode":
                self.assertGreaterEqual(ch.at.inf(), ch.chip)  # nothing before where the change starts

    def test_only_through_the_script(self):
        """The line is modified only by the user and the generated script: without the
        script's mode-rule action, the mode rules do nothing."""
        rs = parse_rules(self.RULES)
        sim, log = self.simulate(rs, LineState.empty(), script=Script())
        self.assertFalse(sim.state.line.kinds())
        self.assertFalse(any(ch.source == "mode" for ch in log.changes))
        script, _ = RulesGenerator(rs, PresenceRatioProblem({}, 10), 0.5).run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))
        self.assertTrue(any(isinstance(a, ModeRulesAction) for e in script.events for a in e.actions))


class GuaranteesTest(unittest.TestCase):
    def test_random_simulations(self):
        rng = random.Random(11)
        script_changes = 0
        for _ in range(12):
            rs, problem, chip, modes, edits = random_world(rng)
            gen = RulesGenerator(rs, problem, rng.choice([0.05, 0.4, 0.9]), rng.random() < 0.7)
            sim = Simulation(LineState.empty(), gen, chip, modes, edits)
            log = sim.run(chip.points[-1][0] + 2)
            script_changes += check_log(log, rs)
        self.assertGreater(script_changes, 8)


class HyperparameterTest(unittest.TestCase):
    QS = [0.001, 0.2, 0.5, 0.8, 0.999]

    def test_schedules(self):
        H = F(10)
        qs = [0.001, 0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95, 0.999]
        for q1, q2 in zip(qs, qs[1:]):
            self.assertLessEqual(budget(q1), budget(q2))
            self.assertGreater(rerun_distance(q1, H), rerun_distance(q2, H))
            self.assertGreaterEqual(rerun_transitions(q1), rerun_transitions(q2))
        for bad in (0, 1, -0.1, 1.5):
            with self.assertRaises(ValueError):
                RulesGenerator(parse_rules([]), PresenceRatioProblem({}, 1), bad)

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

    def test_per_run(self):
        """Lower q: a run does less work, finds a solution no better, and its script asks for a
        new run no sooner, whatever the chip and the modes do next."""
        rng = random.Random(13)
        for _ in range(6):
            rs, problem, chip, modes, edits = random_world(rng)
            sim = Simulation(LineState.empty(), RulesGenerator(rs, problem, 0.5), chip, modes, edits)
            t = F(rng.randint(0, 6))
            sim.run(t)
            known = modes.known(chip.position(t))
            prev = None
            for q in self.QS:
                script, stats = RulesGenerator(rs, problem, q).run(sim.state, chip.position(t), known)
                first = self.first_rerun(script, chip, modes, t)
                if prev:
                    self.assertGreaterEqual(stats.work, prev[0])
                    self.assertLessEqual(stats.key, prev[1])
                    self.assertLessEqual(first, prev[2])
                prev = (stats.work, stats.key, first)

    def test_whole_runs(self):
        """Over a whole simulation, a lower q never triggers more runs from scripts."""
        rng = random.Random(17)
        for _ in range(4):
            rs, problem, chip, modes, edits = random_world(rng)
            counts = []
            for q in (0.05, 0.5, 0.95):
                sim = Simulation(LineState.empty(), RulesGenerator(rs, problem, q), chip, modes, edits)
                log = sim.run(chip.points[-1][0] + 2)
                counts.append(sum(1 for r in log.runs if r[2] == "script"))
            self.assertEqual(counts, sorted(counts), counts)


class ProbabilityFinderTest(unittest.TestCase):
    @staticmethod
    def periodic(lengths=(2, 2), n=60):
        trans, pos = [], F(0)
        for i in range(1, n):
            pos += lengths[(i - 1) % 2]
            trans.append((pos, "2" if i % 2 else "1"))
        return ModeTimeline(0, "1", trans)

    def test_rules_of_a_periodic_pattern(self):
        tl = self.periodic()
        model = ProbabilityFinder().fit(tl.known(F(21, 2)))
        self.assertEqual(model.rules(), ["after mode 1, mode 2 follows with probability 1",
                                         "after mode 2, mode 1 follows with probability 1",
                                         "mode 1 lasts 2 with probability 1",
                                         "mode 2 lasts 2 with probability 1"])
        scen, residual = model.scenarios(tl.known(F(21, 2)), F(16))
        self.assertEqual((len(scen), residual), (1, 0))
        self.assertEqual(scen[0].mode_region("1"), IntervalSet.of(Interval.closed_open(12, 14)))
        self.assertEqual(scen[0].mode_region("2"), IntervalSet.of(Interval.open(F(21, 2), 12), Interval.closed_open(14, 16)))

    def test_probabilities(self):
        tl = ModeTimeline(0, "1", [(1, "2"), (2, "1"), (4, "3"), (5, "1"), (6, "2")])
        model = ProbabilityFinder().fit(tl.known(7))
        self.assertIn("after mode 1, mode 2 follows with probability 2/3", model.rules())
        self.assertIn("mode 1 lasts 2 with probability 1/2", model.rules())
        scen, residual = model.scenarios(tl.known(7), F(9))
        self.assertEqual(sum(s.probability for s in scen) + residual, 1)

    def test_helps_with_low_resources(self):
        """mode 1 → D, D wanted on 3/4 of the line, mode 1 three units out of four. With the
        probability rules, a run with the fewest resources sees that mode 1 will give D
        and adds none; without them it expects half and adds D where mode 2 will be."""
        rs = parse_rules(["mode 1 → D"])
        problem = PresenceRatioProblem({"D": F(3, 4)}, F(8))
        modes = self.periodic((3, 1))
        chip = Chip([(0, 0), (60, 60)])
        err = {}
        for use in (True, False):
            sim = Simulation(LineState.empty(), RulesGenerator(rs, problem, 0.01, use), chip, modes)
            log = sim.run(60)
            check_log(log, rs)
            window = IntervalSet.of(Interval.closed(20, 60))
            err[use] = abs(sim.state.line.integral("D", window) / 40 - F(3, 4))
        self.assertLess(err[True], err[False])
        self.assertEqual(err[True], 0)


if __name__ == "__main__":
    unittest.main()
