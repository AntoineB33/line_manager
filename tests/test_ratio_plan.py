"""The best line of the presence ratio targets and minimum lengths (scripts/ratio_plan.py),
on the whole line, found at once: the examples of the rules generator's logic."""

import time
import unittest
from fractions import Fraction as F

from helpers import Interval  # noqa: F401 (the paths of scripts/)
from generator import MinimumLengthProblem, PresenceRatioProblem, RulesGenerator
from intervals import INF, Interval as Iv
from line import LineState, Op
from modes import ModeTimeline
from region import Region
from resolve import apply_edit
from rules import parse_rules

X = F(3)  # the chip when the generator starts
HALF = F(1, 2)
FAST = 0.5  # seconds: "very quickly" (a few milliseconds here)


def generator(rules, targets, lengths=None, seed=0):
    problems = [(PresenceRatioProblem(targets), 1)]
    if lengths:
        problems.append((MinimumLengthProblem(lengths), 1))
    return RulesGenerator(parse_rules(rules), problems, 0.5, seed=seed)


def perfect(rules, targets, lengths=None, past=None, seed=0, since=-INF):
    """The line after the generator's script from X, and how long the run took; `past` is
    on (since, X]."""
    g = generator(rules, targets, lengths, seed)
    state = LineState.empty()
    if past:
        state = apply_edit(g.rules, state, [Op.put(past, Iv(since, X, False, True))], "user")
    began = time.perf_counter()
    script, stats = g.run(state, X, ModeTimeline(0, "1").known(X))
    took = time.perf_counter() - began
    return script, stats, took


def in_cycle(region, lo, hi, period=2, n=6):
    """region is (X + lo + k period, X + hi + k period] for every k >= 0, read at points:
    inside, at both ends, and between."""
    for k in range(n):
        a, b = X + lo + k * period, X + hi + k * period
        if region.contains(a) or not region.contains(F(a + b) / 2) or not region.contains(b):
            return False
        if region.contains(b + F(period - (hi - lo), 2)):
            return False
    return not region.contains(X)


def after():
    return Region.of(Iv(X, INF, False, False))


