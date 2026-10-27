"""The state of the line: the user's edits, the claims they give, and the line.

Claims are the elements placed by an edit: "K at p%" on a region, or "no K" on a
region, each with its owner (the user, the generated script, or a mode rule), and the
user's removals ("off K": K absent there, until a newer edit decides otherwise). A
kind is claimed at most once at a position. The line is computed from the claims by
the element rules (solver.py); a state is always consistent.

The claims are what applies of the edits now: the user's edits are all kept, in
order (`history`), and the claims are recomputed from them at each edit of the user
(resolve.py), so an edit that gave way applies again when what it contradicted goes.
The elements of the generated script and of the mode rules are not kept that way:
they are the claims of their owners, and what gives way of them is gone.
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
        self.taken: dict[str, dict[str, Region]] = {}  # "off K": removed by the user

    def copy(self) -> Claims:
        c = Claims()
        c.pos = {k: dict(d) for k, d in self.pos.items()}
        c.neg = {k: dict(d) for k, d in self.neg.items()}
        c.taken = {k: dict(d) for k, d in self.taken.items()}
        return c

    def kinds(self) -> list[str]:
        return sorted(set(self.pos) | set(self.neg) | set(self.taken))

    def is_empty(self) -> bool:
        return not any(r for s in (self.pos, self.neg, self.taken) for d in s.values() for r in d.values())

    def positive(self, kind: str) -> Region:
        return union(self.pos.get(kind, {}).values())

    def negative(self, kind: str) -> Region:
        return union(self.neg.get(kind, {}).values())

    def off(self, kind: str) -> Region:
        return union(self.taken.get(kind, {}).values())

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
        neg = {k: self.negative(k) | self.off(k) for k in set(self.neg) | set(self.taken)}
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

    def remove_taken(self, kind: str, region: Region) -> None:
        d = self.taken.get(kind, {})
        for key in list(d):
            d[key] = d[key] - region
            if not d[key]:
                del d[key]

    def add_pos(self, kind: str, region: Region, value: Fraction, owner: str) -> None:
        self.remove_pos(kind, region)
        self.remove_neg(kind, region)
        self.remove_taken(kind, region)
        d = self.pos.setdefault(kind, {})
        d[(value, owner)] = d.get((value, owner), EMPTY) | region

    def add_neg(self, kind: str, region: Region, owner: str) -> None:
        self.remove_pos(kind, region)
        self.remove_neg(kind, region)
        d = self.neg.setdefault(kind, {})
        d[owner] = d.get(owner, EMPTY) | region

    def add_taken(self, kind: str, region: Region, owner: str) -> None:
        d = self.taken.setdefault(kind, {})
        d[owner] = d.get(owner, EMPTY) | region

    def merged(self, other: Claims) -> Claims:
        """These claims and the other's (on other positions, or the same ones)."""
        c = self.copy()
        for mine, theirs in ((c.pos, other.pos), (c.neg, other.neg), (c.taken, other.taken)):
            for k, d in theirs.items():
                for key, r in d.items():
                    m = mine.setdefault(k, {})
                    m[key] = m.get(key, EMPTY) | r
        return c

    def same(self, other: Claims) -> bool:
        for k in set(self.pos) | set(other.pos):
            keys = set(self.pos.get(k, {})) | set(other.pos.get(k, {}))
            if any(self.pos.get(k, {}).get(key, EMPTY) != other.pos.get(k, {}).get(key, EMPTY) for key in keys):
                return False
        for a, b in ((self.neg, other.neg), (self.taken, other.taken)):
            for k in set(a) | set(b):
                keys = set(a.get(k, {})) | set(b.get(k, {}))
                if any(a.get(k, {}).get(key, EMPTY) != b.get(k, {}).get(key, EMPTY) for key in keys):
                    return False
        return True

    def __str__(self) -> str:
        out = [f"{k} {v}% on {r} ({o})" for k in sorted(self.pos) for (v, o), r in sorted(self.pos[k].items())]
        out += [f"no {k} on {r} ({o})" for k in sorted(self.neg) for o, r in sorted(self.neg[k].items())]
        out += [f"off {k} on {r} ({o})" for k in sorted(self.taken) for o, r in sorted(self.taken[k].items())]
        return "\n".join(out) if out else "(no claims)"


@dataclass
class LineState:
    claims: Claims
    line: Line
    history: tuple = ()  # the user's edits, oldest first (resolve.Record)

    @staticmethod
    def empty() -> LineState:
        return LineState(Claims(), Line({}))

    def copy(self) -> LineState:
        return LineState(self.claims.copy(), self.line, self.history)
