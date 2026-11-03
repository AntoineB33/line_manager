"""The sweep: the "or" choices when the positions that act on each other are not discrete.

When a rule that reads a chosen kind uses an interval or a left edge l, every
position acts on the positions near it, and the choices are made by a sweep from
left to right over the positions x + kε (intervals.py):

- at a position m, the "or"s met at m (relative to l: whose left side starts a
  largest interval at m) are decided in their order, each taking the leftmost
  operand that does not contradict the rules with everything on its left;
- from m on, the same operands go on (a "run") up to the first position where the
  decision would differ: the operand cannot go on, an earlier operand can be chosen
  again, or an "or" starts or stops being met. That position exists: there are
  finitely many changes in a bounded stretch, and one happens right after a position
  if needed (x + ε). This is how B gets 1+ε after B at 0 with "B → no E on (0, 1]
  of l" and "B → E": B cannot be at any position of (0, 1], and 1+ε is the first one
  after it.

Why this is the definition (solver.py: each position takes the first operand that
still lets all the other positions be placed, given the choices on its left). A
chosen kind that a left side reads is only placed at or after the position where it
is decided (rules.py), so what is on the left of the sweep, its largest intervals and
their left edges included, never changes again, and a choice only adds consequences:
an operand that contradicts the rules with what is on its left contradicts them
whatever comes next. So if this greedy sweep never meets a position where no
operand can be chosen, its own result shows that each operand it took can be
placed: it is the definition's result.

Looking ahead (`complete`). If the greedy sweep meets a position where nothing can
be chosen, an earlier choice must change. The positions are then decided one block
at a time, from left to right, each operand being kept only if the rest of the line
can still be completed (asked recursively, the greedy sweep first): a depth-first
search in the order of the definition. A block is a position, or the positions
strictly between two positions where a choice can change, which take the same
operand:
- real parts. The rules on chosen kinds move positions by multiples of g, the gcd
  of their distances. Shifting every change whose real part is r (mod g), r not one
  of the ends of the claims and fixed regions (mod g), by a small real amount keeps
  every rule satisfied, and shifting it one way gives an earlier choice of an
  earlier operand: the result of the definition has no change there. Changes are at
  real parts e + n·g;
- ε parts. A real distance keeps the ε part of a position, and an open end adds
  ±1, so the changes are looked for within `eps_reach` of the ε parts of the ends of the
  claims, fixed regions and choices nearby. This is the one assumption the search
  makes; a check between candidate positions refuses the edit if a change falls
  elsewhere.
The search remembers each situation it has completed or failed: what the choices
before P placed and forbade as far as the rules reach from P. A failure also rules
out every situation with no less placed and forbidden (choices only add
consequences) and the same presence just before P. Positions where nothing could be
chosen are remembered too: an operand that leaves one of them with nothing,
whatever comes next, is skipped. Beyond MAX_CALLS steps the edit is refused.

Where the decision can change in a run. Given what is decided before a run, the
consequences of the run [P, m) and of an operand at m are regions whose ends are
fixed or move with m (m + d, d a sum of rule offsets along the rules); whether the
rules hold changes only when a moving end crosses a fixed one. So the changes are
at e - d + jε (e the end of a region, j small), and between two such positions the
decision is the same: the sweep looks only there (and checks a position in between).


Beyond the elements and claims everything is invariant by translation, or repeats
with a period q (an element that repeats); when the choices of the last stretch
(as long as the rules reach) repeat, translated (by a multiple of q, with no ε part,
if the data repeats), the choices repeat forever, possibly with an ε part in the
period (1+ε). On an element unbounded on the left there is no first position: decided
only if the choices do not act on each other (`_far_left`), else `Undetermined`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from intervals import EPS, INF, Interval, IntervalSet, Pos
from region import ZERO, Region, _constant, flcm
from rules import Atom, _fgcd, conjuncts
from solver import EMPTY, Conflict, Undetermined, checkpoint, close, kinds_of, lhs_region, unbound_reach, union

MAX_EVENTS = 3000
MAX_CALLS = 3000
PROBE_CALLS = 40


@dataclass
class _Item:
    rule: int
    lhs: object
    anchor: str
    ops: tuple
    text: str


def _target(o, base: Region) -> Region:
    return base.shift(o.rel) if o.is_point else base.dilate(o.where)


def _after(x) -> Region:
    return Region.of(Interval(x, INF, False, False))


def _upto(x) -> Region:
    return Region.of(Interval(-INF, x, False, True))


def _with(ch, ii: int, oi: int, reg: Region):
    out = [list(row) for row in ch]
    out[ii][oi] = out[ii][oi] | reg
    return out


def _ends(reg: Region, lo, hi) -> list:
    return [e for iv in reg.on(lo, hi).intervals for e in (iv.lo, iv.hi)]


class _Views(dict):
    """kind -> where it is present, for reading values with `Formula.region2`."""

    def __init__(self, vals: dict) -> None:
        super().__init__({k: union(d.values()) for k, d in vals.items()})


class Sweep:
    def __init__(self, rs, prob, vals, fixed, forbid, kinds_in, probe=None, shared=None) -> None:
        self.rs = rs
        self.args = (rs, prob, vals, fixed, forbid, kinds_in)
        self.vals0 = dict(vals)
        self.fixed = fixed
        self.forbid0 = dict(forbid)
        self.claims = {k: dict(prob.pos.get(k, {})) for k in kinds_in}
        # shared by the probes of one solve: their results, and the number of steps
        self.shared = shared if shared is not None else {"probes": {}, "calls": 0, "limit": MAX_CALLS}
        self.probe = probe
        if probe is not None:  # what is present at P - ε, as claims (`_probe`)
            for k, v in probe[1]:
                self.claims[k][v] = self.claims[k].get(v, EMPTY) | Region.point(probe[0] - EPS)
        self.kin = [k for k in kinds_of(rs, prob) if k in kinds_in]
        self._item_at = [j for _, j in rs.or_items]
        self.items = [_Item(i, rs.element[i].lhs, "p" if len({o.anchor for o in rs.element[i].rhs[j].all_lits()}) > 1 else
                            rs.element[i].rhs[j].options[0].anchor, rs.element[i].rhs[j].options,
                            str(rs.element[i].rhs[j])) for i, j in rs.or_items]
        self.pos_lits = {k: [] for k in self.kin}
        self.neg_lits = []
        for i, r in enumerate(rs.element):
            if not set(r.lhs.kinds()) & kinds_in:
                continue
            for it in r.rhs:
                if it.is_or:
                    continue
                o = it.options[0]
                if o.negated:
                    self.neg_lits.append((i, o))
                else:
                    self.pos_lits[o.kind].append((i, o))
        self.lits = [x for k in self.kin for x in self.pos_lits[k]]
        self.op_pos = {k: [] for k in self.kin}
        self.op_neg = []
        for ii, item in enumerate(self.items):
            extra = self.rs.element[item.rule].rhs[self._item_at[ii]].extra
            for oi, o in enumerate(item.ops):
                for lit in (o, *(extra[oi] if extra else ())):
                    if lit.negated:
                        self.op_neg.append((ii, oi, lit))
                    elif lit.kind in self.op_pos:
                        self.op_pos[lit.kind].append((ii, oi, lit))
        self.none = [[EMPTY] * len(it.ops) for it in self.items]
        self.minf = {}  # the operand an "or" relative to l takes at l = -inf (`_at_minf`)
        self.barred = None  # `_outward`: for each "or", where each operand cannot be chosen
        self.deltas = self._deltas()
        self.R = max((abs(d.x) for d in self.deltas), default=Fraction(0)) + 1
        self.reach = self.fwd + self.back + 1
        if probe is not None:
            P, _, forward = probe
            self.inside = Region.of(Interval(P, INF, True, False))
            self.edges_after = Region.of(Interval(P + self.fwd if forward else P, INF, not forward, False))
        # the ends of the data (not what a probe puts at P - ε, which stands for choices)
        claims = {k: prob.pos.get(k, {}) for k in kinds_in}
        statics = list(self.vals0.values()) + [{0: f} for f in self.forbid0.values()] + list(claims.values())
        statics += [{0: p} for p, _ in self.fixed.values()] + [e for _, e in self.fixed.values()]
        self.static_regions = [reg for d in statics for reg in d.values() if reg]
        for it in self.items:
            if not set(it.lhs.kinds()) & kinds_in:
                self.static_regions.append(lhs_region(it.lhs, self.vals0))
        # the data beyond its ends: constant, or repeating with period q (an element that
        # repeats): a repetition of the choices is then q apart, or a
        # multiple of q
        self.periodic_ok = all(_constant(reg.left, reg.pl) for reg in self.static_regions)
        self.q = None
        for reg in self.static_regions:
            if not _constant(reg.right, reg.pr):
                if reg.pr.k:
                    self.periodic_ok = False
                else:
                    self.q = reg.pr.x if self.q is None else flcm(self.q, reg.pr.x)
        bounds = [abs(b.x) for reg in self.static_regions for b in (reg.lo, reg.hi)]
        self.T = max(bounds, default=Fraction(0)) + 2 * self.R
        self.static_ends = sorted({e for reg in self.static_regions for e in _ends(reg, -self.T - self.R, self.T + self.R) if type(e) is Pos})

    # ------------------------------------------------------------------ the line of given choices
    def closure(self, ch, horizon, check: bool = True, forbids: bool = False):
        """The values of all kinds when the "or"s took these choices (ch[item][operand]:
        the positions, or left edges, where it took that operand). The consequences
        relative to l of the rules that read chosen kinds count for the left edges up to
        `horizon` (those that can no longer change). Raises Conflict (if check). With
        `forbids`: (values, where each kind is forbidden), without checking."""
        anchors: dict[int, Region] = {}
        cut = None if horizon == INF else (EMPTY if horizon == -INF else _upto(horizon))
        mask = self.edges_after if self.probe is not None else None
        if cut is not None:
            mask = cut if mask is None else mask & cut
        if check and self.barred is not None and any(r & ch[ii][oi] for ii, row in enumerate(self.barred) for oi, r in enumerate(row)):
            raise Conflict("an operand barred there")
        extra = {}
        for K in self.kin:
            plain, exact = self.fixed.get(K, (EMPTY, {}))
            exact = dict(exact)
            for ii, oi, o in self.op_pos[K]:
                t = self._place(ii, oi, o, ch[ii][oi])
                if t:
                    if o.value is None:
                        plain = plain | t
                    else:
                        exact[o.value] = exact.get(o.value, EMPTY) | t
            extra[K] = (plain, exact)
        # the kinds the choices change, kind after kind
        vals = dict(self.vals0)
        vals.update(close(self.rs, self.kin, self.lits, self.claims, self.vals0, extra, check and not forbids, mask, anchors))

        def base(i, o):
            if i not in anchors:
                anchors[i] = lhs_region(self.rs.element[i].lhs, vals)
            a = anchors[i]
            if o.anchor == "p":
                return a
            e = a.left_edges()
            return e if mask is None else e & mask

        if not check and not forbids:
            return vals
        forbid = dict(self.forbid0)
        for i, o in self.neg_lits:
            t = _target(o, base(i, o))
            forbid[o.kind] = forbid.get(o.kind, EMPTY) | (Region.universe() if unbound_reach(o, anchors[i]) else t)
        for ii, oi, o in self.op_neg:
            t = self._place(ii, oi, o, ch[ii][oi])
            if t:
                forbid[o.kind] = forbid.get(o.kind, EMPTY) | t
        if forbids:
            return vals, forbid
        for k, f in forbid.items():
            bad = union(vals.get(k, {}).values()) & f
            if bad:
                raise Conflict(f"{k} is required to be absent on {bad}")
        return vals

    def _place(self, ii: int, oi: int, o, b: Region) -> Region:
        """Where the element o of operand oi goes, the "or" ii having chosen it on b. On a
        largest interval with no left end, l is -inf: l + [a, +inf) is the whole line
        (requirement.md), l + d and l + [a, b] are at infinity, nothing on the line. An "or"
        relative to l chooses there once, at -inf (`minf`)."""
        it = self.items[ii]
        t = _target(o, self._from(ii, o, b)) if b else EMPTY
        if o.anchor == "l" and o.where.hi == INF:
            if it.anchor == "l":
                if self.minf.get(ii) == oi:
                    return Region.universe()
            elif b and any(c.inf() == -INF and c & b for c in lhs_region(it.lhs, self.vals0).components()):
                return Region.universe()
        return t

    def _at_minf(self, res) -> None:
        """An "or" relative to l, met on a largest interval with no left end, chooses at l =
        -inf, the furthest from 0: last (requirement.md). It takes its first operand that
        the rules allow with the choices of all the others."""
        vals = self.closure(res, INF, check=False)
        for ii, it in enumerate(self.items):
            if it.anchor != "l" or not any(c.inf() == -INF for c in lhs_region(it.lhs, vals).components()):
                continue
            for oi in range(len(it.ops)):
                self.minf[ii] = oi
                if self._valid(res, INF):
                    break
            else:
                del self.minf[ii]
                raise Undetermined(f"{it.text}: no operand fits at l = -inf with the other choices, and this "
                                   "solver does not look ahead to it")

    def _from(self, ii: int, o, b: Region) -> Region:
        """What the operand o of this "or" is relative to, when the "or" chose on b: b, or
        (an "or" that mixes p and l) the left edges of the largest intervals of b."""
        it = self.items[ii]
        if o.anchor == it.anchor:
            return b
        if set(it.lhs.kinds()) & set(self.kin):
            raise Undetermined(f"{it.text}: operands relative to p and to l, on a left side that an 'or' can change")
        met = lhs_region(it.lhs, self.vals0)
        return union(c for c in met.components() if c & b).left_edges()

    def _valid(self, ch, horizon) -> bool:
        try:
            self.closure(ch, horizon)
            return True
        except Conflict:
            return False

    # ------------------------------------------------------------------ the sweep
    def solve(self) -> dict:
        P = self._next_active(self.none, None)
        start = self.none
        if P == -INF:
            start, P = self._far_left()
        self.memo, self.failed, self.dead, self.stuck, self.first = {}, {}, [], -INF, P
        self._grid()
        if start is not self.none:  # the choices do not act on each other: any order gives them
            res = (start if self._valid(start, INF) else None) if P is None else self.complete(P, start, 0)[0]
        elif self.rs.searched:
            res = self._search()
        elif P is None:  # no "or" is met anywhere
            res = self.none if self._valid(self.none, INF) else None
        elif P < 0 and not self.rs.pointwise and self.probe is None:  # (a probe only asks if a solution exists)
            res = self._outward(P)
        else:
            res, _ = self.complete(P, self.none, 0)
        if res is None:
            raise Conflict("no choice of the 'or' operands satisfies the rules")
        self._at_minf(res)
        return self._finish(res)

    # ------------------------------------------------------------------ unbounded on the left
    def _independent(self) -> bool:
        """The choice at one position never changes which operands can be chosen at another:
        the consequences of several choices are the union of the consequences of each.
        That holds when nothing forbids a chosen kind but the data, each chosen kind gets
        a single percentage, and a left side that reads a chosen kind reads one, as
        present, and only with what no choice changes (and K) & X), placing positions
        relative to p. Each position then takes its first operand that the rules allow
        there alone, whatever the others chose: the order in which the positions choose
        (requirement.md: from the closest to 0) does not matter."""
        kin = set(self.kin)
        # (`RuleSet.searched` does not matter: what makes it, a choice acting on what is
        # decided, is excluded below)
        # (an interval to infinity is fine: left of -T, whether it meets the data is the
        # same at every position, the data being constant beyond its ends)
        if self.probe is not None:
            return False
        if any(lit.kind in kin for _, _, lit in self.op_neg):
            return False
        values = {k: {lit.value for _, _, lit in self.op_pos[k]} for k in kin}
        for r in self.rs.element:
            read = set(r.lhs.kinds()) & kin
            if not read:
                continue
            parts = [f for f in conjuncts(r.lhs) if set(f.kinds()) & kin]
            if len(read) > 1 or len(parts) != 1 or not (isinstance(parts[0], Atom) and parts[0].op == "present"):
                return False
            for lit in r.lits():
                if lit.negated or lit.anchor != "p":
                    return False
                if lit.kind in kin:
                    values[lit.kind].add(lit.value)
        return all(len(v) <= 1 for v in values.values())

    def _far_left(self):
        """An "or" met on an element unbounded on the left: there is no first position for
        the sweep. If the choices do not act on each other (`_independent`), left of -T
        every position sees the same data within the reach of the rules (the data is
        constant beyond its ends), so they all take the same operand: the first one the
        rules allow there. Returns those choices and the position where the sweep goes on
        from them. Otherwise `Undetermined`."""
        if not (self._independent() and all(_constant(reg.left, reg.pl) for reg in self.static_regions)):
            raise Undetermined("an 'or' acts on an element unbounded on the left, and its choices act on each other: "
                               "this solver does not decide them")
        X = Pos(-self.T)
        far = Region.of(Interval(-INF, X, False, False))
        ch = self.none
        vals = self.closure(ch, INF, check=False)
        for ii, it in enumerate(self.items):
            where = self._met(it, vals) & far
            if not where:
                continue
            for oi in range(len(it.ops)):
                c = _with(ch, ii, oi, where)
                if self._valid(c, INF):
                    ch = c
                    break
            else:
                raise Conflict(f"{it.text}: no operand can be chosen on {where}")
        return ch, self._next_active(ch, X - EPS)

    # ------------------------------------------------------------------ from 0 outwards
    def _cells(self, L: Fraction) -> list[tuple]:
        """The positions of [-L, L] in cells, each (region, a position of it), the closest to
        0 first and the right one first when two are as close. Inside a cell nothing can
        change: its ends are the ends of the data moved by the distances of the rules, on
        both sides of 0, and where a position and one a distance d from it are as close to
        0 (d / 2)."""
        ds = {d.x for d in self.deltas} | {Fraction(0)}
        xs = {e.x for e in self.static_ends} | {Fraction(0), L}
        cuts = {x + d for x in xs for d in ds} | {d / 2 for d in ds}
        cuts = sorted({x for c in cuts for x in (c, -c) if -L <= x <= L})
        J = self.rs.eps_reach
        out = []
        for n, x in enumerate(cuts):
            out += [(Region.point(Pos(x, j)), Pos(x, j)) for j in range(-J, J + 1)]
            if n + 1 < len(cuts):
                out.append((Region.of(Interval(Pos(x, J + 1), Pos(cuts[n + 1], -J - 1))), Pos((x + cuts[n + 1]) / 2)))

        def turn(cell):
            p = cell[1]
            return abs(p.x), (p.k if p.x > 0 else -p.k if p.x < 0 else abs(p.k)), p.x < 0 or (p.x == 0 and p.k < 0)
        return sorted(out, key=turn)

    def _search(self) -> list | None:
        """The choices of the definition with no order to rely on: the cells from the closest
        to 0, each "or" met there taking its operands in turn, up to a line where the rules
        hold, every anchor has its choice and every choice its anchor. A contradiction stops
        an operand at once only if choosing more cannot take it away (`RuleSet.settled`).
        Past the data the choices are not followed: the edit is refused."""
        cells = self._cells(self.T)
        J = self.rs.eps_reach
        far = _after(Pos(self.T, J)) | Region.of(Interval(-INF, Pos(-self.T, -J), False, False))

        def nxt(ch):
            vals = self.closure(ch, INF, check=False)
            for reg, p in cells:
                for ii, it in enumerate(self.items):
                    if self._active(it, vals, p) and not any(r.contains(p) for r in ch[ii]):
                        if it.anchor == "l":
                            return vals, ii, Region.point(p)
                        if not reg.issubset(self._met(it, vals)):
                            raise Undetermined(f"the sweep could not place exactly where a choice changes around {p}")
                        return vals, ii, reg
            return vals, None, None

        def good(ch, vals) -> bool:
            if not self._valid(ch, INF):
                return False
            for ii, it in enumerate(self.items):
                met = self._met(it, vals)
                if met & far:
                    raise Undetermined("the choices of these rules are only searched on bounded elements")
                if not all(r.issubset(met) for r in ch[ii]):
                    return False  # a choice that took its own anchor away
            return True

        window = union(reg for reg, _ in cells)

        def doomed(ch) -> bool:
            """A contradiction no further choice can take away: what is placed for sure, the
            absences read from the most that can still be placed, against what is forbidden
            for sure. Rules relative to l are left out: their left edges can still move."""
            most = [[r | (window - union(row)) for r in row] for row in ch]
            low, high = self.closure(ch, INF, check=False), self.closure(most, INF, check=False)
            ph = _Views(high)
            sure = {k: dict(d) for k, d in low.items() if k not in self.kin}

            def put(k, v, r):
                d = sure.setdefault(k, {})
                d[v] = d.get(v, EMPTY) | r
            for k in self.kin:
                plain, exact = self.fixed.get(k, (EMPTY, {}))
                put(k, Fraction(100), plain)
                for v, r in [*exact.items(), *self.claims.get(k, {}).items()]:
                    put(k, v, r)
            def aim(ii, o, b):
                """Where the operand places, if that is known for sure (a left edge of a
                left side that no choice changes)."""
                if o.anchor == self.items[ii].anchor:
                    return _target(o, b)
                try:
                    return _target(o, self._from(ii, o, b))
                except Undetermined:
                    return EMPTY
            for ii, oi, o in (x for k in self.kin for x in self.op_pos[k]):
                if ch[ii][oi]:
                    put(o.kind, o.value or Fraction(100), aim(ii, o, ch[ii][oi]))
            forbid = dict(self.forbid0)
            for ii, oi, o in self.op_neg:
                if ch[ii][oi]:
                    forbid[o.kind] = forbid.get(o.kind, EMPTY) | aim(ii, o, ch[ii][oi])
            U = Region.universe()
            for _ in range(len(self.kin) + 1):
                ps = _Views(sure)
                for r in self.rs.element:
                    if set(r.lhs.kinds()) & set(self.kin):
                        m = r.lhs.region2((sure, ps), (high, ph), U, EMPTY)
                        for it in r.rhs:
                            o = it.options[0]
                            if it.is_or or o.anchor != "p" or not m:
                                continue
                            if o.negated:
                                forbid[o.kind] = forbid.get(o.kind, EMPTY) | _target(o, m)
                            elif o.kind in self.kin:
                                put(o.kind, o.value or Fraction(100), _target(o, m))
            ps = _Views(sure)
            return any(ps.get(k, EMPTY) & f for k, f in forbid.items())

        def go(ch):
            checkpoint()
            self.shared["calls"] += 1
            if self.shared["calls"] > self.shared["limit"]:
                raise Undetermined(f"deciding the choices takes more than {MAX_CALLS} attempts")
            if doomed(ch):
                return None
            vals, ii, where = nxt(ch)
            if ii is None:
                return ch if good(ch, vals) else None
            for oi in range(len(self.items[ii].ops)):
                c = _with(ch, ii, oi, where)
                if self.rs.settled and not self._valid(c, INF):
                    continue
                out = go(c)
                if out is not None:
                    return out
            return None
        return go(self.none)

    def _outward(self, P) -> list | None:
        """The choices of the definition when they act on each other on the left of 0. The
        sweep from left to right finds a solution if there is one. So, from the closest cell
        to 0, each "or" keeps the first operand with which a solution is still found, the
        others being barred there (`barred`). Right of the last cell, the order is the
        sweep's own."""
        cells = self._cells(abs(P.x))
        self.static_ends = sorted(set(self.static_ends) | {e for reg, _ in cells for e in _ends(reg, reg.lo, reg.hi) if type(e) is Pos})
        self._grid()
        self.barred = [[EMPTY] * len(it.ops) for it in self.items]
        tries = [0]

        def found():
            tries[0] += 1
            if tries[0] > MAX_CALLS:
                raise Undetermined(f"deciding the choices from 0 takes more than {MAX_CALLS} attempts")
            checkpoint()
            self.memo, self.failed, self.dead, self.stuck = {}, {}, [], -INF
            self.shared["calls"] = 0
            return self.complete(P, self.none, 0)[0]

        def only(ii, oi, reg):
            """The bars with this operand of this "or" the only one on reg."""
            was = list(self.barred[ii])
            self.barred[ii] = [r if o == oi else r | reg for o, r in enumerate(was)]
            return was
        try:
            best = found()
            if best is None:
                return None
            for reg, p in cells:
                for ii, it in enumerate(self.items):
                    if it.anchor == "l" and reg != Region.point(p):
                        continue  # (a left edge is a position)
                    took = [oi for oi, r in enumerate(best[ii]) if r.contains(p)]
                    if not took:
                        continue
                    for oi in range(took[0]):
                        was = only(ii, oi, reg)
                        sol = found()
                        if sol is not None:
                            best, took = sol, [oi]
                            break
                        self.barred[ii] = was
                    else:
                        only(ii, took[0], reg)
                        if not reg.issubset(best[ii][took[0]]):
                            best = found()
                            if best is None:
                                raise Undetermined(f"the sweep could not place exactly where a choice changes around {p}")
            return best
        finally:
            self.barred = None

    def _greedy(self, P, ch, start=0):
        """From P (the "or"s before `start` at P already decided): each position takes the
        leftmost operand that does not contradict the rules with what is on its left.
        ("ok", choices) if that never fails, else ("dead", position, "or") where nothing
        could be chosen (position None: the rules fail once everything is decided)."""
        seen: dict = {}
        events = 0
        while P is not None:
            checkpoint()
            events += 1
            if events > MAX_EVENTS:
                raise Undetermined(f"the choices change more than {MAX_EVENTS} times without repeating")
            if start == 0 and self.periodic_ok and P.x > self.T:
                sig = self._signature(ch, P)
                if self.q is not None:
                    sig = (sig, P.x % self.q, P.k)
                if sig in seen and (P - seen[sig]).x > 0:
                    return "ok", self._repeat(ch, seen[sig], P)
                seen[sig] = P
            ch, failed = self._decide(P, ch, start)
            start = 0
            if failed is not None:
                return "dead", P, failed
            runs = self._runs_at(P, ch)
            if runs:
                P, ch = self._run(P, runs, ch)
            else:
                P = self._next_active(ch, P)
        if not self._valid(ch, INF):
            return "dead", None, None
        return "ok", ch

    def _runs_at(self, P, ch):
        return [(ii, oi) for ii, it in enumerate(self.items) if it.anchor == "p"
                for oi in range(len(it.ops)) if ch[ii][oi].contains(P)]

    # ------------------------------------------------------------------ looking ahead
    def complete(self, P, ch, start=0, U=None):
        """The choices of the definition from P on, given `ch` (the positions before P, and
        the "or"s before `start` at P): each position takes the leftmost operand that
        still lets all the other positions be placed. Returns (choices, None), or (None,
        q) if no completion exists, where q is the first position whose choice could be
        why: changing a choice before q cannot help.

        The greedy sweep is tried first: if it never fails, it is the answer. Otherwise
        the positions from P are decided one block at a time (`_unit`), each operand
        being kept only if the rest can be completed (recursively, greedy first)."""
        checkpoint()
        self.shared["calls"] += 1
        if self.shared["calls"] > self.shared["limit"]:
            raise Undetermined("deciding the choices needs to look ahead further than this solver does "
                               f"({MAX_CALLS} attempts)")
        state = self._state(ch, P)
        ks = self._kset(ch, P)
        key = (P, start, ks, state)
        if key in self.memo:
            return self.memo[key]
        fixed_part, more = self._split_state(state, P)
        fixed_part = (ks, fixed_part)
        for failed in self.failed.get((P, start, fixed_part), ()):
            if all(have.issubset(more.get(label, IntervalSet())) for label, have in failed.items()):
                return None, P - self.reach  # with no less placed and forbidden, it fails too
        # the greedy sweep only from a position with nothing decided after it, and not
        # again before the position where it last failed (it would fail there again)
        r = ("dead", None, None)
        if P > self.stuck and not any(reg.sup() > P for row in ch for reg in row):
            r = self._greedy(P, ch, start)
        if r[0] == "ok":
            res = r[1], None
        else:
            if r[1] is not None:
                self.stuck = r[1]
                if r[2] is not None and r[2] >= 0:
                    self._learn(r[1], r[2])
            # nothing before P could make it possible: fail now (the probe of the start
            # would be this very problem)
            if start == 0 and U is None and P > self.first and self._probe(ch, P) is False:
                res = None, P - self.reach
            else:
                res = self._careful(P, ch, start, self._unit(P, ks) if U is None else U)
        self.memo[key] = res
        if res[0] is None:
            self.failed.setdefault((P, start, fixed_part), []).append(more)
        return res

    def _split_state(self, state, P):
        """The part of the state that must be equal (the choices from P on, and what is
        present right before P, which decides where a largest interval starts), and the
        part where more only makes things harder (what is placed and forbidden): a
        choice, an element or a "no" never removes a consequence."""
        fixed, more = [], {}
        for label, ivs, _, _ in state:
            if label[0] == "or":
                fixed.append((label, ivs))
            else:
                s = IntervalSet._sorted(list(ivs))
                more[label] = s
                if label[0] != "no":
                    fixed.append((label, s.contains(-EPS)))
        return tuple(fixed), more

    def _careful(self, P, ch, start, U):
        """Decide the block [P, U] that starts at P, operand by operand, in order.

        Jumping back: a failure found at d comes from choices that act on d, so at
        positions from d - reach on (reach = how far a choice acts, back and forward).
        If no completion exists for an operand and its first such position is after P,
        the choice at P is not why: the other operands would fail the same way, and the
        failure goes back further at once."""
        try:
            vals = self.closure(ch, P)
        except Conflict:
            return None, P - self.reach
        for ii in range(start, len(self.items)):
            it = self.items[ii]
            if not self._active(it, vals, P):
                continue
            if it.anchor == "l" and U != P:
                raise Undetermined(f"the sweep could not place exactly where a choice changes after {P}")
            where = Region.of(Interval(P, U))
            q = None
            for oi in range(len(it.ops)):
                c = _with(ch, ii, oi, where)
                if not self._valid(c, U):
                    qo = P - self.reach
                else:
                    d = self._doomed(c, U)
                    if d is not None:
                        qo = d - self.reach
                    else:
                        r, qo = self.complete(P, c, ii + 1, U)
                        if r is not None:
                            return r, None
                if qo > P:
                    return None, qo
                q = qo if q is None or qo < q else q
            return None, q
        # every "or" met on the block is decided: the next block
        nxt = self._next_active(ch, U) if not self._runs_at(P, ch) else U + EPS
        if nxt is None:
            return (ch, None) if self._valid(ch, INF) else (None, -INF)
        return self.complete(nxt, ch, 0)

    def _grid(self) -> None:
        """Where a choice can change: real parts r + n·g (r the real parts of the ends of
        the claims and of the fixed regions modulo g, g the gcd of the distances of the
        rules on chosen kinds), and around the ε parts of the ends seen."""
        ds = [abs(e.x) for k in self.kin for _, _, o in self.op_pos[k] for e in (o.where.lo, o.where.hi) if type(e) is Pos]
        ds += [abs(e.x) for _, _, o in self.op_neg for e in (o.where.lo, o.where.hi) if type(e) is Pos]
        ds += [abs(e.x) for lits in self.pos_lits.values() for _, o in lits for e in (o.where.lo, o.where.hi) if type(e) is Pos]
        ds += [abs(e.x) for _, o in self.neg_lits for e in (o.where.lo, o.where.hi) if type(e) is Pos]
        g = None
        for d in ds:
            if d:
                g = d if g is None else _fgcd(g, d)
        self.g = g or Fraction(1)
        self.residues = sorted({e.x % self.g for e in self.static_ends} | {Fraction(0)})

    def _kset(self, ch, P) -> tuple:
        """The ε parts to look at near P: around those of the ends of the claims and fixed
        regions, and of the choices made before P (not their end at P - ε, which is only
        where the decisions have got to). A real distance keeps the ε part of a
        position, so every end that matters has one of these, within `eps_reach`."""
        a, b = P - 2 * self.R, P + 2 * self.R
        ends = [e for e in self.static_ends if a <= e <= b]
        for row in ch:
            for reg in row:
                for iv in reg.on(a - 1, P).intervals:
                    ends += [e for e in (iv.lo, iv.hi) if a <= e < P - EPS]
        J = self.rs.eps_reach
        return tuple(sorted({k for e in ends for k in range(e.k - J, e.k + J + 1)} | set(range(-J, J + 1))))

    def _on_grid(self, x) -> bool:
        return any((x - r) % self.g == 0 for r in self.residues)

    def _unit(self, P, ks):
        """The block that starts at P: P alone if a choice can change right after it,
        else up to just before the next position where one can."""
        if self._on_grid(P.x) and P.k in ks:
            return P
        if self._on_grid(P.x):
            later = [k for k in ks if k > P.k]
            if later:
                return Pos(P.x, later[0]) - EPS
        s = min(r + self.g * (math.floor((P.x - r) / self.g) + 1) for r in self.residues)
        return Pos(s, ks[0]) - EPS

    def _state(self, ch, P):
        """What the completion from P depends on: what the choices before P have placed
        and forbidden as far as the rules reach from P (the left edges before P can no
        longer change), and the choices already made from P on."""
        vals, forbid = self.closure(ch, P - EPS, check=False, forbids=True)
        a, b = P - self.back - EPS, P + self.fwd + 2 * EPS

        def part(label, reg, lo, hi):
            # the part of reg in [lo, hi), relative to P, and whether reg goes on past it
            return label, tuple(reg.on(lo, hi).shift(-P).intervals), reg.contains(lo - EPS), reg.contains(hi)

        out = [part(("or", i, j), reg, P, b + 3 * self.R) for i, row in enumerate(ch) for j, reg in enumerate(row)]
        for k in self.kin:
            out += [part((k, v), reg, a, b) for v, reg in sorted(vals[k].items())]
        out += [part(("no", k), f, a, b) for k, f in sorted(forbid.items())]
        return tuple(out)

    def _learn(self, d, ii) -> None:
        if (d, ii) not in self.dead:
            self.dead.append((d, ii))

    def _probe(self, ch, P):
        """False if no completion exists from P whatever came before P, given only what is
        present at P - ε: the same problem with the "or"s only met from P on, the past
        reduced to what is present at P - ε (as claims), and no consequence of a left
        edge before P (nor up to P + fwd for kinds the past placed after P, whose left
        edges there could differ). That problem has fewer constraints than the real one
        (a choice only adds consequences, and being present at P - ε is the same), so
        if it has no completion, neither has the real one. Its result depends only on
        its key, and is shared."""
        vals = self.closure(ch, P - EPS, check=False)
        atoms = tuple(sorted((k, v) for k in self.kin for v, reg in vals[k].items() if reg.contains(P - EPS)))
        after = Region.of(Interval(P, P + self.fwd))
        static = lambda k: union(list(self.claims[k].values()) + [self.fixed.get(k, (EMPTY, {}))[0]]
                                 + list(self.fixed.get(k, (EMPTY, {}))[1].values()))
        forward = any((union(vals[k].values()) & after) - static(k) for k in self.kin)
        key = (P, atoms, forward)
        probes = self.shared["probes"]
        if key not in probes:
            probes[key] = None  # being computed: no answer meanwhile
            # a probe helps only when it fails, which takes few steps (it uses the probes
            # after it): a small budget of its own, beyond which it says nothing
            sub = Sweep(*self.args, probe=key, shared={"probes": probes, "calls": 0, "limit": PROBE_CALLS})
            try:
                sub.solve()
                probes[key] = True
            except Conflict:
                probes[key] = False
            except Undetermined:
                probes[key] = None
        return probes[key]

    def _doomed(self, ch, h):
        """A position where nothing could be chosen before, still with nothing whatever
        is decided after h (the choices in ch are up to h): no completion exists."""
        for d, ii in self.dead:
            if d > h and self._blocks(ch, d, ii, h):
                return d
        return None

    def _blocks(self, ch, d, ii, h) -> bool:
        it = self.items[ii]
        vals = self.closure(ch, h, check=False)
        # met at d whatever comes next (a left side only grows; a left edge only if fixed)
        if not self._met(it, vals).contains(d) or (it.anchor == "l" and set(it.lhs.kinds()) & set(self.kin)):
            return False
        return not any(self._valid(_with(ch, ii, oi, Region.point(d)), h) for oi in range(len(it.ops)))

    def _next_active(self, ch, after):
        """The first position (after `after`) where an "or" is met, or None."""
        vals = self.closure(ch, INF, check=False)
        best = None
        for it in self.items:
            a = self._met(it, vals)
            if after is not None:
                a = a & _after(after)
            if a:
                m = a.inf()
                if best is None or m < best:
                    best = m
        return best

    def _met(self, it, vals) -> Region:
        """Where the "or" must choose: where its left side is met, or (relative to l) the
        left edges of that."""
        a = lhs_region(it.lhs, vals)
        if it.anchor == "l":
            a = a.left_edges()
            if self.probe is not None:
                a = a & self.edges_after
        return a if self.probe is None else a & self.inside

    def _active(self, it, vals, m) -> bool:
        return self._met(it, vals).contains(m)

    def _decide(self, m, ch, start=0):
        """The "or"s met at m (from `start`) take, in order, the leftmost operand that does
        not contradict the rules with what is on its left. Returns (choices, None), or
        (choices, the "or" that has no operand; -1 if the rules already fail at m)."""
        try:
            vals = self.closure(ch, m)
        except Conflict:
            return ch, -1
        for ii in range(start, len(self.items)):
            it = self.items[ii]
            if not self._active(it, vals, m):
                continue
            for oi in range(len(it.ops)):
                ch2 = _with(ch, ii, oi, Region.point(m))
                try:
                    vals = self.closure(ch2, m)
                except Conflict:
                    continue
                ch = ch2
                break
            else:
                return ch, ii
        return ch, None

    def _run(self, P, runs, ch):
        """The run of the operands decided at P: the next position where the decision
        may differ, and the choices with the run up to just before it."""
        nxt = [e for e in self.static_ends if e > P + self.R]
        cap = nxt[0] if nxt else P + 64 * self.R

        def with_run(Q):
            out = [list(row) for row in ch]
            reg = Region.of(Interval(P, Q))
            for ii, oi in runs:
                out[ii][oi] = out[ii][oi] | reg
            return out

        # where an "or" starts or stops being met (positions up to x only depend on
        # the choices up to x, so the run can be assumed up to the cap)
        vals = self.closure(with_run(cap), cap, check=False)
        end = cap + EPS
        running = {ii for ii, _ in runs}
        for ii, it in enumerate(self.items):
            a = self._met(it, vals)
            if it.anchor == "l":
                change = (a & _after(P)).inf()
            elif ii in running:
                change = (_after(P) - a).inf()
            else:
                change = (a & _after(P)).inf()
            if change < end:
                end = change
        cands = self._candidates(P, end, vals, ch)
        # the run stays valid up to E (validity only gets lost as the run grows)
        E = self._last(lambda Q: self._valid(with_run(Q), Q), P, end - EPS, cands)
        if E < end - EPS:
            end = E + EPS
        # an earlier operand that can be chosen again
        for n, (ii, oi) in enumerate(runs):
            for o2 in range(oi):
                before = runs[:n]

                def can(m, ii=ii, o2=o2, before=before):
                    c = with_run(m - EPS)
                    for jj, oj in before:
                        c[jj][oj] = c[jj][oj] | Region.point(m)
                    c[ii][o2] = c[ii][o2] | Region.point(m)
                    return self._valid(c, m)

                f = self._first(can, P, end, cands)
                if f is not None and f < end:
                    end = f
        return end, with_run(end - EPS)

    def _deltas(self) -> set:
        """Offsets from a decided position to the ends of the regions it changes, the
        differences of two of them."""
        out = self._offsets((len(self.kin) + 1) * self.rs.reach)
        # how far a choice acts on the right of it, and on the left
        self.fwd = max([d.x for d in out] + [Fraction(0)])
        self.back = max([-d.x for d in out] + [Fraction(0)])
        return {a - b for a in out for b in out}

    def _offsets(self, cap) -> set:
        """Offsets from a decided position to the ends of the regions it changes, along
        the rules, up to cap (a path through each kind once stays within the reach of the
        rules times the number of kinds). An end at infinity is not a position: it never
        meets another end (an interval to +inf from p only has the end p + a)."""
        off = {k: set() for k in self.kin}
        out = {ZERO}
        ends = lambda o: {e for e in (o.where.lo, o.where.hi) if type(e) is Pos}
        for ii, oi, o in [(a, b, c) for k in self.kin for a, b, c in self.op_pos[k]]:
            off[o.kind] |= ends(o)
        for _, _, o in self.op_neg:
            out |= ends(o)
        n = len(self.kin) + 1
        keep = lambda x: type(x) is Pos and abs(x.x) <= cap and abs(x.k) <= n + 2
        changed = True
        while changed:
            changed = False
            for K in self.kin:
                for i, r in enumerate(self.rs.element):
                    if K not in r.lhs.kinds():
                        continue
                    for it in r.rhs:
                        if it.is_or:
                            continue
                        o = it.options[0]
                        new = {x for d in off[K] for e in ends(o) for x in [d + e] if keep(x)}
                        if o.negated or o.kind not in off:
                            out |= new
                        elif not new <= off[o.kind]:
                            off[o.kind] |= new
                            changed = True
        for s in off.values():
            out |= s
        return out

    def _candidates(self, lo, hi, vals, ch) -> list:
        """The positions in (lo, hi) where the decision may change."""
        a, b = lo - self.R, hi + self.R if hi != INF else lo + 70 * self.R
        deltas = self.deltas
        ends = {lo} | {e for e in self.static_ends if a <= e <= b}
        for d in vals.values():
            for reg in d.values():
                ends |= {e for e in _ends(reg, a, b) if type(e) is Pos}
        for row in ch:
            for reg in row:
                ends |= {e for e in _ends(reg, a, b) if type(e) is Pos}
        out = set()
        for e in ends:
            for d in deltas:
                x = e + d
                for j in range(-self.rs.eps_reach, self.rs.eps_reach + 1):
                    y = Pos(x.x, x.k + j)
                    if lo < y < hi:
                        out.add(y)
        return sorted(out)

    def _first(self, pred, lo, hi, cands):
        """The first position in (lo, hi) where pred holds, or None. pred is the same
        on the positions strictly between two consecutive candidates: the first of
        them is checked, and a position in the middle confirms it."""
        prev = lo
        for c in [c for c in cands if c < hi] + [hi]:
            f = prev + EPS
            if f < c:
                if pred(f):
                    return f
                if prev.x < c.x and pred(Pos((prev.x + c.x) / 2)):
                    raise Undetermined(f"the sweep could not place exactly where a choice changes between {prev} and {c}")
            if c != hi and pred(c):
                return c
            prev = c
        return None

    def _last(self, pred, lo, hi, cands):
        """The last position x in [lo, hi] with pred(x), pred holding at lo and only
        getting lost as x grows."""
        pts = [c for c in cands if c <= hi]
        if not pts or pts[-1] != hi:
            pts.append(hi)
        i, j = -1, len(pts)  # pred(pts[i]) holds (or i = -1: lo), pred(pts[j]) does not (or j = len)
        while j - i > 1:
            mid = (i + j) // 2
            if pred(pts[mid]):
                i = mid
            else:
                j = mid
        x = pts[i] if i >= 0 else lo
        if j < len(pts):
            nxt = pts[j]
            if x + EPS < nxt and pred(x + EPS):
                if not pred(nxt - EPS):
                    raise Undetermined(f"the sweep could not place exactly where a choice stops between {x} and {nxt}")
                x = nxt - EPS
        return x

    # ------------------------------------------------------------------ end
    def _finish(self, ch) -> dict:
        self.choices = ch
        vals = self.closure(ch, INF)
        return {k: vals[k] for k in self.kin}

    # ------------------------------------------------------------------ repetition
    def _signature(self, ch, P):
        a = P - self.R
        return tuple(tuple(reg.on(a, P).shift(-P).intervals) for row in ch for reg in row)

    def _repeat(self, ch, P1, P2):
        """The choices made before P2, repeating with period P2 - P1 from P2 on."""
        per = P2 - P1
        out = []
        for row in ch:
            new = []
            for reg in row:
                lo = reg.inf() if reg and reg.inf() < P1 else P1
                new.append(Region(lo, P2, reg.on(lo, P2), per, IntervalSet(), per, reg.on(P1, P2).shift(-P1)))
            out.append(new)
        return out