class PerfectScriptTest(unittest.TestCase):
    def check(self, script, stats, took, expected):
        """The line has `expected` ({kind: {value: region}}) and nothing else after X, the
        script is the perfect one (final, definitive everywhere), found quickly."""
        line = script.base.line
        self.assertEqual(stats.chosen, "perfect script")
        self.assertTrue(stats.final)
        self.assertEqual(stats.definitive, INF)
        self.assertLess(took, FAST)
        self.assertEqual(sorted(line.kinds()), sorted(expected))
        for k, vals in expected.items():
            self.assertEqual(set(line.vals[k]), set(vals), k)
            for v, want in vals.items():
                got = line.vals[k][v] & after()
                if isinstance(want, tuple):
                    self.assertTrue(in_cycle(got, *want), f"{k}: {got}")
                else:
                    self.assertEqual(got, want, k)
        self.assertEqual({type(t).__name__ for t in script.rerun_triggers()}, {"Edited"})  # only the user's edits

    def test_one_element_at_100_percent(self):
        self.check(*perfect([], {"A": 1}), {"A": {100: after()}})

    def test_two_elements_at_half_are_both_at_50_percent(self):
        self.check(*perfect([], {"A": HALF, "B": HALF}), {"A": {50: after()}, "B": {50: after()}})

    def test_two_exclusive_elements_with_no_minimum_length_are_refused(self):
        with self.assertRaises(ValueError):
            generator(["B → no A"], {"A": HALF, "B": HALF})

    def test_refused_whatever_the_other_rules_and_the_constraints(self):
        """With the breaks, their rules and mode rules too (screen_break_15_task.py): the
        check comes before any shortcut."""
        from constraints import Breaks
        rules = ["Break15 → NoScreen", "mode 1 → no NoScreen", "A → no B"]
        targets = [(PresenceRatioProblem({"A": HALF, "B": HALF}), 1)]
        with self.assertRaises(ValueError):
            RulesGenerator(parse_rules(rules), targets, 0.5, constraints=[Breaks()])
        with self.assertRaises(ValueError):  # the search alone too
            RulesGenerator(parse_rules(rules), targets, 0.5, constraints=[Breaks()], shortcuts=False)
        RulesGenerator(parse_rules(rules), targets + [(MinimumLengthProblem({"A": 60}), 1)], 0.5,
                       constraints=[Breaks()])  # a minimum length: a best line exists

    def test_two_exclusive_with_no_minimum_length_among_others_are_refused(self):
        """A, B, C at 1/3 that exclude each other, A at least 60: B and C alternate ever
        faster between the pieces of A. One of them at least 1 too: A, B, C take turns."""
        rules = ["A → no B", "A → no C", "B → no C"]
        third = F(1, 3)
        with self.assertRaises(ValueError):
            generator(rules, {"A": third, "B": third, "C": third}, {"A": 60})
        generator(rules, {"A": third, "B": third, "C": third}, {"A": 60, "B": 1})

    def test_one_exclusive_with_no_minimum_length_is_not_refused(self):
        generator(["A → no B"], {"A": HALF, "B": HALF}, {"A": 60})  # B's pieces come between A's

    def test_exclusive_with_an_element_with_no_target_is_not_refused(self):
        generator(["B → no C"], {"A": HALF, "B": HALF})

    def test_two_exclusive_elements_alternate_by_their_minimum_length(self):
        first = set()
        for seed in range(8):
            with self.subTest(seed=seed):
                script, stats, took = perfect(["B → no A"], {"A": HALF, "B": HALF}, {"A": 1, "B": 1}, seed=seed)
                a, b = (0, 1), (1, 2)
                if not script.base.line.presence("A").contains(X + HALF):
                    a, b = b, a
                first.add("A" if a == (0, 1) else "B")
                self.check(script, stats, took, {"A": {100: a}, "B": {100: b}})
        self.assertEqual(first, {"A", "B"})  # drawn at random

    def test_the_element_just_before_the_chip_comes_last(self):
        for seed in range(8):
            with self.subTest(seed=seed):
                script, stats, took = perfect(["B → no A"], {"A": HALF, "B": HALF}, {"A": 1, "B": 1}, past="A", seed=seed)
                self.check(script, stats, took, {"A": {100: (1, 2)}, "B": {100: (0, 1)}})
                self.assertTrue(script.base.line.presence("A").contains(X))  # the past stays

    def test_the_interval_at_the_chip_goes_on_to_its_minimum_length(self):
        """B at (X - 1/2, X]: B goes on to X + 1/2, then the cycle from there, B last (a run
        of the generator while the chip is in a piece must not cut it short)."""
        for seed in range(8):
            with self.subTest(seed=seed):
                script, stats, took = perfect(["B → no A"], {"A": HALF, "B": HALF}, {"A": 1, "B": 1}, past="B", seed=seed,
                                              since=X - HALF)
                line = script.base.line
                b = line.presence("B")
                self.assertTrue(b.contains(X + F(1, 4)) and b.contains(X + HALF))
                self.assertFalse(line.presence("A").contains(X + HALF))
                for k in range(5):  # A on (X + 1/2 + 2k, X + 3/2 + 2k], B on (X + 3/2 + 2k, X + 5/2 + 2k]
                    lo = X + HALF + 2 * k
                    self.assertTrue(line.presence("A").contains(lo + HALF) and line.presence("A").contains(lo + 1), k)
                    self.assertFalse(line.presence("A").contains(lo), k)
                    self.assertTrue(b.contains(lo + F(3, 2)) and b.contains(lo + 2), k)
                    self.assertFalse(b.contains(lo + HALF), k)
                self.assertLess(took, FAST)

    def test_the_same_seed_gives_the_same_script(self):
        lines = {str(perfect(["B → no A"], {"A": HALF, "B": HALF}, {"A": 1, "B": 1}, seed=5)[0].base.line.vals["A"][100])
                 for _ in range(3)}
        self.assertEqual(len(lines), 1)

    def test_a_user_element_after_the_chip_falls_back_to_the_search(self):
        g = generator([], {"A": 1})
        state = apply_edit(g.rules, LineState.empty(), [Op.put("A", Iv(5, 6))], "user")
        self.assertIsNone(g.plan.ops(state, X))
        _, stats = g.run(state, X, ModeTimeline(0, "1").known(X))
        self.assertNotEqual(stats.chosen, "perfect script")

    def test_other_rules_fall_back_to_the_search(self):
        for rules in (["A → B"], ["B → no A on [0, 1]"], ["mode 1 → A"]):
            with self.subTest(rules=rules):
                self.assertFalse(generator(rules, {"A": HALF, "B": HALF}, {"A": 1, "B": 1}).plan.ok)


