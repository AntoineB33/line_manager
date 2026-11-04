"""The rules generator, its scripts, mode rules, the hyperparameter and the probability
finder (requirement.md, second part), on exact simulations."""

import random
import unittest
from fractions import Fraction as F

from helpers import check_log, rand_interval
from constraints import FifteenMinuteBreaks, KeepOn, ScreenBreaks
from generator import PresenceRatioProblem, RulesGenerator, _Context, budget, rerun_distance
from intervals import EPS, INF, Interval, IntervalSet, Pos
from line import GEN, MODE, USER, LineState, Op
from modes import ModeTimeline
from probability import ProbabilityFinder
from problems import MinimumLengthProblem, shortfalls
from region import Region
from resolve import apply_edit
from rules import parse_rules
from script import STEP, ChangeAction, ChipIn, Edited, EditAction, Event, RerunAction, ScoreBelow, Script, TransitionCount, loop_name
from script_text import parse_script
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
            (LineState.empty(), Op.put("A", Interval.closed(0, 1)), "N1"),  # its consequence B on [-1, 0]
            (user, Op.take("C", Interval.closed(4, 5)), "N2"),  # remove the user's C
            (user, Op.put("C", Interval.closed(4, 5), 50), "N2"),  # change it
            (user, Op.put("B", Interval.point(4)), "N2"),  # newest wins: the user's C would go
        ]
        for state, op, why in cases:
            sim, log = self.run_script(Script.of([Event(ChipIn(0, 10), (EditAction((op,)),))]), rs, state)
            self.assertEqual(sim.state.line, state.line, op)
            self.assertTrue(log.refused and log.refused[0][2].startswith(why), (why, log.refused))
        sim, log = self.run_script(Script.of([Event(ChipIn(0, 10), (EditAction((Op.put("A", Interval.closed(2, 3)),)),))]), rs, LineState.empty())
        self.assertEqual(sim.state.line.presence("B"), Region.of(Interval.closed(1, 2)))
        check_log(log, rs)

    def test_at_the_chips_position(self):
        """Only its left side is out of reach: what is at the chip can change."""
        rs = parse_rules(["A → B 1 unit on the left"])
        sim, log = self.run_script(Script.of([Event(ChipIn(0, 10), (EditAction((Op.put("B", Interval.point(0)),)),))]), rs, LineState.empty())
        self.assertEqual(sim.state.line.presence("B"), Region.of(Interval.point(0)))
        self.assertFalse(log.refused)
        check_log(log, rs)

    def test_what_the_rules_placed_from_the_users_element_can_go(self):
        """The script can remove what the user's element brought through the rules, as long
        as the element stays and the rules hold."""
        a = Interval.point(5)
        no_b, x = Op.put_no("B", a), Op.put("X", a)
        for rules, op, gone in ((["A → B or C"], no_b, True), (["A & not X → B"], x, True), (["A → B"], no_b, False)):
            with self.subTest(rules):
                rs = parse_rules(rules)
                user = apply_edit(rs, LineState.empty(), [Op.put("A", a)], USER)
                sim, log = self.run_script(Script.of([Event(ChipIn(0, 10), (EditAction((op,)),))]), rs, user)
                self.assertTrue(sim.state.line.presence("A").contains(5))
                self.assertEqual(sim.state.line.presence("B").contains(5), not gone)
                self.assertEqual(bool(log.refused), not gone)  # (A → B: B cannot go while A stays)
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
        self.assertEqual(sim.state.line.presence("D"), Region.of(IntervalSet.of(Interval.closed_open(0, 5), Interval.closed_open(7, 12), Interval.open_closed(15, 20))))  # (mode 2 at the chip, 15)
        self.assertTrue(any(ch.source == "mode" for ch in log.changes))

    def test_older_user_edits_give_way(self):
        """The chip gets to a position after the edits made before: mode 2 removes the
        user's D from where the chip goes in that mode, and nothing ahead of it."""
        rs = parse_rules(self.RULES)
        sim, _ = self.simulate(rs, LineState.empty(), [UserEdit(F(1), [Op.put("D", Interval.closed(0, 20))])])
        self.assertEqual(sim.state.line.presence("D"), Region.of(IntervalSet.of(
            Interval(0, 5, True, False), Interval(7, 12, True, False), Interval(15, 20, False, True))))

    def test_a_user_removal_gives_way_too(self):
        """The user's "no D" ahead of the chip: mode 1 places D when the chip gets there."""
        rs = parse_rules(self.RULES)
        sim, _ = self.simulate(rs, LineState.empty(), [UserEdit(F(1), [Op.put_no("D", Interval.closed(2, 4))])])
        self.assertEqual(sim.state.line.presence("D"), Region.of(IntervalSet.of(
            Interval(0, 5, True, False), Interval(7, 12, True, False))))

    def test_what_the_mode_rules_won_stays(self):
        """A later edit, computed from all the user's edits, does not bring back what the
        chip took from an older one, and the newest edit wins where it acts."""
        rs = parse_rules(["mode 2 → no A", "B → C 1 unit on the left"])
        chip, modes = Chip([(0, 0), (20, 20)]), ModeTimeline(0, "1", [(5, "2")])
        g = RulesGenerator(rs, PresenceRatioProblem({}, 60), F(1, 2))
        sim = Simulation(LineState.empty(), g, chip, modes, [
            UserEdit(F(1), [Op.put("A", Interval.closed(2, 100))]),
            UserEdit(F(10), [Op.put("B", Interval.point(50))]),
            UserEdit(F(11), [Op.put("A", Interval.point(7))])])
        log = sim.run(12)
        check_log(log, rs)
        self.assertEqual(sim.state.line.presence("A"), Region.of(IntervalSet.of(
            Interval(2, 5, True, False), Interval.point(7), Interval(12, 100, False, True))))

    def test_an_edit_hidden_when_the_chip_passed_does_not_come_back(self):
        """A at 2, hidden by the newer "no A": the chip places D... here no A holds, so take
        the other way: "no D" (older), D (newer) at 2, then the mode rule and a take of D."""
        rs = parse_rules(["mode 1 → no D"])
        chip, modes = Chip([(0, 0), (20, 20)]), ModeTimeline(0, "1")
        g = RulesGenerator(rs, PresenceRatioProblem({}, 60), F(1, 2))
        p = Interval.point(F(5))
        sim = Simulation(LineState.empty(), g, chip, modes, [
            UserEdit(F(1), [Op.put("D", p, 50)]), UserEdit(F(2), [Op.put("D", p)]), UserEdit(F(8), [Op.take("D", Interval.point(F(6)))])])
        log = sim.run(10)
        check_log(log, rs)
        self.assertFalse(sim.state.line.presence("D"))

    def test_a_script_never_changes_itself(self):
        """Whatever made it: the event of a part that changes that part does nothing."""
        rs = parse_rules(self.RULES)
        e = Event(ChipIn(F(1), INF), (ChangeAction(True, loop_name(STEP), Event(ChipIn(F(2), INF), ())),))
        sim, log = self.simulate(rs, LineState.empty(), script=Script.of([e]))
        self.assertTrue(any("does not change itself" in why for _, _, why in log.refused))

    def test_an_edit_where_the_chip_stands_is_newer(self):
        """The chip stands at 5 in mode 2: A there, put after it arrived, stays; the mode
        rule acts on what comes after."""
        rs = parse_rules(["mode 2 → no A"])
        chip, modes = Chip([(0, 0), (5, 5), (20, 5)]), ModeTimeline(0, "2")
        g = RulesGenerator(rs, PresenceRatioProblem({}, 60), F(1, 2))
        sim = Simulation(LineState.empty(), g, chip, modes, [UserEdit(F(10), [Op.put("A", Interval.closed(5, 6))])])
        log = sim.run(12)
        check_log(log, rs)
        self.assertEqual(sim.state.line.presence("A"), Region.of(Interval.closed(5, 6)))

    def test_nothing_removed_away_from_the_chip(self):
        """D at the chip conflicts with the generated A on [-3, -2] through C on [-3, 7]:
        the mode rule is refused rather than removing A, away from the chip. Each position
        is decided on its own: D goes where mode 1 meets no C, (7, 12)."""
        rs = parse_rules(["A → C on [0, 10] of l", "D → no C", "mode 1 → D"])
        state = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(-3, -2))], GEN)
        sim, log = self.simulate(rs, state)
        self.assertEqual(sim.state.line.presence("A"), Region.of(Interval.closed(-3, -2)))
        self.assertEqual(sim.state.line.presence("C"), Region.of(Interval.closed(-3, 7)))
        self.assertEqual(sim.state.line.presence("D"), Region.of(Interval.open(7, 12)))
        self.assertTrue(any("M1:" in why for r in log.runs for _, why in r[4].refused))

    def test_each_position_takes_the_first_operand(self):
        """One stretch of the chip, [0, 5) in mode 1, and the operand changes inside it: D
        cannot go where X is, which comes from Y, on the left of the chip."""
        rs = parse_rules(["mode 1 → D or E", "D → no X", "Y → X 10 units on the right"])
        state = apply_edit(rs, LineState.empty(), [Op.put("Y", Interval.closed(-8, -7))], USER)
        sim, log = self.simulate(rs, state)
        self.assertEqual(sim.state.line.presence("X"), Region.of(Interval.closed(2, 3)))
        self.assertEqual(sim.state.line.presence("E"), Region.of(IntervalSet.of(Interval.closed(2, 3))))
        self.assertEqual(sim.state.line.presence("D") & Region.of(Interval.closed_open(0, 5)),
                         Region.of(IntervalSet.of(Interval.closed_open(0, 2), Interval.open(3, 5))))
        for ch in log.changes:
            if ch.source == "mode":
                self.assertGreaterEqual(ch.at.inf(), ch.chip)  # nothing before where the change starts

    def test_the_first_operand_wins_over_the_users_element(self):
        """D can be chosen by taking the user's older X at the chip: the "or" takes D."""
        rs = parse_rules(["mode 1 → D or E", "D → no X"])
        state = apply_edit(rs, LineState.empty(), [Op.put("X", Interval.closed(2, 3))], USER)
        sim, _ = self.simulate(rs, state)
        self.assertFalse(sim.state.line.presence("X"))
        self.assertFalse(sim.state.line.presence("E"))

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
            solved = plan.mr.with_run(plan.state0, plan.decision, Region.of(_iv(plan.a0, Pos(F(i, 2)))))
            self.assertTrue(_same_line(sim.state.line, solved.line), i)
            self.assertEqual(sim.state.line.presence("S") & Region.of(Interval.closed(0, F(i, 2) + 5)),
                             Region.point(Pos(F(i, 2)) + EPS))  # (B, that S brings, is ruled out at the chip)
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
        sim = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 10), 0.5, constraints=[KeepOn(future)]),
                         Chip([(0, 0), (20, 20)]), ModeTimeline(0, "1"))
        sim.start()
        self.assertIn("keep put Future 100% on chip + (0, +inf)", sim.script.text())
        self.assertIn("if the chip is at or after 0, then satisfy the constraints.", sim.script.text())
        sim.advance(4)
        check_log(sim.log, rs)
        line = sim.state.line
        self.assertEqual(line.presence("Future"), Region.of(Interval(0, INF, False, False)))
        self.assertTrue((line.presence("B") & Region.of(Interval.closed_open(0, 4))).is_empty())  # dragged: none left behind
        self.assertTrue(line.presence("B").contains(5))
        self.assertTrue(any(ch.source == "script" for ch in sim.log.changes))
        script, _ = sim.gen.run(sim.state, F(4), ModeTimeline(0, "1").known(4))
        self.assertNotIn("satisfy", " ".join(str(e) for e in script.events))  # already kept: no edit

    def test_stopped_step(self):
        """STOP set (Pause) while the generator compiles the mode rules: the step raises
        Interrupted, and restore gives back the simulation as it was before it."""
        import solver
        rs = parse_rules(["T → S or R", "S → B on [0, 2] of p", "R → no B 1 unit on the right", "mode 1 → no B"])
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
        self.assertEqual(sim.t, 3)
        self.assertTrue(Region.of(Interval.closed(0, 8)).issubset(sim.state.line.presence("S") | sim.state.line.presence("R")))

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


