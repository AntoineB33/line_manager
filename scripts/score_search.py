"""The search over the whole continuation for OmniApp's score (score.ScoreProblem), after
OmniApp's docs/scheduler_score.md § Degradation: seeds that compete (the window at the shares,
from several phases), each improved over the whole continuation, a change kept only when the
whole scores lower. `Model.rollout` (one or two runs deep) is there but not used: within the
time, it did worse than improving more seeds. (No branch-and-bound yet, and the continuation a
re-plan replaces is not a seed yet.)

It works on a model of the window (c, end], fast enough to try thousands of continuations:
- the schedulable clock u: only where some task may be; what nobody may have (a night) is not
  on it, so a run crosses it;
- stretches of u where nothing changes, each with the tasks allowed there (the rules, the
  resilience 0, the user's tasks: a stretch the user put a task on is that task's, fixed) and
  the local share of each (score.Score's targets there, compensation included, read once);
- the lags at the chip, exact (ScoreProblem.lags_at), the panel open at the chip.
A continuation is a list of runs (task or None for nobody, length on u) covering the window.
Its cost is the score's: criterion 1 integrated piece by piece and closed by the tail, the
panels' shortfall and excess, and first what the quantities by a date miss. The generator then
scores what this returns with the exact score (score.Score.evaluate), like any proposal.
"""

from __future__ import annotations

import math
import time
from fractions import Fraction

from intervals import INF, Interval, IntervalSet
from region import Region


