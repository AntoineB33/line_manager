"""The line computed from its claims: what the element rules make of the elements placed.

Given claims (elements placed, with percentages; "no K" elements) the line is:
- every claimed element;
- closed under the rules: where a left side is met, its literals hold; for an
  ordered "or", an operand is chosen; and nothing more: it is the least such state.
  The rules have no cycle (rules.py refuses one), so each kind follows from the kinds
  its rules read, computed before it;
- the operands are chosen position by position, from left to right (at one position,
  an "or" whose left side reads what another one places comes after it, else the
  rules in written order): each position takes the leftmost operand that can be
  chosen, i.e. the first one that still lets all the other positions be placed, given
  the choices on its left. This is the lexicographically smallest feasible choice in
  that order, with the positions closest to 0 first on the left of 0
  (requirement.md), which also decides an element unbounded on the left.
If no choice satisfies the rules (an element where it must not be, two different
percentages required, ...), the claims are in conflict (`Conflict`).

Positions are x + kε (intervals.py). How it is computed exactly. Kinds that no "or"
choice can change are computed with region algebra, kind after kind (`close`), one step
each: the rules have no cycle. For the others, two solvers:
- the lattices (here), when the rules that read chosen kinds only place single
  positions relative to p. Every relative offset is a multiple of g (their gcd), and
  keeps the ε part, so a rule only relates the points of one lattice r + gZ. The
  residues r in [0, g) are cut at 0, g/2 and every endpoint of every region involved:
  on each piece, all lattices pose the same problem, solved once on a representative.
  On a lattice, the points with |x| <= T (beyond T nothing changes any more) are
  decided by an exact depth-first search in the order above. Beyond T, on each side,
  the problem is translation-invariant: a choice only interacts with the W previous
  points, so the choices form a finite automaton; a state is "good" if an infinite
  path leaves it, and the search accepts a decision of the |x| <= T part only if both
  boundary states are good. The choices on the right ray are then the greedy walk
  through good states, which repeats with a finite period: an infinite element gives
  a result that repeats. On the left ray, the same walk goes leftwards, on the mirrored
  rules (the closest to 0 chooses first);
- the sweep (sweep.py) otherwise: intervals and left edges make every position act
  on the positions near it.
"""

from __future__ import annotations

import bisect
import itertools
import math
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from fractions import Fraction

from intervals import EPS, INF, Interval, IntervalSet, Pos
from region import ZERO, IncompatiblePeriods, Region, flcm
from rules import Atom, RuleSet, _fgcd, is_hidden, lattice_step

sys.setrecursionlimit(20000)


class Conflict(Exception):
    pass


class Undetermined(Exception):
    """This solver does not decide the "or" choices here (the sweep, on an element unbounded
    on the left whose choices act on each other), or deciding them would take the sweep's
    search longer than it is allowed."""


class Interrupted(Exception):
    """The computation was stopped from outside (STOP, set by another thread)."""


STOP = threading.Event()


_limit = threading.local()  # (this thread's: the window's reads are not limited)


@contextmanager
def limited(seconds):
    """What is computed inside gives up (Undetermined) after this time, None: no limit. Inside
    another limit, the earlier of the two holds, and the outer one is back after it."""
    outer = getattr(_limit, "at", None)
    mine = None if seconds is None else time.monotonic() + seconds
    _limit.at = mine if outer is None else outer if mine is None else min(outer, mine)
    try:
        yield
    finally:
        _limit.at = outer


def checkpoint() -> None:
    """Raise Interrupted if STOP is set, Undetermined past the limit: called in the long loops."""
    if STOP.is_set():
        raise Interrupted("stopped")
    at = getattr(_limit, "at", None)
    if at is not None and time.monotonic() > at:
        raise Undetermined("it takes too long to compute")


EMPTY = Region.empty()


def union(regions) -> Region:
    out = EMPTY
    for r in regions:
        out = out | r
    return out


class Line:
    """The line: for each kind, value (percentage) -> region, pairwise disjoint."""

    def __init__(self, vals: dict[str, dict[Fraction, Region]]):
        self.vals = {k: {v: r for v, r in d.items() if r} for k, d in vals.items()}
        self.vals = {k: d for k, d in self.vals.items() if d}

    def kinds(self) -> list[str]:
        """The kinds on the line (not the hidden ones, rules.HIDDEN)."""
        return sorted(k for k in self.vals if not is_hidden(k))

    def presence(self, kind: str) -> Region:
        return union(self.vals.get(kind, {}).values())

    def value_at(self, kind: str, x) -> Fraction:
        for v, r in self.vals.get(kind, {}).items():
            if r.contains(x):
                return v
        return Fraction(0)

    def integral(self, kind: str, window: IntervalSet) -> Fraction:
        return sum((v / 100 * r.measure_on(window) for v, r in self.vals.get(kind, {}).items()), Fraction(0))

    def same_on(self, other: Line, region: Region) -> bool:
        for k in set(self.vals) | set(other.vals):
            vs = set(self.vals.get(k, {})) | set(other.vals.get(k, {}))
            for v in vs:
                a = self.vals.get(k, {}).get(v, EMPTY) & region
                b = other.vals.get(k, {}).get(v, EMPTY) & region
                if a != b:
                    return False
        return True

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Line) and self.same_on(other, Region.universe())

    def __str__(self) -> str:
        lines = [f"{k} {v}% on {r}" for k in self.kinds() for v, r in sorted(self.vals[k].items())]
        return "\n".join(lines) if lines else "(empty line)"


@dataclass
class Problem:
    """Inputs of a computation of the line."""
    pos: dict[str, dict[Fraction, Region]]  # claims: kind -> value -> region
    neg: dict[str, Region] = field(default_factory=dict)  # kind must be absent there


