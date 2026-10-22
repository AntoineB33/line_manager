"""Tests: python -m unittest test_rules_generation   (RULESGEN_SEEDS=n for more random runs)."""
from __future__ import annotations

import math
import multiprocessing
import os
import random
import tempfile
import time
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction

from rules_generation_logic import (
    Add,
    Follow,
    Machine,
    Move,
    Ratio,
    Region,
    Rel,
    Remove,
    Resources,
    Rule,
    Text,
    TrackManager,
    ahead,
    at_or_behind,
    forbids,
    implies,
    restrict,
    usable_cpus,
)

# isort: split
# reverse_logic is only importable once chip (via rules_generation_logic) has extended sys.path.
from reverse_logic import LineSystem, implies_any

SEEDS = int(os.environ.get("RULESGEN_SEEDS", "20"))
F = Fraction


def once(*edits):
    """A rule that makes `edits` at the install position only."""
    return Rule(lambda p, mode, state: list(edits))


@dataclass(frozen=True)
class PutAhead:
    """Random edits to the right of the chip (a function of position and mode)."""
    seed: int

    def __call__(self, position, mode, state):
        rng = random.Random(hash((self.seed, position, mode)))
        for _ in range(rng.randint(0, 2)):
            a = position + F(rng.randint(1, 12), 2)
            region = Region.point(a) if rng.random() < 0.4 else Region.closed(a, a + F(rng.randint(1, 6), 2))
            kind = rng.choice("ABC")
            yield Add(kind, region) if rng.random() < 0.7 else Remove(kind, region)


def random_condition(rng: random.Random, start: Fraction):
    if rng.random() < 0.5:
        return None
    lo = Rel(F(rng.randint(-6, 0), 2)) if rng.random() < 0.7 else start + rng.randint(0, 6)
    hi = Rel(F(rng.randint(1, 6), 2)) if rng.random() < 0.7 else start + rng.randint(4, 12)
    return Ratio(rng.choice("ABF"), lo, hi, F(rng.randint(0, 4), 4), rng.random() < 0.5)


def random_text(rng: random.Random, name: str, start: Fraction) -> Text:
    rules = [Rule(PutAhead(rng.randrange(10 ** 6)),
                  Region.interval(start + rng.randint(0, 8), start + rng.randint(9, 16),
                                  rng.random() < 0.6, False),
                  rng.choice((None, {1}, {2})), when=random_condition(rng, start))
             for _ in range(rng.randint(0, 2))]
    follow = []
    for _ in range(rng.randint(0, 2)):
        lo = Rel(F(rng.randint(-2, 3), 2)) if rng.random() < 0.7 else start + rng.randint(1, 12)
        hi = Rel(F(rng.randint(2, 6), 2)) if rng.random() < 0.7 else start + rng.randint(4, 16)
        follow.append(Follow(rng.choice("FXA"), lo, hi, rng.random() < 0.5, rng.random() < 0.5,
                             modes=rng.choice((None, {1}, {2}))))
    return Text(name, rules=rules, follow=follow)