class FifteenMinuteBreaksTest(unittest.TestCase):
    """The 15-minute breaks as a constraint of the generator (constraints.py)."""
    RULES = ["Break15 → NoScreen", "mode 1 → no NoScreen"]

    def breaks(self, state, chip=0):
        return FifteenMinuteBreaks().breaks(state, F(chip)) & Region.of(Interval.closed(0, 700))

    def with_no_screen(self, a, b):
        return apply_edit(parse_rules(self.RULES), LineState.empty(), [Op.put("NoScreen", Interval.closed(a, b))], USER)

    @staticmethod
    def every_135(*starts):
        """Breaks of 15 starting right after 0 and after each of these: (a, a + 15 + ε]."""
        return Region.of(IntervalSet([Interval(a, Pos(a + 15, 1), False, True) for a in (0,) + starts]))

    def test_empty_line(self):
        """From the chip on, one break every 135: 15 of "no screen", then 2 hours."""
        self.assertEqual(self.breaks(LineState.empty()), self.every_135(135, 270, 405, 540, 675))

    def test_two_hours_after_no_screen(self):
        """30 minutes of "no screen" on [300, 330]: no break until 2 hours after its end;
        10 minutes only: nothing changes."""
        long = self.breaks(self.with_no_screen(300, 330))
        self.assertEqual(long & Region.of(Interval.closed(286, 460)), Region.of(Interval.closed(450, 460)))
        self.assertEqual(self.breaks(self.with_no_screen(300, 310)), self.every_135(135, 270, 405, 540, 675))

    def test_moved_back_into_no_screen(self):
        """A break that would start inside a "no screen" period starts at its start."""
        b = self.breaks(self.with_no_screen(400, 410))
        self.assertTrue(b.contains(400) and b.contains(415) and not b.contains(F(4155, 10)))  # [400, 415], not at 405
        self.assertTrue((b & Region.of(Interval.closed(416, 534))).is_empty() and b.contains(535))  # then 2 hours

    def test_going_on_at_the_chip(self):
        """The chip inside a break: the break stays as it is, and nothing changes."""
        rs = parse_rules(self.RULES)
        k = FifteenMinuteBreaks()
        line = apply_edit(rs, LineState.empty(), k.ops(LineState.empty(), F(0)), GEN)
        self.assertEqual(k.ops(line, F(140)), [])

    def test_script_puts_them(self):
        """The line starts empty: the first run's script puts the breaks after the chip."""
        rs = parse_rules(self.RULES)
        sim = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 180), 0.5,
                                                           constraints=[FifteenMinuteBreaks()]),
                         Chip([(0, 0), (1, 1)]), ModeTimeline(0, "2"))
        sim.run(1)
        check_log(sim.log, rs)
        self.assertEqual(sim.state.line.presence("Break15") & Region.of(Interval.closed(0, 700)),
                         self.every_135(135, 270, 405, 540, 675))
        self.assertTrue(any(ch.source == "script" for ch in sim.log.changes))


    def _back_to_mode_1(self, rules):
        """Mode 2 from 130, mode 1 again at 140, inside the break; no generator run
        completes after the first. What the breaks are around there, the chip at 160."""
        rs = parse_rules(rules)
        chip = Chip([(0, 0)])
        sim = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 240), 0.5, constraints=[FifteenMinuteBreaks()]),
                         chip, ModeTimeline(0, "1", [(130, "2"), (140, "1")]))
        sim.background = True
        sim.start()
        r = sim.request
        sim.complete(r.id, *r.gen.run(r.state, r.chip, r.known))
        while sim.t < 160:
            chip.extend(sim.t + 1, sim.t + 1)
            sim.advance(sim.t + 1)
        check_log(sim.log, rs)
        return sim.state.line.presence("Break15") & Region.of(Interval.closed(100, 200))

    def test_back_to_mode_1_inside_a_break(self):
        """It holds whatever the runs had time for: the script cuts the break at the chip
        and drags a new one."""
        b = self._back_to_mode_1(["Break15 → NoScreen", "mode 1 → no NoScreen", "mode 2 → NoScreen"])
        self.assertEqual(b, Region.of(IntervalSet.of(Interval.closed_open(130, 140), Interval._mk(Pos(160) + EPS, Pos(175) + EPS))))

    def test_back_to_mode_1_inside_a_break_all_the_rules(self):
        """The same with the rules of UI/examples/fifteen_minute_break_modes.py."""
        b = self._back_to_mode_1(["Break15 → NoScreen",
                                  "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
                                  "NotOnComputer → no NoComputerUnlocked", "NotOnPhone → no NoPhoneUnlocked",
                                  "mode 1 → no NoScreen", "mode 2 → NoScreen & NoComputerUnlocked & NoPhoneUnlocked",
                                  "mode 3 → NoScreen & (NotOnComputer or NotOnPhone)"])
        self.assertEqual(b, Region.of(IntervalSet.of(Interval.closed_open(130, 140), Interval._mk(Pos(160) + EPS, Pos(175) + EPS))))


