"""An optimizer for the machine: where to place elements, for the modes the user
tends to follow.

Problem, proofs and the decisions of `plan`: SEMANTICS.md, "The optimizer".
"""
from __future__ import annotations

import math
import multiprocessing
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations

from chip import Mode, Number
from machine import Deadline, Resources, View
from region import EMPTY, INF, num
from reverse_logic import Add, Remove
from track import Region, Rule, Text, Track, ahead

Value = tuple[Fraction, Fraction]  # a*X + b, X the end of an infinite horizon (a = 0 on a finite one)
State = tuple[int, frozenset]  # (mode index, controlled kinds present)
ZERO: Value = (Fraction(0), Fraction(0))


# ---- the problem --------------------------------------------------------------
def _ends(region: Region, lo, hi) -> list[Fraction]:
    """The finite ends of a region within [lo, hi]; an infinite hi needs a region
    that does not repeat forever on the right (a horizon to +inf is a limit)."""
    if math.isinf(hi):
        if region.right is not None:
            raise ValueError("an infinite horizon over elements that repeat forever")
        return [e for q in (region & Region(((lo, True, INF, False),))).parts for e in (q[0], q[2])
                if not math.isinf(e)]
    return region.ends(lo, hi)


@dataclass(frozen=True)
class Problem:
    modes: tuple[Mode, ...]
    base: tuple[Fraction, ...]                             # per mode
    rates: tuple[tuple[tuple[str, Fraction], ...], ...]   # per mode: (kind, rate)
    controlled: tuple[str, ...]                            # the kinds the texts place
    cost: tuple[Fraction, ...]                             # per controlled kind: one element
    tendency: tuple[tuple[str, int], ...]                  # (kind, mode): the first present applies

    @property
    def watched(self) -> tuple[str, ...]:
        """The kinds of the line that the problem reads (not the controlled ones)."""
        kinds = {k for rs in self.rates for k, _ in rs} | {k for k, _ in self.tendency}
        return tuple(sorted(kinds - set(self.controlled)))

    @property
    def steering(self) -> tuple[str, ...]:
        """The watched kinds the tendency reads."""
        return tuple(sorted({k for k, _ in self.tendency} - set(self.controlled)))

    def subsets(self) -> list[frozenset]:
        """Every set of controlled kinds, smallest first (so ties keep fewer elements)."""
        return [frozenset(c) for n in range(len(self.controlled) + 1) for c in combinations(self.controlled, n)]

    def states(self) -> list[State]:
        return [(m, s) for m in range(len(self.modes)) for s in self.subsets()]

    def tend(self, kinds: frozenset, m: int) -> int:
        """The mode the user tends to, where `kinds` are present, coming in mode m."""
        return next((b for k, b in self.tendency if k in kinds), m)

    def rate(self, m: int, kinds: frozenset) -> Fraction:
        return self.base[m] + sum((r for k, r in self.rates[m] if k in kinds), Fraction(0))

    def price(self, kinds: frozenset) -> Fraction:
        return sum((c for k, c in zip(self.controlled, self.cost) if k in kinds), Fraction(0))


Part = tuple[Fraction, Fraction | float, frozenset]  # (lo, hi, watched kinds there); lo == hi: a point


@dataclass(frozen=True)
class Unit:
    """Where a text decides once: the open interval (start, end), with the
    same controlled kinds on all of it, and its end point.  `parts` are its
    open cells and inner points, on which the watched kinds are constant."""
    start: Fraction
    end: Fraction | float  # INF: the last unit of an infinite horizon, without end point
    parts: tuple[Part, ...]
    point_kinds: frozenset


def walk(problem: Problem, parts: Sequence[Part], t: Fraction, m: int, placed: frozenset
         ) -> tuple[Fraction, Fraction, int]:
    """The value (a, b) of `placed` on the parts after t, from mode m, and the
    mode after them.  Predicted modes follow the tendency on each part."""
    a, b = Fraction(0), Fraction(0)
    for lo, hi, kinds in parts:
        if hi <= t:
            continue
        lo = max(lo, t)
        m = problem.tend(kinds | placed, m)
        r = problem.rate(m, kinds | placed)
        if math.isinf(hi):
            a, b = a + r, b - r * lo
        else:
            b += (hi - lo) * r
    return a, b, m