class FillTest(unittest.TestCase):
    """Every target at ratio 1 (ratio_plan.Fill): wherever it can be after the chip, at
    once, whatever the other rules, the modes and the constraints."""

    def breaks(self, rules, q=0.5, targets=None, lengths=None):
        from constraints import Breaks
        g = RulesGenerator(parse_rules(["Break15 → NoScreen", "mode 1 → no NoScreen"] + rules),
                           [(PresenceRatioProblem(targets or {"Task": 1}), 1), (MinimumLengthProblem(lengths or {"Task": 1}), 1)],
                           q, constraints=[Breaks(length=2, pause=3)])
        began = time.perf_counter()
        stages = list(g.stages(LineState.empty(), F(0), ModeTimeline(0, "1").known(0)))
        return stages, time.perf_counter() - began

    def test_related_to_nothing_it_is_everywhere(self):
        stages, took = self.breaks([])
        self.assertEqual(len(stages), 1)
        self.assertEqual(stages[-1][0].base.line.presence("Task"), after_from(0))
        self.assertLess(took, 5)

    def test_it_is_everywhere_but_where_it_is_forbidden(self):
        """With "Break15 → no Task": every gap between the breaks, far past the window, at
        any q (the budget of candidates does not cut it)."""
        for q in (0, F(1, 2)):
            with self.subTest(q=q):
                stages, took = self.breaks(["Break15 → no Task"], q)
                line = stages[-1][0].base.line
                task, brk = line.presence("Task"), line.presence("Break15")
                self.assertTrue((task & brk).is_empty())
                for x in (F(5, 2), 4, F(101, 2), 1000, 10 ** 6 + F(1, 2)):  # breaks every 5: (0, 2], (5, 7], ...
                    self.assertNotEqual(task.contains(x), brk.contains(x), x)
                self.assertLess(took, 5)

    def test_below_1_and_forbidden_nowhere_it_is_at_its_ratio_everywhere(self):
        stages, took = self.breaks([], targets={"Task": HALF, "B": HALF})
        self.assertEqual(len(stages), 1)
        line = stages[-1][0].base.line
        self.assertEqual(line.vals["Task"], {50: after_from(0)})
        self.assertEqual(line.vals["B"], {50: after_from(0)})
        self.assertLess(took, 5)

    def test_below_1_and_forbidden_somewhere_the_search_decides(self):
        for rules in (["Break15 → no B"],):  # ("Task → no B": they alternate, at once)
            with self.subTest(rules=rules):
                stages, _ = self.breaks(rules, targets={"Task": HALF, "B": HALF})
                self.assertNotEqual(stages[-1][1].chosen, "the targets at once: each where it can be")
                line = stages[-1][0].base.line
                self.assertNotEqual(line.vals.get("B"), {50: after_from(0)})

    def test_exclusive_targets_alternate_whatever_the_other_rules(self):
        """A and B at 0.5, "A → no B", A at least 1, with the breaks and their rules: A and
        B alternate by 1 forever, at once (one stage)."""
        stages, took = self.breaks(["A → no B"], targets={"A": HALF, "B": HALF}, lengths={"A": 1})
        self.assertEqual(len(stages), 1)
        line = stages[-1][0].base.line
        a, b = line.presence("A"), line.presence("B")
        first, second = (a, b) if a.contains(HALF) else (b, a)
        for n in (0, 1, 7, 10 ** 5):
            self.assertTrue(first.contains(2 * n + HALF) and second.contains(2 * n + F(3, 2)), n)
            self.assertFalse(second.contains(2 * n + HALF) or first.contains(2 * n + F(3, 2)), n)
        self.assertLess(took, 30)

    def test_exclusive_targets_forbidden_elsewhere_leave_it_to_the_search(self):
        stages, _ = self.breaks(["A → no B", "Break15 → no A"], targets={"A": HALF, "B": HALF}, lengths={"A": 1})
        self.assertNotEqual(stages[-1][1].chosen, "the targets at once: each where it can be")

    def test_a_mode_rule_that_only_forbids_targets_keeps_the_best_line(self):
        """"mode 2 → NoScreen" with "NoScreen → no D": the mode only forbids D at the chip,
        the line found from what is known is the best, made again after the mode acts."""
        targets = [(PresenceRatioProblem({"D": F(3, 4), "E": F(1, 4)}), 1)]
        g = RulesGenerator(parse_rules(["mode 2 → NoScreen", "NoScreen → no D"]), targets, 0.5)
        self.assertTrue(g.fill.ok)
        g = RulesGenerator(parse_rules(["mode 2 → X", "X → D"]), targets, 0.5)
        self.assertFalse(g.fill.ok)  # through the element rules, it adds D

    def test_a_mode_rule_that_reaches_a_target_leaves_it_to_the_search(self):
        """"mode 1 → D": what the mode adds at the chip changes D's share."""
        g = RulesGenerator(parse_rules(["mode 1 → D"]), [(PresenceRatioProblem({"D": F(3, 4), "E": F(1, 4)}), 1)], 0.5)
        self.assertFalse(g.fill.ok)
        g = RulesGenerator(parse_rules(["mode 1 → C"]), [(PresenceRatioProblem({"D": F(3, 4), "E": F(1, 4)}), 1)], 0.5)
        self.assertTrue(g.fill.ok)


