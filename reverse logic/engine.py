"""Edits, history reduction and the prioritized solver.  See SEMANTICS.md.

The line is never stored.  It is recomputed from the sequence of active
edits:

1. Reduction (sec. 2 of SEMANTICS.md).  Each edit contributes a signed integer
   count per kind and point (Add +1, Remove -1, Move -1 on the source and +1 on
   the target).  At every point the sequence of contributions is reduced
   right-to-left (newest first); opposite signs cancel.  Whatever survives
   at the level of edit e is e's *effective* claim there: present (> 0) or
   absent (< 0).

2. Solving (sec. 3).  Levels are processed from newest to oldest.  A level's
   claims are accepted unless they conflict with what newer levels
   accepted; conflicting claims (and every same-level claim needed for the
   conflict) are dropped, i.e. older elements adapt.  The line is the least
   model of the accepted claims under the rules.

3. An edit is accepted iff, once appended as the newest edit, none of its
   effective claims is dropped (sec. 4).
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction

from region import EMPTY, FULL, Region, cells_of, num
from rules import Forbids, Implies, ImpliesAny, RuleSet, anchors


class ContradictionError(ValueError):
    """The edit contradicts the rules by itself; it is not applied."""


class MissingElementError(ValueError):
    """The edit removes or moves an element that is not on the line; it is not applied."""


class DependencyError(ValueError):
    """The edit cannot be reversed because a later active edit requires it."""


# ============================================================================
# Edit operations
# ============================================================================

@dataclass(frozen=True)
class Add:
    kind: str
    region: Region

    def contributions(self):
        return [(+1, self.region)]

    def inverse(self):
        return Remove(self.kind, self.region)


@dataclass(frozen=True)
class Remove:
    kind: str
    region: Region

    def contributions(self):
        return [(-1, self.region)]

    def inverse(self):
        return Add(self.kind, self.region)


@dataclass(frozen=True)
class Move:
    """Translate the elements of `kind` on `region` by `delta`."""
    kind: str
    region: Region
    delta: Fraction

    def __post_init__(self):
        object.__setattr__(self, "delta", num(self.delta))

    @classmethod
    def to(cls, kind: str, region: Region, start) -> Move:
        """Move so that the start of `region` lands on `start`."""
        return cls(kind, region, num(start) - region.start)

    @property
    def target(self) -> Region:
        return self.region.shift(self.delta)

    def contributions(self):
        return [(-1, self.region), (+1, self.region.shift(self.delta))]

    def inverse(self):
        return Move(self.kind, self.region.shift(self.delta), -self.delta)


@dataclass(frozen=True)
class Extend:
    """Grow elements of `kind`: every part of `region` touches one of them."""
    kind: str
    region: Region

    def contributions(self):
        return [(+1, self.region)]

    def inverse(self):
        return Retract(self.kind, self.region)


@dataclass(frozen=True)
class Retract:
    """Shrink elements of `kind`: every part of `region` touches what remains of them."""
    kind: str
    region: Region

    def contributions(self):
        return [(-1, self.region)]

    def inverse(self):
        return Extend(self.kind, self.region)


Op = Add | Remove | Move | Extend | Retract


def inverse(ops) -> list[Op]:
    """The edit that exactly undoes `ops` (Add<->Remove, Extend<->Retract, Move back)."""
    return [op.inverse() for op in ops]


def taken(op: Op) -> Region:
    """Where the op takes elements from: they must be on the line."""
    return op.region if isinstance(op, (Remove, Retract, Move)) else EMPTY


def put(op: Op) -> Region:
    """Where the op puts elements: they must be on the line afterwards, and only there."""
    return op.region if isinstance(op, (Add, Extend)) else op.target if isinstance(op, Move) else EMPTY


def touches(part: Region, other: Region) -> bool:
    """`part` (one interval, disjoint from `other`) is adjacent to `other`.

    Both are canonical and disjoint, so their union has fewer parts iff some
    part of `other` merges with `part`."""
    return len((part | other).parts) < 1 + len(other.parts)


# ============================================================================
# 1. History reduction
# ============================================================================

Claims = dict[str, Region]


def reduce_history(edits: list[list[Op]]) -> list[tuple[Claims, Claims]]:
    """Effective (present, absent) claims of each edit, oldest first."""
    n = len(edits)
    by_kind: dict[str, list[tuple[int, int, Region]]] = defaultdict(list)
    for lvl, ops in enumerate(edits):
        for op in ops:
            for sign, reg in op.contributions():
                if reg:
                    by_kind[op.kind].append((lvl, sign, reg))

    levels: list[tuple[Claims, Claims]] = [({}, {}) for _ in range(n)]
    for kind, items in by_kind.items():
        cells = cells_of([reg for _, _, reg in items])
        pos = [[False] * len(cells) for _ in range(n)]
        neg = [[False] * len(cells) for _ in range(n)]
        for ci, (*_, rep) in enumerate(cells):
            counts = [0] * n
            for lvl, sign, reg in items:
                if rep in reg:
                    counts[lvl] += sign
            stack: list[list[int]] = []  # [level, signed count], newest at bottom
            for lvl in range(n - 1, -1, -1):
                c = counts[lvl]
                while c and stack and (stack[-1][1] > 0) != (c > 0):
                    m = min(abs(c), abs(stack[-1][1]))
                    c -= m if c > 0 else -m
                    stack[-1][1] -= m if stack[-1][1] > 0 else -m
                    if stack[-1][1] == 0:
                        stack.pop()
                if c:
                    stack.append([lvl, c])
            for lvl, c in stack:
                (pos if c > 0 else neg)[lvl][ci] = True
        for lvl in range(n):
            p = Region.from_cells(cells, pos[lvl])
            q = Region.from_cells(cells, neg[lvl])
            if p:
                levels[lvl][0][kind] = p
            if q:
                levels[lvl][1][kind] = q
    return levels


# ============================================================================
# 2. Prioritized solver
# ============================================================================

def _get(d: Claims, k: str) -> Region:
    return d.get(k, EMPTY)


def _rule_atoms(r) -> tuple:
    """Premises, then the head (Implies, Forbids) or the operands (ImpliesAny)."""
    return r.premises + (r.options if isinstance(r, ImpliesAny) else (r.head,))


def _gcd(values) -> Fraction:
    g = Fraction(0)
    for v in values:
        den = math.lcm(g.denominator, v.denominator)
        g = Fraction(math.gcd(int(g * den), int(v * den)), den)
    return g


def _union(a: Claims, b: Claims) -> Claims:
    out = dict(a)
    for k, r in b.items():
        out[k] = _get(out, k) | r
    return out


def _nonempty(d: Claims) -> Claims:
    return {k: r for k, r in d.items() if r}


@dataclass
class _Accepted:
    """What the levels processed so far (all newer than the current one) fixed."""
    base: Claims = field(default_factory=dict)       # accepted present-claims
    neg: Claims = field(default_factory=dict)        # accepted absent-claims
    plus: Claims = field(default_factory=dict)       # least model of the above
    anchor: dict[int, Region] = field(default_factory=dict)        # ImpliesAny anchors in `plus`
    choice: dict[int, list[Region]] = field(default_factory=dict)  # operand chosen per anchor


@dataclass
class Solution:
    state: Claims
    dropped: list[tuple[Claims, Claims]]  # per level (oldest first): dropped (present, absent) claims


class Solver:
    def __init__(self, ruleset: RuleSet, kinds):
        self.rules = ruleset.rules
        self.order = ruleset.order(kinds)
        self.idx = {k: i for i, k in enumerate(self.order)}
        # Rules fire once every atom they read is final: premise kinds for
        # producing rules, all atoms for Forbids.
        self.trig: dict[int, list[int]] = defaultdict(list)
        self.producers: dict[str, list[tuple[int, int | None]]] = defaultdict(list)
        self.any_rules = []
        self.option_kinds = set()
        # Every offset is an integer multiple of g, so rules only relate points
        # of the same lattice r + g*Z (sec. 3 of SEMANTICS.md).
        offsets = [o for r in self.rules for _, o in _rule_atoms(r)]
        self.g = _gcd(offsets) or Fraction(1)
        self.steps = [[(k, int(o / self.g)) for k, o in _rule_atoms(r)] for r in self.rules]
        for ri, r in enumerate(self.rules):
            read = [k for k, _ in r.premises] + ([r.head[0]] if isinstance(r, Forbids) else [])
            self.trig[max(self.idx[k] for k in read)].append(ri)
            if isinstance(r, Implies):
                self.producers[r.head[0]].append((ri, None))
            elif isinstance(r, ImpliesAny):
                self.any_rules.append(ri)
                for j, (k, _) in enumerate(r.options):
                    self.producers[k].append((ri, j))
                    self.option_kinds.add(k)

    # ---- least model ------------------------------------------------------
    def close(self, base: Claims, acc: _Accepted, chi) -> tuple[Claims, dict[int, Region]]:
        """Least model of `base`.  Old ImpliesAny anchors keep their recorded
        choice; new anchors use `chi` (or are not expanded when chi is None)."""
        c = {k: _get(base, k) for k in self.order}
        new_anchor: dict[int, Region] = {}
        for pos in range(len(self.order)):
            for ri in self.trig[pos]:
                r = self.rules[ri]
                if isinstance(r, Forbids):
                    continue
                x = anchors(r, c)
                if isinstance(r, Implies):
                    h, oh = r.head
                    c[h] |= x.shift(oh)
                    continue
                old_a = acc.anchor.get(ri, EMPTY)
                new = x - old_a
                new_anchor[ri] = new
                for j, (k, o) in enumerate(r.options):
                    sel = x & acc.choice[ri][j] if ri in acc.choice else EMPTY
                    if chi is not None:
                        sel |= new & chi[ri][0][j]
                    c[k] |= sel.shift(o)
        return c, new_anchor

    # ---- ordered-disjunction choice --------------------------------------
    def _forbidden(self, k: str, b0: Claims, nb: Claims, chi_of) -> Region:
        """Points y where adding k(y) to b0 creates a new violation."""
        rel: dict[str, dict[Fraction, Region]] = defaultdict(dict)
        rel[k][Fraction(0)] = FULL - b0[k]
        viol = EMPTY

        def add(kind, off, reg):
            if reg:
                rel[kind][off] = rel[kind].get(off, EMPTY) | reg

        for pos in range(self.idx[k], len(self.order)):
            kk = self.order[pos]
            for d, reg in rel[kk].items():
                viol |= reg & _get(nb, kk).shift(-d)
            for ri in self.trig[pos]:
                r = self.rules[ri]
                atoms = r.premises + ((r.head,) if isinstance(r, Forbids) else ())
                cands = sorted({d - o for a, o in atoms for d in rel[a]})
                for c in cands:
                    allp, new = FULL, EMPTY
                    for a, o in atoms:
                        here = rel[a].get(c + o, EMPTY)
                        allp &= here | b0[a].shift(-(c + o))
                        new |= here
                    inst = allp & new
                    if not inst:
                        continue
                    if isinstance(r, Implies):
                        h, oh = r.head
                        add(h, c + oh, inst - b0[h].shift(-(c + oh)))
                    elif isinstance(r, Forbids):
                        viol |= inst
                    else:
                        chosen, fail = chi_of(ri)
                        for j, (ok, oo) in enumerate(r.options):
                            add(ok, c + oo, (inst & chosen[j].shift(-c)) - b0[ok].shift(-(c + oo)))
                        viol |= inst & fail.shift(-c)
        return viol

    def choices(self, b0: Claims, nb: Claims):
        """chi[ri] = (anchor region per operand, fail region): operand j is
        chosen at x iff it is the first whose atom is not forbidden."""
        forb: dict[str, Region] = {}
        chi: dict[int, tuple[list[Region], Region]] = {}

        def chi_of(ri):
            if ri not in chi:
                remaining, chosen = FULL, []
                for ok, oo in self.rules[ri].options:
                    sel = remaining - forb[ok].shift(-oo)
                    chosen.append(sel)
                    remaining -= sel
                chi[ri] = (chosen, remaining)
            return chi[ri]

        for k in reversed(self.order):
            if k in self.option_kinds:
                forb[k] = self._forbidden(k, b0, nb, chi_of)
        for ri in self.any_rules:
            chi_of(ri)
        return chi

    # ---- exact ordered-disjunction choice --------------------------------
    def exact_choices(self, base: Claims, nb: Claims, acc: _Accepted):
        """Operands for the new ImpliesAny anchors such that the level has no
        violation, if such operands exist: the first such assignment, with
        anchors taken in topological order, then rule order, then left to
        right, and operands in their written order.

        Rules only relate points of one lattice r + g*Z, and on every cell of
        residues r below the same finite problem arises, so it is solved once
        per cell by backtracking.  On cells where no assignment exists, the
        approximate choice of `choices` is used: the level then has a
        violation whatever the choice, and its claims adapt."""
        g = self.g
        upper, new_anchor = self.close(base, acc, {
            ri: ([FULL] * len(self.rules[ri].options), EMPTY) for ri in self.any_rules})
        chosen = {ri: [[] for _ in self.rules[ri].options] for ri in self.any_rules}
        if not any(new_anchor.values()):
            return {ri: ([EMPTY] * len(ch), EMPTY) for ri, ch in chosen.items()}
        parts = [p for r in upper.values() for p in r.parts]
        lo, hi = min(p[0] for p in parts), max(p[2] for p in parts)

        regions = [*base.values(), *nb.values(), *acc.anchor.values(),
                   *(r for ch in acc.choice.values() for r in ch)]
        breaks = sorted({Fraction(0)} | {e % g for r in regions for p in r.parts
                                          for e in (p[0], p[2]) if not math.isinf(e)})
        cells = [(b, b) for b in breaks]
        cells += [(a, b) for a, b in zip(breaks, breaks[1:] + [g])]

        unsat = []
        for cell in cells:
            a, b = cell
            r = a if a == b else (a + b) / 2
            ks = range(math.floor((lo - r) / g), math.ceil((hi - r) / g) + 1)
            dec = self._solve_cell(r, ks, base, nb, acc)
            orbit = [Region.point(a + k * g) if a == b else Region.open(a + k * g, b + k * g) for k in ks]
            if dec is None:
                unsat.extend(orbit)
                continue
            for (ri, k), j in dec.items():
                chosen[ri][j].append(orbit[k - ks.start])

        chi = {ri: ([Region.union_of(*c) for c in ch], EMPTY) for ri, ch in chosen.items()}
        if unsat:
            mask = Region.union_of(*unsat)
            b0, _ = self.close(base, acc, None)
            approx = self.choices(b0, nb)
            chi = {ri: ([c | (a & mask) for c, a in zip(chi[ri][0], approx[ri][0])], approx[ri][1] & mask)
                   for ri in chi}
        return chi

    def _solve_cell(self, r: Fraction, ks: range, base: Claims, nb: Claims, acc: _Accepted):
        """Backtracking search on the lattice points r + k*g, k in ks.

        Returns {(rule, k): operand} for every new anchor, or None if every
        assignment has a violation.  Pruning is sound because the rules are
        monotone: an anchor that is not decided yet derives nothing, and
        deciding it can only add atoms, so a violation already present stays."""
        g = self.g
        pts = {k: r + k * g for k in ks}

        def on(reg: Region) -> set[int]:
            return {k for k, x in pts.items() if x in reg} if reg else set()

        base_k = {kk: on(_get(base, kk)) for kk in self.order}
        nb_k = {kk: on(_get(nb, kk)) for kk in self.order}
        old = {ri: (on(acc.anchor.get(ri, EMPTY)), [on(c) for c in acc.choice.get(ri, [])])
               for ri in self.any_rules}

        def run(dec):
            st = {kk: set(v) for kk, v in base_k.items()}
            pending = None
            for pos, kk in enumerate(self.order):
                if st[kk] & nb_k[kk]:
                    return None, None
                for ri in self.trig[pos]:
                    rule, steps = self.rules[ri], self.steps[ri]
                    n_prem = len(rule.premises)
                    reads = steps if isinstance(rule, Forbids) else steps[:n_prem]
                    x = set.intersection(*({k - o for k in st[a]} for a, o in reads))
                    if isinstance(rule, Forbids):
                        if x:
                            return None, None
                    elif isinstance(rule, Implies):
                        h, oh = steps[-1]
                        st[h] |= {k + oh for k in x}
                    else:
                        old_a, old_ch = old[ri]
                        for k in sorted(x):
                            if k in old_a:
                                j = next((j for j, c in enumerate(old_ch) if k in c), None)
                            else:
                                j = dec.get((ri, k))
                                if j is None and pending is None:
                                    pending = (ri, k)
                            if j is not None:
                                h, oh = steps[n_prem + j]
                                st[h].add(k + oh)
            return st, pending

        dec: dict[tuple[int, int], int] = {}
        frames: list[list] = []  # [anchor, operand] for every decision, oldest first
        while True:
            st, pending = run(dec)
            if st is not None:
                if pending is None:
                    return dec
                frames.append([pending, 0])
                dec[pending] = 0
                continue
            while frames:
                anchor, j = frames[-1]
                if j + 1 < len(self.rules[anchor[0]].options):
                    frames[-1][1] = dec[anchor] = j + 1
                    break
                frames.pop()
                del dec[anchor]
            else:
                return None

    # ---- conflicts --------------------------------------------------------
    def violations(self, c: Claims, nb: Claims, new_anchor, chi) -> Claims:
        """Atoms taking part in a violation (absent-claim, Forbids, failed choice)."""
        seeds: Claims = {}

        def seed(k, reg):
            if reg:
                seeds[k] = _get(seeds, k) | reg

        for k in self.order:
            seed(k, c[k] & _get(nb, k))
        for ri, r in enumerate(self.rules):
            if isinstance(r, Forbids):
                x = anchors(r, c) & c[r.head[0]].shift(-r.head[1])
                for a, o in r.premises + (r.head,):
                    seed(a, x.shift(o))
            elif isinstance(r, ImpliesAny) and chi is not None:
                x = new_anchor.get(ri, EMPTY) & chi[ri][1]
                for a, o in r.premises:
                    seed(a, x.shift(o))
        return seeds

    def support(self, seeds: Claims, c: Claims, acc: _Accepted, chi) -> Claims:
        """Every atom outside acc.plus used in some derivation of a seed atom."""
        s = {k: _get(seeds, k) for k in self.order}
        for k in reversed(self.order):
            s[k] = s[k] - _get(acc.plus, k)
            if not s[k]:
                continue
            for ri, j in self.producers[k]:
                r = self.rules[ri]
                if isinstance(r, Implies):
                    x = s[k].shift(-r.head[1]) & anchors(r, c)
                else:
                    assert isinstance(r, ImpliesAny) and j is not None
                    chosen = acc.choice[ri][j] if ri in acc.choice else EMPTY
                    chosen |= chi[ri][0][j] - acc.anchor.get(ri, EMPTY)
                    x = s[k].shift(-r.options[j][1]) & anchors(r, c) & chosen
                for a, o in r.premises:
                    s[a] |= x.shift(o)
        return s

    # ---- one level ----------------------------------------------------------
    def level(self, p: Claims, n: Claims, acc: _Accepted) -> tuple[Claims, Claims]:
        base = _union(acc.base, p)
        nb = _union(acc.neg, n)
        chi = self.exact_choices(base, nb, acc)
        c, new_anchor = self.close(base, acc, chi)

        seeds = self.violations(c, nb, new_anchor, chi)
        drop_n = _nonempty({k: r & c[k] for k, r in n.items()})
        drop_p: Claims = {}
        if seeds or drop_n:
            supp = self.support(seeds, c, acc, chi)
            drop_p = _nonempty({k: r & supp[k] for k, r in p.items()})
            p = _nonempty({k: r - _get(drop_p, k) for k, r in p.items()})
            n = _nonempty({k: r - _get(drop_n, k) for k, r in n.items()})
            base = _union(acc.base, p)
            nb = _union(acc.neg, n)
            c, new_anchor = self.close(base, acc, chi)
            # Theorem 3.2 of SEMANTICS.md: dropping the support removes every conflict.
            assert not self.violations(c, nb, new_anchor, chi), "solver invariant broken"

        acc.base, acc.neg, acc.plus = base, nb, c
        for ri, new in new_anchor.items():
            if ri not in acc.choice:
                r = self.rules[ri]
                assert isinstance(r, ImpliesAny)  # only ImpliesAny rules have anchors
                acc.choice[ri] = [EMPTY] * len(r.options)
            for j in range(len(acc.choice[ri])):
                acc.choice[ri][j] |= new & chi[ri][0][j]
            acc.anchor[ri] = acc.anchor.get(ri, EMPTY) | new
        return drop_p, drop_n

    def solve(self, levels: list[tuple[Claims, Claims]]) -> Solution:
        acc = _Accepted()
        dropped: list[tuple[Claims, Claims]] = [({}, {}) for _ in levels]
        for lvl in range(len(levels) - 1, -1, -1):
            dropped[lvl] = self.level(*levels[lvl], acc)
        return Solution(_nonempty({k: _get(acc.plus, k) for k in self.order}), dropped)


# ============================================================================
# 3. Line system
# ============================================================================

@dataclass
class _Edit:
    id: int
    ops: list[Op]
    active: bool = True
    internal: bool = False  # made by the system (LineSystem.force): never validated or reversed


def _restrict(c: Claims, region: Region) -> Claims:
    return _nonempty({k: r & region for k, r in c.items()})


class LineSystem:
    def __init__(self, rules=()):
        self.ruleset = rules if isinstance(rules, RuleSet) else RuleSet(rules)
        self._edits: list[_Edit] = []
        self._base: Claims = {}  # line of the active edits
        self._pending: list[Op] | None = None
        self._state: Claims = {}  # line of the active edits and the pending edit

    # ---- internals ----------------------------------------------------------
    def _solve(self, edits: list[list[Op]]) -> tuple[Solution, list[tuple[Claims, Claims]]]:
        kinds = {op.kind for ops in edits for op in ops}
        levels = reduce_history(edits)
        return Solver(self.ruleset, kinds).solve(levels), levels

    def _active(self, exclude: frozenset[int] = frozenset(), before: int | None = None) -> list[_Edit]:
        return [e for e in self._edits
                if e.active and e.id not in exclude and (before is None or e.id < before)]

    @staticmethod
    def _check_ops(ops) -> list[Op]:
        ops = list(ops)
        for op in ops:
            if not isinstance(op, (Add, Remove, Move, Extend, Retract)) or not isinstance(op.region, Region):
                raise TypeError(f"not an edit operation: {op!r}")
        # Within one edit and kind: no two ops take from the same place, no two
        # put on the same place, and nothing is added where something is taken
        # or removed where something is put.  The condition is symmetric under
        # `inverse`, so the inverse of a valid edit is valid.
        def added(op):
            return op.region if isinstance(op, (Add, Extend)) else EMPTY

        def removed(op):
            return op.region if isinstance(op, (Remove, Retract)) else EMPTY

        for i, a in enumerate(ops):
            for b in ops[i + 1:]:
                if a.kind != b.kind:
                    continue
                clash = ((taken(a) & taken(b)) | (put(a) & put(b))
                         | (added(a) & taken(b)) | (added(b) & taken(a))
                         | (removed(a) & put(b)) | (removed(b) & put(a)))
                if clash:
                    raise ContradictionError(f"edit uses '{a.kind}' on {clash} in two conflicting ways")
        return ops

    @staticmethod
    def _check_on(state: Claims, ops: list[Op]) -> None:
        """Raise MissingElementError unless `ops` fit the line `state`: they take
        only what is there, put nothing where the same kind already is (except
        where the edit itself takes it away), and every part of an extension or
        retraction touches the element it grows or shrinks."""
        verbs = {Remove: "remove", Move: "move", Retract: "retract"}
        for op in ops:
            have = _get(state, op.kind)
            missing = taken(op) - have
            if missing:
                raise MissingElementError(f"there is no '{op.kind}' on {missing} to {verbs[type(op)]}")
            freed = Region.union_of(*(taken(o) for o in ops if o.kind == op.kind))
            dup = (put(op) & have) - freed
            if dup:
                raise MissingElementError(f"'{op.kind}' is already on {dup}")
            if isinstance(op, (Extend, Retract)):
                rest = have - op.region
                for part in op.region.parts:
                    if not touches(Region((part,)), rest):
                        raise MissingElementError(
                            f"{type(op).__name__.lower()} of '{op.kind}' on {Region((part,))} "
                            f"does not touch {'an element' if isinstance(op, Extend) else 'what remains'}")

    def _validate(self, history: list[list[Op]], state: Claims, ops: list[Op],
                  keep: Region | None = None) -> Solution:
        """Solution of `history + [ops]`, where `state` is the line of `history`.

        Raises MissingElementError if the edit does not fit the line
        (`_check_on`), and ContradictionError if one of its claims would be
        dropped, one of the elements it puts would not be on the line, the edit
        could not be undone right after (sec. 4 of SEMANTICS.md), or the line
        on `keep` would change."""
        self._check_on(state, ops)
        sol, _ = self._solve(history + [ops])
        drop_p, drop_n = sol.dropped[-1]
        if drop_p or drop_n:
            parts = [f"'{k}' present on {r}" for k, r in drop_p.items()]
            parts += [f"'{k}' absent on {r}" for k, r in drop_n.items()]
            raise ContradictionError("edit contradicts the rules: cannot have " + "; ".join(parts))
        for op in ops:
            missing = put(op) - _get(sol.state, op.kind)
            if missing:
                raise ContradictionError(f"edit contradicts the rules: cannot have '{op.kind}' on {missing}")
        try:
            self._check_on(sol.state, inverse(ops))
        except MissingElementError as err:
            raise ContradictionError(f"edit could not be undone afterwards: {err}") from err
        if keep is not None and _restrict(state, keep) != _restrict(sol.state, keep):
            raise ContradictionError(f"edit would change the line on {keep}")
        return sol

    def _no_pending(self):
        if self._pending is not None:
            raise RuntimeError("an edit is pending (a drag is in progress): drop or cancel it first")

    def _replay(self, edits: list[_Edit], skip_invalid: bool) -> tuple[Claims, set[int]]:
        """Line of `edits` applied in order, and the ids of the invalid ones.

        Internal edits are not validated.  An invalid edit is skipped if
        `skip_invalid`; otherwise DependencyError is raised."""
        history: list[list[Op]] = []
        state: Claims = {}
        invalid: set[int] = set()
        for e in edits:
            if e.internal:
                state = self._solve(history + [e.ops])[0].state
            else:
                try:
                    state = self._validate(history, state, e.ops).state
                except (ContradictionError, MissingElementError) as err:
                    if not skip_invalid:
                        raise DependencyError(f"edit {e.id}: {err}") from err
                    invalid.add(e.id)
                    continue
            history.append(e.ops)
        return state, invalid

    def _set_base(self, state: Claims):
        self._base = self._state = state

    def _append(self, ops: list[Op], state: Claims, internal: bool = False) -> int:
        eid = len(self._edits)
        self._edits.append(_Edit(eid, ops, internal=internal))
        self._set_base(state)
        return eid

    # ---- public API ---------------------------------------------------------
    def apply(self, ops, keep: Region | None = None) -> int:
        """Apply an edit as the newest one.  Returns its id.

        Raises MissingElementError if it does not fit the line, and
        ContradictionError if it contradicts the rules by itself or would
        change the line on `keep`; the line is then unchanged."""
        self._no_pending()
        ops = self._check_ops(ops)
        sol = self._validate([e.ops for e in self._active()], self._base, ops, keep)
        return self._append(ops, sol.state)

    def force(self, ops) -> int:
        """Apply an edit made by the system, without the checks of `apply`.

        Used to pin part of the line (rules generation).  None of its claims
        may be dropped: the caller makes them consistent with the rules."""
        self._no_pending()
        ops = list(ops)
        sol, _ = self._solve([e.ops for e in self._active()] + [ops])
        assert not any(sol.dropped[-1]), "forced edit contradicts the rules"
        return self._append(ops, sol.state, internal=True)

    def reverse(self, edit_id: int) -> None:
        """Undo an edit: the line becomes what it would be had it never been made.

        Raises DependencyError, and changes nothing, if a later active edit
        requires it (it would not be valid had this edit never been made)."""
        self._no_pending()
        e = self._find(edit_id)
        if not e.active or e.internal:
            raise ValueError(f"edit {edit_id} cannot be reversed")
        try:
            state, _ = self._replay(self._active(exclude=frozenset({edit_id})), skip_invalid=False)
        except DependencyError as err:
            raise DependencyError(f"a later edit requires edit {edit_id} ({err}); reverse it first") from err
        e.active = False
        self._set_base(state)

    def reverse_all(self, edit_ids) -> list[int]:
        """Undo the given edits and every later edit that requires one of them.

        Returns the ids of all the reversed edits.  The line becomes what it
        would be had none of them been made."""
        self._no_pending()
        gone = set(edit_ids)
        for i in gone:
            e = self._find(i)
            if not e.active or e.internal:
                raise ValueError(f"edit {i} cannot be reversed")
        state, invalid = self._replay(self._active(exclude=frozenset(gone)), skip_invalid=True)
        gone |= invalid
        for i in gone:
            self._edits[i].active = False
        self._set_base(state)
        return sorted(gone)

    def set_pending(self, ops, keep: Region | None = None) -> None:
        """Show `ops` as if applied as the newest edit, without committing it.

        It replaces any previous pending edit and is validated like `apply`
        on the active edits alone, so the result never depends on the previous
        pending edit.  If it is not valid, the previous pending edit stays."""
        ops = self._check_ops(ops)
        sol = self._validate([e.ops for e in self._active()], self._base, ops, keep)
        self._pending = ops
        self._state = sol.state

    def clear_pending(self) -> None:
        self._pending = None
        self._state = self._base

    def commit_pending(self) -> int | None:
        """Make the pending edit a normal edit.  Returns its id (None if no edit is pending)."""
        ops, self._pending = self._pending, None
        return None if ops is None else self._append(ops, self._state)

    def drag(self, block) -> Drag:
        """Start dragging a block: `block` is a list of (kind, region) on the line."""
        self._no_pending()
        return Drag(self, list(block))

    def state(self) -> dict[str, Region]:
        return dict(self._state)

    def requires(self, b_id: int, a_id: int) -> bool:
        """B requires A iff B would not be valid had A never been made, nor any
        edit that itself requires A (those are skipped, oldest first)."""
        b = self._find(b_id)
        self._find(a_id)
        _, invalid = self._replay(self._active(exclude=frozenset({a_id}), before=b_id) + [b], skip_invalid=True)
        return b_id in invalid

    def effective(self, edit_id: int) -> tuple[Claims, Claims]:
        """Effective (present, absent) claims of an active edit in the current history."""
        active = self._active()
        pos = [e.id for e in active].index(self._find(edit_id).id)
        return reduce_history([e.ops for e in active])[pos]

    def _find(self, edit_id: int) -> _Edit:
        if not isinstance(edit_id, int) or not 0 <= edit_id < len(self._edits):
            raise KeyError(f"edit {edit_id} not found")
        return self._edits[edit_id]


class Drag:
    """A block of elements being dragged.

    Every step shows the block moved by `delta` from where it started, as a
    pending edit computed from the active edits alone: after each step the
    line is exactly what it would be had the block been moved there directly.
    A step that is not valid (for example onto an element of the same kind)
    is refused, and the block stays where the previous step put it."""

    def __init__(self, line: LineSystem, block: list[tuple[str, Region]]):
        self.line = line
        self.block = block
        self.delta = Fraction(0)

    def step(self, delta) -> None:
        d = num(delta)
        if d == 0:
            self.line.clear_pending()
        else:
            self.line.set_pending([Move(k, r, d) for k, r in self.block])
        self.delta = d

    def drop(self) -> int | None:
        """End the drag.  Returns the id of the move edit (None if back at the start)."""
        return self.line.commit_pending()

    def cancel(self) -> None:
        self.line.clear_pending()
        self.delta = Fraction(0)
