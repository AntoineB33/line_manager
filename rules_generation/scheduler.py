"""An optimizer for the machine: mode scheduling with switching costs.

Problem, proofs and the decisions of `plan`: SEMANTICS.md, "The optimizer".
"""
from __future__ import annotations

import math
import multiprocessing
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction

from chip import Mode, Number
from machine import Deadline, Resources, View
from region import EMPTY, INF, num
from track import Region, Text, Track, ahead

Value = Fraction | None  # None: unreachable (minus infinity)
# One result of `_chunk` per entry vector: (values after the chunk, back pointers)
ChunkResult = list[tuple[list[Value], list[list[int]]]]


# ---- the dynamic program ----------------------------------------------------
def _chunk(gains: Sequence[Sequence[Fraction]], cost: Sequence[Sequence[Fraction]],
           entries: Sequence[Sequence[Value]]) -> ChunkResult:
    """For each entry vector v (best value of each mode just before the first
    cell), the best value of each mode after the last cell, and the back
    pointers: back[i][b] is the mode before cell i on a best path in mode b on
    cell i.  Ties go to the lowest mode index."""
    k = len(cost)
    out: ChunkResult = []
    for v in entries:
        back = []
        for g in gains:
            nv: list[Value] = [None] * k
            nb = [0] * k
            for b in range(k):
                best, arg = None, 0
                for a in range(k):
                    if v[a] is not None and (best is None or v[a] - cost[a][b] > best):
                        best, arg = v[a] - cost[a][b], a
                nv[b], nb[b] = (None if best is None else best + g[b]), arg
            back.append(nb)
            v = nv
        out.append((list(v), back))
    return out


def _chunk_task(task):
    return _chunk(*task)


def _best(values: Sequence[Value]) -> int:
    """Index of the largest value (the lowest index among equals)."""
    return max((i for i, v in enumerate(values) if v is not None), key=lambda i: (values[i], -i))


Mapper = Callable[[list, float | None], list | None]  # (tasks, timeout) -> results, or None if too late


def solve(gains: Sequence[Sequence[Fraction]], cost: Sequence[Sequence[Fraction]], start: int, *,
          chunk: int = 256, workers: int = 1, mapper: Mapper | None = None,
          time_left: Callable[[], float] | None = None) -> tuple[int, list[int], Fraction]:
    """Best schedule over the first cells: (cells done, mode index of each, value).

    The cells are taken in rounds.  With one worker a round is one chunk, entered
    with the values reached so far.  With several, a round is `workers` chunks,
    each entered from every single mode, computed by `mapper` at the same time
    and composed afterwards (max-plus products are associative, so the values are
    those of one pass).  Before each round, `time_left()` (seconds) must exceed the
    time the previous round took; the result is always exact for the cells done."""
    k, n = len(cost), len(gains)
    v: list[Value] = [Fraction(0) if m == start else None for m in range(k)]
    rounds: list[tuple[list[Value], bool, ChunkResult, int]] = []  # (values before, unit entries, result, first cell)
    done, took = 0, 0.0
    units = [[Fraction(0) if m == a else None for m in range(k)] for a in range(k)]
    while done < n:
        left = None if time_left is None else time_left()
        if left is not None and left <= took:
            break
        began = time.perf_counter()
        if workers <= 1 or mapper is None:
            part = gains[done:done + chunk]
            result = _chunk(part, cost, [v])
            rounds.append((v, False, result, done))
            v, done = result[0][0], done + len(part)
        else:
            parts = [gains[i:i + chunk] for i in range(done, min(n, done + workers * chunk), chunk)]
            results = mapper([(p, cost, units) for p in parts], left)
            if results is None:
                break
            for part, result in zip(parts, results):
                rounds.append((v, True, result, done))
                nv: list[Value] = [None] * k
                for a in range(k):
                    if v[a] is None:
                        continue
                    for b, x in enumerate(result[a][0]):
                        if x is not None and (nv[b] is None or v[a] + x > nv[b]):
                            nv[b] = v[a] + x
                v, done = nv, done + len(part)
        took = time.perf_counter() - began
    modes = [0] * done
    if done == 0:
        return 0, [], Fraction(0)
    value = v[_best(v)]
    assert value is not None
    b = _best(v)
    for before, unit, result, first in reversed(rounds):
        if unit:
            a = _best([None if before[a] is None or result[a][0][b] is None else before[a] + result[a][0][b]
                       for a in range(k)])
            back = result[a][1]
        else:
            back = result[0][1]
        for i in range(len(back) - 1, -1, -1):
            modes[first + i] = b
            b = back[i][b]
    return done, modes, value


