"""The state of the line: the claims (what was placed, by whom) and the line they give.

Claims are the elements placed by an edit: "K at p%" on a region, or "no K" on a
region, each with its owner (the user, the generated script, or a mode rule). A kind
is claimed at most once at a position. The line is computed from the claims by the
element rules (solver.py); a state is always consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from intervals import Interval, IntervalSet
from region import Region
from solver import EMPTY, Line, Problem, union

USER = "user"
GEN = "generated"
MODE = "mode"


def as_region(x) -> Region:
    if isinstance(x, Region):
        return x
    return Region.of(x)


def _percent(p) -> Fraction:
    p = Fraction(p)
    if not 0 < p <= 100:
        raise ValueError(f"percentage {p} not in (0, 100]")
    return p


@dataclass(frozen=True)
class Op:
    """One part of an edit.
    put      K on region at value      (an element)
    put_no   no K on region            (a "no K" element)
    take     K off region              (K must then be absent there)
    take_no  the "no K" elements off region"""
    action: str
    kind: str
    region: Region
    value: Fraction = Fraction(100)

    @staticmethod
    def put(kind, where, value=100) -> Op:
        return Op("put", kind, as_region(where), _percent(value))

    @staticmethod
    def put_no(kind, where) -> Op:
        return Op("put_no", kind, as_region(where))

    @staticmethod
    def take(kind, where) -> Op:
        return Op("take", kind, as_region(where))

    @staticmethod
    def take_no(kind, where) -> Op:
        return Op("take_no", kind, as_region(where))

    def __str__(self) -> str:
        return {"put": f"put {self.kind} {self.value}% on {self.region}", "put_no": f"put no {self.kind} on {self.region}",
                "take": f"take {self.kind} off {self.region}", "take_no": f"take no {self.kind} off {self.region}"}[self.action]


class Claims:
    def __init__(self) -> None:
        self.pos: dict[str, dict[tuple[Fraction, str], Region]] = {}
        self.neg: dict[str, dict[str, Region]] = {}

    def copy(self) -> Claims:
        c = Claims()
        c.pos = {k: dict(d) for k, d in self.pos.items()}
        c.neg = {k: dict(d) for k, d in self.neg.items()}
        return c

    def kinds(self) -> list[str]:
        return sorted(set(self.pos) | set(self.neg))

    def positive(self, kind: str) -> Region:
        return union(self.pos.get(kind, {}).values())

    def negative(self, kind: str) -> Region:
        return union(self.neg.get(kind, {}).values())

    def owned(self, kind: str, owner: str) -> Region:
        return union(r for (v, o), r in self.pos.get(kind, {}).items() if o == owner)

    def owned_no(self, kind: str, owner: str) -> Region:
        return self.neg.get(kind, {}).get(owner, EMPTY)

    def by_value(self, kind: str) -> dict[Fraction, Region]:
        out: dict[Fraction, Region] = {}
        for (v, _), r in self.pos.get(kind, {}).items():
            out[v] = out.get(v, EMPTY) | r
        return out

    def problem(self, extra_neg: dict[str, Region] | None = None) -> Problem:
        neg = {k: self.negative(k) for k in self.neg}
        for k, r in (extra_neg or {}).items():
            neg[k] = neg.get(k, EMPTY) | r
        return Problem({k: self.by_value(k) for k in self.pos}, neg)

    # writing (no rule is applied here: see resolve.py)
    def remove_pos(self, kind: str, region: Region) -> None:
        d = self.pos.get(kind, {})
        for key in list(d):
            d[key] = d[key] - region
            if not d[key]:
                del d[key]

    def remove_neg(self, kind: str, region: Region) -> None:
        d = self.neg.get(kind, {})
        for key in list(d):
            d[key] = d[key] - region
            if not d[key]:
                del d[key]

    def add_pos(self, kind: str, region: Region, value: Fraction, owner: str) -> None:
        self.remove_pos(kind, region)
        self.remove_neg(kind, region)
        d = self.pos.setdefault(kind, {})
        d[(value, owner)] = d.get((value, owner), EMPTY) | region

    def add_neg(self, kind: str, region: Region, owner: str) -> None:
        self.remove_pos(kind, region)
        self.remove_neg(kind, region)
        d = self.neg.setdefault(kind, {})
        d[owner] = d.get(owner, EMPTY) | region

    def same(self, other: Claims) -> bool:
        for k in set(self.pos) | set(other.pos):
            keys = set(self.pos.get(k, {})) | set(other.pos.get(k, {}))
            if any(self.pos.get(k, {}).get(key, EMPTY) != other.pos.get(k, {}).get(key, EMPTY) for key in keys):
                return False
        for k in set(self.neg) | set(other.neg):
            keys = set(self.neg.get(k, {})) | set(other.neg.get(k, {}))
            if any(self.neg.get(k, {}).get(key, EMPTY) != other.neg.get(k, {}).get(key, EMPTY) for key in keys):
                return False
        return True

    def __str__(self) -> str:
        out = [f"{k} {v}% on {r} ({o})" for k in sorted(self.pos) for (v, o), r in sorted(self.pos[k].items())]
        out += [f"no {k} on {r} ({o})" for k in sorted(self.neg) for o, r in sorted(self.neg[k].items())]
        return "\n".join(out) if out else "(no claims)"


@dataclass
class LineState:
    claims: Claims
    line: Line

    @staticmethod
    def empty() -> LineState:
        return LineState(Claims(), Line({}))

    def copy(self) -> LineState:
        return LineState(self.claims.copy(), self.line)