class ChipTests(unittest.TestCase):
    def test_chip_never_moves_left(self):
        m = TrackManager(initial_position=3)
        with self.assertRaises(ValueError):
            m.update_chip(2)
        m.update_chip(3)  # it can stop
        self.assertEqual(m.chip.position, 3)

    def test_unknown_mode_refused(self):
        with self.assertRaises(ValueError):
            TrackManager(modes=(1, 2), initial_mode=3)
        m = TrackManager(modes=(1, 2))
        with self.assertRaises(ValueError):
            m.update_chip(2, [(1, 3)])
        self.assertEqual(m.chip.position, 0)

    def test_transitions_are_told_as_the_chip_meets_them(self):
        m = TrackManager(modes=("a", "b"), initial_mode="a")
        m.update_chip(2, [(1, "b"), (F(3, 2), "a")])
        self.assertEqual([m.chip.mode_at(q) for q in (0, F(99, 100), 1, F(149, 100), F(3, 2))],
                         ["a", "a", "b", "b", "a"])  # the new mode applies from the transition on
        for bad in ([(2, "b")], [(3, "b")], [(F(5, 2), "b"), (F(9, 4), "a")]):
            with self.assertRaises(ValueError):  # at or behind the chip, past its new position, not increasing
                m.update_chip(F(5, 2), bad)
        self.assertEqual((m.chip.position, m.chip.transitions), (2, [(1, "b"), (F(3, 2), "a")]))
        m.update_chip(3, [(3, "b")])  # a transition at the new position applies there
        self.assertEqual(m.chip.mode, "b")
        self.assertFalse(hasattr(Text("t"), "transitions"))  # a text cannot predict, nor decide, them

    def test_rules_run_at_the_first_position_of_every_stretch(self):
        seen = []

        def note(p, mode, state):
            seen.append((p, mode))
            return ()

        m = TrackManager(modes=(1, 2))
        m.switch_rules_text(Text("t", rules=[Rule(note, Region.interval(1, 5, True, False), {2}),
                                             Rule(note, Region.point(F(7, 2)))]))
        self.assertEqual(seen, [])
        # One move through everything: each stretch start is met, none is skipped.
        m.update_chip(6, [(F(1, 2), 2), (2, 1), (3, 2), (5, 1)])
        self.assertEqual(seen, [(1, 2), (3, 2), (F(7, 2), 2)])
        seen.clear()
        m.switch_rules_text(Text("u", rules=[Rule(note, Region.interval(5, 9, True, False))]))
        self.assertEqual(seen, [(6, 1)])  # installed inside a stretch: it runs at the install

    def test_a_stretch_open_on_the_left_runs_as_the_chip_enters_it(self):
        seen = []

        def note(p, mode, state):
            seen.append(p)
            return [Add("A", Region.point(p + 1))]

        m = TrackManager()
        m.switch_rules_text(Text("t", rules=[Rule(note, Region.open(1, 2) | Region.open(2, 3))]))
        m.update_chip(1)
        self.assertEqual(seen, [])  # at 1 the chip is not in the stretch yet
        m.update_chip(5)
        self.assertEqual(seen, [1, 2])  # entering (1, 2), then (2, 3): two stretches
        self.assertEqual(m.track()["A"], Region.points(2, 3))

    def test_conditions_on_the_state_are_watched_at_every_position(self):
        """"if D exceeds a presence ratio of 3/4 over the last 2 units": on [p - 2, p],
        D on [2, 4] covers p - 2 for p in [2, 4], and 6 - p for p in [4, 6], so the
        condition holds exactly on (7/2, 9/2)."""
        seen = []

        def note(p, mode, state):
            seen.append(p)
            return ()

        text = Text("t", rules=[Rule(note, when=Ratio("D", Rel(-2), Rel(0), F(3, 4)))])
        for steps in ([10], [F(i, 3) for i in range(1, 31)], [F(7, 2), F(9, 2), 10]):
            seen.clear()
            m = TrackManager(initial_elements=[("D", Region.closed(2, 4))])
            m.switch_rules_text(text)
            for q in steps:
                m.update_chip(q)
            self.assertEqual(seen, [F(7, 2)], steps)
        seen.clear()
        m = TrackManager(initial_elements=[("D", Region.closed(2, 4))])
        m.switch_rules_text(Text("u", rules=[Rule(note, when=Ratio("D", Rel(-2), Rel(0), F(3, 4), at_least=True))]))
        m.update_chip(10)
        self.assertEqual(seen, [F(7, 2)])  # holds on [7/2, 9/2]: closed, it runs at 7/2 too
        seen.clear()
        m = TrackManager(initial_elements=[("D", Region.closed(0, 4))])
        m.switch_rules_text(Text("v", rules=[Rule(note, Region.interval(3, 9, True, False), {1},
                                                  when=Ratio("D", 0, 4, F(1, 2)))]))
        m.update_chip(5, [(4, 2), (F(9, 2), 1)])  # holds where the mode is 1: from 3, then again from 9/2
        self.assertEqual(seen, [3, F(9, 2)])

    def test_rules_get_the_state_of_the_line_without_the_continuous_edit(self):
        seen = []

        def rules(p, mode, state):
            seen.append(("rules", dict(state)))
            return [Add("B", Region.point(p + 3))] if "A" in state else []

        def rerun(p, mode, state):
            seen.append(("rerun", dict(state)))
            return "U" in state
        m = TrackManager(initial_elements=[("A", Region.point(5))])
        m.switch_rules_text(Text("t", rules=[Rule(rules)], follow=[Follow("X", Rel(1), Rel(2))], rerun=rerun))
        a, b = {"A": Region.point(5)}, {"A": Region.point(5), "B": Region.point(3)}
        self.assertEqual(seen, [("rules", a), ("rerun", b)])
        self.assertEqual(m.track()["X"], Region.closed(1, 2))
        self.assertFalse(m.rerun)
        seen.clear()
        m.user_edit([Add("U", Region.point(9))])  # a user edit asks the text again, not its rules
        self.assertEqual([w for w, _ in seen], ["rerun"])
        self.assertNotIn("X", seen[0][1])
        self.assertTrue(m.rerun)
        m.update_chip(1)
        self.assertTrue(m.rerun)
        m.switch_rules_text(Text("u", rules=[Rule(where=Region.point(2), rerun=True)]))
        self.assertFalse(m.rerun)
        m.update_chip(3)  # a rule with `rerun` ran
        self.assertTrue(m.rerun)


