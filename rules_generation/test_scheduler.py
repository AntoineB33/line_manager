"""Tests of the optimizer: python -m unittest test_scheduler   (RULESGEN_SEEDS=n for more random runs)."""
from __future__ import annotations

import itertools
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
    ModeScheduler,
    Region,
    Remove,
    Resources,
    TrackManager,
    View,
    ahead,
    at_or_behind,
    restrict,
    usable_cpus,
)
from scheduler import Order, Schedule, _pool_mapper, solve
from test_rules_generation import FakeClock, ManualRunner

SEEDS = int(os.environ.get("RULESGEN_SEEDS", "40"))
F = Fraction


def value_of(gains, cost, start, modes) -> Fraction:
    total, prev = F(0), start
    for g, m in zip(gains, modes):
        total += g[m] - cost[prev][m]
        prev = m
    return total


def brute(gains, cost, start) -> Fraction:
    k = len(cost)
    return max(value_of(gains, cost, start, ms) for ms in itertools.product(range(k), repeat=len(gains)))


def random_problem(rng: random.Random, n: int, k: int):
    gains = [[F(rng.randint(-6, 6), rng.randint(1, 3)) for _ in range(k)] for _ in range(n)]
    xs = [F(rng.randint(0, 8), 2) for _ in range(k)]
    jump = F(rng.randint(0, 4), 2)
    cost = [[abs(xs[a] - xs[b]) + jump if a != b else F(0) for b in range(k)] for a in range(k)]  # a metric
    return gains, cost, rng.randrange(k)


def inline_mapper(tasks, timeout):
    from scheduler import _chunk_task
    return [_chunk_task(t) for t in tasks]


def scheduler(**kw) -> ModeScheduler:
    """Off earns nothing; on costs 1/2 per unit and earns 1 where D is; a change costs 1/2."""
    kw = {"base": {"off": 0, "on": F(-1, 2)}, "speed": 1e6, "startup": 0.5, **kw}
    return ModeScheduler({"on": {"D": 1}}, F(1, 2), **kw)


def manager(*demand, **kw) -> TrackManager:
    return TrackManager(modes=("off", "on"), initial_mode="off",
                        initial_elements=[("D", r) for r in demand], **kw)


def start(opt, mgr, cpus=1):
    clock = FakeClock()
    runner = ManualRunner(clock)
    return Machine(mgr, opt, Resources(cpus), clock=clock, runner=runner), clock, runner


def asks(opt: ModeScheduler, view) -> bool:
    """Whether plan would want a run: no text of its own, or its text asks for one."""
    return opt._ours(view) is None or view.rerun


def realized(test: unittest.TestCase, opt: ModeScheduler, m: Machine, run) -> None:
    """The theorem of the install: from the install to s, the mode of the view; from s on, the schedule."""
    follow = m.manager.text.follow
    assert isinstance(follow, Schedule)
    chip, view = m.manager.chip, run.view
    s, end = follow.start, follow.end
    test.assertGreater(s, run.installed_at)
    test.assertEqual(chip.mode_at(run.installed_at), view.mode)
    test.assertEqual(chip.mode_at((run.installed_at + s) / 2), view.mode)
    cuts = opt.cuts(view.track, s, end)
    gains = opt.gains(view.track, cuts)
    y = opt.modes.index(view.mode)
    modes = [opt.modes.index(chip.mode_at(q)) for q in cuts[:-1]]
    for i, q in enumerate(cuts[:-1]):
        test.assertEqual(chip.mode_at((q + cuts[i + 1]) / 2), opt.modes[modes[i]])
    test.assertEqual(value_of(gains, opt.cost, y, modes), follow.value)
    best = brute(gains, opt.cost, y) if len(gains) <= 8 else solve(gains, opt.cost, y, chunk=1)[2]
    test.assertEqual(follow.value, best)


