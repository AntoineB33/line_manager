"""The score of OmniApp's scheduler (OmniApp's docs/scheduler_score.md, 2026-11-07), on a line.

The tasks are kinds of the line. A task is served wherever it is on the line, whoever put it
there; the time the user put a task on (`preplaced`) is that task's alone (its multiplier 1,
every other task's 0). The restrictive periods are the other kinds a task has a resilience
to: its multiplier at a position is the product of its resilience to the kinds there (1 to a
kind it names no resilience for). Nobody is where no task is.

Notation and definitions as in scheduler_score.md:
- schedulable: where some task's multiplier is > 0; the schedulable clock u counts only that
  time, so what nobody may have (a night) is transparent;
- pi_i the nominal share (priority over the sum of priorities), tau_i = max(M_i, floor) / pi_i
  the task's period, lambda_i = lambda + 3 (tau_i - lambda)+ its compensation length (or the
  task's own), rare where tau_i > rare_after, Theta = max tau_i over the ordinary tasks,
  Theta_i = Theta (ordinary) or tau_i (rare);
- the local share q_i (priority times multiplier, normalized), the compensation c_i (the
  deprivations around, decaying over lambda_i on both sides), the target f_i from both, raised
  by the quantities by a date;
- the lag dL/du = served - f - L/tau, criterion 1 its discounted square in units of the
  task's minimum, criterion 2 the shortfall of each panel under its minimum, criterion 3 the
  excess over its maximum (the open panel at the end charged too), D the quantities' shortfall.

The score of the line from the chip c to `end` is (D, J), J = J1 + w J2 + w_X J3: the
integral and the panels on (c, end], and past `end` criterion 1 closed in closed form, each task
then served at its target (`_tail`); the past read back to each task's reach (4 lambda_i).
Within `history` of the chip everything is read; further back (a rare task's reach), only the
task's own presence and the schedulable clock (where no task may be, by the resilience 0 of the
kinds there, is not on it), the task at its nominal share there. Ahead, the compensation reads
the line as far as `history` past `end`.

Formulas: a priority, minimum or maximum given as a formula of the position (`Step`,
`Linear`, `Periodic`, or any object with `at(x)` and `changes(a, b)`) is read at each position
as if its value there held at all times: (c, end] is cut where a formula changes, and each
piece is scored with the values at its start, the whole past measured against them.

Numbers: positions are those of the line (their real parts); the score itself is a float.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction

from intervals import INF, Interval, IntervalSet
from region import Region


# ------------------------------------------------------------------ formulas

class Step:
    """A value per interval: `before` up to the first change, then each (x, v): v from x on
    (from x excluded: (x, next])."""

    def __init__(self, before, changes=()) -> None:
        self.before = float(before)
        self.points = sorted((Fraction(x), float(v)) for x, v in changes)

    def at(self, x) -> float:
        v = self.before
        for p, w in self.points:
            if x > p:
                v = w
        return v

    def changes(self, a, b) -> list:
        return [p for p, _ in self.points if a < p < b]


class Linear:
    """From v0 at x0 to v1 at x1, linearly (constant before and after); read in `steps` steps
    between them (the infinitesimal steps of the requirement, approximated)."""

    def __init__(self, x0, v0, x1, v1, steps: int = 32) -> None:
        self.x0, self.x1, self.v0, self.v1 = Fraction(x0), Fraction(x1), float(v0), float(v1)
        self.steps = steps

    def at(self, x) -> float:
        if x <= self.x0:
            return self.v0
        if x >= self.x1:
            return self.v1
        return self.v0 + (self.v1 - self.v0) * float((Fraction(x) - self.x0) / (self.x1 - self.x0))

    def changes(self, a, b) -> list:
        d = (self.x1 - self.x0) / self.steps
        return [self.x0 + d * k for k in range(self.steps + 1) if a < self.x0 + d * k < b]


class Periodic:
    """A formula over [0, period) repeated: at(x) = f.at(x mod period)."""

    def __init__(self, f, period) -> None:
        self.f, self.period = f, Fraction(period)

    def at(self, x) -> float:
        return self.f.at(Fraction(x) % self.period)

    def changes(self, a, b) -> list:
        out, k = [], (Fraction(a) // self.period) * self.period
        while k < b:
            out += [k + p for p in self.f.changes(Fraction(0), self.period) + [Fraction(0)] if a < k + p < b]
            k += self.period
        return sorted(set(out))


def _at(v, x) -> float:
    return v.at(x) if hasattr(v, "at") else float(v)


def _changes(v, a, b) -> list:
    return v.changes(a, b) if hasattr(v, "changes") else []


# ------------------------------------------------------------------ definitions

@dataclass
class Task:
    priority: object  # a number >= 0, or a formula
    minimum: object  # M_i > 0, or a formula
    maximum: object = math.inf  # X_i >= M_i (inf: none), or a formula
    resilience: dict = field(default_factory=dict)  # period kind -> r in [0, 1] (1 if not named)
    compensation: float | None = None  # its own lambda_i (None: from the default)
    quantities: tuple = ()  # (Q, a, b): Q of presence within [a, b]


@dataclass
class Settings:
    compensation: float = 4 * 3600  # lambda, the default compensation length
    rare_after: float = 168 * 3600  # H: a task with tau_i above it is rare
    floor: float = 60  # max(M_i, floor): the "1 minute" of the score
    minimum_weight: float = 1.0  # w
    maximum_weight: float = 1.0  # w_X
    history: float | None = None  # how far back everything is read (None: 4 lambda, the default compensation length)


@dataclass
class Result:
    D: float
    J: float
    J1: float
    J2: float
    J3: float

    def key(self) -> tuple:
        return (round(self.D), self.J)  # (D within a second is equal: scheduler_score.md)


# ------------------------------------------------------------------ the score

class Score:
    def __init__(self, tasks: dict[str, Task], settings: Settings | None = None) -> None:
        self.tasks = dict(tasks)
        self.s = settings or Settings()
        self.periods = sorted({k for t in self.tasks.values() for k in t.resilience} - set(self.tasks))
        for name, t in self.tasks.items():
            for x in (0,):
                if _at(t.maximum, x) < _at(t.minimum, x):
                    raise ValueError(f"{name}: a maximum execution time below its minimum")

    # --- the rule state at a position (the formulas read there)
    def rule_state(self, x) -> dict:
        names = sorted(self.tasks)
        P = {i: max(0.0, _at(self.tasks[i].priority, x)) for i in names}
        total = sum(P.values())
        pi = {i: (P[i] / total if total > 0 else 1 / len(names)) for i in names}
        M = {i: max(_at(self.tasks[i].minimum, x), 0.0) for i in names}
        X = {i: _at(self.tasks[i].maximum, x) for i in names}
        tau = {i: (max(M[i], self.s.floor) / pi[i] if pi[i] > 0 else math.inf) for i in names}
        lam_d = self.s.compensation
        lam = {i: (self.tasks[i].compensation if self.tasks[i].compensation else lam_d + 3 * max(0.0, tau[i] - lam_d))
               for i in names}
        ordinary = [tau[i] for i in names if pi[i] > 0 and tau[i] <= self.s.rare_after]
        theta = max(ordinary) if ordinary else max((tau[i] for i in names if pi[i] > 0), default=self.s.rare_after)
        for i in names:
            if pi[i] == 0:
                tau[i] = theta  # (a task with no share forgets over Theta)
                lam[i] = self.tasks[i].compensation or lam_d
        rare = {i: tau[i] > self.s.rare_after for i in names}
        Th = {i: (tau[i] if rare[i] else theta) for i in names}
        return dict(P=P, pi=pi, M=M, X=X, tau=tau, lam=lam, rare=rare, Theta=Th, names=names)

    # --- the segments of the line where nothing changes
    def _segments(self, line, preplaced, lo, hi, finest=None, at=()) -> list:
        """The segments of [lo, hi] where nothing on the line changes, none longer than
        `finest` (the targets are read at their middles: the compensation varies inside), cut at
        the positions `at` too."""
        kinds = sorted(self.tasks) + self.periods
        pres = {k: line.presence(k) for k in kinds}
        lo, hi = Fraction(lo), Fraction(hi)
        cuts = {lo, hi} | {Fraction(x) for x in at if lo < Fraction(x) < hi}
        for k in kinds:
            for r in (pres[k], preplaced.get(k, Region.empty())):
                for iv in r.on(lo, hi).intervals:
                    for p in (iv.lo, iv.hi):
                        x = p.x if hasattr(p, "x") else p
                        if lo < x < hi:
                            cuts.add(Fraction(x))
        cuts = sorted(cuts)
        if finest:
            step = Fraction(finest)
            fine = []
            for a, b in zip(cuts, cuts[1:]):
                n = max(1, math.ceil((b - a) / step))
                fine += [a + (b - a) * j / n for j in range(n)]
            cuts = fine + [cuts[-1]]
        segs = []
        for a, b in zip(cuts, cuts[1:]):
            mid = (a + b) / 2
            on = frozenset(k for k in kinds if pres[k].contains(mid))
            pre = next((k for k in sorted(self.tasks) if preplaced.get(k) is not None and preplaced[k].contains(mid)), None)
            segs.append((float(a), float(b), on, pre))
        return segs

    def _history(self) -> float:
        """How far from the chip the whole line is read: `history`, or 4 default compensation
        lengths. Further, each task reads only its own panels and the schedulable clock, as far
        as its own reach (scheduler_score.md § How far back: the environment beyond the history
        kept whole is uniform)."""
        return self.s.history if self.s.history is not None else 4 * self.s.compensation

    def _mu(self, seg) -> dict:
        _, _, on, pre = seg
        out = {}
        for i, t in self.tasks.items():
            if pre is not None:
                out[i] = 1.0 if i == pre else 0.0
                continue
            m = 1.0
            for k in on:
                if k in t.resilience:
                    m *= t.resilience[k]
            out[i] = m
        return out

    def target(self, line, x, preplaced: dict | None = None) -> dict:
        """f_i at x (scheduler_score.md § The target share), under the rule state at x."""
        preplaced = preplaced or {}
        R = self.rule_state(x)
        span = self._history()
        segs = self._segments(line, preplaced, float(x) - span, float(x) + span, min(R["lam"].values()) / 16)
        mus = [self._mu(s) for s in segs]
        sched = [any(m > 0 for m in mu.values()) for mu in mus]
        u0, u = [], 0.0
        for s, ok in zip(segs, sched):
            u0.append(u)
            u += (s[1] - s[0]) if ok else 0.0
        targets = self._targets(R, segs, mus, sched, u0, line, preplaced)
        k = next(k for k, s in enumerate(segs) if s[0] <= float(x) < s[1])
        return targets[k]

    def evaluate(self, line, c, end, preplaced: dict | None = None) -> Result:
        """(D, J) of the line from the chip c to `end` (scheduler_score.md)."""
        preplaced = preplaced or {}
        c, end = Fraction(c), Fraction(end)
        cuts = [c]
        for t in self.tasks.values():
            for v in (t.priority, t.minimum, t.maximum):
                cuts += [Fraction(x) for x in _changes(v, c, end)]
        cuts = sorted(set(cuts)) + [end]
        D = self._shortfall(line, c, end, preplaced)
        J1 = J2 = J3 = 0.0
        for a, b in zip(cuts, cuts[1:]):
            R = self.rule_state(a)
            j1, j2, j3 = self._piece(line, preplaced, R, c, a, b, end)
            J1, J2, J3 = J1 + j1, J2 + j2, J3 + j3
        J = J1 + self.s.minimum_weight * J2 + self.s.maximum_weight * J3
        return Result(D, J, J1, J2, J3)

    # --- one piece (a, b] of (c, end], scored under the rule state R
    def _piece(self, line, preplaced, R, c, a, b, end):
        names = R["names"]
        reach = {i: 4 * R["lam"][i] for i in names}
        full = self._history()
        lo = float(c) - full
        hi = float(end) + full  # (compensation reads the deprivations ahead too, as far as behind)
        segs = self._segments(line, preplaced, lo, hi, min(R["lam"].values()) / 16)
        mus = [self._mu(s) for s in segs]
        sched = [any(m > 0 for m in mu.values()) for mu in mus]
        # the schedulable clock at each segment's start
        u0, u = [], 0.0
        for s, ok in zip(segs, sched):
            u0.append(u)
            if ok:
                u += s[1] - s[0]
        uc = self._u_at(segs, sched, u0, float(c))
        targets = self._targets(R, segs, mus, sched, u0, line, preplaced)
        # the lags, from each task's reach to b
        L = {i: self._far_lag(R, i, line, preplaced, lo, float(c), reach[i]) for i in names}
        run = {i: 0.0 for i in names}  # the open panel of each task, on the schedulable clock
        J1 = J2 = J3 = 0.0
        fa, fb = float(a), float(b)
        for k, (s, ok) in enumerate(zip(segs, sched)):
            x0, x1 = s[0], s[1]
            if x1 <= lo or x0 >= fb or not ok:
                continue
            x0c, x1c = max(x0, lo), min(x1, fb)
            h = x1c - x0c
            served = {i: (1.0 if i in s[2] else 0.0) for i in names}
            ustart = u0[k] + (x0c - x0)
            for i in names:
                tau = R["tau"][i]
                f = targets[k][i]
                A = (served[i] - f) * tau
                B = L[i] - A
                if x1c > fa and x0c < fb:  # (in the piece: criterion 1 on its part)
                    p0 = max(x0c, fa)
                    d = p0 - x0c
                    Bp = B * math.exp(-d / tau)
                    hp = min(x1c, fb) - p0
                    v0 = ustart + d - uc
                    J1 += self._sq_integral(A, Bp, tau, R["Theta"][i], v0, hp) / max(R["M"][i], self.s.floor) ** 2
                L[i] = A + B * math.exp(-h / tau)
                # the panels (criteria 2 and 3)
                if served[i]:
                    run[i] += h
                elif run[i] > 0:
                    # (a panel ending AT the chip is charged too: the continuation ends it there, and a
                    # re-plan must not drop a short panel for nothing; OmniApp's ScoreModel and the search's
                    # model, score_search.Model, charge it)
                    if fa < x0c <= fb or (x0c == fa and fa == float(c)):
                        J2, J3 = self._charge(R, i, run[i], ustart - uc, J2, J3)
                    run[i] = 0.0
        if float(b) == float(end) and not getattr(self, "_no_tail", False):  # the continuation past `end`, closed in closed form
            ue = self._u_at(segs, sched, u0, float(end)) - uc
            for i in names:
                J1 += self._tail(L[i], R["tau"][i], R["Theta"][i], ue) / max(R["M"][i], self.s.floor) ** 2
        for i in names:  # the panel still open at the end of the piece: its excess only (an open one at `end`)
            if run[i] > 0 and float(b) == float(end):
                ue = self._u_at(segs, sched, u0, float(end)) - uc
                X = R["X"][i]
                if run[i] > X:
                    e = run[i] - X
                    J3 += math.exp(-ue / R["Theta"][i]) * R["tau"][i] * e * (2 * R["M"][i] + e) / max(R["M"][i], self.s.floor) ** 2
        return J1, J2, J3

    def _charge(self, R, i, ell, ue, J2, J3):
        M, X, tau, Th = R["M"][i], R["X"][i], R["tau"][i], R["Theta"][i]
        unit = max(M, self.s.floor) ** 2
        disc = math.exp(-max(ue, 0.0) / Th)
        s = max(0.0, M - ell)
        if s:
            J2 += disc * tau * s * (2 * M + s) / unit
        e = max(0.0, ell - X)
        if e:
            J3 += disc * tau * e * (2 * M + e) / unit
        return J2, J3

    @staticmethod
    def _tail(L, tau, Th, ue) -> float:
        """Criterion 1 past `end` for a lag L there, the task served at its target from then on
        (its lag decaying over its period): ∫_0^∞ e^{-(ue + v)/Th} (L e^{-v/tau})^2 dv. A lower
        bound of what any continuation costs, as the rollout of scheduler_score.md closes its
        trials; without it, a lag left behind at the window's end would cost nothing, and
        leaving the window to nobody would look cheap."""
        return math.exp(-ue / Th) * L * L / (1 / Th + 2 / tau)

    @staticmethod
    def _sq_integral(A, B, tau, Th, v0, h) -> float:
        """∫_0^h e^{-(v0 + v)/Th} (A + B e^{-v/tau})^2 dv."""
        def ex(rate):  # ∫_0^h e^{-rate v} dv
            return h if rate == 0 else (1 - math.exp(-rate * h)) / rate
        base = math.exp(-v0 / Th)
        return base * (A * A * ex(1 / Th) + 2 * A * B * ex(1 / Th + 1 / tau) + B * B * ex(1 / Th + 2 / tau))

    @staticmethod
    def _u_at(segs, sched, u0, x) -> float:
        for k, s in enumerate(segs):
            if s[0] <= x <= s[1]:
                return u0[k] + ((x - s[0]) if sched[k] else 0.0)
        return u0[-1] if u0 else 0.0

    def _targets(self, R, segs, mus, sched, u0, line, preplaced) -> list:
        """f_i at each segment (at its middle): the local share, the compensation, the
        quantities' raise."""
        names = R["names"]
        out = []
        cc = self._compensation(R, segs, mus, sched, u0)
        for k, s in enumerate(segs):
            if not sched[k]:
                out.append({i: 0.0 for i in names})
                continue
            mu = mus[k]
            tot = sum(R["P"][i] * mu[i] for i in names)
            if tot > 0:
                q = {i: R["P"][i] * mu[i] / tot for i in names}
            else:
                allowed = [i for i in names if mu[i] > 0]
                q = {i: (1 / len(allowed) if i in allowed else 0.0) for i in names}
            C = sum(cc[i][k] for i in names)
            f = {i: (q[i] * (1 - C) + cc[i][k]) if C <= 1 else cc[i][k] / C for i in names}
            f = self._raise(f, k, segs, sched, mus, line, preplaced, names)
            out.append(f)
        return out

    def _compensation(self, R, segs, mus, sched, u0) -> dict:
        """c_i at each segment's middle, in linear time: for each value v the multiplier of
        task i takes, the deprivations of that value behind and ahead, each a running sum
        decaying over lambda_i (a segment of length l, d away, weighs lambda (1 - e^{-l/lambda})
        e^{-d/lambda}: the integral of the kernel over it), then
        c_i = pi_i / lambda_i * sum_v (mu_i - v)+ (behind_v + ahead_v)."""
        names, n = R["names"], len(segs)
        idx = [k for k in range(n) if sched[k]]
        mid = {k: u0[k] + (segs[k][1] - segs[k][0]) / 2 for k in idx}
        out = {}
        for i in names:
            lam, pi = R["lam"][i], R["pi"][i]
            values = sorted({round(mus[k][i], 12) for k in idx})
            ci = [0.0] * n
            if len(values) > 1 and pi > 0:
                near = {}
                for v in values:
                    near[v] = [0.0] * n
                    acc, at = 0.0, None  # behind: from the left
                    for k in idx:
                        if at is not None:
                            acc *= math.exp(-(mid[k] - at) / lam)
                        near[v][k] += acc
                        ln = segs[k][1] - segs[k][0]
                        if round(mus[k][i], 12) == v:
                            acc = acc * math.exp(-(u0[k] + ln - mid[k]) / lam) + lam * (1 - math.exp(-ln / lam))
                        else:
                            acc *= math.exp(-(u0[k] + ln - mid[k]) / lam)
                        at = u0[k] + ln
                    acc, at = 0.0, None  # ahead: from the right
                    for k in reversed(idx):
                        if at is not None:
                            acc *= math.exp(-(at - mid[k]) / lam)
                        near[v][k] += acc
                        ln = segs[k][1] - segs[k][0]
                        if round(mus[k][i], 12) == v:
                            acc = acc * math.exp(-(mid[k] - u0[k]) / lam) + lam * (1 - math.exp(-ln / lam))
                        else:
                            acc *= math.exp(-(mid[k] - u0[k]) / lam)
                        at = u0[k]
                for k in idx:
                    m = mus[k][i]
                    ci[k] = pi / lam * sum((m - v) * near[v][k] for v in values if m - v > 0)
            out[i] = ci
        return out

    def _raise(self, f, k, segs, sched, mus, line, preplaced, names) -> dict:
        """The quantities by a date: the target raised to the share each asks there."""
        x = (segs[k][0] + segs[k][1]) / 2
        g = {}
        for i in names:
            for Q, a, b in self.tasks[i].quantities:
                if not float(a) <= x < float(b):
                    continue
                served = line.presence(i).measure_on(IntervalSet.of(Interval(Fraction(a), Fraction(x), False, True)))
                missing = max(0.0, float(Q) - float(served))
                avail = sum(min(s[1], float(b)) - max(s[0], x) for j, s in enumerate(segs)
                            if sched[j] and mus[j][i] > 0 and s[1] > x and s[0] < float(b))
                if avail > 0:
                    g[i] = max(g.get(i, 0.0), min(1.0, missing / avail))
        raised = {i: v for i, v in g.items() if v > f[i]}
        if not raised:
            return f
        total = sum(raised.values())
        if total >= 1:
            return {i: (raised[i] / total if i in raised else 0.0) for i in names}
        rest = sum(f[i] for i in names if i not in raised)
        scale = (1 - total) / rest if rest > 0 else 0.0
        return {i: (raised[i] if i in raised else f[i] * scale) for i in names}

    def _far_lag(self, R, i, line, preplaced, lo, c, reach) -> float:
        """The lag of task i at `lo` (where everything starts being read): its own presence
        and the schedulable clock from c - reach, at its nominal share."""
        start = c - reach
        if start >= lo:
            return 0.0
        tau, pi = R["tau"][i], R["pi"][i]
        pres = line.presence(i).on(Fraction(start), Fraction(lo)).intervals
        key = (i, start, lo, tau, pi, tuple((iv.lo, iv.hi) for iv in pres))
        cache = self.__dict__.setdefault("_far_cache", {})
        if key in cache:
            return cache[key]
        if len(cache) > 4096:
            cache.clear()
        blocked = self._nobody(line)

        def du(a, b):  # (schedulable time between a and b: the clock of the score)
            if b <= a:
                return 0.0
            w = IntervalSet.of(Interval(Fraction(a), Fraction(b), False, True))
            return float(Fraction(b) - Fraction(a) - blocked.measure_on(w))
        L, x = 0.0, start
        for iv in pres:
            a = max(float(iv.lo.x if hasattr(iv.lo, "x") else iv.lo), start)
            b = min(float(iv.hi.x if hasattr(iv.hi, "x") else iv.hi), lo)
            if b <= a:
                continue
            L = -pi * tau + (L + pi * tau) * math.exp(-du(x, a) / tau)  # (not served)
            L = (1 - pi) * tau + (L - (1 - pi) * tau) * math.exp(-du(a, b) / tau)  # (served)
            x = b
        cache[key] = -pi * tau + (L + pi * tau) * math.exp(-du(x, lo) / tau)
        return cache[key]

    def _nobody(self, line) -> Region:
        """Where no task may be by its resilience (each forbidden by some kind there): not on
        the schedulable clock."""
        kinds = sorted({k for t in self.tasks.values() for k, r in t.resilience.items() if r == 0})
        regions = tuple(line.presence(k) for k in kinds)
        memo = getattr(self, "_nobody_memo", None)
        if memo is not None and len(memo[0]) == len(regions) and all(a is b or a == b for a, b in zip(memo[0], regions)):
            return memo[1]
        out = None
        for t in self.tasks.values():
            mine = Region.empty()
            for k, r in t.resilience.items():
                if r == 0:
                    mine = mine | line.presence(k)
            out = mine if out is None else out & mine
        out = out if out is not None else Region.empty()
        self._nobody_memo = (regions, out)
        return out

    def _shortfall(self, line, c, end, preplaced) -> float:
        """D: what each quantity misses at its date (one past `end`: what it misses at `end`
        beyond what the time still available to it after `end` can serve)."""
        D = 0.0
        for i, t in self.tasks.items():
            for Q, a, b in t.quantities:
                if Fraction(b) <= c:
                    continue
                upto = min(Fraction(b), end)
                served = line.presence(i).measure_on(IntervalSet.of(Interval(Fraction(a), upto, False, True)))
                missing = max(0.0, float(Q) - float(served))
                if Fraction(b) > end:
                    missing = max(0.0, missing - float(Fraction(b) - end))
                D += missing
        return D


