"""Element rules.

A rule is  L -> R  where
- L is a conjunction of element types and at most one mode literal;
- R is a set of items (U, I, allowed): I a bounded set of offsets, allowed one of
  "present" (s_U > 0), "exact p" (s_U = p) or "absent" (s_U = 0, written "no U").

L is met at x iff s_T(x) > 0 for every type T of L and, if L has "mode m", the
mode at x is m. The rule holds at x iff, when L is met at x, s_U(y) is allowed
for every item and every y in x + I. Its violation set is

    V = { x : L met at x } ∩ ⋃_items ( bad_U ⊕ (-I) ),    bad_U = { y : s_U(y) not allowed }

which is computed exactly. The set of positions where L is met depends on what
is known of the modes, so it is computed for a given mode region (the positions
whose mode is, or is assumed to be, m).

Syntax (both "→" and "->"):
    A → B                     A & B → C 3 units on the left
    A → no B                  mode 1 → D & E
    A → A at -1               A → B 50% on [0, 2)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from fractions import Fraction

from intervals import EMPTY, Interval, IntervalSet, parse_interval
from line import LineState

PRESENT, EXACT, ABSENT = "present", "exact", "absent"


@dataclass(frozen=True)
class Allowed:
    kind: str
    value: Fraction | None = None

    def ok(self, v: Fraction) -> bool:
        if self.kind == PRESENT:
            return v > 0
        if self.kind == ABSENT:
            return v == 0
        return v == self.value

    def repair_value(self) -> Fraction:
        """The value a generator writes to satisfy the item."""
        return {PRESENT: Fraction(100), ABSENT: Fraction(0)}.get(self.kind, self.value)

    def __str__(self) -> str:
        return {PRESENT: "", ABSENT: "no "}.get(self.kind, "")


@dataclass(frozen=True)
class Item:
    etype: str
    rel: IntervalSet
    allowed: Allowed

    def __str__(self) -> str:
        pct = f" {self.allowed.value}%" if self.allowed.kind == EXACT else ""
        where = ""
        if self.rel != IntervalSet.of(Interval.point(0)):
            ivs = self.rel.intervals
            where = f" at {ivs[0].lo}" if len(ivs) == 1 and ivs[0].is_point else f" on {self.rel}"
        return f"{self.allowed}{self.etype}{pct}{where}"


@dataclass(frozen=True)
class Rule:
    lhs_types: frozenset[str]
    lhs_mode: str | None
    rhs: tuple[Item, ...]

    def __post_init__(self) -> None:
        if not self.lhs_types and self.lhs_mode is None:
            raise ValueError("a rule needs a left side")
        if not self.rhs:
            raise ValueError("a rule needs a right side")
        for it in self.rhs:
            if not it.rel or not it.rel.is_bounded():
                raise ValueError("right side offsets must be a non-empty bounded set")

    def reach(self) -> Fraction:
        """Largest |offset| of the right side."""
        return max(max(abs(it.rel.inf()), abs(it.rel.sup())) for it in self.rhs)

    def met(self, state: LineState, mode_region: IntervalSet) -> IntervalSet:
        """Positions where the left side is met, the mode literal (if any) being
        true exactly on mode_region."""
        out = IntervalSet.universe() if self.lhs_mode is None else mode_region
        for t in sorted(self.lhs_types):
            out = out & state.support(t)
        return out

    def item_violations(self, state: LineState, item: Item, met: IntervalSet) -> IntervalSet:
        return met & (bad_set(state, item.etype, item.allowed) + (-item.rel))

    def violations(self, state: LineState, mode_region: IntervalSet, items=None) -> IntervalSet:
        met = self.met(state, mode_region)
        out = EMPTY
        for it in (self.rhs if items is None else items):
            out = out | self.item_violations(state, it, met)
        return out

    def __str__(self) -> str:
        lhs = sorted(self.lhs_types) + ([f"mode {self.lhs_mode}"] if self.lhs_mode is not None else [])
        return " & ".join(lhs) + " → " + " & ".join(map(str, self.rhs))


def bad_set(state: LineState, etype: str, allowed: Allowed) -> IntervalSet:
    if allowed.kind == PRESENT:
        return state.support(etype).complement()
    if allowed.kind == ABSENT:
        return state.support(etype)
    return state.where(etype, lambda p: p.value == allowed.value).complement()


# ---------------------------------------------------------------- parsing

_KEYWORDS = {"mode", "no", "at", "on", "units", "unit"}
_NUM = r"[-+]?\d+(?:/\d+|\.\d+)?"


def _type_name(tok: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tok) or tok in _KEYWORDS:
        raise ValueError(f"bad element type {tok!r}")
    return tok


def _parse_item(text: str) -> Item:
    t = text.strip()
    allowed = Allowed(PRESENT)
    m = re.match(r"no\s+(.*)$", t)
    if m:
        allowed, t = Allowed(ABSENT), m.group(1)
    m = re.match(r"(\w+)\s*(.*)$", t)
    if not m:
        raise ValueError(f"bad item {text!r}")
    etype, rest = _type_name(m.group(1)), m.group(2).strip()
    m = re.match(rf"({_NUM})\s*%\s*(.*)$", rest)
    if m:
        if allowed.kind == ABSENT:
            raise ValueError("'no' takes no percentage")
        p = Fraction(m.group(1))
        if not 0 < p <= 100:
            raise ValueError(f"percentage {p} not in (0, 100]")
        allowed, rest = Allowed(EXACT, p), m.group(2).strip()
    rel = IntervalSet.of(Interval.point(0))
    if rest:
        m1 = re.fullmatch(rf"at\s+({_NUM})", rest)
        m2 = re.fullmatch(r"on\s+(.+)", rest)
        m3 = re.fullmatch(rf"({_NUM})\s+units?\s+on\s+the\s+(left|right)", rest)
        if m1:
            rel = IntervalSet.of(Interval.point(Fraction(m1.group(1))))
        elif m2:
            rel = IntervalSet.of(parse_interval(m2.group(1)))
        elif m3:
            d = Fraction(m3.group(1))
            rel = IntervalSet.of(Interval.point(-d if m3.group(2) == "left" else d))
        else:
            raise ValueError(f"bad location {rest!r}")
    return Item(etype, rel, allowed)


def parse_rule(text: str) -> Rule:
    parts = re.split(r"→|->", text)
    if len(parts) != 2:
        raise ValueError(f"a rule has one arrow: {text!r}")
    lhs_types, lhs_mode = set(), None
    for lit in parts[0].split("&"):
        lit = lit.strip()
        m = re.fullmatch(r"mode\s+(\S+)", lit)
        if m:
            if lhs_mode is not None and lhs_mode != m.group(1):
                raise ValueError("two different modes can never be met together")
            lhs_mode = m.group(1)
        else:
            lhs_types.add(_type_name(lit))
    rhs = tuple(_parse_item(s) for s in parts[1].split("&"))
    return Rule(frozenset(lhs_types), lhs_mode, rhs)
