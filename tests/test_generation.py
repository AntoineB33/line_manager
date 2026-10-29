"""The rules generator, its scripts, mode rules, the hyperparameter and the probability
finder (requirement.md, second part), on exact simulations."""

import random
import unittest
from fractions import Fraction as F

from helpers import check_log, rand_interval, rules_hold
from generator import PresenceRatioProblem, RulesGenerator, _Context, budget, rerun_distance, rerun_transitions
from intervals import INF, Interval, IntervalSet, Pos
from line import GEN, MODE, USER, LineState, Op
from modes import ModeTimeline
from probability import ProbabilityFinder
from problems import MinimumLengthProblem, shortfalls
from region import Region
from resolve import apply_edit
from rules import parse_rules
from script import ChipIn, EditAction, Event, RerunAction, Script, TransitionCount
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


class Oracle(RulesGenerator):
    """A generator that predicts the real modes: the mode rules are compiled for them."""

    def __init__(self, *args, timeline: ModeTimeline, **kw) -> None:
        super().__init__(*args, **kw)
        self.timeline = timeline

    def predicted(self, ctx, end):
        from intervals import EPS
        points = [(ctx.c, self.timeline.mode_at(ctx.c))] + [(x, m) for x, m in self.timeline.transitions if ctx.c < x < end]
        return [(Pos(x), Pos(points[i + 1][0]) - EPS if i + 1 < len(points) else Pos(end), m) for i, (x, m) in enumerate(points)]


