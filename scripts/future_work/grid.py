"""The "or" choices on a grid of step h, exactly, without the restrictions R1, R2, R3
(README.md). Positions are k*h on [lo, hi]. Each anchor takes the leftmost operand
that can be chosen: a depth-first search from left to right, which prunes only on
definite violations (atoms are true, false or unknown), so it needs no monotonicity.

Covered: presence only (no percentages), literals "K" and "no K", single positions
and intervals, relative to p or l, "or" items whose operands mix p and l (R2). Not
covered: compound operands, unbounded claims. A grid is not the continuum: this is a
tool to check that the definition settles as h shrinks, not a solver for the line."""

import re
import sys
from fractions import Fraction as F
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.setrecursionlimit(100000)

from intervals import Pos  # noqa: E402
from rules import And, Atom, Item, Not, Rule, RuleError, _Lhs, _lit  # noqa: E402

_OR = re.compile(r"\bor\b")
_AND = re.compile(r"&|\band\b")


def parse_rule(text: str) -> Rule:
    """Like rules.parse_rule (flat right sides), without the restriction R2."""
    lhs_t, rhs_t = re.split(r"→|->", text)
    p = _Lhs(lhs_t)
    lhs = p.parse()
    items = [Item(tuple(_lit(o) for o in _OR.split(it))) for it in _AND.split(rhs_t)]
    return Rule(lhs, p.mode, tuple(items), text)


class _Anchor:
    def __init__(self, anchor: str) -> None:
        self.anchor = anchor