# ------------------------------------------------------------------ the problem of the generator

FACTORS = {"left": 1, "right": 2, "spread2": 1.5, "spread4": 1, "spread8": 1, "spread16": 1}


class ScoreProblem:
    """OmniApp's score as a problem of the rules generator (problems.py): its error is J on
    the generator's window (c, c + H], and its `shortfall`, D, comes before every error
    (generator._Context.score). Its proposals fill the window from the chip with panels: at
    each position, each task the rules let be there for its minimum (times the strategy's
    factor), or nobody, scored one step ahead by criterion 1 and closed by the tail (the score's
    own measure), the lags carried at each task's share (raised by its quantities). The user's tasks stay where they are (they are the time of their
    task alone); the generator's own task elements in the window are placed again."""

    name = "score"

    def __init__(self, score: Score, window=None) -> None:
        self.score, self.window = score, None if window is None else Fraction(window)
        self._memo = None

    def with_window(self, H):
        return ScoreProblem(self.score, H)

    @property
    def lengths(self) -> dict:
        return {k: Fraction(max(_at(t.minimum, 0), 1)).limit_denominator(1000) for k, t in self.score.tasks.items()}

    def has_targets(self) -> bool:
        return bool(self.score.tasks)

    def _preplaced(self, s) -> dict:
        from line import USER
        return {k: s.claims.owned(k, USER) for k in self.score.tasks}

    def _result(self, ctx, s) -> Result:
        m = self._memo  # (the state itself, not its id: an id is reused once the state is gone)
        if m is None or m[0] is not s or m[1] != ctx.c:
            m = self._memo = (s, ctx.c, self.score.evaluate(s.line, ctx.c, Fraction(ctx.c) + self.window, self._preplaced(s)))
        return m[2]

    def error(self, ctx, s) -> float:
        return self._result(ctx, s).J

    def shortfall(self, ctx, s) -> float:
        return self._result(ctx, s).D

    def extra(self, ctx) -> list:
        return []

    SEARCH_TIME = 4.0  # seconds of the whole-continuation search per run (score_search.py)

    def proposals(self, ctx, pred: str, strategy: str):
        from line import GEN, Op
        from problems import forbidden
        if strategy == "spread4":  # (the first strategy: the search over the whole continuation)
            return self._searched(ctx)
        factor = FACTORS.get(strategy, 1)
        s, c = ctx.state, Fraction(ctx.c)
        end = c + self.window
        W = Region.of(Interval(c, end, False, True))
        sc = self.score
        R = sc.rule_state(c)
        names = R["names"]
        pre = self._preplaced(s)
        taken = Region.empty()
        for r in pre.values():
            taken = taken | r
        memo = getattr(self, "_lags_memo", None)
        if memo is None or memo[0] is not s or memo[1] != c:
            memo = self._lags_memo = (s, c, self.lags_at(s.line, c, pre))
        L = dict(memo[2])
        no = {}
        for i in names:  # (where the rules forbid it, the user's tasks, the kinds it has resilience 0 to)
            no[i] = forbidden(ctx, i) | taken
            for k, r in sc.tasks[i].resilience.items():
                if r == 0:
                    no[i] = no[i] | s.line.presence(k)
        out = {i: [] for i in names}
        t = c
        for _ in range(10000):
            if t >= end:
                break
            ok = [i for i in names if R["pi"][i] > 0 and not no[i].contains(t + Fraction(1, 1000))]
            if not ok:
                nxt = [iv.hi.x if hasattr(iv.hi, "x") else iv.hi for i in names
                       for iv in no[i].on(t, end).intervals if iv.lo <= t + Fraction(1, 1000) <= iv.hi]
                t = min([Fraction(x) for x in nxt if x != INF and Fraction(x) > t] + [end])
                continue
            share = self._shares(R, s.line, out, t)
            ends = {}
            for i in ok:
                want = Fraction(R["M"][i] * factor).limit_denominator(1000)
                stop = [iv.lo.x if hasattr(iv.lo, "x") else iv.lo for iv in no[i].on(t, t + want).intervals]
                b = min([Fraction(x) for x in stop if Fraction(x) > t] + [t + want, end])
                for Q, qa, qb in sc.tasks[i].quantities:  # (a quantity reached: not past it for its sake)
                    if Fraction(qa) <= t < Fraction(qb) and share[i] > R["pi"][i]:
                        left = Fraction(Q) - self._served(s.line, out, i, Fraction(qa), t)
                        if 0 < left < b - t:
                            b = t + left
                ends[i] = b
            # each task for its panel, or nobody, then nobody up to the longest: the one whose lags
            # end the smallest in criterion 1's units (the score's own choice, one step ahead)
            far = max(ends.values())
            unit = {j: max(R["M"][j], sc.s.floor) for j in names}

            def after(i, b):
                """Criterion 1 of: i's panel (or nothing), then nobody up to the longest panel,
                then the tail (each task at its target): the score's own measure, one step ahead."""
                cost, M2, v = 0.0, dict(L), 0.0
                parts = ([(i, float(b - t))] if i is not None else []) + [(None, float(far - (b if i is not None else t)))]
                for who, h in parts:
                    if h <= 0:
                        continue
                    for j in names:
                        tau = R["tau"][j]
                        A = ((1.0 if j == who else 0.0) - share[j]) * tau
                        cost += Score._sq_integral(A, M2[j] - A, tau, R["Theta"][j], v, h) / unit[j] ** 2
                    M2 = self._step(R, share, M2, who, h)
                    v += h
                return cost + sum(Score._tail(M2[j], R["tau"][j], R["Theta"][j], v) / unit[j] ** 2 for j in names)
            best = min([(after(i, ends[i]), i) for i in ok] + [(after(None, t), None)], key=lambda x: (x[0], x[1] or ""))
            i = best[1]
            if i is None:  # nobody: until the shortest panel would end
                b = min(ends.values())
                L = self._step(R, share, L, None, float(b - t))
                t = b
                continue
            b = ends[i]
            out[i].append((t, b))
            L = self._step(R, share, L, i, float(b - t))
            t = b
        ops = []
        for i in names:
            mine = s.claims.owned(i, GEN) & W
            if not mine.is_empty():
                ops.append(Op.take(i, mine))
            if out[i]:
                ops.append(Op.put(i, Region.of(IntervalSet([Interval(a, b, False, True) for a, b in out[i]]))))
        return tuple(ops)

    def _searched(self, ctx):
        """score_search.search's continuation as an edit, once per state and chip."""
        from line import GEN, Op
        from score_search import search
        s, c = ctx.state, Fraction(ctx.c)
        memo = getattr(self, "_search_memo", None)
        if memo is not None and memo[0] is s and memo[1] == c:
            return memo[2]
        W = Region.of(Interval(c, c + self.window, False, True))
        found = search(self, ctx, s, self.SEARCH_TIME)
        ops = []
        for i in sorted(self.score.tasks):
            mine = s.claims.owned(i, GEN) & W
            if not mine.is_empty():
                ops.append(Op.take(i, mine))
            if found.get(i):
                ops.append(Op.put(i, Region.of(IntervalSet([Interval(a, b, False, True) for a, b in found[i]]))))
        self._search_memo = (s, c, tuple(ops))
        return tuple(ops)

    @staticmethod
    def _step(R, share, L, i, h) -> dict:
        """The lags after h of task i (None: nobody), each at its share."""
        out = {}
        for j, v in L.items():
            tau = R["tau"][j]
            A = ((1.0 if j == i else 0.0) - share[j]) * tau
            out[j] = A + (v - A) * math.exp(-h / tau)
        return out

    @staticmethod
    def _served(line, out, i, a, t) -> Fraction:
        """What task i has on [a, t): on the line before the chip, and what the fill put."""
        got = Fraction(0)
        for x, y in out[i]:
            got += max(Fraction(0), min(y, t) - max(x, a))
        first = min([x for x, _ in out[i]], default=t)
        if first > a:
            got += line.presence(i).measure_on(IntervalSet.of(Interval(a, min(first, t), False, True))) if a < min(first, t) else 0
        return got

    def _shares(self, R, line, out, t) -> dict:
        """The nominal shares, raised where a quantity by a date asks more (what it misses over
        the time left to its date; score.Score._raise for the exact target)."""
        share = dict(R["pi"])
        g = {}
        for i, task in self.score.tasks.items():
            for Q, a, b in task.quantities:
                a, b = Fraction(a), Fraction(b)
                if a <= t < b:
                    missing = max(Fraction(0), Fraction(Q) - self._served(line, out, i, a, t))
                    g[i] = max(g.get(i, 0.0), min(1.0, float(missing / (b - t))))
        raised = {i: v for i, v in g.items() if v > share[i]}
        if not raised:
            return share
        total = sum(raised.values())
        rest = sum(v for i, v in share.items() if i not in raised)
        scale = max(0.0, 1 - total) / rest if rest > 0 else 0.0
        return {i: (raised[i] if i in raised else v * scale) for i, v in share.items()}

    def lags_at(self, line, c, preplaced) -> dict:
        """Each task's lag at c, under the rule state at c."""
        sc = self.score
        R = sc.rule_state(c)
        names = R["names"]
        reach = {i: 4 * R["lam"][i] for i in names}
        full = sc._history()
        lo = float(c) - full
        segs = sc._segments(line, preplaced, lo, float(c) + full, min(R["lam"].values()) / 16)
        mus = [sc._mu(x) for x in segs]
        sched = [any(m > 0 for m in mu.values()) for mu in mus]
        u0, u = [], 0.0
        for x, ok in zip(segs, sched):
            u0.append(u)
            u += (x[1] - x[0]) if ok else 0.0
        targets = sc._targets(R, segs, mus, sched, u0, line, preplaced)
        L = {i: sc._far_lag(R, i, line, preplaced, lo, float(c), reach[i]) for i in names}
        for k, (x, ok) in enumerate(zip(segs, sched)):
            if not ok or x[0] >= float(c):
                continue
            h = min(x[1], float(c)) - x[0]
            for i in names:
                tau = R["tau"][i]
                A = ((1.0 if i in x[2] else 0.0) - targets[k][i]) * tau
                L[i] = A + (L[i] - A) * math.exp(-h / tau)
        return L