class ModeRulesTest(unittest.TestCase):
    RULES = ["mode 1 → D", "mode 2 → no D"]
    MODES = [(5, "2"), (7, "1"), (12, "2")]

    def simulate(self, rs, state, edits=(), script=None):
        timeline = ModeTimeline(0, "1", self.MODES)
        gen = Oracle(rs, PresenceRatioProblem({}, 10), 0.5, timeline=timeline)
        if script is not None:
            class Fixed(RulesGenerator):
                def run(self, s, chip, known):
                    from generator import RunStats
                    return script, RunStats()
            gen = Fixed(rs, PresenceRatioProblem({}, 10), 0.5)
        sim = Simulation(state, gen, Chip([(0, 0), (10, 10), (20, 15)]), timeline, list(edits))
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
        self.assertTrue(any("N2:" in why for r in log.runs for _, why in r[4].refused))

    def test_nothing_removed_away_from_the_chip(self):
        """D at the chip conflicts with the generated A on [-3, -2] through C on [-3, 7]:
        the mode rule is refused rather than removing A, away from the chip (and the user's
        A, at the chip, is not removed either: test_users_elements_win). Each position is
        decided on its own: D goes where mode 1 meets no C, (7, 12)."""
        rs = parse_rules(["A → C on [0, 10] of l", "D → no C", "mode 1 → D"])
        state = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(-3, -2))], GEN)
        sim, log = self.simulate(rs, state)
        self.assertEqual(sim.state.line.presence("A"), Region.of(Interval.closed(-3, -2)))
        self.assertEqual(sim.state.line.presence("C"), Region.of(Interval.closed(-3, 7)))
        self.assertEqual(sim.state.line.presence("D"), Region.of(Interval.open(7, 12)))
        self.assertTrue(any("M1:" in why for r in log.runs for _, why in r[4].refused))

    def test_each_position_takes_the_first_operand(self):
        """One stretch of the chip, [0, 5) in mode 1, and the operand changes inside it: D
        cannot go where the user's X is."""
        rs = parse_rules(["mode 1 → D or E", "D → no X"])
        state = apply_edit(rs, LineState.empty(), [Op.put("X", Interval.closed(2, 3))], USER)
        sim, log = self.simulate(rs, state)
        self.assertEqual(sim.state.line.presence("X"), Region.of(Interval.closed(2, 3)))
        self.assertEqual(sim.state.line.presence("E"), Region.of(IntervalSet.of(Interval.closed(2, 3))))
        self.assertEqual(sim.state.line.presence("D") & Region.of(Interval.closed_open(0, 5)),
                         Region.of(IntervalSet.of(Interval.closed_open(0, 2), Interval.open(3, 5))))
        for ch in log.changes:
            if ch.source == "mode":
                self.assertGreaterEqual(ch.at.inf(), ch.chip)  # nothing before where the change starts

    def test_through_a_cycle(self):
        """D at the chip repeats every 3 units ahead of it (a cycle). The user's "no D" at 13
        wins: the chip in mode 1 ([0, 5) and [7, 12)) places D everywhere but at 1, 4, 7
        and 10, whose repeats would reach 13."""
        rs = parse_rules(["mode 1 → D", "D → D 3 units on the right"])
        state = apply_edit(rs, LineState.empty(), [Op.put_no("D", Interval.point(13))], USER)
        sim, log = self.simulate(rs, state)
        mode1 = Region.of(IntervalSet.of(Interval.closed_open(0, 5), Interval.closed_open(7, 12)))
        placed = mode1 - Region.of(IntervalSet(Interval.point(x) for x in (1, 4, 7, 10)))
        self.assertEqual(sim.state.line.presence("D") & mode1, placed)
        self.assertFalse(sim.state.line.presence("D").contains(13))
        self.assertTrue(sim.state.line.presence("D").contains(F(1000 * 3 + 1, 2)))  # 0.5 + 3n, far ahead

    def test_compiled_by_the_generator(self):
        """The chip in mode 1 drags B (S starts it as early as possible). The generator's
        run compiles it: the script's stretch the chip goes through gives, at each step,
        the line solved from its claims, and moves the line with the chip (no solve)."""
        from intervals import EPS
        from mode_rules import _iv, _same_line
        rs = parse_rules(["T → S or no S", "S → R", "S → B on [0, 2] of p", "S → no R on (0, 6] of p", "mode 1 → no B"])
        state = apply_edit(rs, LineState.empty(), [Op.put("T", Interval(0, INF, True, False))], USER)
        # horizon 100: the next run is asked for at 75 / 18, after the positions looked at
        sim = Simulation(state, RulesGenerator(rs, PresenceRatioProblem({}, 100), 0.5), Chip([(0, 0), (20, 20)]),
                         ModeTimeline(0, "1"))
        moving = False
        for i in range(1, 9):
            sim.advance(F(i, 2))
            st = sim._stretch[0]
            plan = st.plan
            solved = plan.mr.with_run(plan.state0, plan.decision, Region.of(_iv(plan.a0, Pos(F(i, 2)) - EPS)))
            self.assertTrue(_same_line(sim.state.line, solved.line), i)
            self.assertEqual(sim.state.line.presence("S") & Region.of(Interval.closed(0, F(i, 2) + 5)),
                             Region.of(Interval.point(F(i, 2))))
            moving |= any(line is not None for _, _, line, _ in plan.pieces)
        self.assertEqual(plan.m, 0)  # one run, decided at the start
        self.assertEqual(len(sim.log.runs), 1)  # compiled by the generator's first run
        self.assertIn(st, sim.script.stretches)
        self.assertTrue(moving)

    def test_constraints(self):
        """The line starts empty; the generator's constraint keeps Future after the chip
        (written from the chip), the rules place the breaks there, and the script's
        compiled mode rules drag the one at the chip."""
        rs = parse_rules(["Future → S or no S", "S → R", "S → B on [0, 2] of p", "S → no R on (0, 6] of p", "mode 1 → no B"])
        future = Op.put("Future", Interval(0, INF, False, False))  # (0, inf)
        sim = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 10), 0.5, constraints=[future]),
                         Chip([(0, 0), (20, 20)]), ModeTimeline(0, "1"))
        sim.start()
        self.assertIn("put Future 100% on chip + (0, +inf)", sim.script.text())
        sim.advance(4)
        check_log(sim.log, rs)
        line = sim.state.line
        self.assertEqual(line.presence("Future"), Region.of(Interval(0, INF, False, False)))
        self.assertTrue((line.presence("B") & Region.of(Interval.closed_open(0, 4))).is_empty())  # dragged: none left behind
        self.assertTrue(line.presence("B").contains(5))
        self.assertTrue(any(ch.source == "script" for ch in sim.log.changes))
        script, _ = sim.gen.run(sim.state, F(4), ModeTimeline(0, "1").known(4))
        self.assertNotIn("Future", " ".join(str(e) for e in script.events))  # already kept: no edit

    def test_stopped_step(self):
        """STOP set (Pause) while the generator compiles the mode rules: the step raises
        Interrupted, and restore gives back the simulation as it was before it."""
        import solver
        rs = parse_rules(["T → S or no S", "S → B on [0, 2] of p", "S → no S on (0, 6] of p", "mode 1 → no B"])
        state = apply_edit(rs, LineState.empty(), [Op.put("T", Interval(0, INF, True, False))], USER)
        sim = Simulation(state, RulesGenerator(rs, PresenceRatioProblem({}, 10), 0.5), Chip([(0, 0), (20, 20)]),
                         ModeTimeline(0, "1"))
        before, changes = sim.state, len(sim.log.changes)
        snap = sim.snapshot()
        solver.STOP.set()
        try:
            with self.assertRaises(solver.Interrupted):
                sim.advance(F(1, 2))
        finally:
            solver.STOP.clear()
        sim.restore(snap)
        self.assertIs(sim.state, before)
        self.assertEqual((sim.t, len(sim.log.changes), len(sim.log.runs), sim.started), (0, changes, 0, False))
        sim.advance(F(3))
        self.assertEqual(sim.state.line.presence("S") & Region.of(Interval.closed(0, 8)), Region.of(Interval.point(3)))

    def test_only_at_the_chip_point(self):
        """The generated X on [2, 3] is in D's way: as the chip goes through it, X goes at
        each position the chip reaches, and only there, so D can be chosen everywhere."""
        rs = parse_rules(["mode 1 → D or E", "D → no X"])
        state = apply_edit(rs, LineState.empty(), [Op.put("X", Interval.closed(2, 3))], GEN)
        sim, log = self.simulate(rs, state)
        self.assertEqual(sim.state.line.presence("D") & Region.of(Interval.closed_open(0, 5)), Region.of(Interval.closed_open(0, 5)))
        self.assertEqual(sim.state.line.presence("X"), Region.empty())

    def test_only_through_the_script(self):
        """The line is modified only by the user and the generated script: without the
        stretches a run compiled, the mode rules do nothing."""
        rs = parse_rules(self.RULES)
        sim, log = self.simulate(rs, LineState.empty(), script=Script())
        self.assertFalse(sim.state.line.kinds())
        self.assertFalse(any(ch.source == "mode" for ch in log.changes))
        script, _ = RulesGenerator(rs, PresenceRatioProblem({}, 10), 0.5).run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))
        self.assertTrue(script.stretches)

    def test_unpredicted_modes(self):
        """A run compiles the mode rules for the modes it predicts (here the mode at the
        chip goes on): where the modes differ, no stretch applies, and the script's
        Uncovered event runs the generator again there."""
        rs = parse_rules(self.RULES)
        sim = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 10), 0.5, use_probability=False),
                         Chip([(0, 0), (10, 10)]), ModeTimeline(0, "1", [(4, "2"), (5, "1")]))
        log = sim.run(10)
        check_log(log, rs)
        runs = [(r[1], r[2]) for r in log.runs]
        self.assertEqual(runs[0], (0, "start"))
        self.assertIn((4, "uncovered"), runs)  # mode 2 where mode 1 was compiled
        self.assertIn((5, "uncovered"), runs)  # mode 1 again, on a line the run from 4 did not compile
        self.assertEqual(sim.state.line.presence("D") & Region.of(Interval.closed_open(0, F(15, 2))),
                         Region.of(IntervalSet.of(Interval.closed_open(0, 4), Interval.closed_open(5, F(15, 2)))))


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
            elif isinstance(trig, TransitionCount):  # (the others answer events: an edit, a line not compiled)
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