class TrackTests(unittest.TestCase):
    def test_edits_must_be_strictly_right_of_the_chip(self):
        for bad in (Region.point(0), Region.closed(0, 1), Region.closed(-2, -1)):
            m = TrackManager()
            with self.assertRaises(ValueError):
                m.switch_rules_text(Text("t", rules=[once(Add("A", Region.point(5)), Add("A", bad))]))
            self.assertEqual(dict(m.track()), {})
            self.assertIsNone(m.current_text_id)
        m = TrackManager()
        m.switch_rules_text(Text("t", rules=[once(Add("A", Region.interval(0, 1, left_closed=False)))]))
        self.assertEqual(dict(m.track()), {"A": Region.interval(0, 1, left_closed=False)})

    def test_refused_edit_is_skipped(self):
        m = TrackManager()
        m.switch_rules_text(Text("t", rules=[once([Remove("A", Region.point(5))], [Add("B", Region.point(6))])]))
        self.assertEqual(dict(m.track()), {"B": Region.point(6)})
        self.assertEqual([r.text_id for r in m.refused], ["t"])

    def test_switch_reverses_right_side_only_and_cuts_at_the_chip(self):
        m = TrackManager(initial_elements=[("I", Region.point(10))])
        m.switch_rules_text(Text("alpha", rules=[once([Add("A", Region.point(2))], [Add("A", Region.closed(3, 6))],
                                                      [Add("A", Region.point(8))])]))
        m.update_chip(4)
        m.switch_rules_text(Text("beta"))
        # {2} is passed, [3, 6] is cut at the chip, {8} is gone; the initial element stays.
        self.assertEqual(dict(m.track()), {"A": Region.point(2) | Region.closed(3, 4), "I": Region.point(10)})

    def test_left_side_keeps_what_it_requires_on_the_right(self):
        m = TrackManager(rules=[implies("A", ("B", 2))])
        m.switch_rules_text(Text("alpha", rules=[once(Add("A", Region.point(1)))]))
        self.assertEqual(dict(m.track()), {"A": Region.point(1), "B": Region.point(3)})
        m.update_chip(2)
        m.switch_rules_text(Text("beta"))
        self.assertEqual(dict(m.track()), {"A": Region.point(1), "B": Region.point(3)})

    def test_switch_brings_back_what_the_text_had_removed(self):
        m = TrackManager(initial_elements=[("U", Region.closed(5, 6))])
        m.switch_rules_text(Text("alpha", rules=[once(Remove("U", Region.closed(5, 6)))]))
        self.assertEqual(dict(m.track()), {})
        m.switch_rules_text(Text("beta"))
        self.assertEqual(dict(m.track()), {"U": Region.closed(5, 6)})

    def test_user_edits_that_require_the_text_are_reversed_too(self):
        m = TrackManager()
        m.switch_rules_text(Text("alpha", rules=[once(Add("A", Region.point(5)))]))
        moved = m.user_edit([Move("A", Region.point(5), 2)])  # moves what alpha placed
        kept = m.user_edit([Add("U", Region.point(9))])
        m.switch_rules_text(Text("beta"))
        self.assertEqual(dict(m.track()), {"U": Region.point(9)})
        self.assertEqual(m.user_edits, [kept])
        self.assertNotIn(moved, m.user_edits)

    def test_only_the_user_changes_the_left_side(self):
        m = TrackManager(rules=[implies("A", ("B", -3))])
        m.update_chip(2)
        m.user_edit([Add("A", Region.point(1))])  # its B at -2 too: the user may
        self.assertEqual(dict(m.track()), {"A": Region.point(1), "B": Region.point(-2)})
        m.switch_rules_text(Text("t", rules=[once(Add("A", Region.point(4)))]))  # its B would be at 1
        self.assertEqual(dict(m.track()), {"A": Region.point(1), "B": Region.point(-2)})
        self.assertEqual(len(m.refused), 1)

    def test_failed_switch_changes_nothing(self):
        m = TrackManager()
        m.switch_rules_text(Text("alpha", rules=[once(Add("A", Region.point(3)))]))

        def broken(position, mode, state):
            yield Add("B", Region.point(5))
            raise RuntimeError("bad text")

        with self.assertRaises(RuntimeError):
            m.switch_rules_text(Text("beta", rules=[Rule(broken)]))
        self.assertEqual(dict(m.track()), {"A": Region.point(3)})
        self.assertEqual(m.current_text_id, "alpha")