def decide(problem: Problem, unit: Unit, t: Fraction, m: int, present: frozenset,
           after: dict[State, Value] | None) -> tuple[Value, frozenset, frozenset]:
    """The best (value, kinds on (t, end), kinds at end) from position t of
    `unit`, in mode m, with the controlled kinds `present` at t; `after` is the
    best value from every state at the end of the unit (None if it has none)."""
    best: tuple[Value, frozenset, frozenset] | None = None
    subsets = problem.subsets()
    for s_o in subsets:
        a, b, m_end = walk(problem, unit.parts, t, m, s_o)
        b -= problem.price(s_o - present)
        if after is None:
            cands = [((a, b), frozenset())]
        else:
            cands = []
            for s_p in subsets:
                w = after[problem.tend(unit.point_kinds | s_p, m_end), s_p]
                cands.append(((a + w[0], b - problem.price(s_p - s_o) + w[1]), s_p))
        for value, s_p in cands:
            if best is None or value > best[0]:
                best = (value, s_o, s_p)
    assert best is not None
    return best


# ---- the dynamic program --------------------------------------------------------
def _step(problem: Problem, unit: Unit, after: dict[State, Value] | None) -> dict[State, Value]:
    return {s: decide(problem, unit, unit.start, s[0], s[1], after)[0] for s in problem.states()}


def _matrix(problem: Problem, unit: Unit) -> list[list[Value | None]]:
    """A[s][s']: the best value of the unit from state s, ending in state s' (None: impossible)."""
    states = problem.states()
    index = {s: i for i, s in enumerate(states)}
    a: list[list[Value | None]] = [[None] * len(states) for _ in states]
    for i, (m, present) in enumerate(states):
        for s_o in problem.subsets():
            _, v, m_end = walk(problem, unit.parts, unit.start, m, s_o)
            v -= problem.price(s_o - present)
            for s_p in problem.subsets():
                j = index[problem.tend(unit.point_kinds | s_p, m_end), s_p]
                w = (Fraction(0), v - problem.price(s_p - s_o))
                if a[i][j] is None or w > a[i][j]:
                    a[i][j] = w
    return a


def _times(x: list[list[Value | None]], y: list[list[Value | None]]) -> list[list[Value | None]]:
    """Max-plus product."""
    n = len(x)
    out: list[list[Value | None]] = [[None] * n for _ in range(n)]
    for i in range(n):
        for k in range(n):
            if x[i][k] is None:
                continue
            for j in range(n):
                if y[k][j] is not None:
                    w = (x[i][k][0] + y[k][j][0], x[i][k][1] + y[k][j][1])
                    if out[i][j] is None or w > out[i][j]:
                        out[i][j] = w
    return out


def _chunk_matrix(task) -> list[list[Value | None]]:
    problem, units = task
    m = _matrix(problem, units[-1])
    for u in reversed(units[:-1]):
        m = _times(_matrix(problem, u), m)
    return m


def _chunk_values(task) -> list[dict[State, Value]]:
    problem, units, after = task
    out = []
    for u in reversed(units):
        after = _step(problem, u, after)
        out.append(after)
    return out[::-1]


Mapper = Callable[[Callable, list, float | None], list | None]  # (function, tasks, timeout) -> results