class ProblemsTest(unittest.TestCase):
    """The optimization problems (problems.py) and their weights."""

    def short(self, *ivs, L=10):
        return [d for _, d in shortfalls(Region.of(IntervalSet(ivs)), F(L), F(-50), F(100))]

    def test_minimum_length(self):
        self.assertEqual(self.short(Interval.closed(0, 10)), [0])
        # a hole of 1: each piece falls short by more than the hole
        a, b = self.short(Interval.closed(0, F(9, 2)), Interval.closed(F(11, 2), 10))
        self.assertTrue(a > 1 and b > 1)
        # a bit on the edge going away: its shortfall grows up to the full length minus its own
        far = [self.short(Interval.closed(0, F(19, 2)), Interval.closed(F(19, 2) + g, 10 + g))[1] for g in (1, 4, 8, 10, 20)]
        self.assertEqual(far, sorted(far))
        self.assertEqual(far[-2:], [F(19, 2), F(19, 2)])

    def run_once(self, rs, state, problems):
        script, stats = RulesGenerator(rs, problems, 0.5).run(state, F(0), ModeTimeline(0, "1").known(0))
        ops = [op for ev in script.events for a in ev.actions if isinstance(a, EditAction) for op in a.ops]
        return (apply_edit(rs, state, ops, GEN) if ops else state), stats

    def test_the_generator_reaches_minimum_lengths(self):
        """A short interval of the user grows after the chip; the user's own stays."""
        rs = parse_rules([])
        state = apply_edit(rs, LineState.empty(), [Op.put("A", IntervalSet.of(Interval.closed(2, 4), Interval.closed(30, 31)))], USER)
        problem = MinimumLengthProblem({"A": 10}, 40)
        after, stats = self.run_once(rs, state, [(problem, 1)])
        ctx = _Context(RulesGenerator(rs, [(problem, 1)], 0.5), after, F(0), ModeTimeline(0, "1").known(0))
        self.assertEqual(problem.error(ctx, after), 0, stats.chosen)
        self.assertTrue(Region.of(IntervalSet.of(Interval.closed(2, 4), Interval.closed(30, 31))).issubset(after.claims.owned("A", USER)))

    def test_weights(self):
        """2 units of A in (0, 20], or none since an interval of A should be 10 long: the
        heavier problem decides."""
        rs = parse_rules([])
        ratio, length = PresenceRatioProblem({"A": F(1, 10)}, 20), MinimumLengthProblem({"A": 10}, 20)
        after, _ = self.run_once(rs, LineState.empty(), [(ratio, 10), (length, 1)])
        self.assertTrue(after.line.presence("A"))
        after, _ = self.run_once(rs, LineState.empty(), [(ratio, 1), (length, 10)])
        self.assertFalse(after.line.presence("A"))