class ContinuousTests(unittest.TestCase):
    """What the chip leaves: what each point had when the chip reached it."""

    def run_steps(self, text, steps, transitions=(), **manager):
        m = TrackManager(**manager)
        m.switch_rules_text(text)
        told = list(transitions)
        for q in steps:
            m.update_chip(q, [t for t in told if m.chip.position < t[0] <= q])
        return m

    def test_an_edit_at_the_chip_paints_its_path_whatever_the_steps(self):
        text = Text("t", follow=[Follow("X", Rel(0), Rel(1), lo_closed=False)])
        for steps in ([F(i, 2) for i in range(1, 9)], [2, 4], [4], [F(1, 3), F(17, 5), 4]):
            m = self.run_steps(text, steps)
            self.assertEqual(dict(m.track()), {"X": Region.interval(0, 5, False, True)}, steps)

    def test_an_edit_ahead_of_the_chip_leaves_nothing(self):
        text = Text("t", follow=[Follow("X", Rel(1), Rel(2))])
        m = self.run_steps(text, [F(1, 2), 2])
        self.assertEqual(dict(m.track()), {"X": Region.closed(3, 4)})
        m.switch_rules_text(Text("u"))
        self.assertEqual(dict(m.track()), {})

    def test_from_the_chip_to_a_fixed_position(self):
        text = Text("t", follow=[Follow("A", Rel(0), 3)])  # "set A from the chip to 3"
        for steps in ([1, 2, 5], [5]):
            m = self.run_steps(text, steps)
            self.assertEqual(dict(m.track()), {"A": Region.interval(0, 3, False, True)})

    def test_modes_stop_it_exactly_at_the_transitions(self):
        text = Text("t", follow=[Follow("X", Rel(0), Rel(1), lo_closed=False, modes={2})])
        for steps in ([F(i, 4) for i in range(1, 25)], [6]):
            m = self.run_steps(text, steps, [(1, 2), (F(5, 2), 1), (4, 2), (5, 1)], modes=(1, 2))
            self.assertEqual(dict(m.track()), {"X": Region.interval(1, F(5, 2), False, True)
                                               | Region.interval(4, 5, False, True)}, steps)

    def test_rules_reaching_behind_the_chip(self):
        # X ahead makes Y one unit to the left of it, so Y lands at the chip.
        text = Text("t", follow=[Follow("X", Rel(F(1, 2)), Rel(1))])
        results = {str(dict(self.run_steps(text, steps, rules=[implies("X", ("Y", -1))]).track()))
                   for steps in ([F(i, 4) for i in range(1, 17)], [1, 4], [4])}
        self.assertEqual(len(results), 1, results)

    def test_an_edit_sustained_by_its_trace(self):
        """Shown or hidden on, both are consistent: it is shown (SEMANTICS.md,
        "What cannot be met strictly", 3).  Without the Y it needs, it is refused."""
        text = Text("t", follow=[Follow("X", Rel(0), Rel(2), lo_closed=False)])
        m = self.run_steps(text, [1, 3], rules=[implies("X", ("Y", -1))], initial_elements=[("Y", Region.closed(-1, 0))])
        self.assertEqual(dict(m.track()), {"X": Region.interval(0, 5, False, True), "Y": Region.closed(-1, 4)})
        m = self.run_steps(text, [1, 3], rules=[implies("X", ("Y", -1))])
        self.assertEqual((dict(m.track()), len(m.refused)), ({}, 1))  # refused once, where it stops being shown

    def test_elements_that_repeat_forever(self):
        """With "A -> B or C" and "no B 1 unit right of B", A on [0, +inf) makes B and
        C alternate forever; the chip goes over them like over any element."""
        from region import INF
        ray = Region(((F(0), True, INF, False),))
        unit = Region.interval(0, 1, True, False)
        results = []
        for steps in ([F(7, 2)], [F(1, 3), 1, F(5, 2), F(7, 2)]):
            m = TrackManager(rules=[implies_any("A", ["B", "C"]), forbids("B", ("B", 1))])
            m.user_edit([Add("A", ray)])
            m.switch_rules_text(Text("t", follow=[Follow("X", Rel(0), Rel(1), lo_closed=False)]))
            for q in steps:
                m.update_chip(q)
            self.assertEqual(m.track()["B"], Region.tiled(unit, 2, 0, None))
            self.assertEqual(m.track()["X"], Region.interval(0, F(9, 2), False, True))
            results.append(dict(m.track()))
        self.assertEqual(results[0], results[1])

    def test_random_moves_do_not_depend_on_the_steps(self):
        for seed in range(SEEDS):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                rules = [[], [implies("A", ("B", 1))], [forbids("C", ("A", -1)), implies("F", ("C", -2))]][seed % 3]
                script = []  # ("move", q, transitions) | ("text", Text) | ("user", ops)
                p = F(0)
                for step in range(8):
                    t = rng.random()
                    if t < 0.3:
                        script.append(("text", random_text(rng, f"t{step}", p)))
                    elif t < 0.4:
                        a = p + F(rng.randint(1, 10), 2)
                        script.append(("user", [Add(rng.choice("ABU"), Region.closed(a, a + 1))]))
                    else:
                        q = p + F(rng.randint(1, 6), 2)
                        tr = sorted({p + F(rng.randint(1, int((q - p) * 4)), 4) for _ in range(rng.randint(0, 2))})
                        script.append(("move", q, [(x, rng.choice((1, 2))) for x in tr]))
                        p = q
                coarse, fine = self._play(script, rules, None), self._play(script, rules, rng)
                self.assertEqual(coarse, fine)

    def _play(self, script, rules, rng):
        m = TrackManager(modes=(1, 2), rules=rules, initial_elements=[("A", Region.closed(2, 3))])
        out = []
        for item in script:
            try:
                if item[0] == "text":
                    m.switch_rules_text(item[1])
                elif item[0] == "user":
                    m.user_edit(item[1])
                else:
                    _, q, tr = item
                    stops = [] if rng is None else sorted(
                        {m.chip.position + (q - m.chip.position) * F(rng.randint(1, 7), 8) for _ in range(3)})
                    for s in [*stops, q]:
                        m.update_chip(s, [t for t in tr if m.chip.position < t[0] <= s])
            except ValueError:
                pass
            out.append(dict(m.track()))
        return out

    def test_random_runs(self):
        for seed in range(SEEDS):
            with self.subTest(seed=seed):
                self._random_run(random.Random(seed), [[], [implies("A", ("B", 2)), forbids("C", ("A", -1)),
                                                           implies("F", ("C", -2))]][seed % 2])

    def _random_run(self, rng: random.Random, rules):
        m = TrackManager(modes=(1, 2), rules=rules, initial_elements=[("A", Region.closed(2, 3))])
        frozen: list[tuple[Fraction, dict]] = []  # (position, track on (-inf, position])
        for step in range(15):
            p0 = m.chip.position
            t = rng.random()
            if t < 0.25:
                if not rules:
                    # Once the previous text is reversed, the right side is what
                    # the remaining user edits make it.
                    m.switch_rules_text(Text("empty"))
                    replay = LineSystem()
                    for i in m.user_edits:
                        replay.apply(m.line._edits[i].ops)
                    self.assertEqual(restrict(m.track(), ahead(p0)), restrict(replay.state(), ahead(p0)))
                m.switch_rules_text(random_text(rng, f"t{step}", p0))
            elif t < 0.4:
                a = p0 + F(rng.randint(-4, 10), 2)
                mine = m.track().get("A", Region()) & ahead(p0)
                ops = ([Move("A", mine, F(rng.randint(1, 4), 2))] if mine and rng.random() < 0.5
                       else [Add(rng.choice("ABU"), Region.closed(a, a + 1))])
                try:
                    m.user_edit(ops)
                    frozen.clear()  # only the user changes the left side
                except ValueError:
                    pass
            else:
                q = p0 + F(rng.randint(0, 4), 2)
                m.update_chip(q, [(q, rng.choice((1, 2)))] if q > p0 and rng.random() < 0.5 else [])
            self.assertEqual(m.line.ruleset.violations(m.line.state()), [])
            frozen.append((m.chip.position, restrict(m.track(), at_or_behind(m.chip.position))))
            for q, past in frozen:  # the left side never changes
                self.assertEqual(restrict(m.track(), at_or_behind(q)), past)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class ManualJob:
    def __init__(self, call: Callable[[], Text | None], clock: FakeClock):
        self.call, self.clock = call, clock
        self.out: tuple[float, Text | None, str | None] | None = None
        self.stopped: bool = False
        self.stopped_at: float | None = None

    def done(self):
        return self.out is not None or self.stopped

    def result(self):
        assert self.out is not None
        return self.out

    def stop(self):
        if self.out is None and not self.stopped:
            self.stopped, self.stopped_at = True, self.clock()