def solve(problem: Problem, units: Sequence[Unit], *, workers: int = 1, chunk: int = 64,
          mapper: Mapper | None = None, time_left: Callable[[], float] | None = None
          ) -> list[dict[State, Value] | None] | None:
    """values[i]: the best value from every state at the start of unit i;
    values[n]: after the last unit (0, or None after an infinite one).
    None if `time_left()` (seconds) runs out first."""
    def late() -> bool:
        return time_left is not None and time_left() <= 0

    n = len(units)
    infinite = bool(units) and math.isinf(units[-1].end)
    finite = list(units[:-1]) if infinite else list(units)
    values: list[dict[State, Value] | None] = [None] * (n + 1)
    if infinite:
        values[n - 1] = _step(problem, units[-1], None)
    else:
        values[n] = {s: ZERO for s in problem.states()}
    end = values[len(finite)]
    if workers <= 1 or mapper is None or len(finite) <= chunk:
        for i in reversed(range(len(finite))):
            if late():
                return None
            values[i] = _step(problem, finite[i], values[i + 1])
        return values
    states = problem.states()
    starts = list(range(0, len(finite), chunk))
    pieces = [finite[i:i + chunk] for i in starts]
    left = None if time_left is None else time_left()
    mats = mapper(_chunk_matrix, [(problem, p) for p in pieces], left)
    if mats is None or late():
        return None
    bounds: list[dict[State, Value]] = [end] * (len(pieces) + 1)  # values at each chunk's end
    for j in reversed(range(len(pieces))):
        nxt = bounds[j + 1]
        vec = {}
        for i, s in enumerate(states):
            best = None
            for k, t in enumerate(states):
                if mats[j][i][k] is not None:
                    w = (mats[j][i][k][0] + nxt[t][0], mats[j][i][k][1] + nxt[t][1])
                    if best is None or w > best:
                        best = w
            assert best is not None
            vec[s] = best
        bounds[j] = vec
    left = None if time_left is None else time_left()
    inner = mapper(_chunk_values, [(problem, p, bounds[j + 1]) for j, p in enumerate(pieces)], left)
    if inner is None or late():
        return None
    for i0, vs in zip(starts, inner):
        values[i0:i0 + len(vs)] = vs
    return values


def _pool_mapper(pool) -> Mapper:
    def run(fn, tasks, timeout):
        try:
            return pool.map_async(fn, tasks).get(None if timeout is None else max(0.0, timeout))
        except multiprocessing.TimeoutError:
            return None
    return run


# ---- the text it generates --------------------------------------------------------
@dataclass(frozen=True)
class Policy:
    """What a text decides from: the units and the best values after each one."""
    problem: Problem
    units: tuple[Unit, ...]
    values: tuple[dict[State, Value] | None, ...]


@dataclass(frozen=True)
class Act:
    """The rule of unit i: place the best controlled kinds for the rest of the
    unit, from the chip's position, mode and the state of the line."""
    policy: Policy
    i: int

    def __call__(self, position: Fraction, mode: Mode, state: Track):
        problem, unit = self.policy.problem, self.policy.units[self.i]
        present = frozenset(k for k in problem.controlled if position in state.get(k, EMPTY))
        _, s_o, s_p = decide(problem, unit, position, problem.modes.index(mode), present,
                             self.policy.values[self.i + 1])
        finite = not math.isinf(unit.end)
        window = Region.interval(position, unit.end, False, True) if finite else ahead(position)
        ops = []
        for k in problem.controlled:
            want = (Region.open(position, unit.end) if finite else ahead(position)) if k in s_o else EMPTY
            if finite and k in s_p:
                want |= Region.point(unit.end)
            have = state.get(k, EMPTY) & window
            if have - want:
                ops.append(Remove(k, have - want))
            if want - have:
                ops.append(Add(k, want - have))
        return [ops] if ops else []


