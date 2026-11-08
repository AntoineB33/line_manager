"""The instant schedule (OmniApp's docs/scheduler_requirements.md § Two paces): for any window
of the line, at once, a schedule of the tasks that breaks no rule it reads and roughly keeps
the target ratios and minimum lengths, with nothing obviously wrong (no idle time a task could
have). It is what the calendar shows where the scheduler (the rules generator, 10 minutes at a
time from the chip) has not made the schedule definitive: a block of A dragged by the user, the
rules just changed, a view thousands of years ahead.

It reads the line only in the window (and a look back before it), never as a whole: the periods
placed by the rules and the constraints (a night every day, the breaks) are read where they are,
however far, and nothing is solved.

- Where a task may be: the window minus the kinds it has resilience 0 to, minus where an element
  rule forbids it ("X → no T"), minus the tasks the user put (their time is theirs alone).
- One task at a time: the window's free time is filled panel after panel, every position where
  some task may be getting one (no idle time a task could have).
- Which task: stride scheduling at the local shares (each task's priority times its multiplier
  where it is, normalized over the tasks allowed there): the task due first, a task being due
  when its lag (served minus its share of the time so far) reaches minus half its minimum,
  (L + M/2) / f from now. The lags start from the look back before the window (the line as it
  is: what was scheduled, what the user put), and the user's tasks in the window count as
  served as the sweep goes through them: a big block of A leaves less A around it.
- How long: the task's minimum, as far as it may go on; where the free time left is shorter
  than the due task's minimum, the task due first among those whose minimum fits, else the due
  task for what is left (no idle time).
Elements rules other than "→ no T" (a task bringing another, percentages) are not read: the
scheduler's own runs keep them.
"""

from __future__ import annotations

import math
from fractions import Fraction

from intervals import INF, Interval, IntervalSet
from line import GEN, USER
from region import Region