def _presence(region: Region, xs: Sequence[Fraction]) -> list[bool]:
    """Whether each of the increasing xs is in `region`, in one sweep."""
    out, parts, j = [], region.parts, 0
    for x in xs:
        while j < len(parts) and (parts[j][2] < x or (parts[j][2] == x and not parts[j][3])):
            j += 1
        out.append(j < len(parts) and x in Region((parts[j],)))
    return out


def _pool_mapper(pool) -> Mapper:
    def run(tasks, timeout):
        try:
            return pool.map_async(_chunk_task, tasks).get(None if timeout is None else max(0.0, timeout))
        except multiprocessing.TimeoutError:
            return None
    return run


# ---- the text it generates --------------------------------------------------
@dataclass(frozen=True)
class Schedule:
    """The rules of a schedule text: its continuous edit (calling it) and `rerun`.

    It holds what the text was computed from, so that it needs nothing else."""
    kind: str  # the served kind
    pieces: tuple[tuple[Region, tuple[str, ...]], ...]  # where the chip is planned in a mode, the kinds it serves
    demand: tuple[tuple[str, Region], ...]  # the kinds of the problem ahead of the chip, when generated
    rates: tuple[tuple[tuple[str, Fraction], ...], ...]  # per mode: (kind, rate)
    start: Fraction    # s: no transition of this text before it
    end: Fraction      # E: the schedule is optimal over (s, E]
    value: Fraction    # its value over (s, E]
    final: bool        # it is optimal over (s, X] for every X >= E as well
    tolerance: Fraction
    lead: Fraction     # how far before E it asks for a new text

    def __call__(self, position: Fraction, mode: Mode, state: Track):
        """`kind` where the planned mode earns from a kind present now, unless it is there already."""
        r = Region.union_of(*(where & Region.union_of(*(state.get(K, EMPTY) for K in kinds))
                              for where, kinds in self.pieces))
        r = (r & ahead(position)) - state.get(self.kind, EMPTY)
        return [(self.kind, r)] if r else []

    def rerun(self, position: Fraction, mode: Mode, state: Track) -> bool:
        """Near E (unless final), or once it may be more than `tolerance` below the optimum."""
        if not self.final and position >= self.end - self.lead:
            return True
        return 2 * self.divergence(position, state) > self.tolerance

    def divergence(self, position: Fraction, state: Track) -> Fraction | float:
        """D: the integral, after the chip (up to E, unless final), of the largest
        change of a mode's rate since the text was computed."""
        if self.final:
            span = ahead(position)
        elif position < self.end:
            span = Region.interval(position, self.end, False, True)
        else:
            return Fraction(0)
        then = dict(self.demand)
        diffs = []  # (kind, where, +1 added or -1 removed)
        for K in sorted({K for rs in self.rates for K, _ in rs}):
            now, was = state.get(K, EMPTY) & span, then.get(K, EMPTY) & span
            diffs += [(K, r, sign) for r, sign in ((now - was, 1), (was - now, -1)) if r]
        ends = sorted({e for _, r, _ in diffs for p in r.parts for e in (p[0], p[2])})
        total = Fraction(0)
        for lo, hi in zip(ends, ends[1:]):
            mid = lo + 1 if math.isinf(hi) else (lo + hi) / 2
            here = [(K, sign) for K, r, sign in diffs if mid in r]
            change = max(abs(sum((sign * rate for K, sign in here for J, rate in rs if J == K), Fraction(0)))
                         for rs in self.rates)
            if change:
                if math.isinf(hi):
                    return INF
                total += (hi - lo) * change
        return total


@dataclass(frozen=True)
class Order:
    """What `plan` decided for a run."""
    start: Fraction
    end: Fraction
    workers: int
    chunk: int
    budget: float
    lead: Fraction