@dataclass(frozen=True)
class Watch:
    """When a text asks for a new one: near the end of its horizon, or once the
    line changed enough that it may be more than `tolerance` below the optimum."""
    problem: Problem
    demand: tuple[tuple[str, Region], ...]  # the watched kinds after `start`, when generated
    start: Fraction
    end: Fraction | float
    lead: Fraction
    tolerance: Fraction

    def __call__(self, position: Fraction, mode: Mode, state: Track) -> bool:
        if not math.isinf(self.end) and position >= self.end - self.lead:
            return True
        return 2 * self.divergence(position, state) > self.tolerance

    def divergence(self, position: Fraction, state: Track) -> Fraction | float:
        """D: +inf if a kind the tendency reads changed after the chip (within the
        horizon); otherwise the integral, there, of the largest change of a
        mode's rate since the text was computed."""
        p = max(position, self.start)
        if not math.isinf(self.end) and p >= self.end:
            return Fraction(0)
        span = ahead(p) if math.isinf(self.end) else Region.interval(p, self.end, False, True)
        then = dict(self.demand)
        diffs = []  # (kind, where, +1 added or -1 removed)
        for k in self.problem.watched:
            now, was = state.get(k, EMPTY) & span, then.get(k, EMPTY) & span
            if now != was and k in self.problem.steering:
                return INF
            diffs += [(k, r, sign) for r, sign in ((now - was, 1), (was - now, -1)) if r]
        if any(not r.is_finite() for _, r, _ in diffs):
            return INF  # a change that repeats forever, after a final horizon
        ends = sorted({e for _, r, _ in diffs for part in r.parts for e in (part[0], part[2])})
        total = Fraction(0)
        for lo, hi in zip(ends, ends[1:]):
            mid = lo + 1 if math.isinf(hi) else (lo + hi) / 2
            here = [(k, sign) for k, r, sign in diffs if mid in r]
            change = max(abs(sum((sign * rate for k, sign in here for j, rate in rs if j == k), Fraction(0)))
                         for rs in self.problem.rates)
            if change:
                if math.isinf(hi):
                    return INF
                total += (hi - lo) * change
        return total


@dataclass(frozen=True)
class Order:
    """What `plan` decided for a run."""
    end: Fraction          # the horizon (the chip's position: no unit, an empty text)
    grid: Fraction         # the spacing of the positions where texts may change their placement
    workers: int
    chunk: int
    budget: float
    lead: Fraction
    tolerance: Fraction


