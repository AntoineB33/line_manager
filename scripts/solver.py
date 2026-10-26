"""The line computed from its claims: what the element rules make of the elements placed.

Given claims (elements placed, with percentages; "no K" elements) the line is:
- every claimed element;
- closed under the rules: where a left side is met, its literals hold; for an
  ordered "or", an operand is chosen;
- the operands are chosen position by position, from left to right (at one position,
  an "or" whose left side reads what another one places comes after it, else the
  rules in written order): each position takes the leftmost operand that can be
  chosen, i.e. the first one that still lets all the other positions be placed, given
  the choices on its left. This is the lexicographically smallest feasible choice in
  that order. On an element unbounded on the left there is no first position: if the
  choices there act on each other, nothing decides them (`Undetermined`).
If no choice satisfies the rules (an element where it must not be, two different
percentages required, ...), the claims are in conflict (`Conflict`).

Positions are x + kε (intervals.py). How it is computed exactly. Kinds that no "or"
choice can change are computed with region algebra. For the others, two solvers:
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
  a result that repeats. A left ray is decided only if its choices do not act on each
  other (then each position simply takes its first valid operand);
- the sweep (sweep.py) otherwise: intervals and left edges make every position act
  on the positions near it.
"""

from __future__ import annotations

import bisect
import itertools
import math
import sys
from dataclasses import dataclass, field
from fractions import Fraction

from intervals import EPS, INF, Interval, IntervalSet, Pos
from region import ZERO, Region
from rules import RuleSet, lattice_step

sys.setrecursionlimit(20000)


class Conflict(Exception):
    pass


class Undetermined(Exception):
    """The rules do not decide the "or" choices here (no first position), or deciding
    them would take the sweep's search longer than it is allowed."""


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
        return sorted(self.vals)

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
    base = anchors if lit.anchor == "p" else anchors.left_edges()
    return base.shift(lit.rel) if lit.is_point else base.dilate(lit.where)


def kinds_of(rs: RuleSet, prob: Problem) -> list[str]:
    extra = sorted((set(prob.pos) | set(prob.neg)) - set(rs.order))
    return list(rs.order) + extra