# ====================================================================== region stage


def lhs_region(f, vals: dict[str, dict[Fraction, Region]]) -> Region:
    """Where a left side (rules.py formula) is met, given the values of its kinds."""
    return f.region(vals, Region.universe(), EMPTY)


def target(lit, anchors: Region) -> Region:
    """Where a literal applies when its rule's left side is met on `anchors`."""
    if unbound_reach(lit, anchors):
        return Region.universe()
    base = anchors if lit.anchor == "p" else anchors.left_edges()
    return base.shift(lit.rel) if lit.is_point else base.dilate(lit.where)


def unbound_reach(lit, anchors: Region) -> bool:
    """A literal relative to l that reaches +inf, on an interval of `anchors` with no
    left end: l is -inf there (unbound), and l + [a, +inf) is the whole line. (With a
    finite end instead, l + [a, b] is at -inf: not on the line, nothing.)"""
    return lit.anchor == "l" and lit.where.hi == INF and anchors.inf() == -INF


def kinds_of(rs: RuleSet, prob: Problem) -> list[str]:
    extra = sorted((set(prob.pos) | set(prob.neg)) - set(rs.order))
    return list(rs.order) + extra


def region_stage(rs: RuleSet, prob: Problem, kinds_in: set[str], check: bool = True):
    """Kinds outside kinds_in are computed here, group after group (`close`). Returns
    (values of out kinds, fixed contributions to in kinds, forbidden regions of in
    kinds)."""
    static = [i for i, r in enumerate(rs.element) if not set(r.lhs.kinds()) & kinds_in]
    lits = [(i, it.options[0]) for i in static for it in rs.element[i].rhs if not it.is_or and not it.options[0].negated]
    out = [k for k in kinds_of(rs, prob) if k not in kinds_in]
    vals = close(rs, out, [(i, o) for i, o in lits if o.kind not in kinds_in], prob.pos, {}, {}, check)
    anchors: dict[int, Region] = {}

    def met(i) -> Region:
        if i not in anchors:
            anchors[i] = lhs_region(rs.element[i].lhs, vals)
        return anchors[i]

    fixed: dict[str, tuple[Region, dict[Fraction, Region]]] = {k: (EMPTY, {}) for k in kinds_of(rs, prob) if k in kinds_in}
    for i, o in lits:
        if o.kind in kinds_in:
            plain, exact = fixed[o.kind]
            t = target(o, met(i))
            if o.value is None:
                fixed[o.kind] = (plain | t, exact)
            else:
                exact[o.value] = exact.get(o.value, EMPTY) | t
    pres = {k: union(d.values()) for k, d in vals.items()}
    forbid = {k: r for k, r in prob.neg.items()}
    for i in static:
        for it in rs.element[i].rhs:
            o = it.options[0]
            if not it.is_or and o.negated:
                forbid[o.kind] = forbid.get(o.kind, EMPTY) | target(o, met(i))
    for k, f in forbid.items():
        if check and k not in kinds_in and (pres.get(k, EMPTY) & f):
            raise Conflict(f"{k} is required to be absent on {pres[k] & f}")
    return vals, fixed, {k: f for k, f in forbid.items() if k in kinds_in}


def _values(K, claims: dict[Fraction, Region], plain: Region, exact: dict[Fraction, Region], check: bool = True) -> dict[Fraction, Region]:
    claimed = union(claims.values())
    out = dict(claims)
    exs = sorted(exact)
    for i, p in enumerate(exs):
        for v, c in claims.items():
            if check and v != p and (exact[p] & c):
                raise Conflict(f"{K} is required at {p}% where it is {v}%: {exact[p] & c}")
        for q in exs[i + 1:]:
            if check and exact[p] & exact[q]:
                raise Conflict(f"{K} is required at {p}% and {q}% on {exact[p] & exact[q]}")
        out[p] = out.get(p, EMPTY) | (exact[p] - claimed)
    rest = plain - claimed - union(exact.values())
    out[Fraction(100)] = out.get(Fraction(100), EMPTY) | rest
    return out


# ====================================================================== the kinds in order

def close(rs: RuleSet, kinds: list[str], lits, claims: dict, base: dict, extra: dict, check: bool = True,
          mask: Region | None = None, met: dict | None = None) -> dict[str, dict[Fraction, Region]]:
    """The values of `kinds` (closed under the rules' groups: rs.blocks): the least state
    that contains their claims and their `extra` contributions (kind -> (plain, exact)),
    and where each literal of `lits` ((element rule index, positive literal placing one of
    these kinds)) holds wherever its left side is met. Other kinds are read from `base`.
    The left edges of the anchors relative to l are kept within `mask`, if given. `met`,
    if given, receives where the left sides are met (rule index -> region).

    The rules have no cycle: each kind only reads kinds computed before it, one step.
    Raises Conflict (if check)."""
    vals = dict(base)
    pres = _Presence(vals)
    by_kind: dict[str, list] = {}
    for i, o in lits:
        by_kind.setdefault(o.kind, []).append((i, o))
    want = set(kinds)
    blocks = [b for b in rs.blocks if b[0] in want] + [(k,) for k in kinds if k not in rs.block_of]
    for block in blocks:
        g = _Group(rs, block, [x for K in block for x in by_kind.get(K, ())], claims, vals, pres, extra, mask,
                   met if met is not None else {})
        state = g.solve()
        for K in block:
            plain, exact = state[K]
            vals[K] = _values(K, claims.get(K, {}), plain, exact, check)
    return {k: vals[k] for k in kinds}


class _Presence(dict):
    """kind -> where it is present, computed from the values when first read."""

    def __init__(self, vals: dict) -> None:
        super().__init__()
        self.vals = vals

    def get(self, k, default=None):
        if not dict.__contains__(self, k):
            if k not in self.vals:
                return default
            self[k] = union(self.vals[k].values())
        return dict.get(self, k)