# ---- the optimizer ----------------------------------------------------------
class ModeScheduler:
    """Chooses the modes of the chip ahead of it.

    Per unit of length, mode m earns `base[m]`, plus `rates[m][K]` where kind K
    is present.  Every change of mode from a to b costs `switch_cost` (one
    number, or a mapping {(a, b): cost} for every pair of distinct modes); the
    costs must be non-negative and satisfy the triangle inequality.  A text
    puts `served` wherever its mode earns from a kind present there, and asks
    for a new one near the end of its horizon, or once the line has changed
    enough that it may be more than `tolerance` below the optimum: a larger
    tolerance means fewer runs, a smaller one a better schedule."""

    def __init__(self, rates: Mapping[Mode, Mapping[str, Number]],
                 switch_cost: Number | Mapping[tuple[Mode, Mode], Number], *,
                 base: Mapping[Mode, Number] | None = None, served: str = "S",
                 lookahead: Number = 20, max_budget: float = 30.0, min_lead: Number = Fraction(1, 10),
                 tolerance: Number = 0, speed: float | None = None, startup: float = 1.0,
                 pool_startup: float = 1.0, name: str = "schedule"):
        base = dict(base or {})
        self.modes: tuple[Mode, ...] = tuple(dict.fromkeys([*rates, *base]))
        if not self.modes:
            raise ValueError("there must be at least one mode")
        self.base = tuple(num(base.get(m, 0)) for m in self.modes)
        self.rates = tuple(tuple(sorted((K, num(r)) for K, r in rates.get(m, {}).items() if num(r) != 0))
                           for m in self.modes)
        self.kinds = tuple(sorted({K for rs in self.rates for K, _ in rs}))
        if served in self.kinds:
            raise ValueError(f"the served kind {served!r} cannot earn: it is placed by the texts")
        k = len(self.modes)
        if isinstance(switch_cost, Mapping):
            missing = [(a, b) for a in self.modes for b in self.modes if a != b and (a, b) not in switch_cost]
            if missing:
                raise ValueError(f"no switching cost for {missing}")
            cost = [[num(switch_cost[a, b]) if a != b else Fraction(0) for b in self.modes] for a in self.modes]
        else:
            cost = [[num(switch_cost) if a != b else Fraction(0) for b in range(k)] for a in range(k)]
        for a in range(k):
            for b in range(k):
                if cost[a][b] < 0:
                    raise ValueError("switching costs must be non-negative")
                for c in range(k):
                    if cost[a][c] > cost[a][b] + cost[b][c]:
                        raise ValueError(f"switching costs must satisfy the triangle inequality: "
                                         f"{self.modes[a]!r}->{self.modes[c]!r} costs more than through "
                                         f"{self.modes[b]!r}")
        self.cost = tuple(tuple(r) for r in cost)
        self.served = served
        self.lookahead = num(lookahead)
        self.min_lead = num(min_lead)
        if self.lookahead <= 0 or self.min_lead <= 0 or not max_budget > 0:
            raise ValueError("lookahead, min_lead and max_budget must be positive")
        self.max_budget = float(max_budget)
        self.tolerance = num(tolerance)
        if self.tolerance < 0:
            raise ValueError("the tolerance must be non-negative")
        self.speed = speed            # max-plus operations per second, measured if None
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

    # ---- the problem --------------------------------------------------
    def cuts(self, track: Track, s: Fraction, end: Fraction) -> list[Fraction]:
        """s, the ends of the problem's kinds strictly between s and `end`, and `end`."""
        inner = {e for K in self.kinds for p in track.get(K, EMPTY).parts for e in (p[0], p[2])
                 if not math.isinf(e) and s < e < end}
        return [s, *sorted(inner), end]

    def rate(self, m: int, present: Callable[[str], bool]) -> Fraction:
        return self.base[m] + sum((r for K, r in self.rates[m] if present(K)), Fraction(0))

    def gains(self, track: Track, cuts: Sequence[Fraction]) -> list[list[Fraction]]:
        """gains[i][m]: what mode m earns over the open cell (cuts[i], cuts[i+1])."""
        mids = [(lo + hi) / 2 for lo, hi in zip(cuts, cuts[1:])]
        present = {K: _presence(track.get(K, EMPTY), mids) for K in self.kinds}
        return [[(hi - lo) * self.rate(m, lambda K: present[K][i]) for m in range(len(self.modes))]
                for i, (lo, hi) in enumerate(zip(cuts, cuts[1:]))]

    def _serves(self, m: int) -> tuple[str, ...]:
        return tuple(K for K, r in self.rates[m] if r > 0)

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
        if self._ours(view) is not None and not view.rerun or view.time < self._retry_at:
            return None
        self.order = self.order_for(view, resources)
        self._serial += 1
        self._expect = self.text_id(self._serial)
        return self.order.budget

    def text_id(self, serial: int) -> str:
        return f"{self.name} {serial}"

    def _ours(self, view: View) -> Schedule | None:
        """The rules of the installed text, if it is a schedule of this optimizer's kind."""
        follow = view.text.follow if view.text is not None else None
        return follow if isinstance(follow, Schedule) and follow.kind == self.served else None

    def order_for(self, view: View, resources: Resources) -> Order:
        """Where the next schedule starts and ends, and the time and CPUs it gets."""
        p, cpus = view.position, resources.cpus
        nxt = next((q for q, _ in view.transitions if q > p), INF)
        budget = self._estimate(view, cpus)
        for _ in range(2):  # the lead depends on the budget, the budget on the cells
            lead = self._lead(budget)
            s = min(p + lead, nxt)
            cuts = self.cuts(view.track, s, s + self.lookahead)
            workers, chunk, budget, n = self._fit(len(cuts) - 1, cpus)
        return Order(s, cuts[n], workers, chunk, budget, lead)

    def _estimate(self, view: View, cpus: int) -> float:
        p = view.position
        cuts = self.cuts(view.track, p, p + self.lookahead)
        return self._fit(len(cuts) - 1, cpus)[2]

    def _fit(self, n: int, cpus: int) -> tuple[int, int, float, int]:
        """(workers, chunk, budget, cells) for at most n cells, within max_budget."""
        k = len(self.modes)
        speed = self._speed()

        def cost(cells: int, workers: int) -> float:
            work = cells * k * k / speed if workers == 1 else \
                self.pool_startup + cells * k ** 3 / (speed * workers) + cells * k / speed
            return 1.25 * (self.startup + work) + 0.1

        for cells in self._shrinking(n):
            options = [(cost(cells, w), w) for w in sorted({1, cpus})]
            budget, workers = min(options)
            if budget <= self.max_budget or cells == 1:
                chunk = 256 if workers == 1 else max(256, -(-cells // (4 * workers)))
                return workers, chunk, min(budget, self.max_budget), cells
        raise AssertionError("unreachable")

    @staticmethod
    def _shrinking(n: int):
        while n > 1:
            yield n
            n = n * 3 // 4
        yield 1

    def _speed(self) -> float:
        if self.speed is None:
            k = len(self.modes)
            g = [[Fraction(i % 7, 3)] * k for i in range(200)]
            began = time.perf_counter()
            _chunk(g, self.cost, [[Fraction(0)] * k])
            self.speed = 200 * k * k / max(time.perf_counter() - began, 1e-6)
        return self.speed

    def _lead(self, budget: float) -> Fraction:
        """How far ahead of the chip a schedule starts: the chip's travel during a run, twice over."""
        travel = Fraction(2 * self._chip_speed * budget).limit_denominator(10 ** 6)
        return max(self.min_lead, travel + self.min_lead)

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
        p, track = view.position, view.track
        y = self.modes.index(view.mode)
        cuts = self.cuts(track, order.start, order.end)
        gains = self.gains(track, cuts)
        margin = 0.05 + 0.05 * deadline.budget  # to send the text back
        pool = None
        try:
            if order.workers > 1:
                pool = multiprocessing.get_context("spawn").Pool(order.workers)
            done, modes, value = solve(gains, self.cost, y, chunk=order.chunk, workers=order.workers,
                                       mapper=_pool_mapper(pool) if pool else None,
                                       time_left=lambda: deadline.remaining() - margin)
        finally:
            if pool is not None:
                pool.terminate()
        if done == 0:
            return None
        return self.text(view, order, cuts[:done + 1], modes, value, self._serial)

    def text(self, view: View, order: Order, cuts: Sequence[Fraction], modes: Sequence[int],
             value: Fraction, serial: int) -> Text:
        """The text of a schedule: mode modes[i] from cuts[i] on, the mode of the view before s."""
        p, track = view.position, view.track
        y = self.modes.index(view.mode)
        s, end = order.start, cuts[-1]
        starts = [(cuts[i], m) for i, m in enumerate(modes) if i == 0 or m != modes[i - 1]]
        parts: dict[int, list] = {}  # mode -> where the schedule is in it: [q, next q[, and [q, end] last
        for j, (q, m) in enumerate(starts):
            last = j == len(starts) - 1
            parts.setdefault(m, []).append((q, True, end if last else starts[j + 1][0], last))
        pieces = [(Region.interval(p, s, False, False), self._serves(y)),
                  *((Region(tuple(ps)), self._serves(m)) for m, ps in parts.items())]
        here = ahead(p)
        demand = tuple((K, track[K] & here) for K in self.kinds if K in track and track[K] & here)
        schedule = Schedule(self.served, tuple((w, ks) for w, ks in pieces if ks), demand, self.rates, s, end,
                            value, self._final(track, end, modes[-1]), self.tolerance, order.lead)
        # The first transition is at s, even to the mode of the view: whatever the chip
        # went through before s, the schedule holds from s on, or the install is refused.
        return Text(self.text_id(serial), follow=schedule, rerun=schedule.rerun,
                    transitions=tuple((q, self.modes[m]) for q, m in starts))

    def _final(self, track: Track, end: Fraction, last: int) -> bool:
        """Nothing changes after `end`, and `last` earns the most there."""
        if self.cuts(track, end, INF)[1:-1]:
            return False
        rates = [self.rate(m, lambda K: (end + 1) in track.get(K, EMPTY)) for m in range(len(self.modes))]
        return rates[last] == max(rates)
