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
    LOOK_BACK = 7 * 86400  # how far before a day the lags are read
    DAY = 86400  # the days the future is cut into: each filled from its own start
    RARE = 168 * 3600  # a task whose period is longer: placed at its due dates (scheduler_score.md)
    CACHE = 256  # days kept

    def __init__(self, rules, score, constraints=()) -> None:
        """rules: the RuleSet; score: score.Score (its tasks: priorities, minimums, resilience);
        constraints: the generator's (the breaks' AsEarlyAsPossible, MinimumGap, MaximumGap),
        placed directly by `placed`."""
        self.rules, self.score = rules, score
        self.tasks = sorted(score.tasks)
        self.periods = sorted({k for t in score.tasks.values() for k in t.resilience})
        self._days: dict = {}  # (lo, hi) -> (inputs, the day's schedule)
        self.constraints = tuple(constraints)
        for k in self.constraints:
            if hasattr(k, "bind"):
                k.bind(rules)
            if hasattr(k, "bind_all"):
                k.bind_all(self.constraints)
        self._placed = None  # (line, claims, chip, the state with the constraints' pieces)

    NEAR = 7 * 86400  # a window ending within this of the chip: the breaks placed only up to its end

    def placed(self, state, chip, until=None):
        """The state with each constraint that places pieces (AsEarlyAsPossible with a length: the
        breaks) placed from the chip, forever, in the constraints' order, each on the line the ones
        before it made (the 5min breaks where the 15min ones leave room), written straight into
        the line with the kinds its pieces come with ("Break15 → NoScreen"): no solving, no edit
        (0.1s for the three breaks with a night). The same placement the generator's constraint
        edits make (constraints.AsEarlyAsPossible.place): what the window shows past the plan,
        and what the instant fill keeps its tasks away from. `until`: a window ending within NEAR
        of the chip needs them only up to its end (the 15min breaks with the 20s ones and a night
        repeat every 61 days: placed forever, 1160 steps of the sweep); further, forever, once.
        Past `until` the pieces are not there: read the state up to it only."""
        from constraints import AsEarlyAsPossible
        from line import Line, LineState
        chip = Fraction(chip)
        until = None if until is None or until - chip > self.NEAR else Fraction(until)
        got = self._placed
        if got is not None and got[0] is state.line and got[1] is state.claims and got[2] == chip and                 (got[4] is None or (until is not None and until <= got[4])):
            return got[3]  # (placed forever, or far enough)
        cur = state
        upto = Region.of(Interval(-INF, chip, False, True))
        for k in self.constraints:
            if not isinstance(k, AsEarlyAsPossible) or k.length is None:
                continue
            pieces = (cur.line.presence(k.kind) & upto) | k.place(cur, chip, until=until)
            vals = dict(cur.line.vals)
            for kind in k._with(self.rules):
                if kind == k.kind:
                    vals[kind] = {Fraction(100): pieces}
                else:
                    old = vals.get(kind, {}).get(Fraction(100))
                    vals[kind] = dict(vals.get(kind, {}))
                    vals[kind][Fraction(100)] = pieces if old is None else old | pieces
            cur = LineState(cur.claims, Line(vals), cur.history)
        self._placed = (state.line, state.claims, chip, cur, until)
        return cur

    def placed_kinds(self) -> set:
        """The kinds `placed` writes: the constraints' and those their pieces come with."""
        from constraints import AsEarlyAsPossible
        out = set()
        for k in self.constraints:
            if isinstance(k, AsEarlyAsPossible) and k.length is not None:
                out |= set(k._with(self.rules))
        return out

    def schedule(self, state, a, b, start=None) -> dict:
        """{task: [(start, end), ...]} on [a, b): the instant schedule, the user's tasks left out
        (they are on the line already), nothing before `start` (none by default; where the plan of
        the scheduler's steps ends, for the window). The same at a position whatever the window it is read in:
        the future is cut into days on a fixed grid (DAY from 0), each filled from its own start
        (or from `start`, in the day it falls in), so looking at t, going away and coming back
        shows the same, as long as the line, the rules and the score are the same (and a day
        computed once is kept, `CACHE`). A rare task (its period over RARE) is placed at its due
        dates, its last start on the line and one period after another: when the scheduler moves
        it, its later ones move with it."""
        a, b = Fraction(a), Fraction(b)
        out: dict = {}
        k = (max(a, start if start is not None else a) // self.DAY) * self.DAY
        while k < b:
            lo, hi = (max(k, Fraction(start)) if start is not None else k), k + self.DAY
            if lo < hi:
                for i, v in self._day(state, lo, hi).items():
                    got = [(max(x, a), min(y, b)) for x, y in v if y > a and x < b]
                    if got:
                        out.setdefault(i, []).extend(got)
            k += self.DAY
        return out

    def _day(self, state, lo, hi) -> dict:
        """One day's instant schedule: the rare tasks at their due dates, then the others."""
        inputs = (state.line, state.claims, self.rules, self.score)
        got = self._days.get((lo, hi))
        if got is not None and all(x is y for x, y in zip(got[0], inputs)):
            return got[1]
        rare = self._rare(state, lo, hi)
        day = self._fill(state, lo, hi, rare)
        if self.BEST and not self.rare(self.score.rule_state(lo)):  # (the stride would place a rare task by its lag)
            day = self._best(state, lo, hi, day)
        for i, v in rare.items():
            day.setdefault(i, []).extend(v)
        if len(self._days) >= self.CACHE:
            self._days.pop(next(iter(self._days)))
        self._days[(lo, hi)] = (inputs, day)
        return day

    # `_best`, off: measured 2026-11-08 on tests/compare_paces.py, it brings the instant schedule
    # from 1.87 to 1.22 times the step's J (geometric mean of 40 cases), but costs 60ms a day where
    # the fill costs 5 (a drag stutters) and the score it follows prefers what looks wrong (a 1min
    # sliver of B before the user's block of A, A against the block): gaps_to_solve_later.md
    BEST = False
    POLISH = 0.02  # seconds of Model.improve per day (`_best`)
    STRIDES = (0.25, 0.5, 0.75)  # the score's own stride policies tried against the fill (`_best`)

    def _best(self, state, lo, hi, fill: dict) -> dict:
        """The fill, or the stride on the score's own targets (score_search.Model.stride: the
        targets with the compensation around the user's blocks, the lags as the score has them)
        if the score puts it lower: each is better where the other is not (the fill keeps a
        task's runs under its maximum, the stride reads the compensation), both in milliseconds."""
        from types import SimpleNamespace
        from score import ScoreProblem
        from score_search import Model
        try:
            m = Model(ScoreProblem(self.score, window=hi - lo), SimpleNamespace(c=lo, state=state, gen=SimpleNamespace(rules=self.rules)), state, 60)
        except Exception:
            return fill
        if m.U <= 0:
            return fill
        import time
        runs = m.from_wall(fill)
        key, picked = m.cost(runs), None
        def idle(r):  # (nobody where a task could be: never shown, whatever the score says)
            u = 0.0
            for who, h in r:
                if who is None and any(st[4] and st[0] < u + h - 1e-6 and st[1] > u + 1e-6 for st in m.st):
                    return True
                u += h
            return False
        for off in self.STRIDES:
            r = m.stride(off)
            c = m.cost(r)
            if c < key and not idle(r):
                runs, key, picked = r, c, r
        if self.POLISH > 0:  # (the step's own improvement, a few milliseconds of it)
            m.deadline = time.perf_counter() + self.POLISH
            r = m.improve(runs)
            if m.cost(r) < key and not idle(r):
                runs, picked = r, r
        return m.to_wall(picked) if picked is not None else fill

    def rare(self, R) -> list:
        return [i for i in self.tasks if R["pi"][i] > 0 and R["tau"][i] > self.RARE]

    def _rare(self, state, lo, hi) -> dict:
        """The rare tasks in [lo, hi): each at its due dates (its last start on the line before lo,
        then one period after another; from 0 if it never was), at the first place from there
        it may have for its minimum in the day."""
        R = self.score.rule_state(lo)
        out = {}
        for i in self.rare(R):
            tau = Fraction(R["tau"][i]).limit_denominator(1000)
            M = Fraction(R["M"][i]).limit_denominator(1000)
            before = state.line.presence(i) & Region.of(Interval(-INF, lo, False, False))
            last = None
            if not before.is_empty():
                e = before.sup()
                e = Fraction(e.x) if hasattr(e, "x") else Fraction(e)
                piece = [iv for iv in before.on(e - 10 * tau - 1, e + 1).intervals]
                last = Fraction(piece[-1].lo.x if hasattr(piece[-1].lo, "x") else piece[-1].lo) if piece else e
            base = last if last is not None else Fraction(0)
            n = max(1, -(-(lo - base) // tau)) if last is not None else max(0, -(-(lo - base) // tau))
            due = base + n * tau
            no = self._forbidden(state, i, lo, hi) | state.claims.owned(i, USER).on(lo, hi)
            for j in self.tasks:
                if j != i:
                    no = no | state.claims.owned(j, USER).on(lo, hi)
            while due < hi:
                t = due
                for _ in range(200):
                    block = (no & IntervalSet.of(Interval(t, min(t + M, hi), False, True))).intervals if t < hi else []
                    if not block:
                        break
                    t = Fraction(block[0].hi.x if hasattr(block[0].hi, "x") else block[0].hi)
                if t < hi:
                    out.setdefault(i, []).append((t, min(t + M, hi)))
                due += tau
        return out

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

    def _fill(self, state, a, b, rare=None) -> dict:
        """The tasks but the rare ones on [a, b), filled from a: `rare` ({task: [(x, y)]}) are
        there already, as the user's tasks are (served, nothing placed on them)."""
        a, b = Fraction(a), Fraction(b)
        R = self.score.rule_state(a)
        names = [i for i in self.tasks]
        rare_names = set(self.rare(R))
        user = {i: state.claims.owned(i, USER).on(a, b) for i in names}
        for i, v in (rare or {}).items():
            user[i] = user[i] | IntervalSet([Interval(x, y, False, True) for x, y in v])
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
        # the run going on at t: (its task, where it began), the user's blocks and the line
        # before `a` included: a task is not kept on past its maximum (R["X"])
        run = (None, a)
        for i in names:
            back = state.line.presence(i).on(a - DAY_X, a)
            ivs = [iv for iv in back.intervals if _hi(iv) >= a]
            if ivs:
                run = (i, _lo(ivs[-1]))
        while t < b and guard < 100000:
            guard += 1
            nxt = next((x for x in cuts if x > t), b)
            mid = (t + nxt) / 2
            mine = next((i for i in names if _has(user[i], mid)), None)
            if mine is not None:  # the user's: served, nothing placed
                self._serve(L, R, mine, float(nxt - t), self._shares(R, pres, no, mid, names))
                run = run if run[0] == mine else (mine, t)
                t = nxt
                continue
            allowed = [i for i in names if R["pi"][i] > 0 and i not in rare_names and not _has(no[i], mid)]
            if not allowed:  # nobody may be here (a night): not on the clock
                run = (None, nxt)
                t = nxt
                continue
            Xs = {i: Fraction(R["X"][i]).limit_denominator(1000) if R["X"][i] != float("inf") else None for i in allowed}
            longer = [i for i in allowed if not (run[0] == i and Xs[i] is not None and t - run[1] >= Xs[i])]
            allowed = longer or allowed  # (at its maximum: another, if there is one)
            share = self._shares(R, pres, no, mid, names)
            room = {i: self._room(t, b, i, no, user, names) - t for i in allowed}
            due = sorted(allowed, key=lambda i: ((L[i] + R["M"][i] / 2) / max(share[i], 1e-12), i))
            Ms = {i: Fraction(R["M"][i]).limit_denominator(1000) for i in allowed}
            # a block of the user's: no panel of its own task against either side of it (the
            # block would only look longer), what is left before it too short for a panel
            # given to the one before (B against both sides of the user's hour of A)
            e, X = self._next_user(t, b, user, names)
            P = next((j for j in names if any(_hi(iv) == t for iv in user[j].intervals)), None)
            least = min(Ms.values())

            def touches(i):
                if i == P:
                    return True
                return X == i and room[i] == e - t and e - (t + min(room[i], Ms[i])) < least
            pick = [i for i in due if not touches(i)] or due
            fits = [i for i in pick if room[i] >= Ms[i]]
            i = fits[0] if fits else pick[0]
            h = min(room[i], Ms[i]) if fits else room[i]
            if X is not None and X != i and room[i] == e - t and 0 < e - (t + h) < least:
                h = e - t  # (the rest before the block: this panel's)
            began = run[1] if run[0] == i else t
            if Xs[i] is not None and h > Xs[i] - (t - began) > 0:
                h = Xs[i] - (t - began)  # (no further than its maximum)
            if h <= 0:
                t = nxt
                continue
            out[i].append((t, t + h))
            self._serve(L, R, i, float(h), share)
            run = (i, began)
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

    @staticmethod
    def _next_user(t, b, user, names):
        """(where the next block of the user's after t starts, its task), (b, None) if none."""
        e, X = b, None
        for j in names:
            for iv in user[j].intervals:
                lo = _lo(iv)
                if lo > t:
                    if lo < e:
                        e, X = lo, j
                    break
        return e, X

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


DAY_X = 86400  # how far before a day the run going on at its start is read


def _lo(iv):
    return Fraction(iv.lo.x) if hasattr(iv.lo, "x") else iv.lo


def _hi(iv):
    return Fraction(iv.hi.x) if hasattr(iv.hi, "x") else iv.hi


def _has(s: IntervalSet, x) -> bool:
    for iv in s.intervals:
        if iv.lo <= x <= iv.hi:
            return True
        if iv.lo > x:
            return False
    return False


def displayed(instant: Instant, line, claims, history, lo, hi, start=None, chip=None):
    """(the line, the claims) the calendar shows on [lo, hi): the line, its tasks after `lo` (past
    the plan of the scheduler's steps) replaced by the instant schedule there, the user's tasks
    kept. The instant schedule's elements are the generator's, as those of the steps: shown as
    they are, nothing tells them apart (OmniApp's requirements § Two paces)."""
    from line import Line, LineState
    lo, hi = Fraction(lo), Fraction(hi)
    if lo >= hi:
        return line, claims
    state = LineState(claims, line, history)
    if chip is not None and instant.constraints:  # (the breaks placed at once, instant.placed)
        state = instant.placed(state, chip, hi)
    got = instant.schedule(state, lo, hi, lo if start is None else start)
    after = Region.of(Interval(lo, hi, False, False))
    vals = dict(line.vals)
    shown = claims.copy()
    for kind in (instant.placed_kinds() if chip is not None else ()):  # past lo: the breaks placed at once
        new = state.line.presence(kind) & after
        d = {v: r - after for v, r in vals.get(kind, {}).items()}
        if not new.is_empty():
            d[Fraction(100)] = d.get(Fraction(100), Region.empty()) | new
        d = {v: r for v, r in d.items() if not r.is_empty()}
        if d:
            vals[kind] = d
        else:
            vals.pop(kind, None)
        c = shown.pos.get(kind, {})
        for key in [key for key in c if key[1] == GEN]:
            c[key] = c[key] - after
            if c[key].is_empty():
                del c[key]
        if not new.is_empty() and kind in [k.kind for k in instant.constraints if hasattr(k, "length")]:
            shown.add_pos(kind, new, Fraction(100), GEN)
    for i in instant.tasks:
        d = shown.pos.get(i, {})
        for key in [key for key in d if key[1] == GEN]:
            d[key] = d[key] - after
            if d[key].is_empty():
                del d[key]
        if got.get(i):
            shown.add_pos(i, Region.of(IntervalSet([Interval(a, b, False, True) for a, b in got[i]])), Fraction(100), GEN)
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
    solved = _solved(instant, shown, after, lo, hi)
    if solved is not None:  # (every kind on the window as the rules make it of what is shown)
        out = dict(line.vals)
        for k in set(out) | set(solved.vals):
            d = {v: r - after for v, r in out.get(k, {}).items()}
            for v, r in solved.vals.get(k, {}).items():
                d[v] = d[v] | (r & after) if v in d else r & after
            d = {v: r for v, r in d.items() if not r.is_empty()}
            if d:
                out[k] = d
            else:
                out.pop(k, None)
        return Line(out), shown
    return Line(vals), shown


def _solved(instant, claims, after, lo, hi):
    """The line the rules make on [lo, hi) of what is shown there (its claims: the user's, the
    breaks placed and the tasks the instant schedule put, the generator's): every kind as on a
    line (which device is locked in a "no screen", ...), and a check that the instant schedule
    breaks no rule, whatever its shape. The rules are read as far before and after as they
    reach. None where they do not decide it, or refuse it (then the line's other kinds are
    shown as they are)."""
    from solver import Conflict, Undetermined, solve
    rs = instant.rules
    reach = getattr(rs, "reach", Fraction(0)) or Fraction(0)
    W = Region.of(Interval(lo - reach - 1, hi + reach + 1, False, False))
    try:
        return solve(rs, claims.on(W).problem())
    except (Conflict, Undetermined, ValueError):
        return None