class ScreenBreaksTest(unittest.TestCase):
    """Several kinds of screen breaks together (constraints.ScreenBreaks), in seconds."""
    M = 60
    BREAKS = [("Break20s", 20, 20 * 60), ("Break5", 5 * 60, 60 * 60), ("Break15", 15 * 60, 120 * 60)]

    def starts(self, regions, kind, a, b):
        return [iv.lo.x for iv in regions[kind].on(a, b).intervals]

    def test_5_and_15(self):
        """A 5min break then a 15min one at the chip is one 15min break; then 1 hour to the
        next 5min one and 2 hours to the next 15min one: every 2h15."""
        k = ScreenBreaks(self.BREAKS[1:], rules=parse_rules(["Break5 → NoScreen", "Break15 → NoScreen"]))
        b = k.breaks(LineState.empty(), F(0))
        self.assertEqual(self.starts(b, "Break15", 0, 30000), [0, 8100, 16200, 24300])
        self.assertEqual(self.starts(b, "Break5", 0, 30000), [4500, 12600, 20700, 28800])
        self.assertEqual(b["Break15"].on(0, 1000).intervals[0].hi, Pos(900, 1))  # (0, 900]

    def test_the_three(self):
        """A 5min or 15min break puts off the 20s ones too."""
        k = ScreenBreaks(self.BREAKS, rules=parse_rules([f"{b} → NoScreen" for b, _, _ in self.BREAKS]))
        b = k.breaks(LineState.empty(), F(0))
        self.assertEqual(self.starts(b, "Break20s", 0, 8100), [2100, 3320, 6000, 7220])
        self.assertEqual(self.starts(b, "Break5", 0, 8100), [4500])
        self.assertEqual(self.starts(b, "Break15", 0, 16300), [0, 8100, 16200])

    def sleep(self):
        rs = parse_rules([f"{b} → NoScreen" for b, _, _ in self.BREAKS] + ["Sleep → NoScreen", "Sleep → NoScreen on [-3600, 0] of l"])
        night = Region(-3600, 82800, IntervalSet.of(Interval.closed_open(-3600, 25200)), 1, IntervalSet(),
                       86400, IntervalSet.of(Interval.closed_open(0, 28800)))
        return rs, apply_edit(rs, LineState.empty(), [Op.put("Sleep", night)], USER)

    def test_sleep(self):
        """"No screen" from 22:00 to 07:00 every day, read from the rules: every break waits
        after it; the 15min break that would start at 22:30 starts at 22:00, and the night
        gets no other break. The same every day."""
        rs, state = self.sleep()
        b = ScreenBreaks(self.BREAKS, rules=rs).breaks(state, F(25200))
        for day in (0, 86400, 2 * 86400):
            self.assertEqual(self.starts(b, "Break5", 25200 + day, 86400 + day),
                             [28800 + day, 36900 + day, 45000 + day, 53100 + day, 61200 + day, 69300 + day, 77400 + day])
            self.assertEqual(self.starts(b, "Break15", 25200 + day, 86400 + day),
                             [32400 + day, 40500 + day, 48600 + day, 56700 + day, 64800 + day, 72900 + day, 79200 + day])
            self.assertEqual(self.starts(b, "Break20s", 79200 + day, 112800 + day), [26400 + 86400 + day])
        unbound = ScreenBreaks(self.BREAKS).breaks(state, F(25200))  # without the rules: no sleep ahead
        self.assertTrue(self.starts(unbound, "Break15", 82800, 111600))

    def test_put_it_holds(self):
        """Once its edits are made, it asks for none: the breaks it reads are not its own."""
        rs, state = self.sleep()
        k = ScreenBreaks(self.BREAKS, rules=rs)
        after = apply_edit(rs, state, k.ops(state, F(25200)), GEN)
        self.assertEqual(k.ops(after, F(25200)), [])
        self.assertEqual(k.ops(after, F(30000)), [])

    def test_script_text(self):
        """Its text in the script is read back."""
        k = ScreenBreaks(self.BREAKS)
        self.assertEqual(str(parse_script("keep " + str(k)[5:], Script()).constraints[0]), str(k))


