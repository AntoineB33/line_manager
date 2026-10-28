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
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from guards import check_mode_edit
from intervals import EPS, Interval, Pos
from line import MODE, LineState, Op
from region import IncompatiblePeriods, Region
from resolve import Refused, apply_edit
from rules import RuleSet, _fgcd
from solver import Conflict, Undetermined, lhs_region, solve

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
        if not any(choice and choice[0] == PUT for choice in decision):
            return state
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
        return LineState(c, solve(self.rs, c.problem()), state.history)

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
        (an operand refused, or the rest of the positions refused); returns at the end."""
        x = lo
        while x <= hi:
            decision, s1, why = self.decide(state, x)
            for w in why:
                yield x, w
            if s1 is not state:
                yield Run(x, Region.point(x), state, s1)
            state = s1
            nxt = x + EPS
            if nxt > hi:
                return
            try:
                end, state2 = self._extend(state, decision, x, nxt, hi)
            except _Changes as e:
                yield e.at, "mode rules: the decision changes at a position that is not looked at; the rest is refused"
                return
            if end is not None:
                at = Region.of(_iv(nxt, end))
                if state2 is not state:
                    yield Run(nxt, at, state, state2)
                state = state2
                nxt = end + EPS
            x = nxt
        return

    def _extend(self, state: LineState, decision: tuple, m: Pos, lo: Pos, hi: Pos):
        """How far the decision taken at m goes on from lo (<= hi): (last position or None,
        the state with the run)."""
        cands = self.candidates(state, m, lo, hi)
        pieces, cur = [], lo
        for c in cands + [None]:
            top = hi if c is None else c - EPS
            if cur <= top:
                pieces.append((cur, top))  # positions strictly between two candidates
            if c is None:
                break
            pieces.append((c, c))
            cur = c + EPS
        last, run_state = None, state
        for a, b in pieces:
            reps = [a] if a == b else [a] + ([Pos((a.x + b.x) / 2)] if a.x < b.x else []) + [b]
            for i, p in enumerate(reps):
                ok, st = self._same(state, decision, lo, p)
                if not ok:
                    if i == 0:
                        return last, run_state
                    raise _Changes(p)
                if i == len(reps) - 1:
                    last, run_state = p, st
        return last, run_state

    def _same(self, state: LineState, decision: tuple, lo: Pos, p: Pos) -> tuple[bool, LineState | None]:
        """With the run on [lo, p), is the decision at p the same? And the state with the
        run on [lo, p]."""
        try:
            before = self.with_run(state, decision, Region.of(_iv(lo, p - EPS))) if lo < p else state
            d, after, _ = self.decide(before, p)
        except (Conflict, Undetermined, IncompatiblePeriods):
            return False, None
        return d == decision, after


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
