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

    def letters(self, k: int) -> list[Letter]:
        """Every letter at k, in the order of the decisions (lexicographic, first operands first)."""
        _, _, _, new = self.data(k)
        ranges = [range(len(self.s.rules[ri].options)) if new[i] else (None,) for i, ri in enumerate(self.anys)]
        return list(product(*ranges))

    def closure(self, lo: int, hi: int, letters: dict[int, Letter]) -> dict[str, set[int]]:
        """The least model on positions lo..hi, with the given operands (none elsewhere)."""
        s = self.s
        st = {kk: {k for k in range(lo, hi + 1) if kk in self.data(k)[0]} for kk in s.order}
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
    v = None
    for kk in solver.order:
        v = _hull(v, dep[kk])
    for ri, rule in enumerate(solver.rules):
        if isinstance(rule, Forbids):
            for a, o in solver.steps[ri]:
                v = _hull(v, _shift(dep[a], o))
    return v


def solve_infinite(cell: Cell) -> dict[int, list[Region]] | None:
    """The operands of the new anchors of the lattice, as regions per rule and
    operand; None if every assignment has a violation."""
    s, g, r = cell.s, cell.g, cell.r
    v = dependency(s)
    vlo, vhi = v if v is not None else (0, 0)
    w = vhi - vlo
    reach = math.ceil(s.reach / g) if s.reach else 0
    radius = reach + max(abs(vlo), abs(vhi)) + max((abs(o) for st in s.steps for _, o in st), default=0) + 1

    # Beyond these indices every datum is periodic in k with these periods.
    regions = [*cell.base.values(), *cell.nb.values(), *cell.acc.anchor.values(),
               *(c for ch in cell.acc.choice.values() for c in ch), *cell.cand.values()]

    def side(right: bool):
        thr, period = None, 1
        for reg in regions:
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

    k_right, t_right = side(True)
    k_left, t_left = side(False)

    def zone(j: int):
        if j - radius > k_right:
            return ("R", (j - k_right) % t_right)
        if j + radius < k_left:
            return ("L", (k_left - j) % t_left)
        return j

    memo_bad: dict = {}

    def ok(j: int, window: dict[int, Letter]) -> bool:
        """No violation anchored at j, with the operands at j + vlo .. j + vhi."""
        key = (zone(j), tuple(window.get(k) for k in range(j + vlo, j + vhi + 1)))
        res = memo_bad.get(key)
        if res is None:
            st = cell.closure(j - radius, j + radius, window)
            res = memo_bad[key] = not cell.bad_at(j, st)
        return res

    # ---- the rest can be placed: on each side, a walk on a finite graph -----
    good_memo: dict = {}

    def node(m: int, state: tuple, right: bool):
        z = zone(m + 1 if right else m - 1)
        zj = zone(m + 1 - vhi if right else m - 1 - vlo)
        periodic = isinstance(z, tuple) and isinstance(zj, tuple) and z[0] == zj[0] == ("R" if right else "L")
        return ((z[0], (m % (t_right if right else t_left)), state) if periodic else (right, m, state)), periodic

    def step(m: int, state: tuple, right: bool):
        """The states after adding a position beyond m (on that side), in letter order."""
        k = m + 1 if right else m - 1
        out = []
        for letter in cell.letters(k):
            if right:
                window = dict(zip(range(k - w, k + 1), (*state, letter)))
                j = k - vhi
                nxt = (*state, letter)[1:] if w else ()
            else:
                window = dict(zip(range(k, k + w + 1), (letter, *state)))
                j = k - vlo
                nxt = (letter, *state)[:-1] if w else ()
            if ok(j, window):
                out.append((letter, nxt))
        return k, out

    def good(m: int, state: tuple, right: bool) -> bool:
        """Some placement of every position beyond m (on that side) has no violation."""
        key, periodic = node(m, state, right)
        if key in good_memo:
            return good_memo[key]
        if not periodic:
            k, nexts = step(m, state, right)
            res = any(good(k, nxt, right) for _, nxt in nexts)
            good_memo[key] = res
            return res
        # A finite graph of periodic nodes: good iff some infinite walk starts there.
        graph: dict = {}
        todo = [(m, state)]
        while todo:
            mm, ss = todo.pop()
            kk, _ = node(mm, ss, right)
            if kk in graph:
                continue
            k, nexts = step(mm, ss, right)
            graph[kk] = []
            for _, nxt in nexts:
                nk, nper = node(k, nxt, right)
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

    def can_finish(a: int, b: int, letters: dict[int, Letter]) -> bool:
        """The positions outside a..b can be placed (a > b: none is decided yet)."""
        if b - a + 2 > w:
            left = tuple(letters[k] for k in range(a, a + w))
            right_state = tuple(letters[k] for k in range(b - w + 1, b + 1))
            return good(a, left, False) and good(b, right_state, True)
        # The decided block is shorter than a window: decide w - (b - a + 1)
        # more positions on the right in every way, then each side is alone.
        extra = list(range(b + 1, a + w))
        for combo in product(*(cell.letters(k) for k in extra)):
            trial = dict(letters)
            trial.update(zip(extra, combo))
            lo, hi = a, a + w - 1
            if all(ok(j, trial) for j in range(lo - vlo, hi - vhi + 1)) and can_finish(lo, hi, trial):
                return True
        return False

    # ---- the decisions, closest to 0 first -----------------------------------
    k0 = round(-r / g)
    k0 = min((k0 - 1, k0, k0 + 1), key=lambda k: _order_key(cell.x(k)))
    letters: dict[int, Letter] = {}
    if not can_finish(k0, k0 - 1, letters):
        return None
    a, b = k0, k0 - 1
    seen_right: dict = {}
    seen_left: dict = {}
    rep_right = rep_left = None  # (first index, period) of the repetition on each side
    while rep_right is None or rep_left is None:
        right = _order_key(cell.x(b + 1)) < _order_key(cell.x(a - 1)) if a <= b else True
        if (right and rep_right is not None) or (not right and rep_left is not None):
            right = not right  # that side repeats already: only the other one is left
        k = b + 1 if right else a - 1
        for letter in cell.letters(k):
            trial = dict(letters)
            trial[k] = letter
            na, nb = (a, k) if right else (k, b)
            js = [k - vhi] if right else [k - vlo]
            if not all(ok(j, trial) for j in js if na <= j + vlo and j + vhi <= nb):
                continue
            if can_finish(na, nb, trial):
                letters, a, b = trial, na, nb
                break
        else:
            raise AssertionError("a placement that could be finished cannot")
        if b - a + 2 > w:
            for is_right, seen in ((True, seen_right), (False, seen_left)):
                if (rep_right if is_right else rep_left) is not None or is_right != right:
                    continue
                m = b if is_right else a
                key, periodic = node(m, tuple(letters[i] for i in (range(b - w + 1, b + 1) if is_right
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
