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
  ±1, so the changes are looked for within ±2 of the ε parts of the ends of the
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

Beyond the elements and claims everything is invariant by translation; when the
choices of the last stretch (as long as the rules reach) repeat, translated, the
choices repeat forever, possibly with an ε part in the period (1+ε). On the left of
an element unbounded on the left there is no first position: `Undetermined`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from intervals import EPS, INF, Interval, IntervalSet, Pos
from region import ZERO, Region, _constant
from rules import _fgcd
from solver import EMPTY, Conflict, Undetermined, _values, kinds_of, lhs_region, union

MAX_EVENTS = 3000
MAX_CALLS = 3000


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


class Sweep:
    def __init__(self, rs, prob, vals, fixed, forbid, kinds_in) -> None:
        self.rs = rs
        self.vals0 = dict(vals)
        self.fixed = fixed
        self.forbid0 = dict(forbid)
        self.claims = {k: prob.pos.get(k, {}) for k in kinds_in}
        self.kin = [k for k in kinds_of(rs, prob) if k in kinds_in]
        self.items = [_Item(i, rs.element[i].lhs, rs.element[i].rhs[j].options[0].anchor, rs.element[i].rhs[j].options,
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
        self.op_pos = {k: [] for k in self.kin}
        self.op_neg = []
        for ii, item in enumerate(self.items):
            for oi, o in enumerate(item.ops):
                if o.negated:
                    self.op_neg.append((ii, oi, o))
                else:
                    self.op_pos[o.kind].append((ii, oi, o))
        self.none = [[EMPTY] * len(it.ops) for it in self.items]
        self.deltas = self._deltas()
        self.R = max((abs(d.x) for d in self.deltas), default=Fraction(0)) + 1
        statics = list(self.vals0.values()) + [{0: f} for f in self.forbid0.values()] + list(self.claims.values())
        statics += [{0: p} for p, _ in self.fixed.values()] + [e for _, e in self.fixed.values()]
        self.static_regions = [reg for d in statics for reg in d.values() if reg]
        for it in self.items:
            if not set(it.lhs.kinds()) & kinds_in:
                self.static_regions.append(lhs_region(it.lhs, self.vals0))
        self.periodic_ok = all(_flat_tails(reg) for reg in self.static_regions)
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
        vals = dict(self.vals0)
        anchors: dict[int, Region] = {}
        cut = None if horizon == INF else (EMPTY if horizon == -INF else _upto(horizon))

        def base(i, o):
            if i not in anchors:
                anchors[i] = lhs_region(self.rs.element[i].lhs, vals)
            a = anchors[i]
            if o.anchor == "p":
                return a
            e = a.left_edges()
            return e if cut is None else e & cut

        for K in self.kin:
            plain, exact = self.fixed.get(K, (EMPTY, {}))
            exact = dict(exact)
            for i, o in self.pos_lits[K]:
                t = _target(o, base(i, o))
                if o.value is None:
                    plain = plain | t
                else:
                    exact[o.value] = exact.get(o.value, EMPTY) | t
            for ii, oi, o in self.op_pos[K]:
                b = ch[ii][oi]
                if b:
                    t = _target(o, b)
                    if o.value is None:
                        plain = plain | t
                    else:
                        exact[o.value] = exact.get(o.value, EMPTY) | t
            vals[K] = _values(K, self.claims[K], plain, exact, check and not forbids)
        if not check and not forbids:
            return vals
        forbid = dict(self.forbid0)
        for i, o in self.neg_lits:
            forbid[o.kind] = forbid.get(o.kind, EMPTY) | _target(o, base(i, o))
        for ii, oi, o in self.op_neg:
            if ch[ii][oi]:
                forbid[o.kind] = forbid.get(o.kind, EMPTY) | _target(o, ch[ii][oi])
        if forbids:
            return vals, forbid
        for k, f in forbid.items():
            bad = union(vals.get(k, {}).values()) & f
            if bad:
                raise Conflict(f"{k} is required to be absent on {bad}")
        return vals

    def _valid(self, ch, horizon) -> bool:
        try:
            self.closure(ch, horizon)
            return True
        except Conflict:
            return False

    # ------------------------------------------------------------------ the sweep
    def solve(self) -> dict:
        P = self._next_active(self.none, None)
        if P == -INF:
            raise Undetermined("an 'or' acts on an element unbounded on the left: with no first position, nothing decides "
                               "its choices")
        self.memo, self.failed, self.dead, self.calls, self.stuck = {}, {}, [], 0, -INF
        self._grid()
        if P is None:  # no "or" is met anywhere
            res = self.none if self._valid(self.none, INF) else None
        else:
            res = self.complete(P, self.none, 0)
        if res is None:
            raise Conflict("no choice of the 'or' operands satisfies the rules")
        return self._finish(res)

    def _greedy(self, P, ch, start=0):
        """From P (the "or"s before `start` at P already decided): each position takes the
        leftmost operand that does not contradict the rules with what is on its left.
        ("ok", choices) if that never fails, else ("dead", position, "or") where nothing
        could be chosen (position None: the rules fail once everything is decided)."""
        seen: dict = {}
        events = 0
        while P is not None:
            events += 1
            if events > MAX_EVENTS:
                raise Undetermined(f"the choices change more than {MAX_EVENTS} times without repeating")
            if start == 0 and self.periodic_ok and P.x > self.T:
                sig = self._signature(ch, P)
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
        still lets all the other positions be placed. None if no completion exists.

        The greedy sweep is tried first: if it never fails, it is the answer. Otherwise
        the positions from P are decided one block at a time (`_unit`), each operand
        being kept only if the rest can be completed (recursively, greedy first)."""
        self.calls += 1
        if self.calls > MAX_CALLS:
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
                return None  # with no less placed and forbidden, it fails too
        # the greedy sweep only from a position with nothing decided after it, and not
        # again before the position where it last failed (it would fail there again)
        r = ("dead", None, None)
        if P > self.stuck and not any(reg.sup() > P for row in ch for reg in row):
            r = self._greedy(P, ch, start)
        if r[0] == "ok":
            res = r[1]
        else:
            if r[1] is not None:
                self.stuck = r[1]
                if r[2] is not None and r[2] >= 0:
                    self._learn(r[1], r[2])
            res = self._careful(P, ch, start, self._unit(P, ks) if U is None else U)
        self.memo[key] = res
        if res is None:
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
        """Decide the block [P, U] that starts at P, operand by operand, in order."""
        try:
            vals = self.closure(ch, P)
        except Conflict:
            return None
        for ii in range(start, len(self.items)):
            it = self.items[ii]
            if not self._active(it, vals, P):
                continue
            if it.anchor == "l" and U != P:
                raise Undetermined(f"the sweep could not place exactly where a choice changes after {P}")
            where = Region.of(Interval(P, U))
            for oi in range(len(it.ops)):
                c = _with(ch, ii, oi, where)
                if not self._valid(c, U) or self._doomed(c, U):
                    continue
                r = self.complete(P, c, ii + 1, U)
                if r is not None:
                    return r
            return None
        # every "or" met on the block is decided: the next block
        nxt = self._next_active(ch, U) if not self._runs_at(P, ch) else U + EPS
        if nxt is None:
            return ch if self._valid(ch, INF) else None
        return self.complete(nxt, ch, 0)

    def _grid(self) -> None:
        """Where a choice can change: real parts r + n·g (r the real parts of the ends of
        the claims and of the fixed regions modulo g, g the gcd of the distances of the
        rules on chosen kinds), and around the ε parts of the ends seen."""
        ds = [abs(e.x) for k in self.kin for _, _, o in self.op_pos[k] for e in (o.where.lo, o.where.hi)]
        ds += [abs(e.x) for _, _, o in self.op_neg for e in (o.where.lo, o.where.hi)]
        ds += [abs(e.x) for lits in self.pos_lits.values() for _, o in lits for e in (o.where.lo, o.where.hi)]
        ds += [abs(e.x) for _, o in self.neg_lits for e in (o.where.lo, o.where.hi)]
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
        position, so every end that matters has one of these, within ±2."""
        a, b = P - 2 * self.R, P + 2 * self.R
        ends = [e for e in self.static_ends if a <= e <= b]
        for row in ch:
            for reg in row:
                for iv in reg.on(a - 1, P).intervals:
                    ends += [e for e in (iv.lo, iv.hi) if a <= e < P - EPS]
        return tuple(sorted({k for e in ends for k in range(e.k - 2, e.k + 3)} | {-2, -1, 0, 1, 2}))

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

    def _doomed(self, ch, h) -> bool:
        """A position where nothing could be chosen before, still with nothing whatever
        is decided after h (the choices in ch are up to h): no completion exists."""
        for d, ii in self.dead:
            if d > h and self._blocks(ch, d, ii, h):
                return True
        return False

    def _blocks(self, ch, d, ii, h) -> bool:
        it = self.items[ii]
        vals = self.closure(ch, h, check=False)
        a = lhs_region(it.lhs, vals)
        if it.anchor == "p":
            if not a.contains(d):  # met at d whatever comes next (a left side only grows)
                return False
        elif set(it.lhs.kinds()) & set(self.kin) or not (a.contains(d) and not a.contains(d - EPS)):
            return False
        return not any(self._valid(_with(ch, ii, oi, Region.point(d)), h) for oi in range(len(it.ops)))

    def _next_active(self, ch, after):
        """The first position (after `after`) where an "or" is met, or None."""
        vals = self.closure(ch, INF, check=False)
        best = None
        for it in self.items:
            a = lhs_region(it.lhs, vals)
            if it.anchor == "l":
                a = a.left_edges()
            if after is not None:
                a = a & _after(after)
            if a:
                m = a.inf()
                if best is None or m < best:
                    best = m
        return best

    def _active(self, it, vals, m) -> bool:
        a = lhs_region(it.lhs, vals)
        return a.contains(m) and (it.anchor == "p" or not a.contains(m - EPS))

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
            a = lhs_region(it.lhs, vals)
            if it.anchor == "l":
                change = (a.left_edges() & _after(P)).inf()
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
        """Offsets from a decided position to the ends of the regions it changes."""
        off = {k: set() for k in self.kin}
        out = {ZERO}
        for ii, oi, o in [(a, b, c) for k in self.kin for a, b, c in self.op_pos[k]]:
            off[o.kind] |= {o.where.lo, o.where.hi}
        for _, _, o in self.op_neg:
            out |= {o.where.lo, o.where.hi}
        for K in self.kin:
            for i, r in enumerate(self.rs.element):
                if K not in r.lhs.kinds():
                    continue
                for it in r.rhs:
                    if it.is_or:
                        continue
                    o = it.options[0]
                    new = {d + e for d in off[K] for e in (o.where.lo, o.where.hi)}
                    if o.negated or o.kind not in off:
                        out |= new
                    else:
                        off[o.kind] |= new
        for s in off.values():
            out |= s
        # how far a choice acts on the right of it, and on the left
        self.fwd = max([d.x for d in out] + [Fraction(0)])
        self.back = max([-d.x for d in out] + [Fraction(0)])
        return {a - b for a in out for b in out}

    def _candidates(self, lo, hi, vals, ch) -> list:
        """The positions in (lo, hi) where the decision may change."""
        a, b = lo - self.R, hi + self.R if hi != INF else lo + 70 * self.R
        ends = {lo} | {e for e in self.static_ends if a <= e <= b}
        for d in vals.values():
            for reg in d.values():
                ends |= {e for e in _ends(reg, a, b) if type(e) is Pos}
        for row in ch:
            for reg in row:
                ends |= {e for e in _ends(reg, a, b) if type(e) is Pos}
        out = set()
        for e in ends:
            for d in self.deltas:
                x = e + d
                for j in (-2, -1, 0, 1, 2):
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


def _flat_tails(reg: Region) -> bool:
    return _constant(reg.left, reg.pl) and _constant(reg.right, reg.pr)