class RulesTest(unittest.TestCase):
    def test_no_of_a_group(self):
        """"no (...)" is its negation, made of literals."""
        from rules import parse_rule
        self.assertEqual(str(parse_rule("X → no (A or B or C)")), "X → no A & no B & no C")
        self.assertEqual(str(parse_rule("X → no (A & B)")), "X → no A or no B")
        with self.assertRaises(ValueError):
            parse_rule("X → no (A 50%)")

    def test_one_rule_can_exclude_several(self):
        """"A → no B & no C" is A excluding B and A excluding C."""
        third = F(1, 3)
        with self.assertRaises(ValueError):  # B and C, with no minimum length
            generator(["A → no B & no C", "B → no C"], {"A": third, "B": third, "C": third}, {"A": 60})
        g = generator(["A → no B & no C", "B → no C"], {"A": third, "B": third, "C": third}, {"A": 60, "B": 60})
        self.assertEqual([sorted(k) for k, _ in g.plan.groups], [["A", "B", "C"]])


class LaidTest(unittest.TestCase):
    """Targets that exclude each other, forbidden where other elements are: their turns go
    on in the stretches left, from one to the next (ratio_plan.Plan._laid)."""

    def test_the_turns_go_on_between_the_breaks(self):
        from constraints import Breaks
        rules = ["Break15 → NoScreen", "NoScreen → no (A or B)", "A → no B"]
        g = RulesGenerator(parse_rules(rules), [(PresenceRatioProblem({"A": HALF, "B": HALF}), 1),
                                               (MinimumLengthProblem({"A": 1, "B": 1}), 1)], 0.5,
                           constraints=[Breaks(length=2, pause=3)])
        began = time.perf_counter()
        script, stats = g.run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))
        took = time.perf_counter() - began
        self.assertEqual(stats.chosen, "the targets at once: each where it can be")
        line = script.base.line
        a, b, ns = line.presence("A"), line.presence("B"), line.presence("NoScreen")
        # breaks on (0, 2], (5, 7], ...: stretches of 3 between them, turns of 1
        for x in (F(1, 2), 1, F(3, 2), 6, F(1001, 2)):
            self.assertTrue(ns.contains(x) and not a.contains(x) and not b.contains(x), x)
        turns = [k for x in (F(5, 2), F(7, 2), F(9, 2), F(15, 2), F(17, 2), F(19, 2), F(25, 2), F(27, 2), F(29, 2))
                 for k in "AB" if line.presence(k).contains(x)]
        self.assertEqual(len(turns), 9)
        self.assertTrue(all(p != q for p, q in zip(turns, turns[1:])), turns)  # A and B take turns, across the breaks
        for n in (10, 1000):  # and so on forever
            self.assertTrue(any(line.presence(k).contains(5 * n + F(5, 2)) for k in "AB"), n)
        self.assertLess(took, 10)


