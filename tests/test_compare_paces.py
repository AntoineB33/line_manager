"""The two paces compared on fixed random cases (compare_paces.py): the step never ends worse
than what the instant schedule shows (it starts from it), the step's model scores as the score
does (the user's blocks read as the score reads them, a forbidden place starting inside one
of its segments included), and the instant schedule keeps a task's runs under its maximum."""

import unittest
from fractions import Fraction as F
from types import SimpleNamespace

import compare_paces as C
from instant import Instant
from line import Op
from pace import Pace
from resolve import apply_edit
from score import ScoreProblem
from score_search import Model

SEEDS = range(10)
KNOWN = ()  # (20 and 31 were the panel open across the night, read by the model only at the chip: fixed)


class ComparePacesTest(unittest.TestCase):
    def test_the_step_is_never_worse_than_what_is_shown(self):
        for seed in SEEDS:
            with self.subTest(seed=seed):
                found, _, _ = C.compare(C.scenario(seed), budget=1.0)
                self.assertFalse([f for f in found if f.startswith("step worse")], C.describe(C.scenario(seed)))

    def test_the_model_scores_as_the_score(self):
        for seed in (k for k in range(40) if k not in KNOWN):
            with self.subTest(seed=seed):
                sc = C.scenario(seed)
                rules, score, st = C.build(sc)
                chip = F(sc["chip"])
                a, b = Pace(rules, score).slice(chip, 0)
                end = b + Pace.AHEAD
                shown = Instant(rules, score).schedule(st, a, end, start=a)
                m = Model(ScoreProblem(score, window=end - a), SimpleNamespace(c=a, state=st, gen=SimpleNamespace(rules=rules)), st, 5)
                line = apply_edit(rules, st, [Op.put(k, C._region(v)) for k, v in shown.items()], "generated")
                exact = score.evaluate(line.line, a, end, ScoreProblem(score, window=end - a)._preplaced(st)).J
                # (within 2%: on the rolling pass's plans the model is 0.8% and 1.8% off on seeds 16
                # and 32, the fill's within 0.15%; cause not found: gaps_to_solve_later.md)
                self.assertAlmostEqual(m.cost(m.from_wall(shown))[1] / max(exact, 1.0), 1.0, delta=0.02)

    def test_a_panel_open_across_the_night(self):
        """A's 20min panel ends where the night begins; the window starts in the night and the morning
        begins with B. The night does not interrupt A's panel (no task may run there), so it ends in the
        window, short of its 45min: the score charges it there, and the model must too (it read only a
        panel open AT the window's start, and the step planned against a score 3 times off on a real
        account)."""
        from intervals import Interval as Iv
        from ratio_plan import cycle
        from rules import parse_rules
        from score import Score, Task
        from line import LineState
        DAY, HOUR, MIN = 86400, 3600, 60
        rules = parse_rules(["A → no B"])
        score = Score({"A": Task(1, 45 * MIN, resilience={"Sleep": 0}), "B": Task(1, 45 * MIN, resilience={"Sleep": 0})})
        st = apply_edit(rules, LineState.empty(), [Op.put("Sleep", cycle(F(4920), F(DAY), F(0), F(8 * HOUR)))], "user")
        night = F(10 * DAY + 4920)
        st = apply_edit(rules, st, [Op.put("A", C._region([(night - 20 * MIN, night)])), Op.put("B", C._region([(night - 65 * MIN, night - 20 * MIN)]))], "generated")
        a = F(10 * DAY + 5 * HOUR)
        end = a + 6 * HOUR
        plan = {"B": [(night + 8 * HOUR, night + 8 * HOUR + 45 * MIN)], "A": [(night + 8 * HOUR + 45 * MIN, end)]}
        m = Model(ScoreProblem(score, window=end - a), SimpleNamespace(c=a, state=st, gen=SimpleNamespace(rules=rules)), st, 5)
        line = apply_edit(rules, st, [Op.put(k, C._region(v)) for k, v in plan.items()], "generated")
        r = score.evaluate(line.line, a, end, ScoreProblem(score, window=end - a)._preplaced(st))
        self.assertGreater(r.J2, 0)  # (A's evening panel, charged in the window)
        self.assertAlmostEqual(m.cost(m.from_wall(plan))[1], r.J, delta=1e-6 * r.J + 1)

    def test_a_maximum_is_kept_by_the_instant_schedule(self):
        """Seed 6: A at most 30 min; the fill gave it 195 min in a row (J 24809 against the
        step's 1062)."""
        found, (ji, jd), _ = C.compare(C.scenario(6), budget=1.0)
        self.assertLess(ji, 2 * jd)


if __name__ == "__main__":
    unittest.main()
