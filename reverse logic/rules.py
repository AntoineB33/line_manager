"""Rules: pointwise, translation-invariant constraints between element kinds.

Every rule is quantified over all anchors x in R.  An atom (K, o) means
"element K is present at x + o".

    Implies(P1..Pn, H)        P1(x+o1) and ... and Pn(x+on)  ->  H(x+oh)
    Forbids(P1..Pn, H)        P1(x+o1) and ... and Pn(x+on)  ->  not H(x+oh)
    ImpliesAny(P1..Pn, O1..Om)
                              P1 ... Pn  ->  O1 or ... or Om, ordered: the
                              first operand that is not blocked is used.

Restriction (from the requirements): the graph "premise kind -> produced
kind" of Implies / ImpliesAny rules must be acyclic.  Forbids rules are
negative clauses (not(P and H)) and are symmetric, so they have no direction
and cannot form a production cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from region import EMPTY, FULL, Region, num

Atom = tuple[str, Fraction]


class RuleCycleError(ValueError):
    pass


def _atom(spec) -> Atom:
    if isinstance(spec, str):
        return (spec, Fraction(0))
    kind, off = spec
    if not isinstance(kind, str):
        raise TypeError(f"element kind must be a str, got {kind!r}")
    return (kind, num(off))


def _atoms(spec) -> tuple[Atom, ...]:
    if isinstance(spec, str) or (isinstance(spec, tuple) and len(spec) == 2 and isinstance(spec[0], str)
                                 and not isinstance(spec[1], str)):
        return (_atom(spec),)
    atoms = tuple(_atom(s) for s in spec)
    if not atoms:
        raise ValueError("a rule needs at least one atom here")
    return atoms


@dataclass(frozen=True)
class Implies:
    premises: tuple[Atom, ...]
    head: Atom


@dataclass(frozen=True)
class Forbids:
    premises: tuple[Atom, ...]
    head: Atom


@dataclass(frozen=True)
class ImpliesAny:
    premises: tuple[Atom, ...]
    options: tuple[Atom, ...]


Rule = Implies | Forbids | ImpliesAny


def implies(when, then) -> Implies:
    """implies("A", "B"); implies(["A", "B"], ("C", -3))."""
    return Implies(_atoms(when), _atom(then))


def forbids(when, then) -> Forbids:
    """forbids("D", "C"): wherever D is, C is not."""
    return Forbids(_atoms(when), _atom(then))


def implies_any(when, options) -> ImpliesAny:
    """implies_any("A", ["B", "C"]): A -> B or C, B preferred."""
    return ImpliesAny(_atoms(when), _atoms(options))


def anchors(rule: Rule, state: dict[str, Region]) -> Region:
    """Anchors x at which every premise of the rule holds in `state`."""
    x = FULL
    for k, o in rule.premises:
        x &= state.get(k, EMPTY).shift(-o)
        if not x:
            break
    return x


class RuleSet:
    def __init__(self, rules=()):
        self.rules: tuple[Rule, ...] = tuple(rules)
        edges: dict[str, set[str]] = {}
        for r in self.rules:
            if not isinstance(r, (Implies, Forbids, ImpliesAny)):
                raise TypeError(f"not a rule: {r!r}")
            kinds = [k for k, _ in r.premises]
            produced = [r.head[0]] if isinstance(r, Implies) else (
                [k for k, _ in r.options] if isinstance(r, ImpliesAny) else [])
            if isinstance(r, Forbids):
                kinds.append(r.head[0])
            for k in kinds + produced:
                edges.setdefault(k, set())
            for p in kinds:
                edges[p].update(produced)
        self._edges = edges
        self._order = self._toposort(edges)

    @staticmethod
    def _toposort(edges: dict[str, set[str]]) -> list[str]:
        indeg = {k: 0 for k in edges}
        for k in edges:
            for h in edges[k]:
                indeg[h] += 1
        ready = sorted(k for k, d in indeg.items() if d == 0)
        order = []
        while ready:
            k = ready.pop(0)
            order.append(k)
            for h in sorted(edges[k]):
                indeg[h] -= 1
                if indeg[h] == 0:
                    ready.append(h)
            ready.sort()
        if len(order) != len(edges):
            cyc = sorted(k for k, d in indeg.items() if d > 0)
            raise RuleCycleError(f"rules form a cycle through kinds {cyc}")
        return order

    def order(self, extra_kinds=()) -> list[str]:
        """Deterministic topological order of all kinds (premises first)."""
        known = set(self._order)
        return self._order + sorted(set(extra_kinds) - known)

    def kinds(self) -> set[str]:
        return set(self._order)

    def violations(self, state: dict[str, Region]) -> list[tuple[Rule, Region]]:
        """Anchors at which a rule fails in `state` (empty list: all rules hold)."""
        out = []
        for r in self.rules:
            x = anchors(r, state)
            if isinstance(r, Implies):
                bad = x - state.get(r.head[0], EMPTY).shift(-r.head[1])
            elif isinstance(r, Forbids):
                bad = x & state.get(r.head[0], EMPTY).shift(-r.head[1])
            else:
                ok = Region.union_of(*(state.get(k, EMPTY).shift(-o) for k, o in r.options))
                bad = x - ok
            if bad:
                out.append((r, bad))
        return out