class SearchTest(unittest.TestCase):
    """The search alone (no shortcut), A and B at 0.5 that exclude each other, at least 60:
    it fills its whole window, in short pieces (it used to keep 6 minutes of A and no B,
    cut to its first stage), quickly."""

    def search(self, window):
        g = RulesGenerator(parse_rules(["A → no B", "C → D"]), [(PresenceRatioProblem({"A": HALF, "B": HALF}), 1),
                           (MinimumLengthProblem({"A": 60, "B": 60}), 1)], 0.5, shortcuts=False, window=window)
        began = time.perf_counter()
        script, stats = g.run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))
        return script.base.line, stats, time.perf_counter() - began

    def test_it_fills_the_window_in_turns(self):
        for window, longest in ((2400, 60), (16200, 130)):
            with self.subTest(window=window):
                line, stats, took = self.search(window)
                self.assertEqual(stats.key[0], 0)  # the shares over the window are the targets
                for k in "AB":
                    ivs = line.presence(k).on(0, window).intervals
                    self.assertEqual(sum((iv.length for iv in ivs), F(0)), window / 2, k)
                    self.assertLessEqual(max(iv.length for iv in ivs), longest, k)  # not blocks
                    self.assertGreaterEqual(min(iv.length for iv in ivs), 60, k)
                self.assertLess(took, 10)


class LaterOperationTest(unittest.TestCase):
    def test_in_an_edit_of_the_generator_a_later_operation_wins(self):
        """Take A off (0, 10], then put A on (2, 4]: A on (2, 4] (as in an edit of the user)."""
        from intervals import Interval as Iv2
        rs = parse_rules(["A → B"])
        s = apply_edit(rs, LineState.empty(), [Op.put("A", Iv2(0, 10, False, True))], "generated")
        s = apply_edit(rs, s, [Op.take("A", Iv2(0, 10, False, True)), Op.put("A", Iv2(2, 4, False, True))], "generated")
        self.assertEqual(s.line.presence("A"), Region.of(Iv2(2, 4, False, True)))