class Grid:
    def __init__(self, texts, claims, h, lo, hi, limit=200000):
        """claims: [(kind, (a, b))], K present on [a, b]."""
        self.rules = [parse_rule(t) for t in texts]
        kinds = []
        for r in self.rules:
            for k in r.lhs.kinds() + [o.kind for o in r.lits()]:
                if k not in kinds:
                    kinds.append(k)
        for k, _ in claims:
            if k not in kinds:
                kinds.append(k)
        edges = {k: set() for k in kinds}
        for r in self.rules:
            for k in r.lhs.kinds():
                edges[k] |= {o.kind for o in r.lits()}
        self.order = _topo(edges)
        self.h, self.lo = F(h), F(lo)
        self.n = int((F(hi) - F(lo)) / self.h) + 1
        self.claims = {(k, i) for k, (a, b) in claims for i in range(self.n) if a <= self.x(i) <= b}
        self.items = [(ri, j) for ri, r in enumerate(self.rules) for j, it in enumerate(r.rhs) if it.is_or]
        self.limit, self.steps = limit, 0

    def x(self, i):
        return self.lo + i * self.h

    def offsets(self, o):
        """The grid offsets (in steps) of a literal's location."""
        if o.where.is_point:
            d = o.where.lo.x / self.h
            return [int(d)] if d.denominator == 1 else []
        span = int(4 / self.h) + 2
        return [k for k in range(-span, span + 1) if o.where.lo <= Pos(k * self.h) <= o.where.hi]

    def item_anchor(self, ri, j):
        """Where an "or" chooses: at each p if one of its operands is relative to p, else
        at the left edges."""
        return "p" if any(o.anchor == "p" for o in self.rules[ri].rhs[j].options) else "l"

    def line(self, took):
        """The three-valued line of a partial assignment took {(index, item): operand}."""
        T = {}
        val = lambda k, i: T.get(k, [False] * self.n)[i] if 0 <= i < self.n else False

        def f3(f, i):
            if isinstance(f, Atom):
                v = val(f.kind, i)
                return None if v is None else f.test(F(100) if v else F(0))
            if isinstance(f, Not):
                v = f3(f.f, i)
                return None if v is None else not v
            vs = [f3(p, i) for p in f.parts]
            if isinstance(f, And):
                return False if False in vs else (None if None in vs else True)
            return True if True in vs else (None if None in vs else False)

        cache = {}

        def lhs(ri, i):
            if (ri, i) not in cache:
                cache[(ri, i)] = f3(self.rules[ri].lhs, i) if 0 <= i < self.n else False
            return cache[(ri, i)]

        def edge(ri, i):  # i starts a run of the left side
            a, b = lhs(ri, i), lhs(ri, i - 1)
            if a is False or b is True:
                return False
            return True if (a is True and b is False) else None

        def anchor(ri, o, i):
            return lhs(ri, i) if o.anchor == "p" else edge(ri, i)

        def lstart(ri, q):
            """l of the run of rule ri's left side that contains q, or None (unknown)."""
            if lhs(ri, q) is not True:
                return None
            j = q
            while True:
                v = lhs(ri, j - 1)
                if v is False:
                    return j
                if v is None:
                    return None
                j -= 1

        self.anchor, self.lstart = anchor, lstart
        for K in self.order:
            row = [(K, i) in self.claims for i in range(self.n)]

            def put(z, v):
                if 0 <= z < self.n and row[z] is not True:
                    row[z] = True if v is True else None

            for ri, r in enumerate(self.rules):
                for j, it in enumerate(r.rhs):
                    for t, o in enumerate(it.options):
                        if o.kind != K or o.negated:
                            continue
                        offs = self.offsets(o)
                        if not it.is_or:
                            for q in range(self.n):
                                a = anchor(ri, o, q)
                                if a is not False:
                                    for d in offs:
                                        put(q + d, a)
                            continue
                        n_item = self.items.index((ri, j))
                        va = self.item_anchor(ri, j)
                        for q in range(self.n):  # the choice variables of this "or"
                            a = lhs(ri, q) if va == "p" else edge(ri, q)
                            if a is False:
                                continue
                            c = took.get((q, n_item))
                            if c is not None and c != t:
                                continue
                            base = q if o.anchor == va else lstart(ri, q)  # R2: relative to l(q)
                            v = True if (a is True and c == t) else None
                            if base is None:
                                for z in range(self.n):
                                    put(z, None)
                                continue
                            for d in offs:
                                put(base + d, v)
            T[K] = row
            cache.clear()
        return T

    def bad(self, T):
        """A definite violation: "no K" where K is present."""
        for ri, r in enumerate(self.rules):
            for it in r.rhs:
                o = it.options[0]
                if it.is_or or not o.negated:
                    continue
                offs = self.offsets(o)
                for i in range(self.n):
                    if self.anchor(ri, o, i) is True:
                        for d in offs:
                            if 0 <= i + d < self.n and T[o.kind][i + d] is True:
                                return True
        return False

    def holds_chosen(self, T, took):
        """Independent check of a result: each "or" met has its chosen operand placed."""
        for (q, n_item), t in took.items():
            ri, j = self.items[n_item]
            va = self.item_anchor(ri, j)
            if self.anchor(ri, _Anchor(va), q) is not True:
                continue
            o = self.rules[ri].rhs[j].options[t]
            base = q if o.anchor == va else self.lstart(ri, q)
            for d in self.offsets(o):
                if 0 <= base + d < self.n and T[o.kind][base + d] is not True:
                    return False
        return True

    def solve(self):
        """The line {kind: [bool per grid point]}, or None (no valid assignment); raises
        TimeoutError beyond `limit` steps."""
        took = {}
        vars_ = [(i, n) for i in range(self.n) for n in range(len(self.items))]

        def dfs(v):
            self.steps += 1
            if self.steps > self.limit:
                raise TimeoutError
            T = self.line(took)
            if self.bad(T):
                return None
            if v == len(vars_):
                assert self.holds_chosen(T, took), "a chosen operand is not on the line"
                return T
            i, n = vars_[v]
            ri, j = self.items[n]
            if self.anchor(ri, _Anchor(self.item_anchor(ri, j)), i) is False:
                return dfs(v + 1)
            for t in range(len(self.rules[ri].rhs[j].options)):
                took[(i, n)] = t
                got = dfs(v + 1)
                if got is not None:
                    return got
            del took[(i, n)]
            return None
        return dfs(0)


def _topo(edges):
    indeg = {k: 0 for k in edges}
    for hs in edges.values():
        for h in hs:
            indeg[h] += 1
    ready, out = sorted(k for k, d in indeg.items() if d == 0), []
    while ready:
        k = ready.pop(0)
        out.append(k)
        for h in sorted(edges[k]):
            indeg[h] -= 1
            if indeg[h] == 0:
                ready.append(h)
    if len(out) != len(edges):
        raise RuleError("there is a cycle between kinds")
    return out


def runs(T, k, g):
    """K's grid points as intervals of x."""
    row, out, start = T.get(k, []), [], None
    for i, v in enumerate(row + [False]):
        if v and start is None:
            start = i
        if not v and start is not None:
            out.append((g.x(start), g.x(i - 1)))
            start = None
    return out
