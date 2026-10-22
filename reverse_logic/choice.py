"""Operands of ordered disjunctions on one lattice r + g*Z.  See SEMANTICS.md, sec. 3.

The positions r + k*g of the new anchors are decided one after the other, the
closest to 0 first (the one on the left first when two are equally close), and
at one position the rules in topological order; each takes the first operand
that still lets the remaining positions be placed without violation.

`solve_finite` does it by backtracking when there are finitely many anchors.
`solve_infinite` does it when there are infinitely many: every violation is
*local* (whether there is one at position j depends only on the operands at
j + vlo .. j + vhi), and far enough from 0 every datum is periodic in k, so
"the rest can be placed" is a question on a finite graph, and the result
repeats on each side from some position on.

When no assignment exists, `keep_infinite` keeps the level's claims one after
the other in the same order, each if an assignment exists with it and the ones
kept before it (with finitely many anchors, `engine.Solver._keep_in_order`).
"""
from __future__ import annotations

import math
from collections.abc import Callable
from fractions import Fraction
from itertools import product

from region import EMPTY, Region, lcm
from rules import Forbids, Implies, ImpliesAny

Letter = tuple  # one operand index (or None: no new anchor there) per ordered disjunction


def _order_key(x: Fraction):
    return (abs(x), x)


def _hull(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return (min(a[0], b[0]), max(a[1], b[1]))


def _shift(a, d):
    return None if a is None else (a[0] + d, a[1] + d)


class Cell:
    """The lattice r + k*g of a cell of residues, and what the level knows on it."""

    def __init__(self, solver, r: Fraction, base, nb, acc, cand, removed=frozenset()):
        self.s, self.r, self.g = solver, r, solver.g
        self.base, self.nb, self.acc, self.cand = base, nb, acc, cand
        self.removed = removed  # (kind, k): atoms of `base` left out
        # The ordered disjunctions, in the order the closure meets them.
        self.anys = [ri for pos in range(len(solver.order)) for ri in solver.trig[pos]
                     if isinstance(solver.rules[ri], ImpliesAny)]
        self.slot = {ri: i for i, ri in enumerate(self.anys)}
        self.unit: Region = Region.point(r)  # the cell of residues, at k = 0
        self._data: dict[int, tuple] = {}

    def x(self, k: int) -> Fraction:
        return self.r + k * self.g

    def data(self, k: int):
        """(base kinds, absent kinds, old operand per rule or None, new anchor per rule) at k."""
        d = self._data.get(k)
        if d is None:
            x, s = self.x(k), self.s
            base = frozenset(kk for kk in s.order
                             if x in self.base.get(kk, EMPTY) and (kk, k) not in self.removed)
            nb = frozenset(kk for kk in s.order if x in self.nb.get(kk, EMPTY))
            old = tuple(next((j for j, c in enumerate(self.acc.choice.get(ri, [])) if x in c), None)
                        if x in self.acc.anchor.get(ri, EMPTY) else None for ri in self.anys)
            new = tuple(x in self.cand.get(ri, EMPTY) for ri in self.anys)
            d = self._data[k] = (base, nb, old, new)
        return d

    def base_at(self, k: int, letters: dict[int, Letter]) -> frozenset:
        """The base kinds at k, given the letters."""
        return self.data(k)[0]

    def letters(self, k: int) -> list[Letter]:
        """Every letter at k, in the order of the decisions (lexicographic, first operands first)."""
        _, _, _, new = self.data(k)
        ranges = [range(len(self.s.rules[ri].options)) if new[i] else (None,) for i, ri in enumerate(self.anys)]
        return list(product(*ranges))

    def dependency(self) -> tuple[int, int] | None:
        """The offsets of the letters that a violation anchored at 0 may depend on."""
        return dependency(self.s)

    def regions(self) -> list[Region]:
        return [*self.base.values(), *self.nb.values(), *self.acc.anchor.values(),
                *(c for ch in self.acc.choice.values() for c in ch), *self.cand.values()]

    def side(self, right: bool) -> tuple[int, int]:
        """(index, period): beyond the index on that side, every datum is periodic in k."""
        g, r = self.g, self.r
        thr, period = None, 1
        for reg in self.regions():
            if reg.is_empty():
                continue
            tail = reg.right if right else reg.left
            ends = [e for p in reg.parts for e in (p[0], p[2]) if not math.isinf(e)]
            ends += [t.at for t in (reg.left, reg.right) if t is not None]
            if ends:
                t = max(ends) if right else min(ends)
                thr = t if thr is None else (max(thr, t) if right else min(thr, t))
            if tail is not None:
                period = math.lcm(period, int(lcm(tail.period, g) / g))
        if thr is None:
            return 0, period
        return (math.ceil((thr - r) / g) + 1 if right else math.floor((thr - r) / g) - 1), period

    def closure(self, lo: int, hi: int, letters: dict[int, Letter]) -> dict[str, set[int]]:
        """The least model on positions lo..hi, with the given operands (none elsewhere)."""
        s = self.s
        st = {kk: set() for kk in s.order}
        for k in range(lo, hi + 1):
            for kk in self.base_at(k, letters):
                st[kk].add(k)
        for pos in range(len(s.order)):
            for ri in s.trig[pos]:
                rule, steps = s.rules[ri], s.steps[ri]
                if isinstance(rule, Forbids):
                    continue
                n_prem = len(rule.premises)
                x = set.intersection(*({k - o for k in st[a]} for a, o in steps[:n_prem]))
                if isinstance(rule, Implies):
                    h, oh = steps[-1]
                    st[h] |= {k + oh for k in x if lo <= k + oh <= hi}
                    continue
                slot = self.slot[ri]
                for k in x:
                    j = self.data(k)[2][slot]
                    if j is None and k in letters:
                        j = letters[k][slot]
                    if j is not None:
                        h, oh = steps[n_prem + j]
                        if lo <= k + oh <= hi:
                            st[h].add(k + oh)
        return st

    def bad_at(self, j: int, st: dict[str, set[int]]) -> bool:
        """A violation anchored at j: an atom on an absent claim, or a Forbids instance."""
        s = self.s
        if any(j in st[kk] for kk in self.data(j)[1]):
            return True
        for ri, rule in enumerate(s.rules):
            if isinstance(rule, Forbids) and all(j + o in st[a] for a, o in s.steps[ri]):
                return True
        return False


class FlagCell(Cell):
    """A cell whose letters also say which of the level's present claims `p`
    are kept: a letter is (operand per ordered disjunction ..., kept per kind
    of `p` ...). A claim at a position without a letter is not kept. `base`
    holds the claims, kept or not."""

    def __init__(self, solver, r: Fraction, base, nb, acc, cand, p):
        super().__init__(solver, r, base, nb, acc, cand)
        self.p = p
        self.kinds = [kk for kk in solver.order if p.get(kk)]
        self.none = (False,) * len(self.kinds)
        self._claims: dict[int, tuple[bool, ...]] = {}

    def claims(self, k: int) -> tuple[bool, ...]:
        """Per kind of `p`: whether the level claims it at k, not already accepted."""
        c = self._claims.get(k)
        if c is None:
            x = self.x(k)
            c = self._claims[k] = tuple(x in self.p[kk] and x not in self.acc.plus.get(kk, EMPTY)
                                        for kk in self.kinds)
        return c

    def base_at(self, k: int, letters: dict[int, Letter]) -> frozenset:
        kept = letters[k][len(self.anys):] if k in letters else self.none
        out = {kk for kk, c, f in zip(self.kinds, self.claims(k), kept) if c and not f}
        return self.data(k)[0] - out

    def letters(self, k: int, kept: tuple[bool, ...] | None = None) -> list[Letter]:
        """Every letter at k with these claims kept (default: none), operands as in Cell."""
        return [ops + (kept or self.none) for ops in super().letters(k)]

    def dependency(self) -> tuple[int, int] | None:
        return _hull(dependency(self.s), claim_dependency(self.s))

    def regions(self) -> list[Region]:
        return [*super().regions(), *self.p.values()]


def dependency(solver) -> tuple[int, int] | None:
    """The offsets (in steps of g) of the operands that a violation anchored at
    0 may depend on: the hull, over every atom it reads, of the operands that
    atom may be derived from (None: no operand matters)."""
    dep: dict[str, tuple[int, int] | None] = {kk: None for kk in solver.order}
    for pos in range(len(solver.order)):
        for ri in solver.trig[pos]:
            rule, steps = solver.rules[ri], solver.steps[ri]
            if isinstance(rule, Forbids):
                continue
            prem = steps[:len(rule.premises)]
            heads = [steps[-1]] if isinstance(rule, Implies) else steps[len(rule.premises):]
            for h, oh in heads:
                d = None
                for a, o in prem:
                    d = _hull(d, _shift(dep[a], o - oh))
                if isinstance(rule, ImpliesAny):
                    d = _hull(d, (-oh, -oh))
                dep[h] = _hull(dep[h], d)
    return _violation_hull(solver, dep)


def claim_dependency(solver) -> tuple[int, int]:
    """The offsets (in steps of g) of the base atoms that a violation anchored
    at 0 may depend on."""
    dep: dict[str, tuple[int, int] | None] = {kk: (0, 0) for kk in solver.order}
    for pos in range(len(solver.order)):
        for ri in solver.trig[pos]:
            rule, steps = solver.rules[ri], solver.steps[ri]
            if isinstance(rule, Forbids):
                continue
            prem = steps[:len(rule.premises)]
            heads = [steps[-1]] if isinstance(rule, Implies) else steps[len(rule.premises):]
            for h, oh in heads:
                for a, o in prem:
                    dep[h] = _hull(dep[h], _shift(dep[a], o - oh))
    return _violation_hull(solver, dep)


def _violation_hull(solver, dep):
    """The hull of `dep` over the atoms a violation anchored at 0 reads."""
    v = None
    for kk in solver.order:
        v = _hull(v, dep[kk])
    for ri, rule in enumerate(solver.rules):
        if isinstance(rule, Forbids):
            for a, o in solver.steps[ri]:
                v = _hull(v, _shift(dep[a], o))
    return v


class _Walk:
    """Violations and placements on the lattice of a cell, for infinitely many positions."""

    def __init__(self, cell: Cell):
        s, g = cell.s, cell.g
        self.cell = cell
        v = cell.dependency()
        self.vlo, self.vhi = v if v is not None else (0, 0)
        self.w = self.vhi - self.vlo
        reach = math.ceil(s.reach / g) if s.reach else 0
        self.radius = (reach + max(abs(self.vlo), abs(self.vhi))
                       + max((abs(o) for st in s.steps for _, o in st), default=0) + 1)
        # Beyond these indices every datum is periodic in k with these periods.
        self.k_right, self.t_right = cell.side(True)
        self.k_left, self.t_left = cell.side(False)
        self.memo_bad: dict = {}
        self.good_memo: dict = {}

    def zone(self, j: int):
        if j - self.radius > self.k_right:
            return ("R", (j - self.k_right) % self.t_right)
        if j + self.radius < self.k_left:
            return ("L", (self.k_left - j) % self.t_left)
        return j

    def ok(self, j: int, window: dict[int, Letter]) -> bool:
        """No violation anchored at j, with the letters at j + vlo .. j + vhi."""
        key = (self.zone(j), tuple(window.get(k) for k in range(j + self.vlo, j + self.vhi + 1)))
        res = self.memo_bad.get(key)
        if res is None:
            st = self.cell.closure(j - self.radius, j + self.radius, window)
            res = self.memo_bad[key] = not self.cell.bad_at(j, st)
        return res

    # ---- the rest can be placed: on each side, a walk on a finite graph -----
    def node(self, m: int, state: tuple, right: bool):
        z = self.zone(m + 1 if right else m - 1)
        zj = self.zone(m + 1 - self.vhi if right else m - 1 - self.vlo)
        periodic = isinstance(z, tuple) and isinstance(zj, tuple) and z[0] == zj[0] == ("R" if right else "L")
        return ((z[0], (m % (self.t_right if right else self.t_left)), state) if periodic
                else (right, m, state)), periodic

    def step(self, m: int, state: tuple, right: bool):
        """The states after adding a position beyond m (on that side), in letter order."""
        w = self.w
        k = m + 1 if right else m - 1
        out = []
        for letter in self.cell.letters(k):
            if right:
                window = dict(zip(range(k - w, k + 1), (*state, letter)))
                j = k - self.vhi
                nxt = (*state, letter)[1:] if w else ()
            else:
                window = dict(zip(range(k, k + w + 1), (letter, *state)))
                j = k - self.vlo
                nxt = (letter, *state)[:-1] if w else ()
            if self.ok(j, window):
                out.append((letter, nxt))
        return k, out

    def good(self, m: int, state: tuple, right: bool) -> bool:
        """Some placement of every position beyond m (on that side) has no violation."""
        good_memo = self.good_memo
        key, periodic = self.node(m, state, right)
        if key in good_memo:
            return good_memo[key]
        if not periodic:
            k, nexts = self.step(m, state, right)
            res = any(self.good(k, nxt, right) for _, nxt in nexts)
            good_memo[key] = res
            return res
        # A finite graph of periodic nodes: good iff some infinite walk starts there.
        graph: dict = {}
        todo = [(m, state)]
        while todo:
            mm, ss = todo.pop()
            kk, _ = self.node(mm, ss, right)
            if kk in graph:
                continue
            k, nexts = self.step(mm, ss, right)
            graph[kk] = []
            for _, nxt in nexts:
                nk, nper = self.node(k, nxt, right)
                graph[kk].append(nk)
                if nper and nk not in graph and nk not in good_memo:
                    todo.append((k, nxt))
        alive = {n for n in graph}
        changed = True
        while changed:
            changed = False
            for n in list(alive):
                if not any((x in alive) or good_memo.get(x, False) for x in graph[n]):
                    alive.discard(n)
                    changed = True
        for n in graph:
            good_memo[n] = n in alive
        return good_memo[key]

    def can_finish(self, a: int, b: int, letters: dict[int, Letter]) -> bool:
        """The positions outside a..b can be placed (a > b: none is decided yet)."""
        w, vlo, vhi = self.w, self.vlo, self.vhi
        if b - a + 2 > w:
            left = tuple(letters[k] for k in range(a, a + w))
            right_state = tuple(letters[k] for k in range(b - w + 1, b + 1))
            return self.good(a, left, False) and self.good(b, right_state, True)
        # The decided block is shorter than a window: decide w - (b - a + 1)
        # more positions on the right in every way, then each side is alone.
        extra = list(range(b + 1, a + w))
        for combo in product(*(self.cell.letters(k) for k in extra)):
            trial = dict(letters)
            trial.update(zip(extra, combo))
            lo, hi = a, a + w - 1
            if all(self.ok(j, trial) for j in range(lo - vlo, hi - vhi + 1)) and self.can_finish(lo, hi, trial):
                return True
        return False

    def first(self) -> int:
        """The position closest to 0."""
        k0 = round(-self.cell.r / self.cell.g)
        return min((k0 - 1, k0, k0 + 1), key=lambda k: _order_key(self.cell.x(k)))

    def next_right(self, a: int, b: int) -> bool:
        """Whether the position after the block a..b, in the order, is on the right."""
        x = self.cell.x
        return _order_key(x(b + 1)) < _order_key(x(a - 1)) if a <= b else True

    def periodic_block(self, a: int, b: int) -> bool:
        """Both ends of the block a..b are in the periodic parts, a window away."""
        return (b - self.radius - self.w > self.k_right) and (a + self.radius + self.w < self.k_left)


def solve_infinite(cell: Cell) -> dict[int, list[Region]] | None:
    """The operands of the new anchors of the lattice, as regions per rule and
    operand; None if every assignment has a violation."""
    s, g = cell.s, cell.g
    wk = _Walk(cell)
    w, vlo, vhi = wk.w, wk.vlo, wk.vhi

    # ---- the decisions, closest to 0 first -----------------------------------
    k0 = wk.first()
    letters: dict[int, Letter] = {}
    if not wk.can_finish(k0, k0 - 1, letters):
        return None
    a, b = k0, k0 - 1
    seen_right: dict = {}
    seen_left: dict = {}
    rep_right = rep_left = None  # (first index, period) of the repetition on each side
    while rep_right is None or rep_left is None:
        right = wk.next_right(a, b)
        if (right and rep_right is not None) or (not right and rep_left is not None):
            right = not right  # that side repeats already: only the other one is left
        k = b + 1 if right else a - 1
        for letter in cell.letters(k):
            trial = dict(letters)
            trial[k] = letter
            na, nb = (a, k) if right else (k, b)
            js = [k - vhi] if right else [k - vlo]
            if not all(wk.ok(j, trial) for j in js if na <= j + vlo and j + vhi <= nb):
                continue
            if wk.can_finish(na, nb, trial):
                letters, a, b = trial, na, nb
                break
        else:
            raise AssertionError("a placement that could be finished cannot")
        if b - a + 2 > w:
            for is_right, seen in ((True, seen_right), (False, seen_left)):
                if (rep_right if is_right else rep_left) is not None or is_right != right:
                    continue
                m = b if is_right else a
                key, periodic = wk.node(m, tuple(letters[i] for i in (range(b - w + 1, b + 1) if is_right
                                                                     else range(a, a + w))), is_right)
                if not periodic:
                    continue
                if key in seen:
                    first = seen[key]
                    if is_right:
                        rep_right = (first + 1, m - first)
                    else:
                        rep_left = (first - 1, first - m)
                else:
                    seen[key] = m

    # ---- regions: the decided positions, then each side's repetition ------------
    def orbit(k: int) -> Region:
        return cell.unit.shift(k * g)

    out: dict[int, list[list[Region]]] = {ri: [[] for _ in s.rules[ri].options] for ri in cell.anys}
    for k, letter in letters.items():
        for i, ri in enumerate(cell.anys):
            if letter[i] is not None:
                out[ri][letter[i]].append(orbit(k))
    for rep, is_right in ((rep_right, True), (rep_left, False)):
        assert rep is not None
        first, period = rep
        for t in range(period):
            k = first + t if is_right else first - t
            letter = letters[k]
            for i, ri in enumerate(cell.anys):
                if letter[i] is not None:
                    tiles = (Region.tiled(orbit(k), period * g, 0, None) if is_right
                             else Region.tiled(orbit(k), period * g, None, 0))
                    out[ri][letter[i]].append(tiles)
    return {ri: [Region.union_of(*c) for c in ch] for ri, ch in out.items()}


def keep_infinite(cell: FlagCell) -> dict[str, Region]:
    """The level's claims on the lattice kept one after the other, the closest
    to 0 first (the one on the left first, then in topological order): each is
    kept if some assignment of operands has no violation with it and the ones
    kept before it, the later ones left out. Returns the claims not kept, as
    regions per kind; they repeat on each side from some position on.

    The block a..b of decided positions grows as in `solve_infinite`. Its
    *states* are the letters it can have with no violation inside it and with
    the rest placeable: every tuple of its letters while it is at most 2w long,
    then the pairs (first w letters, last w letters). The claims are kept or
    not by the states, so once the block is in the periodic parts, a state set
    coming back with the same residues makes the decisions repeat."""
    g = cell.g
    wk = _Walk(cell)
    w, vlo, vhi = wk.w, wk.vlo, wk.vhi

    def grow(states, a, b, k, kept, right):
        """The states of a..b extended at k (b + 1 or a - 1), with these claims kept there."""
        full = b - a + 1 <= 2 * w
        out = set()
        for st in states:
            for letter in cell.letters(k, kept):
                if full:
                    t = (*st, letter) if right else (letter, *st)
                    lo = a if right else k
                    j, ws = (k - vhi, k - w) if right else (k - vlo, k)
                    if ws >= lo and ws + w <= lo + len(t) - 1:
                        if not wk.ok(j, dict(zip(range(ws, ws + w + 1), t[ws - lo:ws - lo + w + 1]))):
                            continue
                    out.add(t if len(t) <= 2 * w else (t[:w], t[len(t) - w:]))
                elif right:
                    win = (*st[1], letter)
                    if wk.ok(k - vhi, dict(zip(range(k - w, k + 1), win))):
                        out.add((st[0], win[1:]))
                else:
                    win = (letter, *st[0])
                    if wk.ok(k - vlo, dict(zip(range(k, k + w + 1), win))):
                        out.add((win[:-1], st[1]))
        return out

    def finishes(st, a, b) -> bool:
        if b - a + 1 <= 2 * w:
            return wk.can_finish(a, b, dict(zip(range(a, b + 1), st)))
        return wk.good(a, st[0], False) and wk.good(b, st[1], True)

    k0 = wk.first()
    a, b = k0, k0 - 1
    states = {()}
    kept_at: dict[int, tuple[bool, ...]] = {}
    seen: dict = {}
    rep = None  # (a, b) when the repetition starts, and its period
    while rep is None:
        right = wk.next_right(a, b)
        k = b + 1 if right else a - 1
        na, nb = (a, k) if right else (k, b)
        kept = list(cell.none)
        for i, claimed in enumerate(cell.claims(k)):
            if claimed:
                kept[i] = True
                if not any(finishes(st, na, nb) for st in grow(states, a, b, k, tuple(kept), right)):
                    kept[i] = False
        states = {st for st in grow(states, a, b, k, tuple(kept), right) if finishes(st, na, nb)}
        assert states, "no placement with the claims kept"
        a, b = na, nb
        kept_at[k] = tuple(kept)
        if right and b - a + 1 > 2 * w and wk.periodic_block(a, b):
            key = ((b - wk.k_right) % wk.t_right, (wk.k_left - a) % wk.t_left, frozenset(states))
            if key in seen:
                a1, b1 = seen[key]
                rep = (a1, b1, b - b1, a1 - a)
            else:
                seen[key] = (a, b)

    a1, b1, p_right, p_left = rep
    out: dict[str, list[Region]] = {}

    def drop(k, tiles):
        for kk, c, f in zip(cell.kinds, cell.claims(k), kept_at[k]):
            if c and not f:
                out.setdefault(kk, []).append(tiles)

    for k in range(a1, b1 + 1):
        drop(k, cell.unit.shift(k * g))
    for t in range(p_right):
        drop(b1 + 1 + t, Region.tiled(cell.unit.shift((b1 + 1 + t) * g), p_right * g, 0, None))
    for t in range(p_left):
        drop(a1 - 1 - t, Region.tiled(cell.unit.shift((a1 - 1 - t) * g), p_left * g, None, 0))
    return {kk: Region.union_of(*regs) for kk, regs in out.items()}


def solve_finite(cell: Cell, ks: range, check: Callable[[dict], bool]) -> dict[tuple[int, int], int] | None:
    """Backtracking over the new anchors at r + k*g, k in ks, closest to 0
    first; `check(dec)` says whether a partial assignment has no violation yet
    (undecided anchors derive nothing, so a violation found stays).  Returns
    {(rule, k): operand}, or None if every assignment has a violation."""
    s = cell.s
    vars_ = sorted(((ri, k) for k in ks for i, ri in enumerate(cell.anys) if cell.data(k)[3][i]),
                   key=lambda v: (_order_key(cell.x(v[1])), cell.slot[v[0]]))
    dec: dict[tuple[int, int], int] = {}
    if not check(dec):
        return None
    i, tried = 0, [0] * len(vars_)
    while i < len(vars_):
        var = vars_[i]
        n = len(s.rules[var[0]].options)
        while tried[i] < n:
            dec[var] = tried[i]
            tried[i] += 1
            if check(dec):
                break
        else:
            del dec[var]
            tried[i] = 0
            i -= 1
            if i < 0:
                return None
            continue
        i += 1
    return dec