class AsEarlyAsPossibleTest(unittest.TestCase):
    def place(self, constraints, chip=0):
        g = RulesGenerator(parse_rules([]), [], 0.5, constraints=constraints)
        script, _ = g.run(LineState.empty(), F(chip), ModeTimeline(0, "1").known(chip))
        return script.base.line.presence("X")

    def test_it_places_a_kind_by_itself(self):
        """AsEarlyAsPossible("X", length=2), no target: X everywhere from the chip; with
        MinimumGap("X", 3): (0, 2], then none for 3, (5, 7], ... forever."""
        from constraints import AsEarlyAsPossible, MinimumGap
        self.assertEqual(self.place([AsEarlyAsPossible("X", length=2)]).on(0, 100),
                         Region.of(Iv(0, 100, False, False)).on(0, 100))
        x = self.place([AsEarlyAsPossible("X", length=2), MinimumGap("X", 3)])
        for n in (0, 1, 2, 1000):
            a = 5 * n
            self.assertTrue(x.contains(a + 1) and x.contains(a + 2), n)
            self.assertFalse(x.contains(a + 3) or x.contains(a + 5), n)

    def test_it_comes_first(self):
        """Break15 1/9 and Free 8/9 that exclude each other: the stretch rule puts Free first
        (the largest target); with AsEarlyAsPossible("Break15"), Break15 comes first."""
        from constraints import AsEarlyAsPossible
        rules = parse_rules(["Break15 → no Free"])
        problems = [(PresenceRatioProblem({"Break15": F(1, 9), "Free": F(8, 9)}), 1), (MinimumLengthProblem({"Break15": 900}), 1)]
        for constraints, first in (((), "Free"), ((AsEarlyAsPossible("Break15"),), "Break15")):
            with self.subTest(first=first):
                g = RulesGenerator(rules, problems, 0.5, constraints=constraints)
                script, _ = g.run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))
                self.assertTrue(script.base.line.presence(first).contains(1))


class MinimumGapTest(unittest.TestCase):
    """constraints.MinimumGap: no interval of a kind within `gap` after the end of another
    one, a constraint of the rules generator (not a rule of the line)."""

    def gap(self):
        from constraints import MinimumGap
        return MinimumGap("Break15", 7200)

    def state(self, *pieces, owner="user"):
        from intervals import Interval as Iv2
        rs = parse_rules([])
        s = LineState.empty()
        for a, b, who in pieces:
            s = apply_edit(rs, s, [Op.put("Break15", Iv2(a, b, False, True))], who)
        return rs, s

    def test_where_it_is_kept(self):
        from intervals import Interval as Iv2
        rs, s = self.state((0, 900, "user"))
        apart = self.gap().apart(s.line.presence("Break15"))
        self.assertTrue(apart.contains(901) and apart.contains(8100) and not apart.contains(8101) and not apart.contains(450))

    def test_the_generator_s_too_close_goes(self):
        rs, s = self.state((0, 900, "user"), (5000, 5900, "generated"))
        ops = self.gap().ops(s, F(0))
        self.assertEqual([o.action for o in ops], ["take"])
        self.assertTrue(ops[0].region.contains(5450) and not ops[0].region.contains(450))

    def test_an_edit_of_the_user_too_close_is_refused(self):
        from simulation import against_constraints
        rs, s = self.state((0, 900, "user"), (5000, 5900, "user"))
        self.assertIsNotNone(against_constraints(rs, s, F(0), [self.gap()]))
        rs, s = self.state((0, 900, "user"), (8100, 9000, "user"))  # 2 hours after: kept
        self.assertIsNone(against_constraints(rs, s, F(0), [self.gap()]))

    def test_the_user_s_edit_wins_over_the_generator_s(self):
        """A break of the user too close after one of the generator: the generator's part
        too close goes, the user's stays."""
        from simulation import against_constraints
        rs, s = self.state((1000, 1900, "generated"), (5000, 5900, "user"))
        self.assertIsNone(against_constraints(rs, s, F(0), [self.gap()]))
        off = self.gap().ops(s, F(0))[0].region
        self.assertTrue(off.contains(1450) and not off.contains(5450))

    def test_after_long_enough_no_screen(self):
        """("Break15", 7200, after="NoScreen", at_least=900): 2 hours after 15 minutes or more
        of "no screen" from anywhere, none after a shorter one."""
        from constraints import MinimumGap
        from intervals import Interval as Iv2
        gap = MinimumGap("Break15", 7200, after="NoScreen", at_least=900)
        rs = parse_rules(["Break15 → NoScreen"])
        for (a, b), pushed in (((1000, 2800), True), ((1000, 1600), False)):  # 30 minutes, 10 minutes
            with self.subTest(no_screen=(a, b)):
                s = apply_edit(rs, LineState.empty(), [Op.put("NoScreen", Iv2(a, b))], "user")
                s = apply_edit(rs, s, [Op.put("Break15", Iv2(5000, 5900, False, True))], "generated")
                ops = gap.ops(s, F(0))
                self.assertEqual(bool(ops), pushed)
                if pushed:
                    self.assertTrue(ops[0].region.contains(5450))
        self.assertTrue(gap.apart(Region.of(Iv2(1000, 2800))).contains(2800 + 7200))
        self.assertTrue(gap.apart(Region.of(Iv2(1000, 1600))).is_empty())

    def test_a_run_after_a_break_waits_for_its_2_hours(self):
        """screen_break_15_rules.py's definitions, a run from chip 9000 (the break of
        (7200, 8100] behind it): the free time goes on to 15300, then a break, every 8100,
        found at once."""
        from constraints import MinimumGap
        rules = BREAK_RULES[:4] + ["Break15 → no Free", "Break15 is always 100%"] + BREAK_RULES[4:]
        g = RulesGenerator(parse_rules(rules), [(PresenceRatioProblem({"Break15": F(1, 9), "Free": F(8, 9)}), 1),
                                                 (MinimumLengthProblem({"Break15": 900}), 1)], 0.5,
                           constraints=[MinimumGap("Break15", 7200)])
        script, _ = g.run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))
        ops, base = g._fill(script.base, F(9000))
        self.assertIsNotNone(ops)  # found at once
        after = apply_edit(g.rules, base, ops, "generated")
        b, free = after.line.presence("Break15"), after.line.presence("Free")
        self.assertTrue(free.contains(9001) and free.contains(15299) and not b.contains(15299))
        for n in range(4):
            x = 15300 + 8100 * n
            self.assertTrue(b.contains(x + 1) and b.contains(x + 900) and not b.contains(x + 901), n)
        self.assertEqual(MinimumGap("Break15", 7200).ops(after, F(9000)), [])  # kept