class StepByStepTest(unittest.TestCase):
    """A simulation run step by step (the interactive program) is the simulation run at once."""

    def test_same_as_at_once(self):
        rs = parse_rules(["A → B or C", "B → no C 1 unit on the right", "mode 1 → E", "mode 2 → no E"])
        problem = PresenceRatioProblem({"A": F(1, 4)}, F(6))
        modes = ModeTimeline(0, "1", [(F(3), "2"), (F(7), "1")])
        edit = [Op.put("A", Interval.closed(9, 10))]
        # the edit at time 41/10 (chip 81/20): not where the scripts ask for a run (every 1/4)
        whole = Simulation(LineState.empty(), RulesGenerator(rs, problem, 0.5), Chip([(0, 0), (4, 4), (12, 8)]), modes,
                           [UserEdit(F(41, 10), edit)])
        whole.run(12)
        chip = Chip([(0, 0)])
        steps = Simulation(LineState.empty(), RulesGenerator(rs, problem, 0.5), chip, modes)
        chip.extend(4, 4)
        chip.extend(F(41, 10), F(81, 20))
        steps.advance(F(41, 10))
        self.assertIsNone(steps.edit_now(edit))
        chip.extend(12, 8)
        steps.advance(12)
        self.assertEqual(steps.state.line, whole.state.line)
        self.assertEqual(steps.state.claims.same(whole.state.claims), True)
        check_log(steps.log, rs)

    def test_background_runs(self):
        """In the background mode a call to run the generator is only recorded: the
        script in place goes on, and the run's script replaces it when it completes; the
        script of an older call does not."""
        rs = parse_rules(["A → B"])
        chip = Chip([(0, 0)])
        sim = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 4), 0.5), chip, ModeTimeline(0, "1"))
        sim.background = True
        sim.start()
        first = sim.request
        self.assertIsNotNone(first)
        self.assertIn("the program starts", sim.script.text())  # the initial script, until the run completes
        self.assertIsNone(sim.edit_now([Op.put("A", Interval.closed(2, 3))]))
        self.assertIs(sim.request, first)  # the initial script does not answer edits
        chip.extend(1, 1)
        sim.advance(1)
        script, stats = first.gen.run(first.state, first.chip, first.known)
        self.assertTrue(sim.complete(first.id, script, stats))
        self.assertIs(sim.script, script)
        self.assertEqual([(t, c) for t, c, _, _, _ in sim.log.runs], [(1, 0)])  # completed at time 1, from chip 0
        self.assertIsNone(sim.request)
        self.assertIsNone(sim.edit_now([Op.put("A", Interval.closed(5, 6))]))  # its script answers edits
        second = sim.request
        self.assertIsNotNone(second)
        self.assertIsNone(sim.edit_now([Op.put("A", Interval.closed(7, 8))]))
        third = sim.request
        self.assertNotEqual(third.id, second.id)  # a newer call: the older run's script is not used
        script2, stats2 = second.gen.run(second.state, second.chip, second.known)
        self.assertFalse(sim.complete(second.id, script2, stats2))
        self.assertIs(sim.script, script)

    def test_late_script_moved(self):
        """A run computes from chip 0 while the chip goes to 3 (background mode, the line
        empty meanwhile): its script, put in place at 3, finds the line it compiled from
        moved by 3, and its stretches apply moved by 3: the break is dragged."""
        rs = parse_rules(["Future → S or no S", "S → R", "S → B on [0, 2] of p", "S → no R on (0, 6] of p", "mode 1 → no B"])
        future = Op.put("Future", Interval(0, INF, False, False))
        chip = Chip([(0, 0)])
        # horizon 180: the next run is asked for at 135 / 18 = 7.5, after the positions looked at
        sim = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 180), 0.5, constraints=[future]),
                         chip, ModeTimeline(0, "1"))
        sim.background = True
        sim.start()
        req = sim.request
        chip.extend(3, 3)
        sim.advance(3)
        self.assertFalse(sim.state.line.kinds())  # nothing yet: the initial script only asks for a run
        script, stats = req.gen.run(req.state, req.chip, req.known)
        self.assertTrue(sim.complete(req.id, script, stats))
        self.assertEqual(sim._shift, 3)
        self.assertEqual(sim.state.line.presence("Future"), Region.of(Interval(3, INF, False, False)))
        chip.extend(7, 7)
        sim.advance(7)
        check_log(sim.log, rs)
        B = sim.state.line.presence("B")
        self.assertTrue((B & Region.of(Interval.closed_open(3, 7))).is_empty())  # dragged from 3 to 7
        self.assertTrue(B.contains(8))
        self.assertFalse(any(r[2] == "uncovered" for r in sim.log.runs))
        self.assertIsNone(sim.request)

    def test_initial_script(self):
        """At the start the script is only an instruction to run the generator once, as
        soon as possible: it runs at the first instant, after the user's edits of it."""
        rs = parse_rules(["A → B"])
        sim = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 4), 0.5), Chip([(0, 0)]),
                         ModeTimeline(0, "1"))
        self.assertEqual(sim.script.text(), "if the program starts, then run the generator once, as soon as possible.")
        self.assertIsNone(sim.edit_now([Op.put("A", Interval.closed(2, 3))]))
        self.assertEqual(sim.log.runs, [])
        sim.start()
        self.assertEqual([(t, why) for t, _, why, _, _ in sim.log.runs], [(0, "start")])
        self.assertNotIn("the program starts", sim.script.text())
        sim.advance(3)
        self.assertEqual(len(sim.log.runs), 1)

    def test_undo_and_rules(self):
        rs = parse_rules(["A → B"])
        chip = Chip([(0, 0)])
        sim = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 4), 0.5), chip, ModeTimeline(0, "1"))
        self.assertIsNone(sim.edit_now([Op.put("A", Interval.closed(2, 3))]))
        self.assertEqual(sim.state.line.kinds(), ["A", "B"])
        sim.set_generator(RulesGenerator(parse_rules(["A → C"]), PresenceRatioProblem({}, 4), 0.5))
        self.assertEqual(sim.state.line.kinds(), ["A", "C"])
        self.assertTrue(sim.undo_now())
        self.assertEqual(sim.state.line.kinds(), [])
        self.assertIsNotNone(sim.edit_now([Op.put("A", Interval.point(1)), Op.put_no("C", Interval.point(1))]))


if __name__ == "__main__":
    unittest.main()
