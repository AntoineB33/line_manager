"""Tests of the optimizer: python -m unittest test_scheduler   (RULESGEN_SEEDS=n for more random runs)."""
from __future__ import annotations

import itertools
import math
import multiprocessing
import os
import random
import time
import unittest
from fractions import Fraction

from rules_generation_logic import (
    Add,
    Deadline,
    Machine,
    Region,
    Resources,
    TendencyPlanner,
    TrackManager,
    View,
    at_or_behind,
    restrict,
    usable_cpus,
)
from region import EMPTY, INF
from scheduler import Act, Problem, Watch, _pool_mapper, decide, solve
from test_rules_generation import FakeClock, ManualRunner

SEEDS = int(os.environ.get("RULESGEN_SEEDS", "20"))
F = Fraction


# ---- a direct evaluation of placements ---------------------------------------------
def outcomes(problem: Problem, pieces, m: int, present: frozenset, before: frozenset):
    """[(probability, value, mode after)] of placing kinds on consecutive pieces
    (lo, hi, watched kinds, placed kinds), lo == hi being a point, from mode m,
    `present` and `before` (every kind) just before: the user switches as the
    tendency says, with its probabilities, where the entry that applies changes."""
    if not pieces:
        return [(F(1), (F(0), F(0)), m)]
    (lo, hi, kinds, placed), rest = pieces[0], pieces[1:]
    here = kinds | placed
    out = []
    for q, m1 in problem.switch(before, here, m):
        r = problem.rate(m1, here)
        v = (r, -r * lo) if math.isinf(hi) else (F(0), (hi - lo) * r)
        v = (v[0], v[1] - problem.price(placed - present))
        for q2, w, m2 in outcomes(problem, rest, m1, placed, here):
            out.append((q * q2, (v[0] + w[0], v[1] + w[1]), m2))
    return out


def value_of(problem: Problem, pieces, m: int, present: frozenset, before=frozenset()) -> tuple[Fraction, Fraction]:
    """The expected value of a placement decided in advance."""
    ways = outcomes(problem, pieces, m, present, before | present)
    return sum(q * v[0] for q, v, _ in ways), sum(q * v[1] for q, v, _ in ways)


def pieces_of(units, pick):
    """The pieces of a placement constant on every unit: pick[i] = (open set, end point set)."""
    out = []
    for u, (s_o, s_p) in zip(units, pick):
        out += [(lo, hi, kinds, s_o) for lo, hi, kinds in u.parts]
        if not math.isinf(u.end):
            out.append((u.end, u.end, u.point_kinds, s_p))
    return out


def brute(problem: Problem, units, m: int, present: frozenset) -> tuple[Fraction, Fraction]:
    """The best expected value over every placement constant on each unit and
    each end point, decided at each unit's start from the mode there."""
    subsets = problem.subsets()

    def best(i: int, m: int, present: frozenset, before: frozenset):
        if i == len(units):
            return F(0), F(0)
        u = units[i]
        choices = [(s, frozenset()) for s in subsets] if math.isinf(u.end) else itertools.product(subsets, subsets)
        values = []
        for s_o, s_p in choices:
            pieces = pieces_of([u], [(s_o, s_p)])
            a, b = F(0), F(0)
            for q, v, m2 in outcomes(problem, pieces, m, present, before):
                w = best(i + 1, m2, s_p, u.point_kinds | s_p) if i + 1 < len(units) else (F(0), F(0))
                a, b = a + q * (v[0] + w[0]), b + q * (v[1] + w[1])
            values.append((a, b))
        return max(values)

    return best(0, m, present, units[0].start_kinds | present)


def random_planner(rng: random.Random, steer: bool = True, **kw) -> TendencyPlanner:
    modes = ["a", "b", "c"][:rng.randint(1, 3)]
    controlled = ["S", "T"][:rng.randint(1, 2)]
    rates = {m: {k: F(rng.randint(-4, 4), rng.randint(1, 2)) for k in rng.sample(controlled + ["D", "E"], 2)}
             for m in modes}
    kinds = ["D", "E", "Q"] + (controlled if steer else [])
    tendency = [(k, rng.choice(modes), rng.choice([F(1), F(1), F(1, 2), F(1, 3), F(0)]))
                for k in rng.sample(kinds, rng.randint(0, 2))]
    return TendencyPlanner(rates, base={m: F(rng.randint(-2, 2), 2) for m in modes}, controlled=controlled,
                           cost={k: F(rng.randint(0, 3), 2) for k in controlled}, tendency=tendency,
                           speed=1e6, startup=0.5, **kw)