class Model:
    def __init__(self, problem, ctx, state, budget: float = 2.0) -> None:
        from line import GEN
        from problems import forbidden
        self.p, self.sc = problem, problem.score
        self.c = Fraction(ctx.c)
        self.end = self.c + problem.window
        self.deadline = time.perf_counter() + budget
        sc = self.sc
        R = self.R = sc.rule_state(self.c)
        self.names = R["names"]
        pre = problem._preplaced(state)
        self.unit = {j: max(R["M"][j], sc.s.floor) for j in self.names}
        # the line without the generator's own tasks in the window: what stays
        W = Region.of(Interval(self.c, self.end, False, True))
        # the targets read over the span the exact score reads (its compensation looks `history`
        # either side), the window's part kept: the model scores what Score.evaluate scores
        full = sc._history()
        segs = sc._segments(state.line, pre, float(self.c) - full, float(self.end) + full, min(R["lam"].values()) / 4,
                            at=(self.c, self.end))
        mus = [sc._mu(x) for x in segs]
        sched = [any(m > 0 for m in mu.values()) for mu in mus]
        u0, u = [], 0.0
        for x, ok in zip(segs, sched):
            u0.append(u)
            u += (x[1] - x[0]) if ok else 0.0
        targets = sc._targets(R, segs, mus, sched, u0, state.line, pre)
        keep = [k for k, x in enumerate(segs) if x[0] >= float(self.c) - 1e-9 and x[1] <= float(self.end) + 1e-9]
        base = u0[keep[0]] if keep else 0.0
        segs, mus, sched, targets = [segs[k] for k in keep], [mus[k] for k in keep], [sched[k] for k in keep], [targets[k] for k in keep]
        u0 = [u0[k] - base for k in keep]
        u = (u0[-1] + ((segs[-1][1] - segs[-1][0]) if sched[-1] else 0.0)) if keep else 0.0
        no = {}
        # (the generator's tasks in the window are what the search replaces: they forbid nothing,
        # a plan of the step before included, pace.py)
        W = Region.of(Interval(self.c, self.end, False, True))
        vals = {k: ({v: r - (state.claims.owned(k, GEN) & W) for v, r in d.items()} if k in sc.tasks else d)
                for k, d in state.line.vals.items()}
        for i in self.names:
            no[i] = forbidden(ctx, i, vals)
            for k, r in sc.tasks[i].resilience.items():
                if r == 0:
                    no[i] = no[i] | state.line.presence(k)
        # the stretches of u: (u start, u end, wall start, wall end, allowed, fixed task, shares)
        self.st = []
        for k, (x, ok) in enumerate(zip(segs, sched)):
            if not ok:
                continue
            mid = Fraction(x[0] + x[1]) / 2
            fixed = x[3]
            allowed = frozenset([fixed]) if fixed else frozenset(
                i for i in self.names if R["pi"][i] > 0 and mus[k][i] > 0 and not no[i].contains(mid))
            self.st.append((u0[k], u0[k] + x[1] - x[0], x[0], x[1], allowed, fixed, targets[k]))
        self.U = u
        self.L0 = problem.lags_at(state.line, self.c, pre)
        # the panel open at the chip: its task and how long it has run (on u, back to the chip)
        self.open = (None, 0.0)
        for i in self.names:
            pres = state.line.presence(i) - (state.claims.owned(i, GEN) & W)
            if pres.contains(self.c):
                iv = next(v for v in pres.on(self.c - 10 ** 7, self.c + 1).intervals if v.lo <= self.c <= v.hi)
                self.open = (i, float(self.c - (iv.lo.x if hasattr(iv.lo, "x") else iv.lo)))
        self.quantities = [(i, float(Q), float(a), float(b)) for i in self.names for Q, a, b in sc.tasks[i].quantities
                           if float(b) > float(self.c)]
        self.served_before = {}
        for i, Q, a, b in self.quantities:
            lo = Fraction(a)
            self.served_before[(i, a, b)] = float(state.line.presence(i).measure_on(
                IntervalSet.of(Interval(lo, self.c, False, True)))) if lo < self.c else 0.0

    def out_of_time(self) -> bool:
        return time.perf_counter() > self.deadline

    # ---- what a continuation costs
    def stretch_at(self, u):
        for s in self.st:
            if s[0] <= u < s[1]:
                return s
        return None

    def cost(self, runs) -> tuple:
        """(D, J) of a continuation, the score's."""
        R, names, unit = self.R, self.names, self.unit
        L = dict(self.L0)
        J1 = J2 = J3 = 0.0
        cur, length = self.open
        served = {}
        u, k = 0.0, 0
        for who, h in runs:
            end = u + h
            while u < end - 1e-9 and k < len(self.st):
                s = self.st[k]
                if u >= s[1] - 1e-9:
                    k += 1
                    continue
                piece = min(end, s[1]) - u
                if s[5] is not None:
                    who_here = s[5]
                else:
                    who_here = who if who in s[4] else None
                f = s[6]
                for j in names:
                    tau = R["tau"][j]
                    A = ((1.0 if j == who_here else 0.0) - f[j]) * tau
                    B = L[j] - A
                    J1 += _sq(A, B, tau, R["Theta"][j], u, piece) / unit[j] ** 2
                    L[j] = A + B * math.exp(-piece / tau)
                if who_here != cur:
                    if cur is not None:
                        J2, J3 = self._charge(cur, length, u, J2, J3)
                    cur, length = who_here, 0.0
                if who_here is not None:
                    length += piece
                    w0 = s[2] + (u - s[0])
                    served[who_here] = served.get(who_here, []) + [(w0, w0 + piece)]
                u += piece
            u = end
        if cur is not None and length > R["X"][cur]:  # (the open panel at the end: its excess)
            e = length - R["X"][cur]
            J3 += math.exp(-u / R["Theta"][cur]) * R["tau"][cur] * e * (2 * R["M"][cur] + e) / unit[cur] ** 2
        for j in names:
            J1 += math.exp(-u / R["Theta"][j]) * L[j] * L[j] / (1 / R["Theta"][j] + 2 / R["tau"][j]) / unit[j] ** 2
        D = 0.0
        for i, Q, a, b in self.quantities:
            got = self.served_before[(i, a, b)] + sum(max(0.0, min(y, b) - max(x, a)) for x, y in served.get(i, ()))
            missing = max(0.0, Q - got)
            if b > float(self.end):
                missing = max(0.0, missing - (b - float(self.end)))
            D += missing
        s = self.sc.s
        return (round(D), J1 + s.minimum_weight * J2 + s.maximum_weight * J3)

    def _charge(self, i, ell, u, J2, J3):
        R = self.R
        M, X, tau, Th = R["M"][i], R["X"][i], R["tau"][i], R["Theta"][i]
        disc = math.exp(-u / Th) / self.unit[i] ** 2
        if ell < M:
            s = M - ell
            J2 += disc * tau * s * (2 * M + s)
        if ell > X:
            e = ell - X
            J3 += disc * tau * e * (2 * M + e)
        return J2, J3

    # ---- building continuations
    def free_until(self, who, u) -> float:
        """How far from u the task may run on (the stretches that allow it, unbroken)."""
        e = u
        for s in self.st:
            if s[1] <= e + 1e-9:
                continue
            if s[0] > e + 1e-9 or (who not in s[4] and s[5] != who):
                break
            e = s[1]
        return e

    def options(self, u, factors=(1, 1.5, 2)):
        """(who, length) to try at u: each allowed task for its minimum times each factor, as far
        as it may run, and nobody until the next stretch or the shortest minimum."""
        s = self.stretch_at(u)
        if s is None:
            return []
        if s[5] is not None:  # (the user's task: that one, to its end)
            return [(s[5], s[1] - u)]
        out = []
        for i in sorted(s[4]):
            room = self.free_until(i, u) - u
            for f in factors:
                h = min(self.R["M"][i] * f, room, self.U - u)
                if h > 0 and (i, h) not in out:
                    out.append((i, h))
        nob = min([self.R["M"][i] for i in s[4]] + [s[1] - u, self.U - u])
        out.append((None, max(nob, 1.0)))
        return out

    def base(self, runs, u, until, L=None, offset: float = 0.5):
        """The base policy from u to `until`: stride scheduling read from the lags, the allowed
        task due first for its minimum, a task being due when its lag reaches minus half its
        minimum, at its share: (L + M/2) / f from now. (Most behind over its share instead puts
        a 0.66% task a little behind first, a surplus it forgets over days.)"""
        out = list(runs)
        guard = 0
        while u < min(until, self.U) - 1e-9 and guard < 10000:
            guard += 1
            s = self.stretch_at(u)
            if s is None:
                break
            if s[5] is not None:
                h = s[1] - u
                out.append((s[5], h))
                u += h
                continue
            ok = sorted(s[4])
            if not ok:
                out.append((None, s[1] - u))
                u = s[1]
                continue
            lag = self._lags(out) if L is None else L
            i = min(ok, key=lambda j: ((lag[j] + offset * self.R["M"][j]) / max(s[6][j], 1e-12), j))
            h = min(self.R["M"][i], self.free_until(i, u) - u, self.U - u)
            out.append((i, h))
            u += h
            L = None
        return out

    def _lags(self, runs) -> dict:
        R, L = self.R, dict(self.L0)
        u, k = 0.0, 0
        for who, h in runs:
            end = u + h
            while u < end - 1e-9 and k < len(self.st):
                s = self.st[k]
                if u >= s[1] - 1e-9:
                    k += 1
                    continue
                piece = min(end, s[1]) - u
                w = s[5] if s[5] is not None else (who if who in s[4] else None)
                for j in self.names:
                    tau = R["tau"][j]
                    A = ((1.0 if j == w else 0.0) - s[6][j]) * tau
                    L[j] = A + (L[j] - A) * math.exp(-piece / tau)
                u += piece
            u = end
        return L

    def rollout(self, horizon, depth: int = 2, factors=(1, 1.5, 2)) -> list:
        """One decision at a time: each option, then each option for the next decision (two
        runs deep), completed by the base policy to `horizon` past it and closed by the tail;
        the best first decision is kept. Runs out of its budget: the base policy finishes."""
        runs, u = [], 0.0
        while u < self.U - 1e-9:
            if self.out_of_time():
                return self.base(runs, u, self.U)
            best = None
            for who, h in self.options(u, factors):
                first = runs + [(who, h)]
                tails = []
                for who2, h2 in (self.options(u + h, factors=(1,)) if depth > 1 else []) or [(None, 0.0)]:
                    trial = first + ([(who2, h2)] if h2 > 0 else [])
                    v = u + h + h2
                    trial = self.base(trial, v, min(self.U, v + horizon))
                    tails.append(self.cost(trial))
                key = min(tails)
                if best is None or key < best[0]:
                    best = (key, who, h)
            runs.append((best[1], best[2]))
            u += best[2]
        return runs

    def stride(self, offset: float = 0.5) -> list:
        """The window at the shares, nobody never: the base policy alone."""
        return self.base([], 0.0, self.U, offset=offset)

    def improve(self, runs, focus=None, fraction: int = 4) -> list:
        """Changes to the whole continuation, each kept when the whole scores lower: swap two
        neighbouring runs, give a run to another allowed task or to nobody, move the boundary
        between two neighbours by a `fraction` of the shorter one; again until nothing helps.
        `focus`: (u0, u1) on the schedulable clock: only the runs that meet it are changed (the
        whole still scored), for the finer passes on the slice a step commits (pace.py)."""
        best = self.cost(runs)
        changed = True
        while changed and not self.out_of_time():
            changed = False
            for n in range(len(runs)):
                if self.out_of_time():
                    break
                if focus is not None:
                    u = sum(h for _, h in runs[:n])
                    if u + runs[n][1] <= focus[0] or u >= focus[1]:
                        continue
                cands = []
                if n + 1 < len(runs):
                    cands.append(runs[:n] + [runs[n + 1], runs[n]] + runs[n + 2:])
                    (a, ha), (b, hb) = runs[n], runs[n + 1]
                    for d in (min(ha, hb) / fraction, -min(ha, hb) / fraction):
                        if ha + d > 1 and hb - d > 1:
                            cands.append(runs[:n] + [(a, ha + d), (b, hb - d)] + runs[n + 2:])
                u = sum(h for _, h in runs[:n])
                s = self.stretch_at(u)
                if s is not None and s[5] is None:
                    for who in sorted(s[4]) + [None]:
                        if who != runs[n][0]:
                            cands.append(runs[:n] + [(who, runs[n][1])] + runs[n + 1:])
                for cand in cands:
                    if not self.valid(cand):
                        continue
                    key = self.cost(cand)
                    if key < best:
                        runs, best, changed = cand, key, True
                        break
        return runs

    def valid(self, runs) -> bool:
        """Each run's task allowed all along it (the user's stretches kept as they are)."""
        u = 0.0
        for who, h in runs:
            if who is not None and self.free_until(who, u) < u + h - 1e-6:
                return False
            u += h
        return True

    def from_wall(self, panels: dict) -> list:
        """The runs of a schedule on the line ({task: [(a, b), ...]}), on the schedulable clock:
        each stretch's part given to the task the panels put there (or to nobody), neighbours of
        the same task merged."""
        runs = []
        marks = sorted((float(a), float(b), k) for k, v in panels.items() for a, b in v)
        for st in self.st:
            pos = st[2]
            while pos < st[3] - 1e-9:
                here = next(((a, b, k) for a, b, k in marks if a <= pos < b), None)
                if here is not None and (here[2] in st[4] or here[2] == st[5]):
                    end, who = min(here[1], st[3]), here[2]
                else:
                    nxt = [a for a, _, _ in marks if pos < a < st[3]]
                    end, who = (min(nxt) if nxt else st[3]), (st[5] if st[5] is not None else None)
                if runs and runs[-1][0] == who:
                    runs[-1] = (who, runs[-1][1] + end - pos)
                else:
                    runs.append((who, end - pos))
                pos = end
        return runs

    def u_of(self, x) -> float:
        """The schedulable clock at the line's position x (0 at the window's start)."""
        x = float(x)
        for st in self.st:
            if st[2] <= x < st[3]:
                return st[0] + (x - st[2])
            if x < st[2]:
                return st[0]
        return self.U

    def to_wall(self, runs) -> dict:
        """The runs on the line: {task: [(a, b), ...]}, a run crossing what nobody may have split
        around it; the user's stretches left out (they are there already)."""
        out = {}
        u = 0.0
        for who, h in runs:
            end = u + h
            if who is not None:
                for s in self.st:
                    a, b = max(u, s[0]), min(end, s[1])
                    if b - a > 1e-9 and s[5] is None and who in s[4]:
                        wa = Fraction(s[2] + (a - s[0])).limit_denominator(1000)
                        wb = Fraction(s[2] + (b - s[0])).limit_denominator(1000)
                        out.setdefault(who, []).append((wa, wb))
            u = end
        return out