class SolveTests(unittest.TestCase):
    def test_exact_against_every_schedule(self):
        rng = random.Random(1)
        for _ in range(SEEDS * 5):
            gains, cost, y = random_problem(rng, rng.randint(1, 6), rng.randint(1, 3))
            best = brute(gains, cost, y)
            for kw in ({"chunk": 1}, {"chunk": 2}, {"chunk": 256},
                       {"chunk": 1, "workers": 2, "mapper": inline_mapper},
                       {"chunk": 2, "workers": 3, "mapper": inline_mapper}):
                done, modes, value = solve(gains, cost, y, **kw)
                self.assertEqual((done, value), (len(gains), best), kw)
                self.assertEqual(value_of(gains, cost, y, modes), value)

    def test_anytime_result_is_exact_for_the_cells_done(self):
        rng = random.Random(2)
        for _ in range(SEEDS):
            gains, cost, y = random_problem(rng, 7, 2)
            for rounds, workers in ((0, 1), (1, 1), (3, 1), (1, 2), (2, 2)):
                left = iter([1.0] * rounds + [-1.0] * 99)
                done, modes, value = solve(gains, cost, y, chunk=2, workers=workers,
                                           mapper=inline_mapper, time_left=lambda: next(left))
                self.assertEqual(done, min(len(gains), 2 * rounds * workers))
                self.assertEqual(value, brute(gains[:done], cost, y) if done else 0)
                self.assertEqual(value_of(gains[:done], cost, y, modes), value)

    def test_a_round_too_late_is_left_out(self):
        gains, cost, y = random_problem(random.Random(3), 6, 2)
        calls = []

        def mapper(tasks, timeout):
            calls.append(len(tasks))
            return inline_mapper(tasks, timeout) if len(calls) == 1 else None
        done, modes, value = solve(gains, cost, y, chunk=1, workers=2, mapper=mapper)
        self.assertEqual((done, value), (2, brute(gains[:2], cost, y)))

    def test_parallel_in_real_processes(self):
        rng = random.Random(4)
        gains, cost, y = random_problem(rng, 300, 3)
        sequential = solve(gains, cost, y)
        with multiprocessing.get_context("spawn").Pool(2) as pool:
            parallel = solve(gains, cost, y, chunk=40, workers=2, mapper=_pool_mapper(pool))
        self.assertEqual(parallel[:1] + parallel[2:], sequential[:1] + sequential[2:])
        self.assertEqual(value_of(gains, cost, y, parallel[1]), sequential[2])


class ProblemTests(unittest.TestCase):
    def test_no_schedule_beats_the_best_switching_at_ends(self):
        """Lemma: transitions anywhere, even inside cells, never do better."""
        rng = random.Random(5)
        for _ in range(SEEDS * 3):
            opt = ModeScheduler({"a": {"D": rng.randint(-2, 3)}, "b": {"E": rng.randint(-2, 3), "D": 1}},
                                {("a", "b"): F(rng.randint(0, 4), 2), ("b", "a"): F(rng.randint(0, 4), 2),
                                 ("a", "c"): 1, ("c", "a"): 1, ("b", "c"): 1, ("c", "b"): 1},
                                base={"a": 0, "b": F(-1, 2), "c": F(rng.randint(-1, 1), 2)})
            track = {K: Region.union_of(*(Region.closed(x, x + F(rng.randint(0, 4), 2))
                                          for x in (F(rng.randint(0, 16), 2) for _ in range(3))))
                     for K in "DE"}
            s, end = F(rng.randint(0, 3), 2), F(9)
            cuts = opt.cuts(track, s, end)
            gains = opt.gains(track, cuts)
            y = rng.randrange(3)
            best = solve(gains, opt.cost, y)[2]
            for _ in range(10):
                qs = sorted({s + F(rng.randint(0, 36), 4) for _ in range(rng.randint(0, 5))} - {end})
                qs = [q for q in qs if q < end]
                schedule = [(q, rng.randrange(3)) for q in qs]
                self.assertLessEqual(self.integrate(opt, track, s, end, y, schedule), best)

    @staticmethod
    def integrate(opt, track, s, end, y, schedule) -> Fraction:
        """Exact value of a schedule given by its transitions, wherever they are."""
        ends = sorted({e for K in opt.kinds for p in track.get(K, Region()).parts for e in (p[0], p[2])
                       if s < e < end} | {q for q, _ in schedule} | {s, end})
        mode_at = lambda x: ([y] + [m for q, m in schedule if q <= x])[-1]  # noqa: E731
        total, prev = F(0), y
        for _, m in schedule:
            total -= opt.cost[prev][m]
            prev = m
        for lo, hi in zip(ends, ends[1:]):
            mid = (lo + hi) / 2
            total += (hi - lo) * opt.rate(mode_at(mid), lambda K: mid in track.get(K, Region()))
        return total

    def test_invalid_problems_are_refused(self):
        with self.assertRaises(ValueError):
            ModeScheduler({"a": {"D": 1}, "b": {}, "c": {}},
                          {("a", "b"): 1, ("b", "a"): 1, ("b", "c"): 1, ("c", "b"): 1, ("a", "c"): 3, ("c", "a"): 1})
        with self.assertRaises(ValueError):
            ModeScheduler({"a": {"D": 1}, "b": {}}, -1)
        with self.assertRaises(ValueError):
            ModeScheduler({"a": {"D": 1}, "b": {}}, {("a", "b"): 1})
        with self.assertRaises(ValueError):
            ModeScheduler({"a": {"S": 1}}, 0)
        with self.assertRaises(ValueError):
            ModeScheduler({}, 0)
        with self.assertRaises(ValueError):
            ModeScheduler({"a": {"D": 1}}, 0, tolerance=-1)