class ResourcesTest(unittest.TestCase):
    """A lower q: a run does less work (counted: time is noisy), and asks for no more runs."""

    @staticmethod
    def solves(q):
        import mode_rules
        import resolve
        import solver
        count = [0]
        real = solver.solve

        def counted(*a, **k):
            count[0] += 1
            return real(*a, **k)

        modules = (mode_rules, resolve, solver)
        for m in modules:
            m.solve = counted
        try:
            rs = parse_rules(["Break15 → NoScreen", "mode 1 → no NoScreen", "mode 2 → NoScreen"])
            gen = RulesGenerator(rs, PresenceRatioProblem({"Other": F(1, 4)}, 240), q, constraints=[FifteenMinuteBreaks()])
            gen.run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))
        finally:
            for m in modules:
                m.solve = real
        return count[0]

    def test_a_lower_q_solves_less(self):
        work = [self.solves(q) for q in (F(1, 10), F(3, 10), F(1, 2), F(7, 10), F(9, 10))]
        self.assertEqual(work, sorted(work), work)
        self.assertLess(work[0], work[-1])

    def test_the_text_does_not_depend_on_what_is_compiled(self):
        rs = parse_rules(["mode 1 → D"])
        low = RulesGenerator(rs, PresenceRatioProblem({}, 60), F(1, 10)).run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))[0]
        high = RulesGenerator(rs, PresenceRatioProblem({}, 60), F(9, 10)).run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))[0]
        self.assertGreater(low.covered[0][1], high.covered[0][1])  # a lower q reruns later, its text goes further
        self.assertLess(low.stretches[-1].b, high.stretches[-1].b)  # and it compiles less


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
        qs = [0, 0.001, 0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95, 0.999, 1]
        for q1, q2 in zip(qs, qs[1:]):
            self.assertLessEqual(budget(q1), budget(q2))
            self.assertGreater(rerun_distance(q1, H), rerun_distance(q2, H))
        for bad in (-0.1, 1.5):
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


