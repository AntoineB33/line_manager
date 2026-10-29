"""The mode rules acting at the chip, one position after the other.

A mode rule can only modify what is at the chip's position, a single point. When the
chip is at x (a position x + kε: it passes x+ε, x+2ε, ... as it leaves x), the mode
rules whose mode is the mode at x are decided at x, in their order, on the line as it
is at that moment:
- a rule whose left side is not met at x does nothing;
- for each item, the leftmost operand that can be chosen: one that already holds at x
  (nothing to do), else the first whose edit on {x} is accepted: it is resolved like
  any edit (resolve.py) and must respect M1 and N2 (guards.py) with the chip at x:
  only the claims at x change, nothing changes on (-inf, x), the user's elements stay;
- if no operand is accepted, the item does nothing at x (edits from the user win).
So what the chip has gone through is decided, and nothing on its right.

How it is computed exactly. Between two positions the chip goes through infinitely
many, but the decision only changes at a few. From a position m where it was decided,
the same decision goes on (a run) over [m, x) as x moves right. The claims and the
line then have fixed ends, and ends that move with x, at x + d (d a sum of rule
distances along the rules). Whether the decision at x is the same changes only where
a moving end crosses a fixed one: at e - d + jε, e an end of the state or m, d a
multiple of g (the gcd of the rule distances) with |d| <= 2 * depth * reach, |j| <= 2.
With a cycle that moves positions (rules.py), what is placed at x goes on without end:
d then goes as far as the ends of the state, and a period of its repeating parts.
The run is checked at each of those positions and at the first, middle and last
position between two of them; a check that fails between them refuses the rest of
the positions rather than guessing.

A run is compiled ahead (`_Plan`): decided once, it is kept from one step of the chip
to the next, its positions being those of the state where it started, and each stretch
between two of them is checked once, up to its end, before the chip gets there. On
such a stretch the line has the same ends, each fixed or moving with the chip (above):
the first and middle checks give which ones move, and the last check must be that
line exactly. A step of the chip inside a checked stretch then only moves those ends,
with no solve (`_affine`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from guards import check_mode_edit
from intervals import EPS, INF, Interval, IntervalSet, Pos
from line import MODE, LineState, Op
from region import IncompatiblePeriods, Region
from resolve import Refused, apply_edit
from rules import RuleSet, _fgcd
from solver import Conflict, Line, Undetermined, checkpoint, lhs_region, solve

HOLDS, PUT, NONE = "holds", "put", "none"


@dataclass
class Run:
    """A change of the mode rules: one decision on the positions `at`, the first being
    `start`. It is continuous: each position changes when the chip is there."""
    start: Pos
    at: Region
    before: LineState
    after: LineState


class ModeRules:
    def __init__(self, rs: RuleSet, mode: str) -> None:
        self.rs = rs
        self.rules = [r for r in rs.mode_rules if r.mode == mode]
        self.items = [(r, it) for r in self.rules for it in r.rhs]
        dists = [abs(e.x) for r in rs.element for o in r.lits() for e in (o.where.lo, o.where.hi) if type(e) is Pos]
        g = None
        for d in dists:
            if d:
                g = d if g is None else _fgcd(g, d)
        self.g = g
        # how far a moving end can be from x; with a cycle that moves positions, what is
        # placed at x can go on without end: as far as the ends of the state (`candidates`)
        self.span = None if rs.moving else 2 * (len(rs.order) + 1) * rs.reach
        self.plan: _Plan | None = None  # the run being compiled ahead

    # ------------------------------------------------------------ one position

    def decide(self, state: LineState, x: Pos) -> tuple[tuple, LineState, list[str]]:
        """The decision at x on this state, the state after it, and why operands were refused."""
        s, out, why = state, [], []
        for r in self.rules:
            met = lhs_region(r.lhs, s.line.vals).contains(x)
            for it in r.rhs:
                if not met:
                    out.append(None)
                    continue
                choice = (NONE,)
                for oi, o in enumerate(it.options):
                    if _holds(o, s, x):
                        choice = (HOLDS, oi)
                        break
                    at = Region.point(x)
                    op = Op.put_no(o.kind, at) if o.negated else Op.put(o.kind, at, o.value or 100)
                    try:
                        after = apply_edit(self.rs, s, [op], MODE)
                    except Refused as e:
                        why.append(f"{r}: {o} at {x}: {e}")
                        continue
                    reason = check_mode_edit(s, after, at, x)
                    if reason:
                        why.append(f"{r}: {o} at {x}: {reason}")
                        continue
                    s, choice = after, (PUT, oi, _gone(s, after, x, o.kind))
                    break
                out.append(choice)
        return tuple(out), s, why

    def with_run(self, state: LineState, decision: tuple, at: Region) -> LineState:
        """The state with the decision's edits on `at`: the operands placed, and the older
        claims the edit removed at its position (M1: at that position only) removed on
        `at` too."""
        c = self.run_claims(state, decision, at)
        return state if c is None else LineState(c, solve(self.rs, c.problem()), state.history)

    def run_claims(self, state: LineState, decision: tuple, at: Region):
        """The claims of `with_run`, or None if the decision places nothing."""
        if not any(choice and choice[0] == PUT for choice in decision):
            return None
        c = state.claims.copy()
        for (r, it), choice in zip(self.items, decision):
            if choice and choice[0] == PUT:
                for kind, positive in choice[2]:
                    (c.remove_pos if positive else c.remove_neg)(kind, at)
                o = it.options[choice[1]]
                if o.negated:
                    c.add_neg(o.kind, at, MODE)
                else:
                    c.add_pos(o.kind, at, o.value or 100, MODE)
        return c

    # ------------------------------------------------------------ the positions of a sweep

    def candidates(self, state: LineState, m: Pos, lo: Pos, hi: Pos) -> list[Pos]:
        """The positions of [lo, hi] where a run started at m can change its decision."""
        regions = [r for d in state.claims.pos.values() for r in d.values()]
        regions += [r for d in state.claims.neg.values() for r in d.values()]
        regions += [r for d in state.claims.taken.values() for r in d.values()]
        regions += [r for d in state.line.vals.values() for r in d.values()]
        span = self.span
        if span is None:  # every end of the state, and a period of each repeating part
            far = [abs(e.x) for r in regions if r for e in (r.lo, r.hi)] + [abs(lo.x), abs(hi.x)]
            span = 2 * max(far) + 2 * max([r.pl.x for r in regions] + [r.pr.x for r in regions] + [Fraction(0)]) + 2 * self.rs.reach
        a, b = lo.x - span - 1, hi.x + span + 1
        ends = {m}
        for reg in regions:
            for iv in reg.on(a, b).intervals:
                ends.update(e for e in (iv.lo, iv.hi) if type(e) is Pos)
        out = set()
        for e in ends:
            if self.g is None:
                ks = [0]
            else:
                n = int(span // self.g)
                ks = range(max(-n, math.ceil((e.x - hi.x - 1) / self.g)), min(n, math.floor((e.x - lo.x + 1) / self.g)) + 1)
            for k in ks:
                base = e - (k * self.g if self.g else 0)
                for j in range(-2, 3):
                    c = base + EPS * j
                    if lo <= c <= hi:
                        out.add(c)
        return sorted(out)

    def act(self, state: LineState, lo: Pos, hi: Pos):
        """The chip goes through [lo, hi]: yields Run (a change) and (position, reason)
        (an operand refused, or the rest of the positions refused); returns at the end.
        A run compiled at an earlier step goes on if the line is still the one it made."""
        x = lo
        while x <= hi:
            plan = self.plan
            if plan is not None and plan.last is state and plan.next == x:
                nxt = x
            elif plan is not None and plan.pending is not None and plan.before is state and plan.m == x:
                why, s1 = plan.pending  # decided ahead (`prepare`): it happens now
                plan.pending = None
                for w in why:
                    yield x, w
                if s1 is not state:
                    yield Run(x, Region.point(x), state, s1)
                state = plan.last = s1
                nxt = x + EPS
                if nxt > hi:
                    return
            else:
                decision, s1, why = self.decide(state, x)
                for w in why:
                    yield x, w
                if s1 is not state:
                    yield Run(x, Region.point(x), state, s1)
                state = s1
                plan = self.plan = _Plan(self, s1, decision, x)
                nxt = x + EPS
                if nxt > hi:
                    return
            try:
                end = plan.extend(nxt, hi)
            except _Changes as e:
                self.plan = None
                yield e.at, "mode rules: the decision changes at a position that is not looked at; the rest is refused"
                return
            if end is None:  # the decision changes at nxt
                self.plan = None
                x = nxt
                continue
            state2 = plan.state_at(end)
            if state2 is not state:
                yield Run(nxt, Region.of(_iv(nxt, end)), state, state2)
            state = plan.last = state2
            plan.next = end + EPS
            x = end + EPS
        return

    def prepare(self, state: LineState, x: Pos, ahead=Fraction(1)) -> None:
        """Compile ahead the run the chip at x (not gone through yet) starts, checked up to
        x + ahead at least: the line is not changed, the decision at x happens when the
        chip goes through x (`act`)."""
        plan = self.plan
        if plan is not None and ((plan.last is state and plan.next == x) or
                                 (plan.pending is not None and plan.before is state and plan.m == x)):
            return
        decision, s1, why = self.decide(state, x)
        plan = _Plan(self, s1, decision, x)
        plan.before, plan.pending = state, (why, s1)
        self.plan = plan
        try:
            plan.extend(x + EPS, Pos(x.x + ahead))
        except _Changes:
            pass  # the chip will refuse there when it gets there

    def prepared(self, state: LineState, x: Pos) -> bool:
        plan = self.plan
        return plan is not None and ((plan.last is state and plan.next == x) or
                                     (plan.pending is not None and plan.before is state and plan.m == x))

    def _same(self, state: LineState, decision: tuple, lo: Pos, p: Pos) -> tuple[bool, LineState | None]:
        """With the run on [lo, p), is the decision at p the same? And the state with the
        run on [lo, p]."""
        try:
            before = self.with_run(state, decision, Region.of(_iv(lo, p - EPS))) if lo < p else state
            d, after, _ = self.decide(before, p)
        except (Conflict, Undetermined, IncompatiblePeriods):
            return False, None
        return d == decision, after


class _Plan:
    """A run compiled ahead: decided at m on `state0`, the same decision on [m + ε,
    checked], `checked` growing one stretch at a time (between two positions where the
    decision can change, or one such position). Each stretch keeps its checked states,
    and the line as an affine function of the chip's position when one is found."""

    def __init__(self, mr: ModeRules, state0: LineState, decision: tuple, m: Pos) -> None:
        self.mr, self.state0, self.decision, self.m = mr, state0, decision, m
        self.a0 = m + EPS
        self.checked: Pos | None = None
        self.over = False  # the decision differs right after `checked`
        self.pieces: list[tuple[Pos, Pos, object, dict]] = []  # (a, b, line of p or None, {p: state})
        self.queue: list[Pos] = []  # the positions where the decision can change, ahead
        self.top: Pos | None = None  # how far they were looked for
        self.last, self.next = state0, self.a0  # the state the run made last, and where it goes on
        self.before, self.pending = None, None  # decided ahead of the chip (`prepare`): the state before, (why, after)

    def extend(self, nxt: Pos, hi: Pos):
        """The last position of [nxt, hi] the run reaches (None: not nxt), checking the
        stretches up to hi that are not yet."""
        self.pieces = [pc for pc in self.pieces if pc[1] >= nxt]
        while not self.over and (self.checked is None or self.checked < hi):
            checkpoint()
            self._stretch(hi)
        if self.checked is None or self.checked < nxt:
            return None
        return self.checked if self.checked < hi else hi

    def _stretch(self, hi: Pos) -> None:
        cur = self.a0 if self.checked is None else self.checked + EPS
        if not self.queue and (self.top is None or self.top < cur):
            g = self.mr.g or self.mr.rs.reach or Fraction(1)
            self.top = Pos(max(hi.x, cur.x) + g)
            self.queue = self.mr.candidates(self.state0, self.m, cur, self.top)
        self.queue = [c for c in self.queue if c >= cur]
        if self.queue and self.queue[0] == cur:
            a = b = self.queue.pop(0)
        else:
            a, b = cur, (self.queue[0] - EPS if self.queue else self.top)
        reps = [a] if a == b else [a] + ([Pos((a.x + b.x) / 2)] if a.x < b.x else []) + [b]
        states = {}
        for i, p in enumerate(reps):
            ok, st = self.mr._same(self.state0, self.decision, self.a0, p)
            if not ok:
                if i == 0:
                    self.over = True
                    return
                raise _Changes(p)
            states[p] = st
        line = None
        if len(reps) == 3:
            line = _affine(states[reps[0]].line, states[reps[1]].line, reps[0], reps[1] - reps[0])
            if line is not None:
                try:
                    if not _same_line(line(b), states[b].line):
                        line = None
                except _NotAffine:
                    line = None
        self.pieces.append((a, b, line, states))
        self.checked = b

    def state_at(self, p: Pos) -> LineState:
        """The state with the run on [m + ε, p], p checked."""
        for a, b, line, states in self.pieces:
            if a <= p <= b:
                if p in states:
                    return states[p]
                if line is not None:
                    try:
                        c = self.mr.run_claims(self.state0, self.decision, Region.of(_iv(self.a0, p)))
                        return self.state0 if c is None else LineState(c, line(p), self.state0.history)
                    except _NotAffine:
                        pass
                break
        return self.mr.with_run(self.state0, self.decision, Region.of(_iv(self.a0, p)))