class ManualRunner:
    """Runs a job in this process only when the test calls `finish`, so the
    test chooses what happens on the line, and on the clock, during a run."""
    def __init__(self, clock: FakeClock):
        self.clock = clock
        self.jobs: list[ManualJob] = []

    def start(self, optimizer, view, deadline, resources) -> ManualJob:
        job = ManualJob(lambda: optimizer.generate(view, deadline, resources), self.clock)
        self.jobs.append(job)
        return job

    def pending(self) -> list[ManualJob]:
        return [j for j in self.jobs if not j.done()]

    def finish(self) -> None:
        """The oldest job returns now, unless it was stopped."""
        job = self.jobs.pop(0)
        if job.stopped:
            return
        try:
            job.out = (self.clock(), job.call(), None)
        except RuntimeError as err:
            job.out = (self.clock(), None, repr(err))


class Scripted:
    """An optimizer whose decisions the test chooses: `budget(view, resources)`
    and `text(view, deadline)`, or constants."""
    def __init__(self, budget=None, text=None):
        self.budget, self.text = budget, text
        self.plans = []      # (view, resources) of every call to plan
        self.generated = []  # resources of every run

    def plan(self, view, resources):
        self.plans.append((view, resources))
        return self.budget(view, resources) if callable(self.budget) else self.budget

    def generate(self, view, deadline, resources):
        self.generated.append(resources)
        return self.text(view, deadline) if callable(self.text) else self.text


def machine(optimizer, limit=None, performance=F(1, 2), **manager):
    clock = FakeClock()
    runner = ManualRunner(clock)
    m = Machine(TrackManager(**manager), optimizer, Resources(limit), clock=clock, runner=runner,
                performance=performance)
    return m, clock, runner