# ---- the optimizer ----------------------------------------------------------------
class TendencyPlanner:
    """Places the `controlled` kinds ahead of the chip.

    Per unit of length, mode m earns `base[m]`, plus `rates[m][K]` where kind K
    is present (rates may be negative: a cost); each element of a controlled
    kind K costs `cost[K]`.  The modes are not decided here: the user decides
    them, but tends to follow `tendency`, a list of (kind, mode): where kind is
    present, placed by a text or not, the user tends to switch to that mode
    (the first present kind of the list applies); elsewhere to stay in the mode.
    Texts are optimal for that tendency from every position and actual mode,
    among placements that change only at multiples of the grid or at ends of
    the kinds the tendency reads (SEMANTICS.md, Theorem 3).

    The machine's performance h in (0, 1) sets, with f = (1 - h)/h (1 at h = 1/2):
    the horizon `lookahead`*f, the grid `resolution`/f, the time `time_scale`*f
    and the tolerance `scale`*f.  Towards 0 they tend to an infinite horizon,
    an infinitely fine grid (whose optimum tends to the best value of all
    placements, Theorem 5), unlimited time and no new run; towards 1 to no
    unit at all (an empty text), no time, and a new run at every change."""

    def __init__(self, rates: Mapping[Mode, Mapping[str, Number]], *,
                 base: Mapping[Mode, Number] | None = None, controlled: Sequence[str] = (),
                 cost: Number | Mapping[str, Number] = 0,
                 tendency: Sequence[tuple[str, Mode]] | Mapping[str, Mode] = (),
                 lookahead: Number = 20, resolution: Number = 1, time_scale: float = 10.0,
                 scale: Number = 1, min_lead: Number = Fraction(1, 10), speed: float | None = None,
                 startup: float = 1.0, pool_startup: float = 1.0, name: str = "plan"):
        base = dict(base or {})
        tendency = list(tendency.items()) if isinstance(tendency, Mapping) else list(tendency)
        modes = tuple(dict.fromkeys([*rates, *base, *(m for _, m in tendency)]))
        if not modes:
            raise ValueError("there must be at least one mode")
        controlled = tuple(dict.fromkeys(controlled))
        for k in controlled:
            if not isinstance(k, str):
                raise TypeError(f"element kind must be a str, got {k!r}")
        costs = tuple(num(cost[k] if isinstance(cost, Mapping) else cost) for k in controlled)
        if any(c < 0 for c in costs):
            raise ValueError("element costs must be non-negative")
        self.problem = Problem(
            modes, tuple(num(base.get(m, 0)) for m in modes),
            tuple(tuple(sorted((k, num(r)) for k, r in rates.get(m, {}).items() if num(r) != 0)) for m in modes),
            controlled, costs, tuple((k, modes.index(m)) for k, m in tendency))
        self.lookahead, self.resolution = num(lookahead), num(resolution)
        self.min_lead, self.scale = num(min_lead), num(scale)
        if min(self.lookahead, self.resolution, self.min_lead, self.scale) <= 0 or not time_scale > 0:
            raise ValueError("lookahead, resolution, time_scale, scale and min_lead must be positive")
        self.time_scale = float(time_scale)
        self.speed = speed            # part decisions per second, measured if None
        self.startup = float(startup)  # seconds before a run's process starts working
        self.pool_startup = float(pool_startup)  # seconds to start its worker processes
        self.name = name
        # What plan remembers between observations (sent to the run with the optimizer).
        self.order: Order | None = None
        self._serial = 0
        self._expect: str | None = None
        self._failures = 0
        self._retry_at = -math.inf
        self._seen: tuple[Fraction, float] | None = None
        self._chip_speed = 0.0  # units of length per second, a decaying maximum

    # ---- the problem ---------------------------------------------------
    def bounds(self, track: Track, p: Fraction, end: Fraction | float, grid: Fraction | None) -> list:
        """p, the multiples of `grid` and the ends of the kinds the tendency reads
        strictly between p and `end`, and `end`.  With grid None: the ends of every
        watched kind instead (the cells)."""
        kinds = self.problem.watched if grid is None else self.problem.steering
        inner = {e for k in kinds for e in _ends(track.get(k, EMPTY), p, end) if p < e < end}
        if grid is not None:
            top = end if not math.isinf(end) else p + 1  # an infinite horizon has no grid
            inner |= {k * grid for k in range(math.floor(p / grid) + 1, math.ceil(top / grid))
                      if p < k * grid < end}
        return [p, *sorted(inner), end]

    def units(self, track: Track, bounds: Sequence) -> list[Unit]:
        watched = self.problem.watched

        def kinds_at(x) -> frozenset:
            return frozenset(k for k in watched if x in track.get(k, EMPTY))

        ends = {e for k in watched for e in _ends(track.get(k, EMPTY), bounds[0], bounds[-1])}
        out = []
        for lo, hi in zip(bounds, bounds[1:]):
            cuts = [lo, *sorted(e for e in ends if lo < e < hi), hi]
            parts: list[Part] = []
            for i, (a, b) in enumerate(zip(cuts, cuts[1:])):
                if i:
                    parts.append((a, a, kinds_at(a)))
                parts.append((a, b, kinds_at(a + 1 if math.isinf(b) else (a + b) / 2)))
            out.append(Unit(lo, hi, tuple(parts), frozenset() if math.isinf(hi) else kinds_at(hi)))
        return out

    # ---- Optimizer.plan -------------------------------------------------
    def plan(self, view: View, resources: Resources) -> float | None:
        self._watch_chip(view)
        if self._expect is not None:
            if view.text_id == self._expect:
                self._failures = 0
            else:  # the run gave no text, or it was refused
                self._failures += 1
                self._retry_at = view.time + min(30.0, 0.5 * 2 ** (self._failures - 1))
                self.startup, self.pool_startup = min(60.0, self.startup * 2), min(60.0, self.pool_startup * 2)
            self._expect = None
        if self._ours(view) and not view.rerun or view.time < self._retry_at:
            return None
        self.order = self.order_for(view, resources)
        self._serial += 1
        self._expect = self.text_id(self._serial)
        return self.order.budget

    def text_id(self, serial: int) -> str:
        return f"{self.name} {serial}"

    def _ours(self, view: View) -> bool:
        rerun = view.text.rerun if view.text is not None else None
        return isinstance(rerun, Watch) and rerun.problem == self.problem

    def order_for(self, view: View, resources: Resources) -> Order:
        """The horizon and grid of the next text, and the time and CPUs its run gets."""
        h, p, cpus = view.performance, view.position, resources.cpus
        f = (1 - h) / h
        grid, allowed = self.resolution / f, self.time_scale * float(f)
        units = self.units(view.track, self.bounds(view.track, p, p + self.lookahead * f, grid))
        workers, chunk, budget, n = 1, 1, allowed, 0
        lo, hi = 0, len(units)  # the most units whose run fits in the time allowed
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self._fit(units[:mid], cpus)[2] <= allowed:
                lo = mid
            else:
                hi = mid - 1
        if lo:
            n = lo
            workers, chunk, budget = self._fit(units[:n], cpus)
        end = units[n - 1].end if n else p
        lead = Fraction(2 * self._chip_speed * budget).limit_denominator(10 ** 6) + self.min_lead
        return Order(end, grid, workers, chunk, budget, lead, self.scale * f)

    def _fit(self, units: Sequence[Unit], cpus: int) -> tuple[int, int, float]:
        """(workers, chunk, budget) for `units`: one process, or `cpus` if faster."""
        s, u = len(self.problem.states()), len(self.problem.subsets())
        parts = sum(len(x.parts) for x in units)
        n, speed = len(units), self._speed()

        def cost(workers: int) -> float:
            if workers == 1:
                work = parts * s * u * u / speed
            else:
                work = self.pool_startup + (parts * (s * u * u + s * u * u) + n * s ** 3) / (speed * workers)
            return 1.25 * (self.startup + work) + 0.1

        budget, workers = min((cost(w), w) for w in sorted({1, cpus}))
        chunk = max(1, -(-n // (4 * workers))) if workers > 1 else max(1, n)
        return workers, chunk, budget

    def _speed(self) -> float:
        if self.speed is None:
            pr = self.problem
            unit = Unit(Fraction(0), Fraction(1), ((Fraction(0), Fraction(1), frozenset()),), frozenset())
            after = {s: ZERO for s in pr.states()}
            began, rounds = time.perf_counter(), 0
            while rounds < 3 or time.perf_counter() - began < 0.01:
                _step(pr, unit, after)
                rounds += 1
            ops = rounds * len(pr.states()) * len(pr.subsets()) ** 2
            self.speed = ops / max(time.perf_counter() - began, 1e-6)
        return self.speed

    def _watch_chip(self, view: View) -> None:
        if self._seen is not None:
            p0, t0 = self._seen
            dt = view.time - t0
            if dt > 0:
                self._chip_speed = max(float(view.position - p0) / dt, self._chip_speed * 0.5 ** dt)
        self._seen = (view.position, view.time)

    # ---- Optimizer.generate --------------------------------------------
    def generate(self, view: View, deadline: Deadline, resources: Resources) -> Text | None:
        order = self.order or self.order_for(view, resources)
        if order.end == view.position:
            return self.text(view, order, [], [None], self._serial)
        units = self.units(view.track, self.bounds(view.track, view.position, order.end, order.grid))
        margin = 0.05 + 0.05 * deadline.budget  # to send the text back
        pool = None
        try:
            if order.workers > 1 and len(units) > order.chunk:
                pool = multiprocessing.get_context("spawn").Pool(order.workers)
            values = solve(self.problem, units, workers=order.workers, chunk=order.chunk,
                           mapper=_pool_mapper(pool) if pool else None,
                           time_left=lambda: deadline.remaining() - margin)
        finally:
            if pool is not None:
                pool.terminate()
        if values is None:
            return None
        return self.text(view, order, units, values, self._serial)

    def text(self, view: View, order: Order, units: Sequence[Unit], values, serial: int) -> Text:
        """The text of a policy; without units, an empty text that asks for a new
        one at every change of the problem."""
        policy = Policy(self.problem, tuple(units), tuple(values))
        rules = []
        for i, u in enumerate(units):
            where = Region(((u.start, True, u.end, False),))
            rules += [Rule(Act(policy, i), where, frozenset({m})) for m in self.problem.modes]
        here = ahead(view.position)
        demand = tuple((k, view.track[k] & here) for k in self.problem.watched
                       if k in view.track and view.track[k] & here)
        end = order.end if units else INF
        watch = Watch(self.problem, demand, view.position, end, order.lead, order.tolerance)
        return Text(self.text_id(serial), rules=tuple(rules), rerun=watch)