class _NotAffine(Exception):
    pass


def _affine(l1: Line, l2: Line, p1: Pos, s):
    """The line at p as a function of p, from l1 at p1 and l2 at p1 + s: each end of each
    region fixed or moving with p (`_affine_region`); None if they are not so."""
    if set(l1.vals) != set(l2.vals) or any(set(l1.vals[k]) != set(l2.vals[k]) for k in l1.vals):
        return None
    parts = {}
    for k, d in l1.vals.items():
        parts[k] = {}
        for v, r1 in d.items():
            f = _affine_region(r1, l2.vals[k][v], s)
            if f is None:
                return None
            parts[k][v] = f
    return _AffineLine(parts, p1)


class _AffineLine:
    """The line at p: each region an `_AffineRegion` of p - p1."""

    def __init__(self, parts: dict, p1: Pos) -> None:
        self.parts, self.p1 = parts, p1

    def __call__(self, p: Pos) -> Line:
        return Line({k: {v: f(p - self.p1) for v, f in d.items()} for k, d in self.parts.items()})

def _affine_region(r1: Region, r2: Region, s):
    """r(d) with r(0) = r1 and r(s) = r2, each end fixed or moving by d, the left part
    fixed and the right one fixed or moving; None if r1 and r2 are not so."""
    try:
        if r1 == r2:
            return _AffineRegion(r1, [], r1, False)
        A = r1.lo if r1.lo <= r2.lo else r2.lo
        X = r1.hi if r1.hi >= r2.hi else r2.hi
        left = Region.of(Interval(-INF, A, False, False))
        if (r1 & left) != (r2 & left):
            return None
        right = Region.of(Interval(X, INF, True, False))
        tail = r1 & right
        moves = (r2 & right) != tail
        if moves and (r2 & Region.of(Interval(X + s, INF, True, False))) != tail.shift(s):
            return None
    except IncompatiblePeriods:
        return None
    i1 = r1.on(A, X).intervals
    i2 = r2.on(A, X + s if moves else X).intervals
    if len(i1) != len(i2):
        return None
    ends = []
    for a, b in zip(i1, i2):
        lo, hi = _moved(a.lo, b.lo, s), _moved(a.hi, b.hi, s)
        if lo is None or hi is None:
            return None
        ends.append((a.lo, lo, a.hi, hi))
    return _AffineRegion(r1 & left, ends, tail, moves)