class _Group:
    """One kind (the rules have no cycle). A state is kind -> (plain, exact): where the
    rules (and `extra`) require it present, and at exact percentages."""

    def __init__(self, rs, block, lits, claims, vals, pres, extra, mask, met) -> None:
        self.rs, self.block, self.lits, self.vals, self.pres, self.extra, self.mask = rs, block, lits, vals, pres, extra, mask
        self.met = met
        self.claims = {K: claims.get(K, {}) for K in block}

    def empty(self) -> dict:
        return {K: (EMPTY, {}) for K in self.block}

    def solve(self) -> dict:
        checkpoint()
        return self.step(self.empty(), self.empty())

    def step(self, a: dict, b: dict) -> dict:
        """The state the rules require from the state a, absences read from b."""
        U = Region.universe()
        pos, neg = (self.vals, self.pres), (self.vals, self.pres)
        out = {K: (self.extra.get(K, (EMPTY, {}))[0], dict(self.extra.get(K, (EMPTY, {}))[1])) for K in self.block}
        met: dict[int, Region] = self.met
        for i, o in self.lits:
            lhs = self.rs.element[i].lhs
            if i not in met:
                met[i] = lhs.region2(pos, neg, U, EMPTY)
            m = met[i]
            if o.anchor == "l" and not unbound_reach(o, m):
                edges = m.left_edges()
                if self.mask is not None:
                    edges = edges & self.mask
                t = edges.shift(o.rel) if o.is_point else edges.dilate(o.where)
            else:
                t = target(o, m)
            plain, exact = out[o.kind]
            if o.value is None:
                out[o.kind] = (plain | t, exact)
            else:
                exact[o.value] = exact.get(o.value, EMPTY) | t
        return out


# ====================================================================== lattice stage


@dataclass(frozen=True)
class KP:
    """What is fixed about one kind at one point."""
    claim: Fraction | None = None  # claimed percentage
    fixed: bool = False  # present whatever the choices
    exacts: frozenset = frozenset()  # percentages required whatever the choices
    forbidden: bool = False