def _sq(A, B, tau, Th, v0, h) -> float:
    def ex(rate):
        return h if rate == 0 else (1 - math.exp(-rate * h)) / rate
    return math.exp(-v0 / Th) * (A * A * ex(1 / Th) + 2 * A * B * ex(1 / Th + 1 / tau) + B * B * ex(1 / Th + 2 / tau))


SEEDS = (0.25, 0.75, 0.0, 0.5, 1.0)  # the base policy's offsets, in the order they are tried


def search(problem, ctx, state, budget: float = 4.0) -> dict:
    """The best continuation found in `budget` seconds, on the line: {task: [(a, b), ...]}.
    Seeds compete: the window at the shares, the base policy due at (L + offset M) / f for each
    of SEEDS, each improved over the whole continuation in turn while the time lasts; the best
    kept. The improvement ends in a local optimum that depends on the seed: measured on account
    3's shape after 60 days at the shares (a day's window), the offsets 0, 0.25, 0.5, 0.75, 1,
    1.5 end at 159322, 158943, 176554, 159951, 186397, 168637 (stride in the past's phase:
    167545), each in about 1.6s. A rollout as seed spent the budget before its improvement
    (two runs deep: 130310 where the stride seed improved reached 97551 in 1.9s, test_score)."""
    m = Model(problem, ctx, state, budget)
    if m.U <= 0:
        return {}
    best, key = None, None
    for off in SEEDS:
        runs = m.improve(m.stride(off))
        k = m.cost(runs)
        if key is None or k < key:
            best, key = runs, k
        if m.out_of_time():
            break
    return m.to_wall(best)