class _AffineRegion:
    """r(d): the fixed part on the left, the intervals between (each end fixed or moving
    by d), and the part on the right, fixed or moving by d."""

    def __init__(self, fixed: Region, ends: list, tail: Region, moves: bool) -> None:
        self.fixed, self.ends, self.tail, self.moves = fixed, ends, tail, moves

    def __call__(self, d) -> Region:
        if not self.ends and not self.moves:
            return self.tail
        ivs = []
        for lo, lm, hi, hm in self.ends:
            x, y = (lo + d if lm else lo), (hi + d if hm else hi)
            if not x <= y:
                raise _NotAffine
            ivs.append(Interval._mk(x, y))
        return Region.of(IntervalSet(ivs)) | self.fixed | (self.tail.shift(d) if self.moves else self.tail)

def _moved(e1, e2, s):
    """True if the end moved by s, False if it stayed, None otherwise."""
    if e1 == e2:
        return False
    if type(e1) is Pos and type(e2) is Pos and e2 == e1 + s:
        return True
    return None


def _same_line(l1: Line, l2: Line) -> bool:
    if set(l1.vals) != set(l2.vals):
        return False
    try:
        return all(set(l1.vals[k]) == set(l2.vals[k]) and all(l1.vals[k][v] == l2.vals[k][v] for v in l1.vals[k])
                   for k in l1.vals)
    except IncompatiblePeriods:
        return False


class _Changes(Exception):
    def __init__(self, at: Pos) -> None:
        self.at = at


def _iv(a: Pos, b: Pos) -> Interval:
    return Interval(a, b)


def _gone(before: LineState, after: LineState, x: Pos, placed: str) -> tuple:
    """(kind, positive): the claims at x the edit removed, besides those of the kind it
    places (which the put replaces anyway)."""
    out = []
    for k in sorted(set(before.claims.pos) | set(before.claims.neg)):
        if k == placed:
            continue
        if before.claims.positive(k).contains(x) and not after.claims.positive(k).contains(x):
            out.append((k, True))
        if before.claims.negative(k).contains(x) and not after.claims.negative(k).contains(x):
            out.append((k, False))
    return tuple(out)


def _holds(o, s: LineState, x: Pos) -> bool:
    if o.negated:
        return not s.line.presence(o.kind).contains(x)
    if o.value is not None:
        return s.line.vals.get(o.kind, {}).get(o.value, Region.empty()).contains(x)
    return s.line.presence(o.kind).contains(x)