class Spec:
    """The rules acting on one lattice, offsets in lattice steps. lrules: (names whose
    presence makes the left side, item) for the "or" items and the items of the rules
    that read chosen kinds. A name "!K" is K read as absent ("not K"); in a left side it
    is ~index. With such names the choices no longer only add consequences: atoms are
    then evaluated with three values (`_evaluate3`)."""

    def __init__(self, kinds: list[str], kinds_in: set[str], g: Fraction, lrules, mirror=False):
        sgn = -1 if mirror else 1
        self.kinds = kinds
        self.idx = {k: i for i, k in enumerate(kinds)}
        self.in_order = [k for k in kinds if k in kinds_in]
        self.defs, self.negs, self.ors, self.or_ranks = [], [], [], []
        self.has_neg = any(k.startswith("!") for names, _, _ in lrules for k in names)
        for names, it, rank in lrules:
            lhs = tuple(~self.idx[k[1:]] if k.startswith("!") else self.idx[k] for k in names)
            if it.is_or:
                self.ors.append((lhs, tuple((self.idx[o.kind], sgn * int(o.rel.x / g), o.value) for o in it.options)))
                self.or_ranks.append(rank)
            else:
                o = it.options[0]
                d = sgn * int(o.rel.x / g)
                (self.negs if o.negated else self.defs).append((lhs, self.idx[o.kind], d, o.value) if not o.negated else (lhs, self.idx[o.kind], d))
        # dist[K]: how far (in steps) an atom of K can be from the choices it depends on
        dist = [0] * len(kinds)
        far = lambda lhs: max((dist[l if l >= 0 else ~l] for l in lhs), default=0)
        for k in self.in_order:
            ki = self.idx[k]
            for lhs, kk, d, _ in self.defs:
                if kk == ki:
                    dist[ki] = max(dist[ki], far(lhs) + abs(d))
            for lhs, os in self.ors:
                for kk, d, _ in os:
                    if kk == ki:
                        dist[ki] = max(dist[ki], far(lhs) + abs(d))
        D = max(dist, default=0)
        # rho: the choices involved in one violation lie within rho + 1 consecutive points
        spans = [2 * D] + [far(lhs) + abs(d) + dist[kk] for lhs, kk, d in self.negs]
        self.rho = max(spans)
        self.n_opts = [len(os) for _, os in self.ors]
        self.by_kind_defs = {i: [x for x in self.defs if x[1] == i] for i in range(len(kinds))}
        self.by_kind_ors = {i: [(oi, t, d, v) for oi, (_, os) in enumerate(self.ors) for t, (kk, d, v) in enumerate(os) if kk == i] for i in range(len(kinds))}
        self.in_idx = [self.idx[k] for k in self.in_order]
        self.independent = self._independent()

    def _independent(self) -> bool:
        """No violation can involve the choices of two different positions. A production
        of an atom is (the offsets, in steps back from the atom, of the choices it uses,
        its value); an atom may also be present by itself (no choice). With a kind read
        as absent, a choice can also remove consequences: not shown independent."""
        if self.has_neg:
            return False
        inset = set(self.in_idx)
        none = [(frozenset(), None)]
        prods = {ki: set(none) for ki in self.in_idx}
        for ki in self.in_idx:
            out = set(none)
            for lhs, kk, d, v in self.defs:
                if kk == ki:
                    for combo in itertools.product(*[prods[l] if l in inset else none for l in lhs]):
                        out.add((frozenset(d + t for pat, _ in combo for t in pat), v))
            for lhs, os in self.ors:
                for kk, d, v in os:
                    if kk == ki:
                        for combo in itertools.product(*[prods[l] if l in inset else none for l in lhs]):
                            out.add((frozenset([d]) | frozenset(d + t for pat, _ in combo for t in pat), v))
            if len(out) > 64:
                return False
            prods[ki] = out
        for ps in prods.values():
            if any(len(pat) > 1 for pat, _ in ps):
                return False
            vals = [(pat, v) for pat, v in ps if v is not None]
            if any(v1 != v2 and len(p1 | p2) > 1 for p1, v1 in vals for p2, v2 in vals):
                return False
        for lhs, ki, d in self.negs:
            for combo in itertools.product(*[prods[l] if l in inset else none for l in lhs]):
                pat = frozenset(t for p, _ in combo for t in p)
                for q, _ in (prods[ki] if ki in inset else none):
                    if len(pat | frozenset(t - d for t in q)) > 1:
                        return False
        return True

    def evaluate(self, pat, choose, a: int, b: int, ca: int, cb: int, optimistic=False):
        """Atoms on points a..b (choices given by `choose`, missing ones contribute
        nothing) and whether a violation shows on ca..cb. Monotone: atoms only grow
        with more choices, so a violation found is a violation of every completion.
        With a kind read as absent: `_evaluate3`."""
        if self.has_neg:
            return self._evaluate3(pat, (lambda k, oi: None) if optimistic else choose, a, b, ca, cb)
        n = b - a + 1
        nk = len(self.kinds)
        P = [pat(a + i) for i in range(n)]
        present = [[kp.fixed or kp.claim is not None for kp in p] for p in P]
        exs: dict[tuple[int, int], set] = {}
        for ki in self.in_idx:
            defs = self.by_kind_defs[ki]
            ors = self.by_kind_ors[ki]
            for i in range(n):
                kp = P[i][ki]
                pr = kp.fixed or kp.claim is not None
                ex = set(kp.exacts)
                for lhs, _, d, v in defs:
                    j = i - d
                    if 0 <= j < n and all(present[j][l] for l in lhs):
                        pr = True
                        if v is not None:
                            ex.add(v)
                for oi, t, d, v in ors:
                    j = i - d
                    if 0 <= j < n and all(present[j][l] for l in self.ors[oi][0]) and (optimistic or choose(a + j, oi) == t):
                        pr = True
                        if v is not None:
                            ex.add(v)
                present[i][ki] = pr
                if ex:
                    exs[(i, ki)] = ex
        bad = False
        for i in range(max(0, ca - a), min(n, cb - a + 1)):
            p, row = P[i], present[i]
            for ki in self.in_idx:
                if row[ki]:
                    kp = p[ki]
                    if kp.forbidden:
                        bad = True
                        break
                    ex = exs.get((i, ki))
                    if ex and (len(ex) > 1 or (kp.claim is not None and kp.claim not in ex)):
                        bad = True
                        break
            if bad:
                break
            for lhs, ki, d in self.negs:
                t = i + d
                if 0 <= t < n and present[t][ki] and all(row[l] for l in lhs):
                    bad = True
                    break
            if bad:
                break
        return present, exs, P, bad

    @staticmethod
    def lhs_value(row, lhs):
        """A left side on one point, with three values: True, False, None (unknown)."""
        out = True
        for l in lhs:
            v = row[l] if l >= 0 else (None if row[~l] is None else not row[~l])
            if v is False:
                return False
            if v is None:
                out = None
        return out

    def _evaluate3(self, pat, choose, a: int, b: int, ca: int, cb: int):
        """`evaluate` when a left side reads a chosen kind as absent: then a choice can
        remove a consequence as well as add one, and a missing choice is unknown, not
        absent. An atom is True, False or None (unknown): unknown when it depends on a
        choice not given, or on an anchor out of a..b. Only definite violations count, so
        a violation found is a violation of every completion. The atoms of kinds read
        only as present are as `evaluate` makes them when every choice is given."""
        n = b - a + 1
        P = [pat(a + i) for i in range(n)]
        present = [[kp.fixed or kp.claim is not None for kp in p] for p in P]
        exs: dict[tuple[int, int], set] = {}
        for ki in self.in_idx:
            defs = self.by_kind_defs[ki]
            ors = self.by_kind_ors[ki]
            for i in range(n):
                kp = P[i][ki]
                pr = kp.fixed or kp.claim is not None
                unknown = False
                ex = set(kp.exacts)
                for lhs, _, d, v in defs:
                    j = i - d
                    c = self.lhs_value(present[j], lhs) if 0 <= j < n else None
                    if c:
                        pr = True
                        if v is not None:
                            ex.add(v)
                    elif c is None:
                        unknown = True
                for oi, t, d, v in ors:
                    j = i - d
                    c = self.lhs_value(present[j], self.ors[oi][0]) if 0 <= j < n else None
                    if c is False:
                        continue
                    took = choose(a + j, oi) if 0 <= j < n else None
                    if c and took == t:
                        pr = True
                        if v is not None:
                            ex.add(v)
                    elif took is None or took == t:
                        unknown = True
                present[i][ki] = True if pr else (None if unknown else False)
                if ex:
                    exs[(i, ki)] = ex
        bad = False
        for i in range(max(0, ca - a), min(n, cb - a + 1)):
            p, row = P[i], present[i]
            for ki in self.in_idx:
                if row[ki]:
                    kp = p[ki]
                    ex = exs.get((i, ki))
                    if kp.forbidden or (ex and (len(ex) > 1 or (kp.claim is not None and kp.claim not in ex))):
                        bad = True
                        break
            if bad:
                break
            for lhs, ki, d in self.negs:
                t = i + d
                if 0 <= t < n and present[t][ki] is True and self.lhs_value(row, lhs) is True:
                    bad = True
                    break
            if bad:
                break
        return present, exs, P, bad

    def met(self, row, lhs) -> bool:
        """Some choice could meet this left side on this point (`evaluate` optimistic)."""
        return self.lhs_value(row, lhs) is not False if self.has_neg else all(row[l] for l in lhs)

    def value(self, P, present, exs, i, ki) -> Fraction | None:
        if present[i][ki] is None:
            raise Undetermined("an atom of the lattice is not decided where its value is read")
        if not present[i][ki]:
            return None
        kp = P[i][ki]
        if kp.claim is not None:
            return kp.claim
        ex = exs.get((i, ki))
        return next(iter(ex)) if ex else Fraction(100)