def random_track(rng: random.Random, lo=0, hi=12) -> dict[str, Region]:
    out = {}
    for k in "DEQ":
        parts = []
        for _ in range(rng.randint(0, 2)):
            a = F(rng.randint(lo * 2, hi * 2), 2)
            parts.append(Region.point(a) if rng.random() < 0.2 else Region.closed(a, a + F(rng.randint(1, 6), 2)))
        if parts:
            out[k] = Region.union_of(*parts)
    return out


def view_of(track, position=F(0), mode="a", performance=F(1, 2), time_=0.0) -> View:
    return View(position, mode, track, None, (), time_, (), None, False, F(performance))


def inline_mapper(fn, tasks, timeout):
    return [fn(t) for t in tasks]


class ProblemTests(unittest.TestCase):
    def test_exact_against_every_placement(self):
        for seed in range(SEEDS * 3):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                opt = random_planner(rng)
                pr = opt.problem
                track = random_track(rng, 0, 4)
                end = rng.choice([F(rng.randint(1, 6)), INF])
                grid = rng.choice([None, F(1), F(3, 2)])
                units = opt.units(track, opt.bounds(track, F(0), end, grid))[:3]
                values = solve(pr, units)
                for m, present in pr.states():
                    self.assertEqual(values[0][m, present], brute(pr, units, m, present))

    def test_without_steering_finer_placements_do_not_help(self):
        """When the tendency reads no placed kind, a placement constant on the
        cells is the best of all (SEMANTICS.md, Lemma 1)."""
        for seed in range(SEEDS * 3):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                opt = random_planner(rng, steer=False)
                pr = opt.problem
                track = random_track(rng, 0, 4)
                units = opt.units(track, opt.bounds(track, F(0), F(5), None))
                values = solve(pr, units)
                m, present = rng.choice(pr.states())
                for _ in range(20):
                    pieces = []
                    for u in units:
                        for lo, hi, kinds in u.parts:
                            cuts = sorted({lo, hi} | ({lo + (hi - lo) * F(rng.randint(1, 9), 10)
                                                       for _ in range(rng.randint(0, 2))} if lo != hi else set()))
                            for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
                                if i:
                                    pieces.append((a, a, kinds, rng.choice(pr.subsets())))
                                pieces.append((a, b, kinds, rng.choice(pr.subsets())))
                            if lo == hi:
                                pieces.append((lo, lo, kinds, rng.choice(pr.subsets())))
                        pieces.append((u.end, u.end, u.point_kinds, rng.choice(pr.subsets())))
                    self.assertLessEqual(value_of(pr, pieces, m, present, units[0].start_kinds),
                                         values[0][m, present])

    def test_steering_tends_to_the_best_value_as_the_grid_gets_finer(self):
        """"Element A incites the user to switch to mode busy": an A, however short,
        makes the user busy, where they earn 1; A itself costs 3 per unit of
        length.  The best value over (0, 4] is 4, reached by no placement (the
        chip's own point cannot be changed); on a grid of g, the best is 4 - g
        (SEMANTICS.md, Theorem 5)."""
        opt = TendencyPlanner({"busy": {"A": -3}}, base={"idle": 0, "busy": 1}, controlled=["A"],
                              tendency=[("A", "busy")])
        idle = opt.problem.modes.index("idle")
        for g in (F(1), F(1, 2), F(1, 8), F(1, 64)):
            units = opt.units({}, opt.bounds({}, F(0), F(4), g))
            self.assertEqual(solve(opt.problem, units)[0][idle, frozenset()], (0, 4 - g))
        for x in (F(1, 10), F(1, 1000)):  # an A of length x from the chip: 4 - x - 3x
            pieces = [(F(0), x, frozenset(), frozenset({"A"})), (x, F(4), frozenset(), frozenset())]
            self.assertLess(value_of(opt.problem, pieces, idle, frozenset())[1], 4)

    def test_parallel_is_the_same(self):
        for seed in range(SEEDS):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                opt = random_planner(rng)
                track = random_track(rng, 0, 30)
                units = opt.units(track, opt.bounds(track, F(0), rng.choice([F(31), INF]), rng.choice([None, F(1)])))
                one = solve(opt.problem, units)
                many = solve(opt.problem, units, workers=3, chunk=rng.randint(1, 4), mapper=inline_mapper)
                self.assertEqual(one, many)

    def test_an_infinite_horizon_is_best_for_every_long_enough_one(self):
        for seed in range(SEEDS):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                opt = random_planner(rng)
                track = random_track(rng, 0, 4)
                (a, b) = solve(opt.problem, opt.units(track, opt.bounds(track, F(0), INF, None)))[0][0, frozenset()]
                for x in (F(200), F(1000)):  # long enough: the slopes of two policies may be close
                    finite = solve(opt.problem, opt.units(track, opt.bounds(track, F(0), x, None)))[0][0, frozenset()]
                    # With a horizon x, the best is a*x + b, up to a final element's cost.
                    self.assertLessEqual(abs(finite[1] - (a * x + b)), sum(opt.problem.cost))

    def test_late_runs_give_nothing(self):
        opt = random_planner(random.Random(1))
        track = random_track(random.Random(2), 0, 30)
        units = opt.units(track, opt.bounds(track, F(0), F(31), F(1)))
        self.assertIsNone(solve(opt.problem, units, time_left=lambda: 0.0))

    def test_invalid_problems_are_refused(self):
        with self.assertRaises(ValueError):
            TendencyPlanner({"a": {"S": 1}}, controlled=["S"], cost=-1)
        with self.assertRaises(ValueError):
            TendencyPlanner({})
        with self.assertRaises(ValueError):
            TendencyPlanner({"a": {"S": 1}}, resolution=0)
        with self.assertRaises(ValueError):
            TendencyPlanner({"a": {"S": 1}}, tendency=[("D", "a", F(3, 2))])

    def test_the_expected_score_decides(self):
        """D starts just after 0 and makes the user busy with probability q. S,
        decided at 0, earns 1 on (0, 1) if busy and loses 1 if idle, and costs
        3/4: the text places it iff q - (1 - q) > 3/4.  Planning for the most
        likely scenario would place it as soon as q > 1/2."""
        track = {"D": Region.open(0, 1)}
        for q, placed in ((F(3, 4), False), (F(15, 16), True)):
            opt = TendencyPlanner({"busy": {"S": 1}, "idle": {"S": -1}}, controlled=["S"], cost=F(3, 4),
                                  tendency=[("D", "busy", q)])
            pr = opt.problem
            idle = pr.modes.index("idle")
            units = opt.units(track, opt.bounds(track, F(0), F(1), F(1)))
            values = solve(pr, units)
            _, s_o, _ = decide(pr, units[0], F(0), idle, frozenset(), frozenset(), values[1])
            self.assertEqual("S" in s_o, placed, q)
            self.assertEqual(values[0][idle, frozenset()], (0, max(F(0), 2 * q - 1 - F(3, 4))))