class Instant:
    LOOK_BACK = 7 * 86400  # how far before the window the lags are read

    def __init__(self, rules, score) -> None:
        """rules: the RuleSet; score: score.Score (its tasks: priorities, minimums, resilience)."""
        self.rules, self.score = rules, score
        self.tasks = sorted(score.tasks)
        self.periods = sorted({k for t in score.tasks.values() for k in t.resilience})

    # ---- what the window holds
    def _forbidden(self, state, i, a, b) -> IntervalSet:
        """Where task i may not be in [a, b): resilience 0, a rule "X → no i" (X another kind
        than a task: one task at a time is the sweep's)."""
        out = IntervalSet()
        for k, r in self.score.tasks[i].resilience.items():
            if r == 0:
                out = out | state.line.presence(k).on(a, b)
        for rule in self.rules.element:
            kinds = rule.lhs.kinds()
            if len(kinds) != 1 or kinds[0] in self.score.tasks or getattr(rule.lhs, "op", None) != "present":
                continue
            for it in rule.rhs:
                o = it.options[0]
                if not it.is_or and o.negated and o.kind == i and o.where.is_point:
                    out = out | state.line.presence(kinds[0]).on(a, b)
        return out

    def schedule(self, state, a, b) -> dict:
        """{task: [(start, end), ...]} on [a, b): the instant schedule of the window, the user's
        tasks left out (they are on the line already)."""
        a, b = Fraction(a), Fraction(b)
        R = self.score.rule_state(a)
        names = [i for i in self.tasks]
        user = {i: state.claims.owned(i, USER).on(a, b) for i in names}
        no = {i: self._forbidden(state, i, a, b) for i in names}
        pres = {k: state.line.presence(k).on(a, b) for k in self.periods}
        L = self._lags(state, a, R)
        out = {i: [] for i in names}
        # the positions where something changes: the sweep goes from one to the next
        cuts = {a, b}
        for s in list(user.values()) + list(no.values()) + list(pres.values()):
            for iv in s.intervals:
                for x in (iv.lo, iv.hi):
                    x = Fraction(x.x) if hasattr(x, "x") else x
                    if isinstance(x, Fraction) and a < x < b:
                        cuts.add(x)
        cuts = sorted(cuts)
        t = a
        guard = 0
        while t < b and guard < 100000:
            guard += 1
            nxt = next((x for x in cuts if x > t), b)
            mid = (t + nxt) / 2
            mine = next((i for i in names if _has(user[i], mid)), None)
            if mine is not None:  # the user's: served, nothing placed
                self._serve(L, R, mine, float(nxt - t), self._shares(R, pres, no, mid, names))
                t = nxt
                continue
            allowed = [i for i in names if R["pi"][i] > 0 and not _has(no[i], mid)]
            if not allowed:  # nobody may be here (a night): not on the clock
                t = nxt
                continue
            share = self._shares(R, pres, no, mid, names)
            room = {i: self._room(t, b, i, no, user, names) - t for i in allowed}
            due = sorted(allowed, key=lambda i: ((L[i] + R["M"][i] / 2) / max(share[i], 1e-12), i))
            fits = [i for i in due if room[i] >= Fraction(R["M"][i]).limit_denominator(1000)]
            i = fits[0] if fits else due[0]
            h = min(room[i], Fraction(R["M"][i]).limit_denominator(1000)) if fits else room[i]
            if h <= 0:
                t = nxt
                continue
            out[i].append((t, t + h))
            self._serve(L, R, i, float(h), share)
            t = t + h
        return {i: v for i, v in out.items() if v}

    def _room(self, t, b, i, no, user, names) -> Fraction:
        """How far from t task i may go on: up to where it is forbidden, or a task of the user's."""
        end = b
        for s in [no[i]] + [user[j] for j in names]:
            for iv in s.intervals:
                lo = Fraction(iv.lo.x) if hasattr(iv.lo, "x") else iv.lo
                if lo > t:
                    end = min(end, lo)
                    break
        return end

    def _shares(self, R, pres, no, x, names) -> dict:
        """The local shares at x: priority times multiplier, normalized over the tasks allowed."""
        mu = {}
        for i in names:
            m = 0.0 if _has(no[i], x) else 1.0
            for k, r in self.score.tasks[i].resilience.items():
                if m and k in pres and _has(pres[k], x):
                    m *= r
            mu[i] = m
        tot = sum(R["P"][i] * mu[i] for i in names)
        return {i: (R["P"][i] * mu[i] / tot if tot > 0 else 0.0) for i in names}

    @staticmethod
    def _serve(L, R, i, h, share) -> None:
        """The lags after h of task i, each task at its share (no forgetting: rough)."""
        for j in L:
            L[j] += (h if j == i else 0.0) - share.get(j, 0.0) * h

    def _lags(self, state, a, R) -> dict:
        """Each task's lag at a: what it had of the look back's time some task could have,
        minus its share of it."""
        lo = a - self.LOOK_BACK
        names = self.tasks
        served = {i: float(state.line.presence(i).measure_on(IntervalSet.of(Interval(lo, a, False, True)))) for i in names}
        total = sum(served.values())
        return {i: served[i] - R["pi"][i] * total for i in names}


def _has(s: IntervalSet, x) -> bool:
    for iv in s.intervals:
        if iv.lo <= x <= iv.hi:
            return True
        if iv.lo > x:
            return False
    return False


def displayed(instant: Instant, line, claims, history, lo, hi):
    """The line the calendar shows on [lo, hi): the line, its tasks after `lo` (where the
    scheduler's steps have not made it definitive) replaced by the instant schedule there (the
    user's tasks kept, they are the line's). Nothing to do past hi or before lo."""
    from line import Line, LineState
    lo, hi = Fraction(lo), Fraction(hi)
    if lo >= hi:
        return line
    state = LineState(claims, line, history)
    got = instant.schedule(state, lo, hi)
    after = Region.of(Interval(lo, hi, False, False))
    vals = dict(line.vals)
    for i in instant.tasks:
        mine = claims.owned(i, USER)
        old = vals.get(i, {})
        keep = {v: (r - after) | (r & mine & after) for v, r in old.items()}
        new = Region.of(IntervalSet([Interval(a, b, False, True) for a, b in got.get(i, ())])) if got.get(i) else Region.empty()
        if not new.is_empty():
            keep[Fraction(100)] = keep.get(Fraction(100), Region.empty()) | new
        keep = {v: r for v, r in keep.items() if not r.is_empty()}
        if keep:
            vals[i] = keep
        else:
            vals.pop(i, None)
    return Line(vals)