class Ray:
    """The translation-invariant part of a lattice beyond its decided core."""

    def __init__(self, spec: Spec, pattern, vars_: tuple[int, ...], W: int):
        self.spec, self.pattern, self.vars, self.W = spec, pattern, vars_, W
        self.options = list(itertools.product(*[range(spec.n_opts[oi]) for oi in vars_]))
        self._good: dict = {}
        self._valid: dict = {}

    def valid(self, state, v) -> bool:
        key = (state, v)
        if key not in self._valid:
            seq = state + (v,)
            pos = {oi: j for j, oi in enumerate(self.vars)}

            def choose(k, oi):
                vec = seq[k] if 0 <= k < len(seq) else None
                return vec[pos[oi]] if vec is not None and oi in pos else None

            _, _, _, bad = self.spec.evaluate(lambda k: self.pattern, choose, 0, self.W, 0, self.W)
            self._valid[key] = not bad
        return self._valid[key]

    def successors(self, state):
        for v in self.options:
            if self.valid(state, v):
                yield v, state[1:] + (v,)

    def good(self, state) -> bool:
        """Some infinite sequence of valid choices leaves this state."""
        if state in self._good:
            return self._good[state]
        seen, stack, succ = {state}, [state], {}
        while stack:
            s = stack.pop()
            succ[s] = [t for _, t in self.successors(s)]
            for t in succ[s]:
                if t not in seen and t not in self._good:
                    seen.add(t)
                    stack.append(t)
        alive = set(seen)
        changed = True
        while changed:
            changed = False
            for s in list(alive):
                if not any((t in alive) if t in seen else self._good[t] for t in succ[s]):
                    alive.discard(s)
                    changed = True
        for s in seen:
            self._good[s] = s in alive
        return self._good[state]

    def walk(self, state):
        """Greedy choices from a good state: (prefix, cycle) of choice vectors."""
        seen: dict = {}
        seq = []
        while state not in seen:
            seen[state] = len(seq)
            for v, t in self.successors(state):
                if self.good(t):
                    seq.append(v)
                    state = t
                    break
            else:
                raise AssertionError("a good state has a good successor")
        start = seen[state]
        return seq[:start], seq[start:]