BREAK_RULES = ["Break15 → NoScreen",
               "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
               "NotOnComputer → no NoComputerUnlocked", "NotOnPhone → no NoPhoneUnlocked",
               "mode 1 → no NoScreen", "mode 2 → NoScreen & NoComputerUnlocked & NoPhoneUnlocked",
               "mode 3 → NoScreen & (NotOnComputer or NotOnPhone)"]


class BreaksAndTasksTest(unittest.TestCase):
    """The 15min breaks of screen_break_15.py, with A and B at 0.5 that exclude each other
    and A at least 60."""

    def first_run(self, tasks: bool):
        """The line after the first run and how many times the line was solved."""
        import mode_rules
        import solver
        from constraints import Breaks
        rules = BREAK_RULES + (["A → no B"] if tasks else [])
        problems = [(PresenceRatioProblem({"A": HALF, "B": HALF}), 1), (MinimumLengthProblem({"A": 60}), 1)] if tasks else []
        g = RulesGenerator(parse_rules(rules), problems, 0.5,
                           constraints=[Breaks("Break15", "NoScreen", length=900, pause=7200)])
        n, real = [0], solver.solve

        def counted(*a, **k):
            n[0] += 1
            return real(*a, **k)
        from unittest import mock
        with mock.patch.object(solver, "solve", counted), mock.patch.object(mode_rules, "solve", counted):
            script, _ = g.run(LineState.empty(), F(0), ModeTimeline(0, "1").known(0))
        return script.base.line, n[0]

    def test_they_alternate_by_60(self):
        line, _ = self.first_run(True)
        a, b = line.presence("A"), line.presence("B")
        first, second = (a, b) if a.contains(30) else (b, a)
        for n in (0, 1, 2, 100, 10 ** 4):
            x = 120 * n
            self.assertTrue(first.contains(x + 1) and first.contains(x + 60), n)
            self.assertTrue(second.contains(x + 61) and second.contains(x + 120), n)
            self.assertFalse(second.contains(x + 30) or first.contains(x + 90), n)

    def test_the_tasks_do_not_slow_the_mode_rules(self):
        """About as many solves as without the tasks: the mode rules and the breaks read
        nothing of A and B, so their ends and their period (120, against 8100 for the
        breaks) must not cut the compiled runs nor make the sweep go further (it took 1249
        solves and a minute; 127 without the tasks)."""
        _, plain = self.first_run(False)
        _, tasks = self.first_run(True)
        self.assertLess(tasks, 2 * plain, (tasks, plain))