class MachineTests(unittest.TestCase):
    def test_optimizer_decides_when_and_how_long(self):
        opt = Scripted()
        m, clock, runner = machine(opt)
        m.update_chip(1)
        m.user_edit([Add("U", Region.point(5))])
        m.poll()
        # Asked at every observation, with what the line is then; it declined each time.
        self.assertEqual([v.position for v, _ in opt.plans], [0, 1, 1, 1])
        self.assertEqual(opt.plans[2][0].track, {"U": Region.point(5)})
        self.assertEqual((m.runs, runner.jobs), ([], []))
        opt.budget = 10**6  # any positive time: the optimizer decides
        clock.now = 7.0
        m.poll()
        self.assertEqual((m.runs[0].deadline.start, m.runs[0].deadline.budget), (7.0, 10**6))
        asked = len(opt.plans)
        m.update_chip(2)
        m.poll()
        self.assertEqual(len(opt.plans), asked)  # not asked while a run is in progress
        self.assertEqual(len(runner.pending()), 1)

    def test_the_view_holds_the_text_the_modes_and_the_performance(self):
        opt = Scripted()
        m, clock, runner = machine(opt, modes=(1, 2), performance=F(1, 4))
        text = Text("t", rerun=lambda p, mode, state: p >= 2)
        m.manager.switch_rules_text(text)
        m.update_chip(1, [(1, 2)])
        view = opt.plans[-1][0]
        self.assertEqual((view.text, view.transitions, view.rerun, view.mode, view.performance),
                         (text, ((1, 2),), False, 2, F(1, 4)))
        m.update_chip(2)
        self.assertTrue(opt.plans[-1][0].rerun)
        m.performance = F(9, 10)  # the user may change it at any time
        opt.budget = 1
        m.poll()
        self.assertIsNone(m.runs[0].view.text)  # a run is not sent the text
        self.assertEqual((m.runs[0].view.rerun, m.runs[0].view.performance), (True, F(9, 10)))
        for bad in (0, 1, -F(1, 10), F(11, 10), "x"):  # in (0, 1): the ends are only limits
            with self.assertRaises((ValueError, TypeError)):
                m.performance = bad

    def test_invalid_plan_is_recorded_and_raises_nothing(self):
        def raises(view, resources):
            raise RuntimeError("plan bug")

        for bad in (0, -1, True, "1", float("nan"), float("inf"), raises):
            m, _, runner = machine(Scripted(budget=bad))
            m.update_chip(3)  # the chip moves, whatever the optimizer answers
            self.assertEqual(m.manager.chip.position, 3)
            self.assertEqual((m.runs, runner.jobs), ([], []))
            self.assertEqual(len(m.refused_plans), 2)

    def test_resources_are_every_cpu_up_to_the_limit(self):
        n = usable_cpus()
        self.assertEqual(Resources().cpus, n)
        self.assertEqual(Resources(1).cpus, 1)
        for bad in (0, -1, n + 1, True, "1", 1.0):
            with self.assertRaises(ValueError):
                Resources(bad)  # pyright: ignore[reportArgumentType]
        opt = Scripted()
        m, _, _ = machine(opt)
        m.resources = Resources(1)  # the user may change the limit at any time
        opt.budget = 1
        m.poll()
        m.runner.finish()  # pyright: ignore[reportAttributeAccessIssue]
        self.assertEqual((opt.plans[-1][1], opt.generated), (Resources(1), [Resources(1)]))

    def test_text_applies_from_the_chip_at_install(self):
        calls = []

        def rules(p, mode, state):
            calls.append(p)
            return [Add("A", Region.point(p + 1))]

        opt = Scripted(budget=1, text=lambda view, deadline: Text("t", rules=[Rule(rules)]))
        m, clock, runner = machine(opt, initial_elements=[("I", Region.closed(1, 4))])
        m.update_chip(3)  # the chip moves while the run is in progress
        clock.now = 1.0
        runner.finish()
        self.assertEqual(m.manager.current_text_id, None)  # installed only at the next observation
        m.update_chip(F(7, 2))
        run = m.runs[0]
        self.assertEqual((run.view.position, run.outcome, run.installed_at), (0, "installed", F(7, 2)))
        self.assertEqual(calls, [F(7, 2)])  # its rules first run at the install position
        self.assertEqual(dict(m.manager.track()), {"I": Region.closed(1, 4), "A": Region.point(F(9, 2))})

    def test_run_past_its_deadline_is_stopped(self):
        m, clock, runner = machine(Scripted(budget=1, text=Text("never")))
        clock.now = 1.0
        m.poll()  # exactly at the deadline: still running
        self.assertEqual(m.runs[0].outcome, "running")
        clock.now = 1.5
        m.poll()
        self.assertEqual((m.runs[0].outcome, m.runs[0].finished), ("stopped", 1.5))
        runner.finish()  # what it would have returned never reaches the line
        m.poll()
        self.assertEqual(m.manager.current_text_id, None)

    def test_late_result_is_discarded(self):
        texts = iter([Text("first"), Text("late"), Text("on time")])
        opt = Scripted(budget=1, text=lambda view, deadline: next(texts))
        m, clock, runner = machine(opt)
        runner.finish()
        m.poll()
        self.assertEqual(m.manager.current_text_id, "first")
        clock.now = 2.5  # the second run started at 0 with 1 s, and returns before anything stops it
        runner.finish()
        m.poll()
        self.assertEqual((m.runs[1].outcome, m.manager.current_text_id), ("late", "first"))
        clock.now = 3.5  # the third started at 2.5: returning at 3.5 is exactly on time
        runner.finish()
        m.poll()
        self.assertEqual((m.runs[2].outcome, m.manager.current_text_id), ("installed", "on time"))

    def test_failed_empty_and_refused_runs_change_nothing(self):
        def bad_rules(p, mode, state):
            raise RuntimeError("bad text")

        def broken(view):
            raise RuntimeError("generator bug")

        results = iter([lambda v: Text("first", rules=[Rule(lambda p, mode, state: [Add("A", Region.point(p + 5))])]),
                        broken, lambda v: None,
                        lambda v: Text("behind", rules=[Rule(lambda p, mode, state: [Add("A", Region.point(p))])]),
                        lambda v: Text("raises", rules=[Rule(bad_rules)])])
        opt = Scripted(budget=1, text=lambda view, deadline: next(results)(view))
        m, _, runner = machine(opt)
        runner.finish()
        m.poll()
        start = dict(m.manager.track())
        for outcome in ("error", "no text", "refused", "refused"):
            m.update_chip(m.manager.chip.position + 2)
            runner.finish()
            m.poll()
            self.assertEqual((m.runs[-2].outcome, m.manager.current_text_id), (outcome, "first"))
            self.assertEqual(dict(m.manager.track()), start)

    def test_random_machine_runs(self):
        for seed in range(SEEDS // 2):
            with self.subTest(seed=seed):
                self._random_machine_run(random.Random(seed))

    def _random_machine_run(self, rng: random.Random):
        first: dict[str, Fraction] = {}  # text id -> position of its first rules evaluation
        holder: list[Machine] = []

        def budget(view, resources):
            if holder:
                self.assertFalse(holder[0].running)
            return None if rng.random() < 0.3 else F(rng.randint(1, 4), 2)

        def text(view, deadline):
            if rng.random() < 0.15:
                return None
            tid, inner = f"t{rng.randrange(10**9)}", PutAhead(rng.randrange(10**6))

            def rules(p, mode, state):
                first.setdefault(tid, p)
                return inner(p, mode, state)
            return Text(tid, rules=[Rule(rules)], follow=random_text(rng, tid, view.position).follow)

        m, clock, runner = machine(Scripted(budget, text), modes=(1, 2), rules=[implies("A", ("B", 2))],
                                   initial_elements=[("A", Region.closed(2, 3))])
        holder.append(m)
        frozen: list[tuple[Fraction, dict]] = []
        for _ in range(20):
            t = rng.random()
            clock.now += rng.choice((0, 0.25, 0.5, 1))
            if t < 0.35 and runner.jobs:
                runner.finish()
            elif t < 0.5:
                try:
                    m.user_edit([Add(rng.choice("ABU"), Region.point(m.manager.chip.position + rng.randint(1, 6)))])
                except ValueError:
                    pass
            elif t < 0.6:
                m.poll()
            else:
                q = m.manager.chip.position + F(rng.randint(0, 4), 2)
                m.update_chip(q, [(q, rng.choice((1, 2)))] if q > m.manager.chip.position else [])
            self.assertLessEqual(len(runner.pending()), 1)  # one run at a time
            if runner.pending():
                self.assertTrue(m.running)
            for run in m.runs:
                if run.finished is not None:
                    self.assertEqual(run.outcome == "late", run.finished > run.deadline.end
                                     and run.outcome != "stopped")
                if run.outcome == "stopped":
                    assert run.finished is not None
                    self.assertGreater(run.finished, run.deadline.end)
                if run.outcome == "installed":
                    assert run.finished is not None and run.text_id is not None
                    self.assertLessEqual(run.finished, run.deadline.end)
                    self.assertEqual(first[run.text_id], run.installed_at)  # applies from the install
            installed = [r for r in m.runs if r.outcome == "installed"]
            self.assertEqual(m.manager.current_text_id, installed[-1].text_id if installed else None)
            self.assertEqual(m.manager.line.ruleset.violations(m.manager.line.state()), [])
            p = m.manager.chip.position
            frozen.append((p, restrict(m.manager.track(), at_or_behind(p))))
            for q, past in frozen:  # no user edit reaches the left side here
                self.assertEqual(restrict(m.manager.track(), at_or_behind(q)), past)


# ---- runs in real processes: these must be importable, hence picklable ----
@dataclass(frozen=True)
class PointAhead:
    """A picklable rules text: an A `offset` ahead of the chip."""
    offset: int

    def __call__(self, p, mode, state):
        return [Add("A", Region.point(p + self.offset))]


class Once:
    """Plans a single run of `budget` seconds, which calls `work(view, deadline, resources, *args)`."""
    def __init__(self, budget, work, *args):
        self.budget, self.work, self.args = budget, work, args
        self.planned = False

    def plan(self, view, resources):
        if self.planned:
            return None
        self.planned = True
        return self.budget

    def generate(self, view, deadline, resources):
        return self.work(view, deadline, resources, *self.args)


def _anytime(view, deadline, resources):
    """Improves its answer until half of its time is used, then returns it."""
    rounds = 0
    while deadline.remaining() > deadline.budget / 2:
        rounds += 1
    return Text(f"after {rounds} rounds", rules=[Rule(PointAhead(1))])


def _write_cpus(path):
    with open(path, "w") as f:
        f.write(str(usable_cpus()))


def _report_cpus(view, deadline, resources, path):
    """Reports the CPUs the run may use, and those of a process it starts."""
    child = multiprocessing.get_context("spawn").Process(target=_write_cpus, args=(path,))
    child.start()
    child.join()
    with open(path) as f:
        return Text(f"{usable_cpus()} {f.read()}")


def _beat(path):
    """Forever, ignoring every deadline: append to `path`."""
    while True:
        with open(path, "a") as f:
            f.write(".")
        time.sleep(0.01)


def _runaway(view, deadline, resources, run_path, child_path):
    multiprocessing.get_context("spawn").Process(target=_beat, args=(child_path,)).start()
    _beat(run_path)


def _unpicklable_text(view, deadline, resources):
    return Text("lambda", rules=[Rule(lambda p, mode, state: [])])


def _wait(m: Machine, timeout: float = 60) -> None:
    stop = time.monotonic() + timeout
    while m.runs and m.runs[0].outcome == "running" and time.monotonic() < stop:
        m.poll()
        time.sleep(0.01)


class ProcessTests(unittest.TestCase):
    def test_run_in_its_own_process_is_installed(self):
        m = Machine(TrackManager(), Once(5, _anytime))
        _wait(m)
        run = m.runs[0]
        self.assertEqual(run.outcome, "installed", run.reason)
        assert run.finished is not None and run.installed_at is not None
        self.assertLessEqual(run.finished, run.deadline.end)
        self.assertEqual(m.manager.track()["A"], Region.point(run.installed_at + 1))

    def test_cpu_limit_holds_for_the_run_and_the_processes_it_starts(self):
        n = usable_cpus()
        for limit in sorted({1, min(2, n), n}):
            with self.subTest(limit=limit), tempfile.TemporaryDirectory() as d:
                m = Machine(TrackManager(), Once(60, _report_cpus, os.path.join(d, "cpus")), Resources(limit))
                _wait(m)
                self.assertEqual((m.runs[0].outcome, m.runs[0].reason), ("installed", ""))
                self.assertEqual(m.runs[0].text_id, f"{limit} {limit}")

    def test_hard_stop_ends_the_run_and_every_process_it_started(self):
        with tempfile.TemporaryDirectory() as d:
            run_path, child_path = os.path.join(d, "run"), os.path.join(d, "child")
            m = Machine(TrackManager(), Once(4, _runaway, run_path, child_path))
            run = m.runs[0]
            # No observation from here on: the stop must come by itself.
            while not (os.path.exists(run_path) and os.path.exists(child_path)):
                self.assertLess(time.monotonic(), run.deadline.end, "the processes did not start in time")
                time.sleep(0.01)
            time.sleep(max(0.0, run.deadline.end - time.monotonic()) + 1)
            sizes = os.path.getsize(run_path), os.path.getsize(child_path)
            time.sleep(0.5)
            self.assertEqual((os.path.getsize(run_path), os.path.getsize(child_path)), sizes)
            m.poll()
            run = m.runs[0]
            self.assertEqual(run.outcome, "stopped")
            assert run.finished is not None
            self.assertLess(run.finished - run.deadline.end, 0.5)

    def test_what_cannot_be_pickled_is_an_error_of_the_run(self):
        m = Machine(TrackManager(), Once(30, _unpicklable_text))
        _wait(m)
        self.assertEqual(m.runs[0].outcome, "error")
        self.assertIn("cannot be sent back", m.runs[0].reason)
        m = Machine(TrackManager(), Once(30, lambda view, deadline, resources: None))
        self.assertEqual(m.runs[0].outcome, "error")  # the optimizer itself cannot be sent
        self.assertEqual(m.manager.current_text_id, None)


if __name__ == "__main__":
    unittest.main()