def region_stage(rs: RuleSet, prob: Problem, kinds_in: set[str], check: bool = True):
    """Kinds outside kinds_in are computed here, in the order of the rules (no cycle:
    a left side only reads kinds already computed). Returns (values of out kinds,
    fixed contributions to in kinds, forbidden regions of in kinds)."""
    vals: dict[str, dict[Fraction, Region]] = {}
    fixed: dict[str, tuple[Region, dict[Fraction, Region]]] = {}
    static = [r for r in rs.element if not set(r.lhs.kinds()) & kinds_in]
    anchors: dict[int, Region] = {}

    def met(r) -> Region:
        if id(r) not in anchors:
            anchors[id(r)] = lhs_region(r.lhs, vals)
        return anchors[id(r)]

    for K in kinds_of(rs, prob):
        claims = prob.pos.get(K, {})
        plain, exact = EMPTY, {}
        for r in static:
            for it in r.rhs:
                o = it.options[0]
                if it.is_or or o.negated or o.kind != K:
                    continue
                t = target(o, met(r))
                if o.value is None:
                    plain = plain | t
                else:
                    exact[o.value] = exact.get(o.value, EMPTY) | t
        if K in kinds_in:
            fixed[K] = (plain, exact)
            continue
        vals[K] = _values(K, claims, plain, exact, check)
    pres = {k: union(d.values()) for k, d in vals.items()}
    forbid = {k: r for k, r in prob.neg.items()}
    for r in static:
        for it in r.rhs:
            o = it.options[0]
            if not it.is_or and o.negated:
                forbid[o.kind] = forbid.get(o.kind, EMPTY) | target(o, met(r))
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
    that read chosen kinds."""

    def __init__(self, kinds: list[str], kinds_in: set[str], g: Fraction, lrules, mirror=False):
        sgn = -1 if mirror else 1
        self.kinds = kinds
        self.idx = {k: i for i, k in enumerate(kinds)}
        self.in_order = [k for k in kinds if k in kinds_in]
        self.defs, self.negs, self.ors, self.or_ranks = [], [], [], []
        for names, it, rank in lrules:
            lhs = tuple(self.idx[k] for k in names)
            if it.is_or:
                self.ors.append((lhs, tuple((self.idx[o.kind], sgn * int(o.rel.x / g), o.value) for o in it.options)))
                self.or_ranks.append(rank)
            else:
                o = it.options[0]
                d = sgn * int(o.rel.x / g)
                (self.negs if o.negated else self.defs).append((lhs, self.idx[o.kind], d, o.value) if not o.negated else (lhs, self.idx[o.kind], d))
        # dist[K]: how far (in steps) an atom of K can be from the choices it depends on
        dist = [0] * len(kinds)
        for k in self.in_order:
            ki = self.idx[k]
            for lhs, kk, d, _ in self.defs:
                if kk == ki:
                    dist[ki] = max(dist[ki], max((dist[l] for l in lhs), default=0) + abs(d))
            for lhs, os in self.ors:
                for kk, d, _ in os:
                    if kk == ki:
                        dist[ki] = max(dist[ki], max((dist[l] for l in lhs), default=0) + abs(d))
        D = max(dist, default=0)
        # rho: the choices involved in one violation lie within rho + 1 consecutive points
        spans = [2 * D] + [max((dist[l] for l in lhs), default=0) + abs(d) + dist[kk] for lhs, kk, d in self.negs]
        self.rho = max(spans)
        self.n_opts = [len(os) for _, os in self.ors]
        self.by_kind_defs = {i: [x for x in self.defs if x[1] == i] for i in range(len(kinds))}
        self.by_kind_ors = {i: [(oi, t, d, v) for oi, (_, os) in enumerate(self.ors) for t, (kk, d, v) in enumerate(os) if kk == i] for i in range(len(kinds))}
        self.in_idx = [self.idx[k] for k in self.in_order]
        self.independent = self._independent()

    def _independent(self) -> bool:
        """No violation can involve the choices of two different positions. A production
        of an atom is (the offsets, in steps back from the atom, of the choices it uses,
        its value); an atom may also be present by itself (no choice)."""
        inset = set(self.in_idx)
        none = [(frozenset(), None)]
        prods = {}
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
        with more choices, so a violation found is a violation of every completion."""
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

    def value(self, P, present, exs, i, ki) -> Fraction | None:
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
        g, T = lp.g, lp.T
        self.kmin = -int((T + r) // g)
        self.kmax = int((T - r) // g)
        self.R2 = 3 * lp.spec.rho + 1

    def x(self, k: int) -> Fraction:
        return self.r + k * self.lp.g

    def pat(self, k: int) -> tuple:
        return self.lp.pattern_at(self.x(min(max(k, self.kmin - 1), self.kmax + 1)))  # constant beyond the core

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
                    if all(present[i][l] for l in lhs):
                        self.vars.append((k, oi))
        mid_r, mid_l = self.kmax + R2, self.kmin - R2
        self.ray_vars = (tuple(oi for oi, (lhs, _) in enumerate(spec.ors) if all(present[mid_l - lo][l] for l in lhs)),
                         tuple(oi for oi, (lhs, _) in enumerate(spec.ors) if all(present[mid_r - lo][l] for l in lhs)))
        self.vars.sort(key=lambda v: (v[0], spec.or_ranks[v[1]]))
        if self.ray_vars[0] and not spec.independent:
            raise Undetermined("an 'or' acts on an element unbounded on the left, where its choices act on each other: "
                               "with no first position, nothing decides them")
        _, _, _, bad = spec.evaluate(self.pat, lambda k, oi: None, lo, hi, lo, hi)
        if bad:
            return False
        W = self.lp.W
        self.rays = (Ray(self.lp.mirror_spec, self.pat(self.kmin - 1), self.ray_vars[0], W),
                     Ray(spec, self.pat(self.kmax + 1), self.ray_vars[1], W))
        self.assign: dict[tuple[int, int], int] = {}
        if not self._dfs(0):
            return False
        self.walks = (self.rays[0].walk(self._boundary(0)), self.rays[1].walk(self._boundary(1)))
        return True

    def _boundary(self, side: int):
        W = self.lp.W
        ks = range(self.kmax - W + 1, self.kmax + 1) if side else range(self.kmin + W - 1, self.kmin - 1, -1)
        return tuple(tuple(self.assign.get((k, oi), 0) for oi in self.ray_vars[side]) for k in ks)

    def _dfs(self, i: int) -> bool:
        if i == len(self.vars):
            return self.rays[0].good(self._boundary(0)) and self.rays[1].good(self._boundary(1))
        k, oi = self.vars[i]
        spec, R2 = self.lp.spec, self.R2
        for t in range(spec.n_opts[oi]):
            self.assign[(k, oi)] = t
            _, _, _, bad = spec.evaluate(self.pat, self._choose_core, k - 2 * R2, k + 2 * R2, k - R2, k + R2)
            if not bad and self._dfs(i + 1):
                return True
        del self.assign[(k, oi)]
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
        self.g = lattice_step(o for _, it, _ in lrules for o in it.options) or Fraction(1)
        self.spec = Spec(kinds, kinds_in, self.g, lrules)
        self.mirror_spec = Spec(kinds, kinds_in, self.g, lrules, mirror=True)
        self.W = self.spec.rho + 1  # history a ray state keeps
        all_regions = [r for d in regions.values() for v in d.values() for r in (v.values() if isinstance(v, dict) else [v])]
        bounds = [Fraction(0)]
        self.residues = {ZERO, Pos(self.g / 2)}
        for reg in all_regions:
            if not reg:
                continue
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
        self.T = max(abs(b) for b in bounds) + (2 * self.W + 2 * self.spec.rho + 4) * self.g
        self._cache: dict[Fraction, tuple] = {}
        # membership tests on the window that matters, by binary search
        a, b = -self.T - 4 * self.g, self.T + 4 * self.g
        self._fast: dict[int, _Fast] = {}
        for reg in all_regions:
            self._fast[id(reg)] = _Fast(reg.on(a, b), reg, a, b)

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

    def pattern_at(self, x: Pos) -> tuple:
        if x in self._cache:
            return self._cache[x]
        row = []
        for k in self.kinds:
            d = self.regions.get(k, {})
            claim = None
            has = lambda reg: self._fast[id(reg)].contains(x) if id(reg) in self._fast else reg.contains(x)
            for v, reg in d.get("claims", {}).items():
                if has(reg):
                    claim = v
            fixed = has(d["fixed"]) if "fixed" in d else False
            exacts = frozenset(v for v, reg in d.get("exact", {}).items() if has(reg))
            forb = has(d["forbidden"]) if "forbidden" in d else False
            row.append(KP(claim, fixed or bool(exacts), exacts, forb))
        self._cache[x] = tuple(row)
        return self._cache[x]

    def solve(self) -> dict[str, dict[Fraction, Region]]:
        """Regions of the kinds of kinds_in; raises Conflict."""
        g = self.g
        results = []
        for piece, r in self.pieces():
            lat = Lattice(self, r)
            if not lat.solve():
                raise Conflict(f"no choice of 'or' operands satisfies the rules on the lattice {r} + {g}Z")
            results.append((piece, lat.atoms()))
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


class _Fast:
    """x in a region, by binary search on its intervals within [a, b)."""

    def __init__(self, s: IntervalSet, reg: Region, a, b):
        self.ivs = s.intervals
        self.los = [iv.lo for iv in self.ivs]
        self.reg, self.a, self.b = reg, a, b

    def contains(self, x) -> bool:
        if not self.a <= x < self.b:
            return self.reg.contains(x)
        i = bisect.bisect_right(self.los, x) - 1
        return i >= 0 and self.ivs[i].contains(x)


# ====================================================================== entry point


def solve(rs: RuleSet, prob: Problem) -> Line:
    """The line of these claims; raises Conflict if the rules cannot be satisfied,
    Undetermined if they do not decide the choices."""
    has_or = bool(rs.or_items)
    kinds_in = set(rs.choice_dependent) if has_or else set()
    vals, fixed, forbid = region_stage(rs, prob, kinds_in)
    if not has_or:
        return Line(vals)
    if not rs.lattice:
        from sweep import Sweep
        vals.update(Sweep(rs, prob, vals, fixed, forbid, kinds_in).solve())
        return Line(vals)
    kinds = kinds_of(rs, prob)
    regions: dict[str, dict] = {}
    # the lattice rules: a left side is the presence of its chosen kinds and of a
    # "context" pseudo-kind, where the rest of the left side is met (its left edges for
    # an "or" relative to l)
    lrules = []
    for i, r in enumerate(rs.element):
        cd, ctx = rs.split[i]
        for j, it in enumerate(r.rhs):
            if not (it.is_or or cd):
                continue
            o = it.options[0]
            name = f"#{i}{o.anchor}"
            if name not in regions:
                reg = lhs_region(ctx, vals)
                regions[name] = {"fixed": reg.left_edges() if o.anchor == "l" else reg}
            lrules.append((cd + (name,), it, rs.or_rank.get((i, j))))
            if o.negated and o.kind not in kinds_in:  # read where a chosen kind forbids it
                regions[o.kind] = {"fixed": union(vals.get(o.kind, {}).values())}
    for k in kinds:
        if k in kinds_in:
            plain, exact = fixed.get(k, (EMPTY, {}))
            regions[k] = {"claims": prob.pos.get(k, {}), "fixed": plain, "exact": exact, "forbidden": forbid.get(k, EMPTY)}
    lp = LatticeProblem(kinds + sorted(n for n in regions if n.startswith("#")), kinds_in, regions, lrules)
    vals.update(lp.solve())
    return Line(vals)
