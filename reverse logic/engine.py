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

from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction

from region import EMPTY, FULL, Region, cells_of, num
from rules import Forbids, Implies, ImpliesAny, RuleSet, anchors


class ContradictionError(ValueError):
    """The edit contradicts the rules by itself; it is not applied."""


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

    def contributions(self):
        return [(-1, self.region), (+1, self.region.shift(self.delta))]

    def inverse(self):
        return Move(self.kind, self.region.shift(self.delta), -self.delta)


Op = Add | Remove | Move


def inverse(ops) -> list[Op]:
    """The edit that exactly undoes `ops` (Add<->Remove, Move back)."""
    return [op.inverse() for op in ops]


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
                if j is None:
                    x = s[k].shift(-r.head[1]) & anchors(r, c)
                else:
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
        b0, _ = self.close(base, acc, None)
        chi = self.choices(b0, nb)
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
                acc.choice[ri] = [EMPTY] * len(self.rules[ri].options)
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


class LineSystem:
    def __init__(self, rules=()):
        self.ruleset = rules if isinstance(rules, RuleSet) else RuleSet(rules)
        self._edits: list[_Edit] = []
        self._state: Claims = {}

    # ---- internals ----------------------------------------------------------
    def _solve(self, edits: list[list[Op]]) -> tuple[Solution, list[tuple[Claims, Claims]]]:
        kinds = {op.kind for ops in edits for op in ops}
        levels = reduce_history(edits)
        return Solver(self.ruleset, kinds).solve(levels), levels

    def _active(self, exclude: set[int] = frozenset(), before: int | None = None) -> list[_Edit]:
        return [e for e in self._edits
                if e.active and e.id not in exclude and (before is None or e.id < before)]

    @staticmethod
    def _check_ops(ops) -> list[Op]:
        ops = list(ops)
        for op in ops:
            if not isinstance(op, (Add, Remove, Move)) or not isinstance(op.region, Region):
                raise TypeError(f"not an edit operation: {op!r}")
        added: Claims = {}
        removed: Claims = {}
        for op in ops:
            if isinstance(op, Add):
                added[op.kind] = _get(added, op.kind) | op.region
            elif isinstance(op, Remove):
                removed[op.kind] = _get(removed, op.kind) | op.region
        for k in added:
            both = added[k] & _get(removed, k)
            if both:
                raise ContradictionError(f"edit both adds and removes '{k}' on {both}")
        return ops

    def _accepted_on_top(self, history: list[list[Op]], ops: list[Op]) -> tuple[bool, Solution, tuple]:
        sol, _ = self._solve(history + [ops])
        drop_p, drop_n = sol.dropped[-1]
        return not (drop_p or drop_n), sol, (drop_p, drop_n)

    # ---- public API ---------------------------------------------------------
    def apply(self, ops) -> int:
        """Apply an edit as the newest one.  Returns its id.

        Raises ContradictionError if the edit contradicts the rules by itself;
        the line is then unchanged."""
        ops = self._check_ops(ops)
        ok, sol, (drop_p, drop_n) = self._accepted_on_top([e.ops for e in self._active()], ops)
        if not ok:
            parts = [f"'{k}' present on {r}" for k, r in drop_p.items()]
            parts += [f"'{k}' absent on {r}" for k, r in drop_n.items()]
            raise ContradictionError("edit contradicts the rules: cannot have " + "; ".join(parts))
        eid = len(self._edits)
        self._edits.append(_Edit(eid, ops))
        self._state = sol.state
        return eid

    def reverse(self, edit_id: int) -> None:
        """Undo an edit: the line becomes what it would be had it never been made."""
        e = self._find(edit_id)
        if not e.active:
            raise ValueError(f"edit {edit_id} is already reversed")
        e.active = False
        self._state = self._solve([x.ops for x in self._active()])[0].state

    def state(self) -> dict[str, Region]:
        return dict(self._state)

    def requires(self, b_id: int, a_id: int) -> bool:
        """B requires A iff B would have been rejected had A never been made."""
        b = self._find(b_id)
        self._find(a_id)
        history = [e.ops for e in self._edits[:b_id] if e.active and e.id != a_id]
        return not self._accepted_on_top(history, b.ops)[0]

    def effective(self, edit_id: int) -> tuple[Claims, Claims]:
        """Effective (present, absent) claims of an active edit in the current history."""
        active = self._active()
        pos = [e.id for e in active].index(self._find(edit_id).id)
        return reduce_history([e.ops for e in active])[pos]

    def _find(self, edit_id: int) -> _Edit:
        if not isinstance(edit_id, int) or not 0 <= edit_id < len(self._edits):
            raise KeyError(f"edit {edit_id} not found")
        return self._edits[edit_id]