def planner(**kw) -> TendencyPlanner:
    """S earns 1 per unit in "busy" and costs 1 in "idle"; each S costs 1/2.
    Where D is, the user tends to be busy; where Q is, idle."""
    kw = {"speed": 1e6, "startup": 0.5, **kw}
    return TendencyPlanner({"busy": {"S": 1}, "idle": {"S": -1}}, controlled=["S"], cost=F(1, 2),
                           tendency=[("D", "busy"), ("Q", "idle")], **kw)


def manager(**kw) -> TrackManager:
    return TrackManager(modes=("idle", "busy"), initial_mode="idle",
                        initial_elements=[("D", Region.closed(2, 6)), ("Q", Region.closed(6, 7)),
                                          ("D", Region.closed(F(15, 2), 8))], **kw)


def start(opt, mgr, cpus=1, performance=F(1, 2)):
    clock = FakeClock()
    runner = ManualRunner(clock)
    return Machine(mgr, opt, Resources(cpus), clock=clock, runner=runner, performance=performance), clock, runner


class TextTests(unittest.TestCase):
    def test_installs_the_best_placement(self):
        opt = planner()
        m, clock, runner = start(opt, manager(), performance=F(1, 10))
        runner.finish()
        m.poll()
        self.assertEqual(m.runs[0].outcome, "installed")
        # As the tendency says: busy on D, idle on Q, busy again at 15/2, and it stays busy after 8.
        moves = [(2, "busy"), (6, "idle"), (F(15, 2), "busy")]
        m.update_chip(20, moves)
        self.assertEqual(m.manager.track()["S"] & at_or_behind(20),
                         Region.open(2, 6) | Region.interval(F(15, 2), 20, False, True))
        self.assertEqual(len(m.runs), 1)  # nothing changed: no new run

    def test_a_placed_element_that_incites_a_mode(self):
        """The text places A, which makes the user busy, then stops paying for it."""
        opt = TendencyPlanner({"busy": {"A": -3}}, base={"idle": 0, "busy": 1}, controlled=["A"],
                              tendency=[("A", "busy")], resolution=F(1, 2), speed=1e6, startup=0.5)
        m, clock, runner = start(opt, TrackManager(modes=("idle", "busy"), initial_mode="idle"))
        runner.finish()
        m.poll()
        self.assertEqual(m.manager.track()["A"], Region.point(F(1, 2)))  # the first grid point
        m.update_chip(F(1, 2), [(F(1, 2), "busy")])  # the user follows the tendency
        m.update_chip(5)
        self.assertEqual(m.manager.track()["A"], Region.point(F(1, 2)))
        self.assertEqual(len(m.runs), 1)

    def test_texts_react_to_the_actual_modes(self):
        """From every position and mode, the text does what is best for the
        tendency from there (SEMANTICS.md, Theorem 3)."""
        for seed in range(SEEDS):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                opt = random_planner(rng)
                pr = opt.problem
                track = random_track(rng, 0, 8)
                mgr = TrackManager(modes=pr.modes, initial_mode=pr.modes[0],
                                   initial_elements=list(track.items()))
                view = view_of(dict(mgr.track()), mode=pr.modes[0], performance=F(1, 3))
                text = opt.generate(view, Deadline(0.0, 1e9, time.monotonic), Resources(1))
                mgr.switch_rules_text(text)
                act = text.rules[0].action
                assert isinstance(act, Act)
                values, units = act.policy.values, act.policy.units
                p = F(0)
                for _ in range(6):
                    q = p + F(rng.randint(1, 8), 4)
                    tr = sorted({p + F(rng.randint(1, int((q - p) * 4)), 4) for _ in range(rng.randint(0, 2))})
                    moves, last = [], mgr.chip.mode
                    for x in tr:  # only changes of mode are transitions where rules run
                        mode = rng.choice(pr.modes)
                        if mode != last:
                            moves.append((x, mode))
                            last = mode
                    tr = [x for x, _ in moves]
                    mgr.update_chip(q, moves)
                    # At every rule start on the way, the placement is the best decision there.
                    starts = sorted({u.start for u in units if p < u.start <= q} | set(tr))
                    for x, x2 in zip(starts, [*starts[1:], q]):
                        i = max(j for j, u in enumerate(units) if u.start <= x)
                        u = units[i]
                        mode = pr.modes.index(mgr.chip.mode_at(x))
                        present = frozenset(k for k in pr.controlled if x in mgr.track().get(k, EMPTY))
                        before = frozenset(k for k in pr.watched if x in mgr.track().get(k, EMPTY))
                        _, s_o, s_p = decide(pr, u, x, mode, present, before, values[i + 1])
                        y = (x + min(u.end, x2)) / 2
                        if x < y:
                            self.assertEqual({k for k in pr.controlled if y in mgr.track().get(k, EMPTY)}, set(s_o))
                    p = q

    def test_performance_sets_horizon_grid_time_and_tolerance(self):
        opt = planner()
        track = {"D": Region.union_of(*(Region.closed(2 * i, 2 * i + 1) for i in range(400)))}
        hs = [F(1, 20), F(1, 10), F(1, 4), F(1, 2), F(3, 4), F(9, 10)]
        orders = [opt.order_for(view_of(track, performance=h), Resources(1)) for h in hs]
        for (h1, o1), o2 in zip(zip(hs, orders), orders[1:]):
            self.assertGreaterEqual(o1.end, o2.end, h1)
            self.assertLessEqual(o1.grid, o2.grid)
            self.assertGreaterEqual(o1.budget, o2.budget)
            self.assertGreater(o1.tolerance, o2.tolerance)
        self.assertEqual(orders[3].grid, 1)
        # Close to 1: no time for a single unit; the text is empty, and asks for a new one at any change.
        near = opt.order_for(view_of(track, performance=F(999, 1000)), Resources(1))
        self.assertEqual(near.end, 0)
        text = opt.generate(view_of(track, performance=F(999, 1000)), Deadline(0.0, near.budget, time.monotonic),
                            Resources(1))
        self.assertEqual(text.rules, ())
        self.assertTrue(text.rerun(F(0), "idle", {**track, "D": track["D"] | Region.closed(1, F(3, 2))}))

    def test_the_time_of_a_run_depends_on_the_cpus(self):
        opt = TendencyPlanner({"a": {"S": 1, "T": 1, "U": 1}, "b": {"D": 1}, "c": {}}, controlled=["S", "T", "U"],
                              speed=1e4, startup=0.1, pool_startup=0.1, time_scale=1e9)
        track = {"D": Region.union_of(*(Region.closed(2 * i, 2 * i + 1) for i in range(500)))}
        view = view_of(track, performance=F(1, 2))
        one, many = opt.order_for(view, Resources(1)), opt.order_for(view, Resources(usable_cpus()))
        self.assertEqual(one.workers, 1)
        if usable_cpus() >= 64:
            self.assertGreater(many.workers, 1)
            self.assertLess(many.budget, one.budget)
        else:
            self.assertLessEqual(many.budget, one.budget)

    def test_changes_start_a_run_by_their_effect(self):
        opt = planner()
        m, clock, runner = start(opt, manager(), performance=F(1, 3))  # tolerance 2
        runner.finish()
        m.poll()
        self.assertEqual(m.runs[0].outcome, "installed")
        watch = m.manager.text.rerun
        assert isinstance(watch, Watch)
        self.assertEqual(watch.tolerance, 2)
        m.update_chip(1)
        self.assertEqual(len(m.runs), 1)
        m.user_edit([Add("Z", Region.closed(10, 11))])  # not in the problem
        self.assertEqual(len(m.runs), 1)
        m.user_edit([Add("Q", Region.closed(3, 4))])  # the tendency changes
        self.assertEqual(len(m.runs), 2)

    def test_the_score_is_within_twice_the_divergence(self):
        """SEMANTICS.md, Theorem 4: a change of the rates that the tendency does
        not read costs the text at most 2 D."""
        for seed in range(SEEDS):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                opt = TendencyPlanner({"a": {"S": 1, "D": F(rng.randint(-3, 3))}, "b": {"S": -1, "E": 1}},
                                      controlled=["S"], cost=F(rng.randint(0, 2), 2),
                                      tendency=[("Q", "b"), ("S", "a")][:rng.randint(1, 2)], speed=1e6)
                pr = opt.problem
                track = random_track(rng, 0, 6)
                end, grid = F(8), F(1, 2)
                units = opt.units(track, opt.bounds(track, F(0), end, grid))
                values = solve(pr, units)
                new = dict(track)
                for k in "DE":
                    a = F(rng.randint(0, 14), 2)
                    new[k] = (new.get(k, EMPTY) | Region.closed(a, a + 1)) if rng.random() < 0.5 else \
                        new.get(k, EMPTY) - Region.closed(a, a + 2)
                demand = tuple((k, r) for k, r in track.items() if k in pr.watched)
                d = Watch(pr, demand, F(0), end, F(0), F(0)).divergence(F(0), new)
                new_units = opt.units(new, opt.bounds(new, F(0), end, grid))  # the same units: Q did not change
                self.assertEqual([u.start for u in units], [u.start for u in new_units])
                best = solve(pr, new_units)[0][0, frozenset()][1]
                # What the old text decides, valued on the new line.
                pick, m, present = [], 0, frozenset()
                for i, u in enumerate(units):
                    _, s_o, s_p = decide(pr, u, u.start, m, present, u.start_kinds, values[i + 1])
                    pick.append((s_o, s_p))
                    ((_, m),) = pr.switch(u.start_kinds | present, u.parts[0][2] | s_o, m)
                    ((_, m),) = pr.switch(u.parts[0][2] | s_o, u.point_kinds | s_p, m)
                    present = s_p
                got = value_of(pr, pieces_of(new_units, pick), 0, frozenset(), new_units[0].start_kinds)[1]
                self.assertLessEqual(got, best)
                self.assertGreaterEqual(got, best - 2 * d)

    def test_a_refused_install_is_tried_again_later(self):
        opt = planner()
        m, clock, runner = start(opt, manager())
        runner.jobs[0].call = lambda: None  # the run gives nothing
        runner.finish()
        m.poll()
        self.assertEqual((m.runs[0].outcome, len(m.runs)), ("no text", 1))  # waits before trying again
        clock.now = 0.6
        m.poll()
        self.assertEqual(len(m.runs), 2)

    def test_random_runs(self):
        for seed in range(SEEDS // 2):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                opt = random_planner(rng)
                pr = opt.problem
                mgr = TrackManager(modes=pr.modes, initial_mode=pr.modes[0],
                                   initial_elements=list(random_track(rng, 0, 10).items()))
                m, clock, runner = start(opt, mgr, performance=F(rng.randint(1, 3), 4))
                frozen: list[tuple[Fraction, dict]] = []
                for _ in range(20):
                    clock.now += rng.choice((0, 0.25, 1))
                    t = rng.random()
                    if t < 0.3 and runner.jobs:
                        runner.finish()
                        m.poll()
                    elif t < 0.4:
                        a = m.manager.chip.position + F(rng.randint(1, 8), 2)
                        m.user_edit([Add(rng.choice("DEQ"), Region.closed(a, a + 1) - m.manager.track().get(
                            "D", EMPTY) - m.manager.track().get("E", EMPTY) - m.manager.track().get("Q", EMPTY))]
                                    if rng.random() < 0.8 else [])
                        frozen.clear()
                    else:
                        q = m.manager.chip.position + F(rng.randint(0, 6), 4)
                        m.update_chip(q, [(q, rng.choice(pr.modes))] if q > m.manager.chip.position else [])
                    self.assertLessEqual(len(runner.pending()), 1)
                    p = m.manager.chip.position
                    frozen.append((p, restrict(m.manager.track(), at_or_behind(p))))
                    for q, past in frozen:
                        self.assertEqual(restrict(m.manager.track(), at_or_behind(q)), past)


class ProcessTests(unittest.TestCase):
    def test_the_machine_with_its_optimizer_in_a_process(self):
        opt = planner(speed=None, startup=2.0)
        m = Machine(manager(), opt, performance=F(1, 2))
        stop = time.monotonic() + 60
        while m.runs[0].outcome == "running" and time.monotonic() < stop:
            m.poll()
            time.sleep(0.01)
        self.assertEqual(m.runs[0].outcome, "installed", m.runs[0].reason)
        m.update_chip(5, [(2, "busy")])
        self.assertEqual(m.manager.track()["S"] & at_or_behind(5), Region.interval(2, 5, False, True))
        m.close()

    def test_generate_with_worker_processes(self):
        opt = planner()
        track = {"D": Region.union_of(*(Region.closed(2 * i, 2 * i + 1) for i in range(40)))}
        units = opt.units(track, opt.bounds(track, F(0), F(81), F(1, 2)))
        with multiprocessing.get_context("spawn").Pool(2) as pool:
            many = solve(opt.problem, units, workers=2, chunk=10, mapper=_pool_mapper(pool))
        self.assertEqual(many, solve(opt.problem, units))


if __name__ == "__main__":
    unittest.main()