class MachineTests(unittest.TestCase):
    def test_installs_the_best_schedule_and_its_edit(self):
        opt = scheduler()
        m, clock, runner = start(opt, manager(Region.closed(2, 6), Region.closed(7, 8), Region.closed(12, F(25, 2))))
        self.assertEqual(len(m.runs), 1)
        runner.finish()
        m.poll()
        run = m.runs[0]
        self.assertEqual(run.outcome, "installed", run.reason)
        realized(self, opt, m, run)
        # On from 2 to 8, across the gap: 6 units on (-3), 5 of demand (+5), two changes (-1).
        self.assertEqual(m.manager.chip.transitions, [(F(1, 10), "off"), (2, "on"), (8, "off")])
        self.assertEqual(m.manager.track()["S"], Region.union_of(Region.closed(2, 6), Region.interval(7, 8, True, False)))
        self.assertEqual(m.manager.text.follow.value, 1)

    def test_no_new_run_while_nothing_changes(self):
        opt = scheduler()
        m, clock, runner = start(opt, manager(Region.closed(2, 6)))
        runner.finish()
        m.poll()
        self.assertTrue(opt._ours(m._view()).final)
        for i in range(1, 60):
            clock.now += 0.1
            m.update_chip(F(i, 2))
        self.assertEqual(len(m.runs), 1)
        self.assertEqual(m.manager.track()["S"], Region.interval(2, 6, True, False))  # what the chip went through stays

    def test_a_change_of_the_problem_starts_a_run(self):
        opt = scheduler()
        m, clock, runner = start(opt, manager(Region.closed(2, 6)))
        runner.finish()
        m.poll()
        m.update_chip(3)
        self.assertEqual(len(m.runs), 1)
        m.user_edit([Add("D", Region.closed(9, 12))])
        self.assertEqual(len(m.runs), 2)
        runner.finish()
        m.poll()
        realized(self, opt, m, m.runs[1])
        self.assertEqual(m.manager.track()["S"], Region.union_of(Region.interval(2, 6, True, False),
                                                                 Region.interval(9, 12, True, False)))
        m.user_edit([Remove("D", Region.closed(9, 12))])
        runner.finish()
        m.poll()
        self.assertEqual(m.manager.track()["S"], Region.interval(2, 6, True, False))
        self.assertEqual(len(m.runs), 3)

    def test_the_served_kind_placed_by_someone_else(self):
        opt = scheduler()
        m, clock, runner = start(opt, manager(Region.closed(2, 6)))
        runner.finish()
        m.poll()
        m.user_edit([Add("S", Region.closed(3, 4))])  # the text's edit leaves it out: no new text
        self.assertEqual(len(m.runs), 1)
        self.assertEqual(m.manager.track()["S"], Region.interval(2, 6, True, False))
        m.user_edit([Remove("S", Region.closed(3, 4))])
        self.assertEqual(m.manager.track()["S"], Region.interval(2, 6, True, False))
        self.assertEqual(len(m.runs), 1)

    def test_a_tolerance_saves_runs_within_its_bound(self):
        opt = scheduler(tolerance=3)
        m, clock, runner = start(opt, manager(Region.closed(2, 10)))
        runner.finish()
        m.poll()
        m.update_chip(3)
        m.user_edit([Remove("D", Region.open(5, 6))])  # D = 1: at most 2 below the optimum
        self.assertEqual((len(m.runs), m.manager.text.follow.divergence(F(3), m.manager.track())), (1, 1))
        self.assertEqual(m.manager.track()["S"], Region.union_of(Region.interval(2, 5, True, True),
                                                                 Region.interval(6, 10, True, False)))
        m.user_edit([Add("D", Region.closed(12, 13))])  # D = 2 in all: 4 > 3
        self.assertEqual(len(m.runs), 2)
        runner.finish()
        m.poll()
        realized(self, opt, m, m.runs[1])

    def test_the_score_is_within_twice_the_divergence(self):
        """Theorem 5, on random edits after the install."""
        for seed in range(SEEDS):
            rng = random.Random(seed)
            opt = scheduler(tolerance=10 ** 6, lookahead=30)
            demand = [Region.closed(x, x + F(rng.randint(1, 6), 2)) for x in range(2, 26, 4)]
            m, clock, runner = start(opt, manager(*demand))
            runner.finish()
            m.poll()
            for _ in range(4):
                x = F(rng.randint(2, 56), 2)
                part = Region.closed(x, x + F(rng.randint(1, 4), 2))
                track = m.manager.track()
                op = Remove("D", part & track["D"]) if part & track.get("D", Region()) and rng.random() < 0.5 \
                    else Add("D", part - track.get("D", Region()))
                if op.region:
                    m.user_edit([op])
            sched, chip = m.manager.text.follow, m.manager.chip
            p = max(chip.position, sched.start)
            track = m.manager.track()
            y = opt.modes.index(chip.mode_at(p))
            best = solve(opt.gains(track, opt.cuts(track, p, sched.end)), opt.cost, y)[2]
            here = [(q, opt.modes.index(mode)) for q, mode in chip.transitions if p < q <= sched.end]
            score = ProblemTests.integrate(opt, track, p, sched.end, y, here)
            self.assertLessEqual(score, best)
            self.assertLessEqual(best - score, 2 * sched.divergence(chip.position, track))

    def test_the_end_of_the_horizon_starts_a_run(self):
        opt = scheduler(lookahead=10)
        demand = Region.union_of(*(Region.closed(4 * i + 1, 4 * i + 3) for i in range(10)))
        m, clock, runner = start(opt, manager(demand))
        runner.finish()
        m.poll()
        end = m.manager.text.follow.end
        self.assertFalse(m.manager.text.follow.final)
        m.update_chip(end - 2)
        self.assertEqual(len(m.runs), 1)
        m.update_chip(end - F(1, 20))
        self.assertEqual(len(m.runs), 2)
        runner.finish()
        m.poll()
        realized(self, opt, m, m.runs[1])
        self.assertGreater(m.manager.text.follow.end, end)

    def test_a_refused_install_is_tried_again_later(self):
        opt = scheduler()
        m, clock, runner = start(opt, manager(Region.closed(2, 6)))
        m.update_chip(1)  # past where the text starts
        runner.finish()
        m.poll()
        self.assertEqual(m.runs[0].outcome, "refused")
        self.assertEqual(len(m.runs), 1)  # it waits
        clock.now += 1
        m.poll()
        self.assertEqual(len(m.runs), 2)
        runner.finish()
        m.poll()
        self.assertEqual(m.runs[1].outcome, "installed")
        realized(self, opt, m, m.runs[1])

    def test_a_schedule_starts_ahead_of_where_the_chip_will_be(self):
        opt = scheduler(lookahead=40)
        m, clock, runner = start(opt, manager(Region.closed(30, 35)))
        runner.finish()
        for i in range(1, 6):
            clock.now += 1
            m.update_chip(2 * i)  # 2 units per second
        m.user_edit([Add("D", Region.closed(40, 45))])
        lead = opt.order.start - m.manager.chip.position
        self.assertGreaterEqual(lead, F(2) * 2 * F(opt.order.budget) * F(9, 10))

    def test_the_time_of_a_run_depends_on_the_cpus(self):
        demand = Region(tuple((F(i, 4), True, F(i, 4) + F(1, 8), True) for i in range(4000)))
        view = View(F(0), "a", {"D": demand}, None, (), 0.0)
        opt = ModeScheduler({"a": {"D": 1}, "b": {}, "c": {"D": F(1, 2)}, "d": {}}, 1, lookahead=1000,
                            speed=2e4, startup=0.5, pool_startup=0.5, max_budget=1000)
        one, many = opt.order_for(view, Resources(1)), opt.order_for(view, Resources(min(8, usable_cpus())))
        self.assertEqual(one.workers, 1)
        if usable_cpus() >= 8:
            self.assertEqual(many.workers, 8)
            self.assertLess(many.budget, one.budget)
        small = start(scheduler(), manager(Region.closed(2, 6)))[0]._view()
        self.assertEqual(opt.order_for(small, Resources(usable_cpus())).workers, 1)  # not worth the processes

    def test_generate_with_worker_processes(self):
        demand = Region.union_of(*(Region.closed(F(i, 2), F(i, 2) + F(1, 4)) for i in range(0, 80, 3)))
        opt = scheduler(lookahead=40)
        m = start(opt, manager(demand))[0]
        view = m.runs[0].view
        texts = []
        for workers, chunk in ((1, 256), (2, 5)):
            opt.order = Order(opt.order.start, opt.order.end, workers, chunk, 60.0, opt.order.lead)
            texts.append(opt.generate(view, Deadline(time.monotonic(), 60.0, time.monotonic), Resources(workers)))
        self.assertEqual(texts[0].follow.value, texts[1].follow.value)
        self.assertEqual(texts[0].follow.end, texts[1].follow.end)

    def test_random_runs(self):
        for seed in range(SEEDS):
            with self.subTest(seed=seed):
                self._random_run(random.Random(seed))

    def _random_run(self, rng: random.Random):
        opt = ModeScheduler({"a": {"D": 1, "E": -1}, "b": {"E": 2}, "c": {}},
                            {("a", "b"): 1, ("b", "a"): 1, ("a", "c"): F(1, 2), ("c", "a"): F(1, 2),
                             ("b", "c"): F(1, 2), ("c", "b"): F(1, 2)},
                            base={"a": F(-1, 4), "b": F(-1, 2)}, lookahead=rng.choice((5, 12)), speed=1e6,
                            startup=0.5, min_lead=F(1, 4))
        mgr = TrackManager(modes=("a", "b", "c"), initial_mode="c",
                           initial_elements=[(rng.choice("DE"), Region.closed(x, x + F(rng.randint(1, 6), 2)))
                                             for x in range(1, 20, 4)])
        m, clock, runner = start(opt, mgr)
        frozen = []
        for _ in range(30):
            t = rng.random()
            clock.now += rng.choice((0, 0.5, 1))
            before = len(m.runs)
            if t < 0.35 and runner.jobs:
                runner.finish()
                m.poll()
                if m.runs[before - 1].outcome == "installed":
                    realized(self, opt, m, m.runs[before - 1])
            elif t < 0.5:
                x = m.manager.chip.position + rng.randint(1, 12)
                track = m.manager.track()
                free = Region.closed(x, x + F(rng.randint(1, 4), 2)) - track.get("D", Region()) - track.get("E", Region())
                m.user_edit([Add(rng.choice("DE"), free)] if free and rng.random() < 0.8 else [])
            else:
                wanted = not m.running and asks(opt, m._view())
                m.update_chip(m.manager.chip.position + F(rng.randint(0, 3), 4))
                if not wanted and not asks(opt, m._view()) and not opt._failures:
                    self.assertEqual(len(m.runs), before)  # nothing changed: no new run
            p = m.manager.chip.position
            frozen.append((p, restrict(m.manager.track(), at_or_behind(p))))
            for q, past in frozen:
                self.assertEqual(restrict(m.manager.track(), at_or_behind(q)), past)


class ProcessTests(unittest.TestCase):
    def test_the_machine_with_its_optimizer_in_a_process(self):
        opt = scheduler(speed=None, startup=2.0)
        m = Machine(manager(Region.closed(2, 6), Region.closed(7, 8)), opt)
        stop = time.monotonic() + 60
        while m.runs[0].outcome == "running" and time.monotonic() < stop:
            m.poll()
            time.sleep(0.01)
        self.assertEqual(m.runs[0].outcome, "installed", m.runs[0].reason)
        self.assertEqual(m.manager.track()["S"], Region.union_of(Region.closed(2, 6), Region.interval(7, 8, True, False)))
        self.assertEqual(len(m.runs), 1)
        m.close()


if __name__ == "__main__":
    unittest.main()