def after_from(x):
    return Region.of(Iv(x, INF, False, False))


class SumTest(unittest.TestCase):
    """The presence ratio targets sum to 1, else the definitions are refused."""

    def test_targets_that_do_not_sum_to_1_are_refused(self):
        for targets in ({"A": 1, "B": 1}, {"A": 1, "B": HALF}, {"A": HALF}, {"A": F(1, 3), "B": F(1, 3)}):
            with self.subTest(targets=targets):
                with self.assertRaises(ValueError):
                    RulesGenerator(parse_rules([]), [(PresenceRatioProblem(targets), 1)], 0.5)

    def test_the_targets_of_all_the_problems_together(self):
        with self.assertRaises(ValueError):
            RulesGenerator(parse_rules([]), [(PresenceRatioProblem({"A": 1}), 1), (PresenceRatioProblem({"B": 1}), 1)], 0.5)

    def test_no_target_is_no_ratio_problem(self):
        self.assertEqual(PresenceRatioProblem({}).targets, {})

    def test_the_window_refuses_them(self):
        """The definitions of an example (start, as in screen_break_15_task.py with A and B
        at 1): refused, and no window opens."""
        import io
        import os
        import sys
        from contextlib import redirect_stdout
        from unittest import mock
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "UI"))
        import app
        out = io.StringIO()
        with mock.patch("gui.run") as run, redirect_stdout(out):
            app.start(rules=[], problems=[(PresenceRatioProblem({"Task": 1}), 1)])  # (valid: to check the mock)
            self.assertTrue(run.called)
            run.reset_mock()
            app.start(rules=[], problems=[(PresenceRatioProblem({"A": 1, "B": 1}), 1)])
        self.assertFalse(run.called)
        self.assertIn("the definitions are refused: the presence ratio targets sum to 2, not 1", out.getvalue())


class NoHorizonTest(unittest.TestCase):
    """The targets are on the whole line: a problem has no horizon, and the window of the
    generator's search (when there is no perfect script) is its own."""

    def test_a_problem_takes_no_horizon(self):
        with self.assertRaises(TypeError):
            PresenceRatioProblem({"A": 1}, 100)
        with self.assertRaises(TypeError):
            PresenceRatioProblem({"A": 1}, horizon=100)
        with self.assertRaises(TypeError):
            MinimumLengthProblem({"A": 1}, 100)

    def test_the_examples_give_no_horizon(self):
        import glob
        import os
        examples = glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "UI", "examples", "*.py"))
        self.assertTrue(examples)
        for f in examples:
            with self.subTest(os.path.basename(f)):
                with open(f, encoding="utf-8") as text:
                    self.assertNotIn("horizon=", text.read())

    def test_the_search_window_follows_the_definitions(self):
        from constraints import Breaks
        from generator import SMALLEST_WINDOW
        rules = ["C → D"]  # (no perfect script)
        self.assertEqual(generator(rules, {"A": HALF, "B": HALF}).window, SMALLEST_WINDOW)
        self.assertEqual(generator(rules, {"A": HALF, "B": HALF}, {"A": 1000}).window, 4000)  # 4 times the longest minimum length
        g = RulesGenerator(parse_rules(rules), [(PresenceRatioProblem({"A": HALF, "B": HALF}), 1)], 0.5,
                           constraints=[Breaks(length=900, pause=7200)])
        self.assertEqual(g.window, 2 * 8100)  # twice a break and its pause
        self.assertTrue(all(p.window == g.window for p, _ in g.problems))  # its problems are bound to it


if __name__ == "__main__":
    unittest.main()
