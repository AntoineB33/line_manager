"""Element rules and mode rules.

An element rule is  L → R:
- L, the left side: a boolean expression on element kinds and percentages, met at a
  position p or not. An atom is  K  (K present, i.e. above 0%), K p%  (exactly p; K 0%
  means K absent),  K >= p%,  K > p%,  K <= p%,  K < p%  (an absent K counts as 0%),
  combined with  not / no,  and / &,  or / |  and parentheses.
- R, the right side: items, all applied. An item is
    a literal                 K [p%] | no K          at a location
    an ordered disjunction    K1 or K2 or ...        (the leftmost operand that can be chosen)
  A location is relative to an anchor: "at d", "d units on the left/right" or
  "on [a, b)" (default: at 0), then "of p" or "of l". p is a position where L is met;
  l is the left edge of the largest interval where L is met that contains p (a
  largest interval unbounded on the left has no left edge: nothing is placed for it).
  Without "of", a single position is relative to p and an interval to l. "K" requires
  K present (a consequence gets 100%), "K p%" requires exactly p, "no K" and "K 0%"
  require K absent. Positions are x + kε (intervals.py): "(0, 1] of l" is [l+ε, l+1].

A mode rule is  mode m [& L] → R : the same, met at the chip's position when its mode
is m; it can add or remove elements only at the chip's position, so all its locations
are "at 0 of p".

Kinds may place each other in a cycle (A → A 1 unit on the right). RuleSet groups the
kinds that do (strongly connected through "a left side reads K, its rule places K'
present"), each group after the groups it reads: solver.py computes a group with a
cycle as a fixpoint. A "no K" places nothing, so it makes no cycle; nor does a mode
rule, which acts once, at the chip, and is not kept true. A cycle "moves" when one of
its literals is not "at 0 of p": then what it places can go on without end.

Restrictions, checked by RuleSet:
- the operands of an "or" are all relative to p or all relative to l;
- a kind is "chosen" if it is a present operand of an "or" or is placed by a rule whose
  left side reads a chosen kind. A left side reads chosen kinds only as "present"
  (K, K p% with p > 0, K >= p%, K > p%), never under "not": choosing more never removes
  a consequence. Then:
  - if those rules read chosen kinds in a conjunction  K1 & ... & Kn & L'  and place
    single positions relative to p, and the operands of the "or"s are present elements
    at single positions, the choices are solved on lattices (solver.py, `lattice`),
    unless chosen kinds place each other in a cycle that moves, or that a left side
    reads as absent (one choice would then act without end);
  - otherwise they are solved by a sweep from left to right (sweep.py), and a chosen
    kind that a left side reads is only placed at or after the position where it is
    decided (at an offset >= 0 from p or l): what is left of the sweep never changes.

Syntax: "→" or "->"; items joined by "&" or "and"; operands joined by "or"; an
optional leading "if" and the words "element"/"then" are ignored:
    A → B 100%                  A & B → C 45% 3 units on the left of p
    A → no B                    A → B 1% 1 unit on the right of l
    A → B or C                  A & not C → B 50% on [0, 2) of l
    A >= 50% | D → E            mode 1 → D 100% & E 100%
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction

from intervals import Interval, parse_interval


# ---------------------------------------------------------------- left sides

_TESTS = {"present": lambda v, p: v > 0, "=": lambda v, p: v == p, "<": lambda v, p: v < p, "<=": lambda v, p: v <= p,
          ">": lambda v, p: v > p, ">=": lambda v, p: v >= p}


@dataclass(frozen=True)
class Atom:
    kind: str
    op: str = "present"
    value: Fraction | None = None

    def test(self, v: Fraction) -> bool:
        return _TESTS[self.op](v, self.value)

    def kinds(self) -> list[str]:
        return [self.kind]

    def region(self, vals, universe, empty):
        out = empty
        for v, r in vals.get(self.kind, {}).items():
            if self.test(v):
                out = out | r
        if self.test(Fraction(0)):
            pres = empty
            for r in vals.get(self.kind, {}).values():
                pres = pres | r
            out = out | (universe - pres)
        return out

    def region2(self, pos, neg, universe, empty):
        """`region` read through two views (values, presence), each kind -> ...: the
        present part from `pos`, the absent part from `neg`. Grows with `pos`, shrinks
        with `neg`; with the same view twice, it is `region`."""
        out = empty
        for v, r in pos[0].get(self.kind, {}).items():
            if v and self.test(v):
                out = out | r
        if self.test(Fraction(0)):
            out = out | (universe - neg[1].get(self.kind, empty))
        return out

    def holds(self, value_of) -> bool:
        return self.test(value_of(self.kind))

    def __str__(self) -> str:
        if self.op == "present":
            return self.kind
        return f"{self.kind} {_fmt(self.value)}%" if self.op == "=" else f"{self.kind} {self.op} {_fmt(self.value)}%"


@dataclass(frozen=True)
class Not:
    f: object

    def kinds(self) -> list[str]:
        return self.f.kinds()

    def region(self, vals, universe, empty):
        return universe - self.f.region(vals, universe, empty)

    def region2(self, pos, neg, universe, empty):
        return universe - self.f.region2(neg, pos, universe, empty)

    def holds(self, value_of) -> bool:
        return not self.f.holds(value_of)

    def __str__(self) -> str:
        return f"not {_paren(self.f)}"


@dataclass(frozen=True)
class And:
    parts: tuple

    def kinds(self) -> list[str]:
        return list(dict.fromkeys(k for p in self.parts for k in p.kinds()))

    def region(self, vals, universe, empty):
        out = universe
        for p in self.parts:
            out = out & p.region(vals, universe, empty)
        return out

    def region2(self, pos, neg, universe, empty):
        out = universe
        for p in self.parts:
            out = out & p.region2(pos, neg, universe, empty)
        return out

    def holds(self, value_of) -> bool:
        return all(p.holds(value_of) for p in self.parts)

    def __str__(self) -> str:
        return " & ".join(_paren(p) for p in self.parts)


@dataclass(frozen=True)
class Or:
    parts: tuple

    def kinds(self) -> list[str]:
        return list(dict.fromkeys(k for p in self.parts for k in p.kinds()))

    def region(self, vals, universe, empty):
        out = empty
        for p in self.parts:
            out = out | p.region(vals, universe, empty)
        return out

    def region2(self, pos, neg, universe, empty):
        out = empty
        for p in self.parts:
            out = out | p.region2(pos, neg, universe, empty)
        return out

    def holds(self, value_of) -> bool:
        return any(p.holds(value_of) for p in self.parts)

    def __str__(self) -> str:
        return " | ".join(_paren(p) for p in self.parts)


TRUE = And(())


def _paren(f) -> str:
    return f"({f})" if isinstance(f, (And, Or)) and len(f.parts) > 1 else str(f)


def _fmt(x: Fraction) -> str:
    return str(x.numerator) if x.denominator == 1 else str(x)


def conjuncts(f) -> tuple:
    return f.parts if isinstance(f, And) else (f,)


# ---------------------------------------------------------------- right sides

@dataclass(frozen=True)
class Lit:
    kind: str
    where: Interval  # relative to the anchor
    anchor: str = "p"  # "p" or "l"
    value: Fraction | None = None  # exact percentage, or None: present (100% if added)
    negated: bool = False

    @property
    def is_point(self) -> bool:
        return self.where.is_point

    @property
    def rel(self):
        """The offset from the anchor (a position), for a single position."""
        return self.where.lo if self.where.is_point else None

    def __str__(self) -> str:
        s = ("no " if self.negated else "") + self.kind + (f" {_fmt(self.value)}%" if self.value is not None else "")
        if not self.is_point:
            return s + f" on {self.where} of {self.anchor}"
        d = self.rel.x
        if d:
            s += f" {_fmt(abs(d))} unit{'s' if abs(d) != 1 else ''} on the {'right' if d > 0 else 'left'}"
        return s + (f" of {self.anchor}" if d or self.anchor != "p" else "")


@dataclass(frozen=True)
class Item:
    options: tuple[Lit, ...]
    text: str = ""  # how it is written, when an operand is a hidden kind

    @property
    def is_or(self) -> bool:
        return len(self.options) > 1

    def __str__(self) -> str:
        return self.text or " or ".join(map(str, self.options))


HIDDEN = "§"  # prefix of the hidden kinds that stand for an operand "B & C" of an "or"


def is_hidden(kind: str) -> bool:
    return kind.startswith(HIDDEN)


@dataclass(frozen=True)
class Rule:
    lhs: object  # a formula (TRUE: a mode rule with no condition on elements)
    mode: str | None
    rhs: tuple[Item, ...]
    text: str = ""
    written: tuple[Lit, ...] = ()  # every literal as written, in order (when hidden kinds stand for some)
    internal: tuple = ()  # the rules of its hidden kinds: §(B & C) → B & C

    @property
    def is_mode_rule(self) -> bool:
        return self.mode is not None

    def lits(self) -> list[Lit]:
        return [o for it in self.rhs for o in it.options]

    def written_lits(self) -> list[Lit]:
        return list(self.written) or self.lits()

    def __str__(self) -> str:
        left = ([f"mode {self.mode}"] if self.mode is not None else []) + ([str(self.lhs)] if conjuncts(self.lhs) else [])
        return " & ".join(left) + " → " + " & ".join(map(str, self.rhs))


class RuleError(ValueError):
    pass


class RuleSet:
    def __init__(self, rules: list[Rule]) -> None:
        # the rules of the hidden kinds come after the written ones (one per hidden kind)
        internal = {ir.lhs.kind: ir for r in rules for ir in r.internal}
        self.rules = list(rules) + list(internal.values())
        self.element = [r for r in self.rules if not r.is_mode_rule]
        self.mode_rules = [r for r in self.rules if r.is_mode_rule]
        # kinds in order of first appearance in the text of the rules (tie-breaks)
        rank: dict[str, int] = {}
        for r in self.rules:
            for k in r.lhs.kinds() + [o.kind for o in r.written_lits()]:
                rank.setdefault(k, len(rank))
        self.rank = rank
        for r in self.rules:
            for it in r.rhs:
                if it.is_or and len({o.anchor for o in it.options}) > 1:
                    raise RuleError(f"{r}: the operands of an 'or' are all relative to p or all relative to l")
            if r.is_mode_rule and any(o.anchor != "p" or o.where != Interval.point(0) for o in r.lits() + r.written_lits()):
                raise RuleError(f"{r}: a mode rule can only act at the position of the chip")
            if not r.is_mode_rule and not r.lhs.kinds():
                raise RuleError(f"{r}: an element rule needs a kind on its left side")
        # the kinds in groups that place each other (strongly connected through the element
        # rules: a left side reads K, its rule places K' present), each group after those
        # it reads; a group with a cycle is computed as a fixpoint (solver.py)
        edges: dict[str, set[str]] = {k: set() for k in rank}
        places: dict[str, set[str]] = {k: set() for k in rank}
        for r in self.element:
            for k in r.lhs.kinds():
                edges[k] |= {o.kind for o in r.lits()}
                places[k] |= {o.kind for o in r.lits() if not o.negated}
        self.edges = edges
        self.blocks = _groups(places)
        self.order = [k for b in self.blocks for k in b]
        self.block_of = {k: n for n, b in enumerate(self.blocks) for k in b}
        self.cyclic = {k for b in self.blocks for k in b if len(b) > 1 or k in places[k]}
        # a cycle that moves positions: one of its literals is not "at 0 of p", so what it
        # places can go on without end (A → A 1 unit on the right)
        self.moving = set()
        for r in self.element:
            for o in r.lits():
                if not o.negated and o.kind in self.cyclic and (o.anchor != "p" or o.where != Interval.point(0)) \
                        and any(self.block_of.get(k) == self.block_of[o.kind] for k in r.lhs.kinds()):
                    self.moving |= set(self.blocks[self.block_of[o.kind]])
        # chosen kinds: those an "or" choice can change
        chosen = {o.kind for r in self.element for it in r.rhs if it.is_or for o in it.options if not o.negated}
        dep, todo = set(), list(chosen)
        while todo:
            k = todo.pop()
            if k not in dep:
                dep.add(k)
                todo += [o.kind for r in self.element if k in r.lhs.kinds() for o in r.lits() if not o.negated]
        self.choice_dependent = dep
        # each element rule's left side as (chosen kinds that must be present, chosen kinds
        # that must be absent, the rest), and which solver the choices need
        lattice = all(not o.negated and o.is_point for r in self.element for it in r.rhs if it.is_or for o in it.options)
        self.split: list[tuple[tuple[str, ...], tuple[str, ...], object]] = []
        absent_read = []  # the rules that read a chosen kind as absent: the lattices only
        for r in self.element:
            read = set(r.lhs.kinds()) & dep
            if read and not _monotone(r.lhs, dep):
                absent_read.append((r, read))
            cd, ncd, rest = [], [], []
            for f in conjuncts(r.lhs):
                if isinstance(f, Atom) and f.kind in dep and f.op == "present":
                    cd.append(f.kind)
                elif isinstance(f, Not) and isinstance(f.f, Atom) and f.f.kind in dep and f.f.op == "present":
                    ncd.append(f.f.kind)
                elif isinstance(f, Atom) and f.kind in dep and f.op == "=" and f.value == 0:  # K 0%: K absent
                    ncd.append(f.kind)
                elif set(f.kinds()) & dep:
                    lattice = False
                else:
                    rest.append(f)
            if read and any(o.anchor != "p" or not o.is_point for o in r.lits()):
                lattice = False
            self.split.append((tuple(dict.fromkeys(cd)), tuple(dict.fromkeys(ncd)), And(tuple(rest))))
        # a cycle through chosen kinds: on lattices only if it stays at one position and
        # reads them as present (a choice then places a fixed set of points); otherwise
        # what one choice places can go on without end, and the sweep decides
        if dep & self.cyclic and (dep & self.moving or absent_read):
            lattice = False
        self.lattice = lattice
        if absent_read and not lattice:
            r, read = absent_read[0]
            raise RuleError(f"{r}: a kind an 'or' can change ({', '.join(sorted(read))}) can only be required present "
                            "(not under 'not', not below a percentage) when the choices need intervals or left edges, "
                            "or when those kinds place each other in a cycle")
        # causal: a chosen kind that a left side reads is placed at or after the position
        # where it is decided (needed by the sweep)
        read_kinds = {k for r in self.element for k in r.lhs.kinds()}
        self.causal = True
        for r in self.element:
            decided = set(r.lhs.kinds()) & dep
            for it in r.rhs:
                for o in it.options:
                    if (it.is_or or decided) and not o.negated and o.kind in dep and o.kind in read_kinds and o.where.lo < 0:
                        self.causal = False
                        if not lattice:
                            raise RuleError(f"{r}: {o.kind} is read by a left side and an 'or' can change it, so it is placed "
                                            "at or after the position where it is decided (an offset >= 0)")
        # the "or" items, in the order they are decided at one position: an item whose
        # left side reads what another item can place comes after it, else rule order
        items = [(i, j) for i, r in enumerate(self.element) for j, it in enumerate(r.rhs) if it.is_or]
        below = {}
        for i, j in items:
            out, todo = set(), [o.kind for o in self.element[i].rhs[j].options]
            while todo:
                k = todo.pop()
                if k not in out:
                    out.add(k)
                    todo += list(edges.get(k, ()))
            below[(i, j)] = out
        self.or_items: list[tuple[int, int]] = []
        while len(self.or_items) < len(items):
            rest = [a for a in items if a not in self.or_items]
            # "or"s that place what the other reads (a cycle): the first in rule order
            ready = [a for a in rest if not any(b != a and set(self.element[a[0]].lhs.kinds()) & below[b] and
                                                 not set(self.element[b[0]].lhs.kinds()) & below[a] for b in rest)]
            self.or_items.append((ready or rest)[0])
        self.or_rank = {a: n for n, a in enumerate(self.or_items)}
        ends = [abs(e.x) for r in self.rules for o in r.lits() for e in (o.where.lo, o.where.hi) if e not in (math.inf, -math.inf)]
        self.reach = max(ends, default=Fraction(0))
        self.ruled = set(self.order) | {k for r in self.rules for k in r.lhs.kinds()} | {o.kind for r in self.rules for o in r.lits()}
        # more elements can only add contradictions: left sides read presence, and no
        # position depends on where an interval starts
        self.monotone = all(_reads_presence(r.lhs) and all(o.anchor == "p" for o in r.lits()) for r in self.element)
        # how far from the ε part of what it starts from a chain of rules can place: an open
        # end adds ±1, and a chain goes through each kind once (a cycle: `moving`, `drifts`)
        opened = [1 for r in self.rules for o in r.lits() for e in (o.where.lo, o.where.hi) if e not in (math.inf, -math.inf) and e.k]
        self.eps_reach = 1 + min(max(len(opened), 1), len(self.order) + 1)
        # a cycle with an open end moves ε parts at each turn: no bound
        self.drifts = any(o.kind in self.moving and self.block_of.get(k) == self.block_of[o.kind] and
                          any(e not in (math.inf, -math.inf) and e.k for e in (o.where.lo, o.where.hi))
                          for r in self.element for o in r.lits() for k in r.lhs.kinds())
        sizes = [e for e in ends if e] + [o.where.length for r in self.rules for o in r.lits() if 0 < o.where.length < math.inf]
        self.smallest_offset = min(sizes, default=None)

    def kinds(self) -> list[str]:
        return list(self.order)


def _groups(edges: dict[str, set[str]]) -> list[tuple[str, ...]]:
    """The strongly connected groups of this graph, each after the groups with an edge to
    it, in alphabetical order when nothing else decides (Tarjan, then Kahn)."""
    index, low, stack, on, comps = {}, {}, [], set(), []
    for root in sorted(edges):
        if root in index:
            continue
        work = [(root, iter(sorted(edges[root])))]
        index[root] = low[root] = len(index)
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            w = next(it, None)
            if w is not None:
                if w not in index:
                    index[w] = low[w] = len(index)
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(sorted(edges[w]))))
                elif w in on:
                    low[v] = min(low[v], index[w])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                comps.append(tuple(sorted(comp)))
    of = {k: c for c in comps for k in c}
    succ = {c: {of[h] for k in c for h in edges[k]} - {c} for c in comps}
    indeg = {c: 0 for c in comps}
    for hs in succ.values():
        for h in hs:
            indeg[h] += 1
    ready = sorted(c for c, d in indeg.items() if d == 0)
    out = []
    while ready:
        c = ready.pop(0)
        out.append(c)
        for h in sorted(succ[c]):
            indeg[h] -= 1
            if indeg[h] == 0:
                ready.append(h)
        ready.sort()
    return out


def _monotone(f, kinds: set) -> bool:
    """f only grows when these kinds get present: they are read as present, never under not."""
    if isinstance(f, Atom):
        return f.kind not in kinds or not f.test(Fraction(0))
    if isinstance(f, Not):
        return not set(f.kinds()) & kinds
    return all(_monotone(p, kinds) for p in f.parts)


def _reads_presence(f) -> bool:
    if isinstance(f, Atom):
        return f.op == "present"
    return isinstance(f, (And, Or)) and all(_reads_presence(p) for p in f.parts)


def lattice_step(lits) -> Fraction | None:
    """gcd of the nonzero offsets of these single-position literals."""
    g = None
    for o in lits:
        if o.rel:
            d = abs(o.rel.x)
            g = d if g is None else _fgcd(g, d)
    return g


def _fgcd(a: Fraction, b: Fraction) -> Fraction:
    d = a.denominator * b.denominator // math.gcd(a.denominator, b.denominator)
    return Fraction(math.gcd(int(a * d), int(b * d)), d)


# ---------------------------------------------------------------- parsing

_KEYWORDS = {"mode", "no", "not", "at", "on", "of", "units", "unit", "or", "and", "if", "then", "element", "elements"}
_NUM = r"[-+]?\d+(?:/\d+|\.\d+)?"


def _kind(tok: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok) or tok in _KEYWORDS:
        raise RuleError(f"bad element kind {tok!r}")
    return tok


def _pct(s: str, allow_zero: bool) -> Fraction:
    v = Fraction(s)
    if not (0 <= v <= 100 if allow_zero else 0 < v <= 100):
        raise RuleError(f"percentage {v} not in {'[0' if allow_zero else '(0'}, 100]")
    return v


_TOKEN = re.compile(rf"\s*(>=|<=|≥|≤|[()&|!<>=]|{_NUM}\s*%|[A-Za-z_][A-Za-z0-9_]*|[^\s()&|!<>=]+)")


def _tokens(text: str) -> list[str]:
    out, i = [], 0
    text = text.strip()
    while i < len(text):
        m = _TOKEN.match(text, i)
        out.append(m.group(1))
        i = m.end()
        while i < len(text) and text[i].isspace():
            i += 1
    return [t for t in out if t not in ("element", "elements")]


class _Lhs:
    """expr := conj (or conj)* ; conj := unary (and unary)* ;
    unary := not unary | ( expr ) | mode NAME | KIND [cmp] [p%]"""

    def __init__(self, text: str) -> None:
        self.t, self.i, self.mode = _tokens(text), 0, None

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self):
        tok = self.peek()
        if tok is None:
            raise RuleError("the left side ends too early")
        self.i += 1
        return tok

    def parse(self):
        f = self.expr(True)
        if self.peek() is not None:
            raise RuleError(f"unexpected {self.peek()!r} in the left side")
        return f

    def expr(self, top=False):
        parts = [self.conj(top)]
        while self.peek() in ("or", "|"):
            self.next()
            parts.append(self.conj(False))
        if len(parts) > 1 and top and self.mode is not None:
            raise RuleError("'mode m' is a condition of the whole left side")
        return parts[0] if len(parts) == 1 else Or(tuple(parts))

    def conj(self, top):
        parts = []
        while True:
            u = self.unary(top)
            if u is not None:
                parts.append(u)
            if self.peek() not in ("and", "&"):
                break
            self.next()
        if not parts:
            return TRUE
        return parts[0] if len(parts) == 1 else And(tuple(parts))

    def unary(self, top):
        tok = self.next()
        if tok in ("not", "no", "!"):
            return Not(self.unary(False))
        if tok == "(":
            f = self.expr()
            if self.next() != ")":
                raise RuleError("missing ')'")
            return f
        if tok == "mode":
            if not top or self.mode is not None:
                raise RuleError("'mode m' appears once, as a condition of the whole left side")
            self.mode = self.next()
            return None
        kind = _kind(tok)
        op = "present"
        if self.peek() in (">=", "<=", ">", "<", "=", "≥", "≤"):
            op = {"≥": ">=", "≤": "<="}.get(self.next(), self.t[self.i - 1])
        if self.peek() is not None and self.peek().endswith("%"):
            v = _pct(self.next()[:-1].strip(), True)
            return Atom(kind, "=" if op == "present" else op, v)
        if op != "present":
            raise RuleError(f"{kind} {op} needs a percentage")
        return Atom(kind)


def _lit(text: str) -> Lit:
    t = re.sub(r"\belements?\b", " ", text).strip()
    negated = False
    m = re.match(r"no\s+(.*)$", t)
    if m:
        negated, t = True, m.group(1)
    m = re.match(r"(\w+)\s*(.*)$", t)
    if not m:
        raise RuleError(f"bad literal {text!r}")
    kind, rest = _kind(m.group(1)), m.group(2).strip()
    value = None
    m = re.match(rf"({_NUM})\s*%\s*(.*)$", rest)
    if m:
        if negated:
            raise RuleError("'no' takes no percentage")
        value = _pct(m.group(1), True)
        rest = m.group(2).strip()
        if value == 0:  # "B 0%" is "no B": an element with 0% is absent
            negated, value = True, None
    anchor = None
    m = re.match(r"(.*?)\s*\bof\s+(p|l)$", rest)
    if m:
        rest, anchor = m.group(1).strip(), m.group(2)
    where = Interval.point(0)
    if rest:
        m1 = re.fullmatch(rf"at\s+({_NUM})", rest)
        m2 = re.fullmatch(r"on\s+([\[({].+)", rest)
        m3 = re.fullmatch(rf"({_NUM})\s+units?\s+(?:on\s+|to\s+)?(?:the\s+)?(left|right)", rest)
        if m1:
            where = Interval.point(Fraction(m1.group(1)))
        elif m2:
            where = parse_interval(m2.group(1))
        elif m3:
            d = Fraction(m3.group(1))
            where = Interval.point(-d if m3.group(2) == "left" else d)
        else:
            raise RuleError(f"bad location {rest!r}")
    if anchor is None:
        anchor = "p" if where.is_point else "l"
    return Lit(kind, where, anchor, value, negated)


_INTERVAL = re.compile(r"\bon\s*[\[({][^\])}]*[\])}]")


class _Rhs:
    """expr := ors (& ors)* ; ors := unary (or unary)* ; unary := ( expr ) | literal.
    "&" separates the items and "or" binds tighter: "B or C & D" is (B or C) & D;
    parentheses group anything: "(B & C) or D". The parentheses of an interval
    ("on (0, 1] of l") belong to its literal."""

    def __init__(self, text: str) -> None:
        saved: list[str] = []

        def keep(m):
            saved.append(m.group(0))
            return f"\x00{len(saved) - 1}\x00"
        t = _INTERVAL.sub(keep, text)
        self.t = [re.sub(r"\x00(\d+)\x00", lambda m: saved[int(m.group(1))], s).strip()
                  for s in re.split(r"(\(|\)|&|\band\b|\bor\b)", t)]
        self.t = [s for s in self.t if s]
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else None

    def next(self):
        tok = self.peek()
        if tok is None:
            raise RuleError("the right side ends too early")
        self.i += 1
        return tok

    def parse(self):
        node = self.expr()
        if self.peek() is not None:
            raise RuleError(f"unexpected {self.peek()!r} in the right side")
        return node

    def expr(self):
        parts = [self.ors()]
        while self.peek() in ("&", "and"):
            self.next()
            parts.append(self.ors())
        return parts[0] if len(parts) == 1 else ("and", parts)

    def ors(self):
        parts = [self.unary()]
        while self.peek() == "or":
            self.next()
            parts.append(self.unary())
        return parts[0] if len(parts) == 1 else ("or", parts)

    def unary(self):
        tok = self.next()
        if tok == "(":
            node = self.expr()
            if self.next() != ")":
                raise RuleError("missing ')' in the right side")
            return node
        if tok in (")", "&", "and", "or"):
            raise RuleError(f"unexpected {tok!r} in the right side")
        return ("lit", _lit(tok))


def _conjuncts(node) -> list:
    return [c for p in node[1] for c in _conjuncts(p)] if node[0] == "and" else [node]


def _operands(node) -> list[tuple[Lit, ...]]:
    """The operands of an "or", in the order they are tried: each "or" takes its leftmost
    operand that can be chosen, the left ones first, so ((B or C) & D) or E tries B & D,
    C & D, then E."""
    if node[0] == "lit":
        return [(node[1],)]
    if node[0] == "or":
        return [o for p in node[1] for o in _operands(p)]
    out = [()]
    for p in node[1]:
        out = [a + b for a in out for b in _operands(p)]
    return out


def _shown(node, inner=False) -> str:
    if node[0] == "lit":
        return str(node[1])
    s = (" or " if node[0] == "or" else " & ").join(_shown(p, True) for p in node[1])
    return f"({s})" if inner else s


def _lits_of(node) -> list[Lit]:
    return [node[1]] if node[0] == "lit" else [o for p in node[1] for o in _lits_of(p)]


def parse_rule(text: str) -> Rule:
    t = text.strip()
    t = re.sub(r"^if\s+", "", t)
    t = re.sub(r",?\s*then\s+", " → ", t) if "→" not in t and "->" not in t else t
    parts = re.split(r"→|->", t)
    if len(parts) != 2:
        raise RuleError(f"a rule has one arrow: {text!r}")
    p = _Lhs(parts[0])
    lhs = p.parse()
    tree = _Rhs(parts[1]).parse()
    items, internal = [], []
    for node in _conjuncts(tree):
        if node[0] == "lit":
            items.append(Item((node[1],)))
            continue
        lits = _lits_of(node)
        if len({o.anchor for o in lits}) > 1:
            raise RuleError(f"{text}: the operands of an 'or' are all relative to p or all relative to l")
        anchor = lits[0].anchor
        opts, hidden = [], False
        for conj in _operands(node):
            if len(conj) == 1:
                opts.append(conj[0])
                continue
            # the operand "B & C" is the hidden kind §(B & C), placed where the operand
            # goes, whose rule places B and C (relative to it: at l, it is l itself)
            name = HIDDEN + "(" + " & ".join(map(str, conj)) + ")"
            opts.append(Lit(name, Interval.point(0), anchor))
            inner = tuple(Item((Lit(o.kind, o.where, "p", o.value, o.negated),)) for o in conj)
            internal.append(Rule(Atom(name), None, inner, f"{name} → " + " & ".join(map(str, conj))))
            hidden = True
        items.append(Item(tuple(opts), _shown(node) if hidden else ""))
    written = tuple(_lits_of(tree)) if internal else ()
    return Rule(lhs, p.mode, tuple(items), text, written, tuple(internal))


def parse_rules(texts: list[str]) -> RuleSet:
    return RuleSet([parse_rule(t) for t in texts])