class RerunRulesTest(unittest.TestCase):
    """The rerun rules of a script depend on the context, not on a formula alone."""

    @staticmethod
    def reruns(rules, targets, q=0.5, constraints=()):
        gen = RulesGenerator(parse_rules(rules), PresenceRatioProblem(targets, 100), q, constraints=constraints)
        script, _ = gen.run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))
        return script.rerun_triggers()

    @staticmethod
    def of(triggers, kind):
        return [t for t in triggers if isinstance(t, kind)]

    def test_nothing_to_optimise_only_the_user_edits(self):
        self.assertEqual(self.reruns(["Break15 → NoScreen"], {}, constraints=[FifteenMinuteBreaks()]), [Edited(True)])

    def test_mode_rules_are_compiled_ahead_by_a_run(self):
        t = self.reruns(["Break15 → NoScreen", "mode 1 → no NoScreen"], {}, constraints=[FifteenMinuteBreaks()])
        self.assertEqual(t, [ChipIn(F(25, 6), INF), Edited(True)])

    def test_a_constraint_that_goes_with_the_chip_needs_runs(self):
        keep = KeepOn(Op.put("F", Region.of(Interval.closed(1, 5))))
        self.assertEqual(self.of(self.reruns([], {}, constraints=[keep]), ChipIn), [ChipIn(F(75), INF)])

    def test_targets_without_modes_watch_the_score(self):
        t = self.reruns(["C → D"], {"A": F(1, 2)})  # (a rule: no best line found at once, ratio_plan.py)
        self.assertTrue(self.of(t, ScoreBelow))
        self.assertFalse(self.of(t, ChipIn))

    def test_the_best_line_needs_only_the_user_edits(self):
        """Found at once (rules_generator_requirements.md): nothing to watch but the user."""
        self.assertEqual(self.reruns([], {"A": F(1, 2)}), [Edited(True)])

    def test_only_the_modes_that_reach_the_target(self):
        rules = ["mode 1 → C", "mode 2 → no A", "mode 3 → B"]
        self.assertEqual([c.mode for c in self.of(self.reruns(rules, {"A": F(1, 2)}), ChipIn) if c.mode], ["2"])
        self.assertEqual([c.mode for c in self.of(self.reruns(rules, {"B": F(1, 2)}), ChipIn) if c.mode], ["3"])

    def test_a_mode_can_reach_the_target_through_the_element_rules(self):
        rules = ["mode 2 → X", "X → Y", "Y → no A", "mode 3 → B"]
        self.assertEqual([c.mode for c in self.of(self.reruns(rules, {"A": F(1, 2)}), ChipIn) if c.mode], ["2"])

    def test_a_lower_q_waits_longer_for_the_same_mode(self):
        rules = ["mode 2 → no A"]
        at = [next(c.lo for c in self.of(self.reruns(rules, {"A": F(1, 2)}, q), ChipIn) if c.mode) for q in (0.1, 0.5, 0.9)]
        self.assertEqual(at, sorted(at, reverse=True))
        self.assertGreater(at[0], at[2])


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
        heavier problem decides (in the search: with a rule, ratio_plan.py has no line)."""
        rs = parse_rules(["C → D"])
        ratio, length = PresenceRatioProblem({"A": F(1, 10)}, 20), MinimumLengthProblem({"A": 10}, 20)
        after, _ = self.run_once(rs, LineState.empty(), [(ratio, 10), (length, 1)])
        self.assertTrue(after.line.presence("A"))
        after, _ = self.run_once(rs, LineState.empty(), [(ratio, 1), (length, 10)])
        self.assertFalse(after.line.presence("A"))

    def test_the_best_line_meets_both_ratio_and_minimum_length(self):
        """With no rule, A at 10% everywhere after the chip: its ratio, and one interval
        longer than 10 (rules_generator_requirements.md), whatever the weights."""
        rs = parse_rules([])
        ratio, length = PresenceRatioProblem({"A": F(1, 10)}, 20), MinimumLengthProblem({"A": 10}, 20)
        for weights in ((10, 1), (1, 10)):
            after, stats = self.run_once(rs, LineState.empty(), [(ratio, weights[0]), (length, weights[1])])
            self.assertEqual(stats.chosen, "perfect script")
            self.assertEqual(after.line.vals["A"], {10: Region.of(Interval(0, INF, False, False))})


class SameResultTest(unittest.TestCase):
    """requirement.md: the same script, published at the same time, gives the same line once
    the chip stops, however often its loops iterate."""

    RULES = ["mode 2 → C", "A → B"]

    def script(self, gen):
        script = gen.run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))[0]
        kept = [e for e in script.events if not any(isinstance(a, RerunAction) for a in e.actions)]
        return script.with_events(kept)

    def line(self, step):
        rs = parse_rules(self.RULES)
        modes = ModeTimeline(0, "1", [(F(7, 3), "2"), (F(22, 3), "1"), (F(14), "2")])
        gen = RulesGenerator(rs, [], 0.5, constraints=[KeepOn(Op.put("D", Interval.open_closed(1, 3))), FifteenMinuteBreaks("E", "F", 2, 3)])
        sim = Simulation(LineState.empty(), gen, Chip([(0, 0), (20, 20)]), modes)
        sim.start()
        sim.replace_script(self.script(gen))
        t = F(0)
        while t < 30:
            t += step
            sim.advance(t)
        return sim.state.line

    def test_whatever_the_steps(self):
        lines = [self.line(step) for step in (F(1), F(1, 7), F(13, 4))]
        self.assertTrue(lines[0].kinds())
        self.assertEqual(lines[0], lines[1])
        self.assertEqual(lines[0], lines[2])


class ChangingTheScriptTest(unittest.TestCase):
    def simulation(self, text):
        sim = Simulation(LineState.empty(), RulesGenerator(parse_rules([]), [], 0.5), Chip([(0, 0), (20, 20)]), ModeTimeline(0, "1"))
        sim.start()
        sim.replace_script(parse_script(text, Script()))
        return sim

    def test_a_loop_adds_to_another_one(self):
        add = 'if the chip is at or after 2, then add to every 1 s: "if the chip is at or after 3, then put A 100% on [5, 6]".'
        sim = self.simulation(add)
        sim.advance(1)
        self.assertEqual(len(sim.script.loops), 1)
        sim.advance(4)
        self.assertEqual([loop.period for loop in sim.script.loops], [F(1, 20), F(1)])
        self.assertTrue(sim.state.line.presence("A"))

    def test_the_loops_apart_iterate_on_their_own(self):
        sim = self.simulation("every 1 s:\n  if the chip is at or after 3, then put A 100% on [5, 6].")
        sim.apart = True
        sim.advance(4)
        self.assertFalse(sim.state.line.presence("A"))
        sim.iterate(F(1))
        self.assertTrue(sim.state.line.presence("A"))

    def test_the_line_is_shown_again(self):
        sim = self.simulation("if the chip is at or after 3, then show the line.")
        sim.advance(4)
        self.assertEqual(sim.shown, 1)


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
        self.assertNotIn("the program starts", sim.script.text())  # the first script of the run is there at once
        self.assertIsNone(sim.edit_now([Op.put("A", Interval.closed(2, 3))]))
        self.assertIs(sim.request, first)  # the initial script does not answer edits
        chip.extend(1, 1)
        sim.advance(1)
        script, stats = first.gen.run(first.state, first.chip, first.known)
        self.assertTrue(sim.complete(first.id, script, stats))
        self.assertEqual(sim.script.events, script.events)
        self.assertEqual([(t, c) for t, c, _, _, _ in sim.log.runs], [(1, 0)])  # completed at time 1, from chip 0
        self.assertIsNone(sim.request)
        self.assertIsNone(sim.edit_now([Op.put("A", Interval.closed(5, 6))]))  # its script answers edits
        second = sim.request
        self.assertIsNotNone(second)
        self.assertIsNone(sim.edit_now([Op.put("A", Interval.closed(7, 8))]))
        third = sim.request
        self.assertNotEqual(third.id, second.id)  # a newer call: the older run's script is not used
        script2, stats2 = second.gen.run(second.state, second.chip, second.known)
        before = sim.script
        self.assertFalse(sim.complete(second.id, script2, stats2))
        self.assertIs(sim.script, before)

    def test_late_script_moved(self):
        """A run computes from chip 0 while the chip goes to 3 (background mode): the script
        published at the start of the run keeps the constraint meanwhile, and the run's
        script, put in place at 3, goes on from the line as it is: the break is dragged."""
        rs = parse_rules(["Future → S or no S", "S → R", "S → B on [0, 2] of p", "S → no R on (0, 6] of p", "mode 1 → no B"])
        future = Op.put("Future", Interval(0, INF, False, False))
        chip = Chip([(0, 0)])
        # horizon 180: the next run is asked for at 135 / 18 = 7.5, after the positions looked at
        sim = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 180), 0.5, constraints=[KeepOn(future)]),
                         chip, ModeTimeline(0, "1"))
        sim.background = True
        sim.start()
        req = sim.request
        chip.extend(3, 3)
        sim.advance(3)
        self.assertTrue(sim.state.line.presence("Future").contains(5))  # the constraint holds without the run
        script, stats = req.gen.run(req.state, req.chip, req.known)
        self.assertTrue(sim.complete(req.id, script, stats))
        self.assertTrue(sim.state.line.presence("Future").contains(4))
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
        self.assertEqual(sim.script.text(), "every 1/20 s:\n  if the program starts, then run the generator once, as soon as possible.")
        self.assertIsNone(sim.edit_now([Op.put("A", Interval.closed(2, 3))]))
        self.assertEqual(sim.log.runs, [])
        sim.start()
        self.assertEqual([(t, why) for t, _, why, _, _ in sim.log.runs], [(0, "start")])
        self.assertNotIn("the program starts", sim.script.text())
        sim.advance(3)
        self.assertEqual(len(sim.log.runs), 1)

    def test_rules(self):
        rs = parse_rules(["A → B"])
        chip = Chip([(0, 0)])
        sim = Simulation(LineState.empty(), RulesGenerator(rs, PresenceRatioProblem({}, 4), 0.5), chip, ModeTimeline(0, "1"))
        self.assertIsNone(sim.edit_now([Op.put("A", Interval.closed(2, 3))]))
        self.assertEqual(sim.state.line.kinds(), ["A", "B"])
        sim.set_generator(RulesGenerator(parse_rules(["A → C"]), PresenceRatioProblem({}, 4), 0.5))
        self.assertEqual(sim.state.line.kinds(), ["A", "C"])
        self.assertIsNotNone(sim.edit_now([Op.put("A", Interval.point(1)), Op.put_no("C", Interval.point(1))]))


if __name__ == "__main__":
    unittest.main()