class Lattice:
    """One lattice r + gZ of a piece of residues."""

    def __init__(self, lp: LatticeProblem, r: Fraction):
        self.lp, self.r = lp, r
        g = lp.g
        self.kmin = -int((r - lp.lo) // g)
        self.kmax = int((lp.hi - r) // g)
        self.R2 = 3 * lp.spec.rho + 1
        self._pats = lp.patterns(r, self.kmin - 1, self.kmax + 1)

    def x(self, k: int) -> Fraction:
        return self.r + k * self.lp.g

    def pat(self, k: int) -> tuple:
        return self._pats[min(max(k, self.kmin - 1), self.kmax + 1) - self.kmin + 1]  # constant beyond the core

    def solve(self) -> bool:
        spec = self.lp.spec
        R2 = self.R2
        lo, hi = self.kmin - 2 * R2, self.kmax + 2 * R2
        present, _, _, bad = spec.evaluate(self.pat, lambda k, oi: None, lo, hi, lo, hi, optimistic=True)
        # variables: anchors that some choice could meet
        self.vars = []
        for i in range(hi - lo + 1):
            k = lo + i
            if self.kmin <= k <= self.kmax:
                for oi, (lhs, _) in enumerate(spec.ors):
                    if spec.met(present[i], lhs):
                        self.vars.append((k, oi))
        mid_r, mid_l = self.kmax + R2, self.kmin - R2
        self.ray_vars = (tuple(oi for oi, (lhs, _) in enumerate(spec.ors) if spec.met(present[mid_l - lo], lhs)),
                         tuple(oi for oi, (lhs, _) in enumerate(spec.ors) if spec.met(present[mid_r - lo], lhs)))
        self.vars.sort(key=self._turn)  # the closest to 0 first, the one on the right if two are as close
        _, _, _, bad = spec.evaluate(self.pat, lambda k, oi: None, lo, hi, lo, hi)
        if bad:
            return False
        W = self.lp.W
        self.rays = (Ray(self.lp.mirror_spec, self.pat(self.kmin - 1), self.ray_vars[0], W),
                     Ray(spec, self.pat(self.kmax + 1), self.ray_vars[1], W))
        self.assign: dict[tuple[int, int], int] = {}
        if not self._dfs():
            return False
        self.walks = (self.rays[0].walk(self._boundary(0)), self.rays[1].walk(self._boundary(1)))
        return True

    def _boundary(self, side: int):
        W = self.lp.W
        ks = range(self.kmax - W + 1, self.kmax + 1) if side else range(self.kmin + W - 1, self.kmin - 1, -1)
        return tuple(tuple(self.assign.get((k, oi), 0) for oi in self.ray_vars[side]) for k in ks)

    def _turn(self, v: tuple) -> tuple:
        x = self.x(v[0])
        return (-x if x < 0 else x), x < 0, self.lp.spec.or_ranks[v[1]]

    def _dfs(self) -> bool:
        """The first choices that work, in the order of `vars`. With its own stack: a line
        can have more choices than Python can have nested calls."""
        spec, R2 = self.lp.spec, self.R2
        n = len(self.vars)
        tried = [0] * (n + 1)  # at each depth, the operands already tried
        i = 0
        while i >= 0:
            if i == n:
                if self.rays[0].good(self._boundary(0)) and self.rays[1].good(self._boundary(1)):
                    return True
                i -= 1
                continue
            k, oi = self.vars[i]
            found = False
            while tried[i] < spec.n_opts[oi] and not found:
                self.assign[(k, oi)] = tried[i]
                tried[i] += 1
                found = not spec.evaluate(self.pat, self._choose_core, k - 2 * R2, k + 2 * R2, k - R2, k + R2)[3]
            if found:
                i += 1
            else:
                del self.assign[(k, oi)]
                tried[i] = 0
                i -= 1
        return False

    def _choose_core(self, k, oi):
        return self.assign.get((k, oi))

    def choose(self, k: int, oi: int):
        if self.kmin <= k <= self.kmax:
            return self.assign.get((k, oi))
        side = 1 if k > self.kmax else 0
        i = k - self.kmax - 1 if side else self.kmin - k - 1
        prefix, cycle = self.walks[side]
        vec = prefix[i] if i < len(prefix) else cycle[(i - len(prefix)) % len(cycle)]
        vars_ = self.ray_vars[side]
        return vec[vars_.index(oi)] if oi in vars_ else None

    def atoms(self):
        """(start_left, period_left, start_right, period_right, {k: {kind index: value}})."""
        spec, R2 = self.lp.spec, self.R2
        (pl, cl), (pr, cr) = self.walks
        LL, LR = len(cl), len(cr)
        sL = self.kmin - len(pl) - R2 - 1
        sR = self.kmax + len(pr) + R2 + 1
        a, b = sL - LL - 2 * R2, sR + LR + 2 * R2
        present, exs, P, _ = spec.evaluate(self.pat, self.choose, a, b, a, a - 1)
        out = {}
        for k in range(sL - LL, sR + LR):
            i = k - a
            out[k] = {ki: spec.value(P, present, exs, i, ki) for ki in spec.in_idx if present[i][ki]}
        return sL, LL, sR, LR, out


class LatticeProblem:
    """The kinds of kinds_in, on every lattice, given the rest as region patterns."""

    def __init__(self, kinds: list[str], kinds_in: set[str], regions: dict[str, dict], lrules):
        """regions[kind] holds Regions under keys: 'claims' {value: Region}, 'fixed',
        'exact' {value: Region}, 'forbidden'."""
        self.kinds, self.kinds_in, self.regions = kinds, kinds_in, regions
        self.g = lattice_step(o for _, it, _ in lrules for o in it.options) or self._free_step(regions)
        self.spec = Spec(kinds, kinds_in, self.g, lrules)
        self.mirror_spec = Spec(kinds, kinds_in, self.g, lrules, mirror=True)
        self.W = self.spec.rho + 1  # history a ray state keeps
        all_regions = [r for d in regions.values() for v in d.values() for r in (v.values() if isinstance(v, dict) else [v])]
        bounds = []
        self.residues = {ZERO, Pos(self.g / 2)}
        for reg in all_regions:
            if not reg or reg.is_everything():
                continue  # the same at every position: it has no bounds
            bounds += [reg.lo.x, reg.hi.x]
            for iv in reg.core.intervals:
                for e in (iv.lo, iv.hi):
                    if type(e) is Pos:
                        bounds.append(e.x)
                        self.residues.add(e % self.g)
            for pat, period, base in ((reg.left, reg.pl, reg.lo), (reg.right, reg.pr, reg.hi)):
                full = pat == IntervalSet.of(Interval(ZERO, period, True, False))
                if pat and not full:
                    if period % self.g:
                        raise ValueError("a repeating pattern whose period is not a multiple of the lattice step")
                    for iv in pat.intervals:
                        for e in (iv.lo, iv.hi):
                            self.residues.add((e + base) % self.g)
            self.residues |= {reg.lo % self.g, reg.hi % self.g}
        # the window is around the data: where 0 is means nothing
        margin = (2 * self.W + 2 * self.spec.rho + 4) * self.g
        if self._without_end(regions, lrules):
            bounds.append(Fraction(0))  # the first to choose is there
        first, last = min(bounds, default=Fraction(0)), max(bounds, default=Fraction(0))
        if first < 0 < last:  # beyond the window is further from 0 than all of it
            first, last = -max(-first, last), max(-first, last)
        self.lo, self.hi = first - margin, last + margin
        # the intervals of each region on the window that matters
        a, b = self.lo - 4 * self.g, self.hi + 4 * self.g
        self._ivs = {id(reg): reg.on(a, b).intervals for reg in all_regions}
        self._los = {i: [iv.lo for iv in ivs] for i, ivs in self._ivs.items()}

    @staticmethod
    def _without_end(regions: dict, lrules) -> bool:
        """Whether choices may go on forever: the first to choose is then the closest to 0,
        whatever is elsewhere. What forbids is not where a choice is made."""
        for d in regions.values():
            for key, v in d.items():
                if key != "forbidden":
                    for reg in (v.values() if isinstance(v, dict) else [v]):
                        if (reg.left or reg.right) and not reg.is_everything():
                            return True
        return any(it.is_or and len(lhs) == 1 and regions[lhs[0]]["fixed"].is_everything() for lhs, it, _ in lrules)

    @staticmethod
    def _free_step(regions: dict) -> Fraction:
        """The step when no rule has a distance: a position acts on no other one, so any step
        is exact, and the longer the fewer positions to go through: the common period of
        what repeats (it repeats in one step), else longer than the line."""
        period, far = None, Fraction(1)
        for d in regions.values():
            for reg in (r for v in d.values() for r in (v.values() if isinstance(v, dict) else [v])):
                if not reg:
                    continue
                far = max(far, abs(reg.lo.x) + 1, abs(reg.hi.x) + 1)
                for pat, p in ((reg.left, reg.pl), (reg.right, reg.pr)):
                    if pat and pat != IntervalSet.of(Interval(ZERO, p, True, False)):
                        if p.k:
                            return Fraction(1)
                        period = p.x if period is None else flcm(period, p.x)
        return period or 2 * far

    def pieces(self) -> list[tuple[IntervalSet, Pos]]:
        """(residues, representative) covering [0, g)."""
        rs = sorted(self.residues)
        out = []
        for i, a in enumerate(rs):
            out.append((IntervalSet.of(Interval.point(a)), a))
            b = rs[i + 1] if i + 1 < len(rs) else Pos(self.g)
            if a + EPS < b:
                rep = Pos((a.x + b.x) / 2) if a.x < b.x else a + EPS
                out.append((IntervalSet.of(Interval.open(a, b)), rep))
        return out

    def patterns(self, r: Pos, k0: int, k1: int) -> list[tuple]:
        """The patterns at r + k g for k = k0..k1: for each kind, its claim, whether it is
        fixed, its exact values, whether it is forbidden. Computed interval by interval:
        the k of an interval [lo, hi] are those with lo <= r + k g <= hi."""
        g, n = self.g, k1 - k0 + 1

        def where(reg):
            ivs = self._ivs[id(reg)]
            if len(ivs) > n:  # few positions, many intervals: each position finds its own
                los = self._los[id(reg)]
                start = max(_first_k(ivs[0].lo, r, g, k0), k0) - k0
                end = min(_last_k(ivs[-1].hi, r, g, k1), k1) - k0
                for i in range(start, end + 1):
                    x = r + (k0 + i) * g
                    j = bisect.bisect_right(los, x) - 1
                    if j >= 0 and ivs[j].contains(x):
                        yield i
                return
            for iv in ivs:
                a = max(_first_k(iv.lo, r, g, k0), k0)
                b = min(_last_k(iv.hi, r, g, k1), k1)
                yield from range(a - k0, b - k0 + 1)

        cols = []
        for k in self.kinds:
            d = self.regions.get(k, {})
            claim, fixed, exacts, forb = [None] * n, [False] * n, [frozenset()] * n, [False] * n
            for v, reg in d.get("claims", {}).items():
                for i in where(reg):
                    claim[i] = v
            if "fixed" in d:
                for i in where(d["fixed"]):
                    fixed[i] = True
            for v, reg in d.get("exact", {}).items():
                for i in where(reg):
                    exacts[i] = exacts[i] | {v}
            if "forbidden" in d:
                for i in where(d["forbidden"]):
                    forb[i] = True
            cols.append((claim, fixed, exacts, forb))
        return [tuple(KP(c[i], f[i] or bool(e[i]), e[i], b[i]) for c, f, e, b in cols) for i in range(n)]

    def solve(self) -> dict[str, dict[Fraction, Region]]:
        """Regions of the kinds of kinds_in; raises Conflict."""
        g = self.g
        results = []
        same: dict[tuple, tuple] = {}  # most lattices pose one of a few problems
        for piece, r in self.pieces():
            lat = Lattice(self, r)
            # (the order of the choices depends on which side of 0 and g/2 the residue is)
            half = Pos(g / 2)
            key = (r == ZERO, (r > half) - (r < half), lat.kmin, lat.kmax, tuple(lat._pats))
            if key not in same:
                if not lat.solve():
                    raise Conflict(f"no choice of 'or' operands satisfies the rules on the lattice {r} + {g}Z")
                same[key] = lat.atoms()
            results.append((piece, same[key]))
        if not results:
            return {}
        # one region per (kind, value), on common bounds and periods
        SL = min(a[0] for _, a in results)
        SR = max(a[2] for _, a in results)
        PL = math.lcm(*(a[1] for _, a in results))
        PR = math.lcm(*(a[3] for _, a in results))
        parts: dict[tuple[int, Fraction], tuple[list, list, list]] = {}
        for piece, (sL, LL, sR, LR, out) in results:
            def at(k):
                if k >= sR:
                    return out[sR + (k - sR) % LR]
                if k < sL:
                    return out[sL - LL + (k - sL) % LL]
                return out[k]
            for k in range(SL - PL, SR + PR):
                for ki, v in at(k).items():
                    core, left, right = parts.setdefault((ki, v), ([], [], []))
                    if k < SL:
                        left.extend(piece.shift((k - SL + PL) * g).intervals)
                    elif k < SR:
                        core.extend(piece.shift(k * g).intervals)
                    else:
                        right.extend(piece.shift((k - SR) * g).intervals)
        res: dict[str, dict[Fraction, Region]] = {}
        for (ki, v), (core, left, right) in parts.items():
            res.setdefault(self.kinds[ki], {})[v] = Region(SL * g, SR * g, IntervalSet(core), PL * g, IntervalSet(left), PR * g, IntervalSet(right))._compact()
        return res


def _first_k(lo, r: Pos, g: Fraction, default: int) -> int:
    """The least k with r + k g >= lo."""
    if type(lo) is not Pos:
        return default
    k = math.ceil((lo.x - r.x) / g)
    return k + 1 if r.x + k * g == lo.x and r.k < lo.k else k


def _last_k(hi, r: Pos, g: Fraction, default: int) -> int:
    """The greatest k with r + k g <= hi."""
    if type(hi) is not Pos:
        return default
    k = math.floor((hi.x - r.x) / g)
    return k - 1 if r.x + k * g == hi.x and r.k > hi.k else k


# ====================================================================== entry point


SATISFY_SECONDS = 5  # how long a set of rules may take to show a line: beyond, it is refused
SATISFY_TRIES = 400  # lines tried at most


def satisfiable(rs: RuleSet) -> str | None:
    """None if a line satisfies these rules, else why the set is refused (requirement.md: a set
    of rules impossible to satisfy is refused). A set is accepted only on a line found: the
    empty line, then the kinds that left sides read claimed on repeating patterns, on the
    grid of the rules' distances, the fewest first. So an impossible set is always refused;
    a possible one whose every line is beyond these patterns is refused too."""
    read = [k for k in rs.order if any(k in r.lhs.kinds() for r in rs.element)]
    values = {Fraction(100)}
    for r in rs.element:
        values |= {o.value for o in r.lits() if o.value is not None}
        todo = [r.lhs]
        while todo:
            f = todo.pop()
            if isinstance(f, Atom):
                if f.value:
                    values.add(f.value)
            else:
                todo += list(getattr(f, "parts", ())) + ([f.f] if hasattr(f, "f") else [])
    values = sorted(values)
    h = None
    for r in rs.rules:
        for o in r.lits():
            for e in (o.where.lo, o.where.hi):
                if type(e) is Pos and e.x:
                    h = abs(e.x) if h is None else _fgcd(h, abs(e.x))
    h = h or Fraction(1)
    tries = 0
    with limited(SATISFY_SECONDS):
        try:
            for m in (1, 2):
                cells = [c for k in range(m) for c in (Interval.point(k * h), Interval(k * h, (k + 1) * h, False, False))]
                per_kind = list(itertools.product([None] + values, repeat=len(cells)))
                for combo in itertools.product(per_kind, repeat=len(read)):
                    pos = {}
                    for kind, pattern in zip(read, combo):
                        for v in set(pattern) - {None}:
                            pat = IntervalSet([c for c, w in zip(cells, pattern) if w == v])
                            pos.setdefault(kind, {})[v] = Region(0, 0, IntervalSet(), m * h, pat, m * h, pat)._compact()
                    tries += 1
                    try:
                        solve(rs, Problem(pos))
                        return None
                    except (Conflict, Undetermined, IncompatiblePeriods):
                        pass
                    if tries >= SATISFY_TRIES:
                        return f"no line satisfying these rules was found in {SATISFY_TRIES} tries"
        except Undetermined:
            return "no line satisfying these rules was found in reasonable time"
    return "no line satisfying these rules was found"


def solve(rs: RuleSet, prob: Problem) -> Line:
    """The line of these claims; raises Conflict if the rules cannot be satisfied,
    Undetermined if they do not decide the choices."""
    has_or = bool(rs.or_items)
    kinds_in = set(rs.choice_dependent) if has_or else set()
    vals, fixed, forbid = region_stage(rs, prob, kinds_in)
    if not has_or:
        return Line(vals)
    return _solve_or(rs, prob, kinds_in, vals, fixed, forbid)


def _solve_or(rs: RuleSet, prob: Problem, kinds_in, vals, fixed, forbid) -> Line:
    if not rs.lattice:
        from sweep import Sweep
        vals.update(Sweep(rs, prob, vals, fixed, forbid, kinds_in).solve())
        return Line(vals)
    kinds = kinds_of(rs, prob)
    regions: dict[str, dict] = {}
    # the lattice rules: a left side is the presence of its chosen kinds, the absence of
    # those it reads under "not" ("!K"), and the presence of a "context" pseudo-kind,
    # where the rest of the left side is met (its left edges for an "or" relative to l)
    lrules = []
    for i, r in enumerate(rs.element):
        cd, ncd, ctx = rs.split[i]
        for j, it in enumerate(r.rhs):
            if not (it.is_or or cd or ncd):
                continue
            o = it.options[0]
            name = f"#{i}{o.anchor}"
            if name not in regions:
                reg = lhs_region(ctx, vals)
                regions[name] = {"fixed": reg.left_edges() if o.anchor == "l" else reg}
            lrules.append((cd + tuple("!" + k for k in ncd) + (name,), it, rs.or_rank.get((i, j))))
            if o.negated and o.kind not in kinds_in:  # read where a chosen kind forbids it
                regions[o.kind] = {"fixed": union(vals.get(o.kind, {}).values())}
    for k in kinds:
        if k in kinds_in:
            plain, exact = fixed.get(k, (EMPTY, {}))
            regions[k] = {"claims": prob.pos.get(k, {}), "fixed": plain, "exact": exact, "forbidden": forbid.get(k, EMPTY)}
    if any(_repeats(reg) for d in regions.values() for v in d.values() for reg in (v.values() if isinstance(v, dict) else [v])):
        # the lattice takes the data as constant beyond its window: data that repeats there
        # (e.g. breaks every 135 units, claimed) goes to the sweep, which follows it
        from sweep import Sweep
        vals.update(Sweep(rs, prob, vals, fixed, forbid, kinds_in).solve())
        return Line(vals)
    lp = LatticeProblem(kinds + sorted(n for n in regions if n.startswith("#")), kinds_in, regions, lrules)
    vals.update(lp.solve())
    return Line(vals)


def _repeats(reg: Region) -> bool:
    """The region has a part beyond its ends that repeats without being constant."""
    full = lambda pat, period: pat == IntervalSet.of(Interval(ZERO, period, True, False))
    return bool(reg) and ((reg.right and not full(reg.right, reg.pr)) or (reg.left and not full(reg.left, reg.pl)))
