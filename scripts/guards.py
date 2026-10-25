"""What a generated script is allowed to do, checked exactly.

N1  it never changes (-inf, chip]: the left side of the chip and the chip's position;
N2  it never removes or changes an element added by the user;
N3  it never adds a violation of an element rule, whatever the unknown modes turn out to be.

For N3, W_r(S) is the set of positions where rule r is violated in state S if
every position whose mode is not known yet may have any mode ("possible
violations"). Violations at a position x only depend on the mode at x, so
W_r(S') ⊆ W_r(S) for every rule r  <=>  for every future of the modes, every
violation of S' is a violation of S.
"""

from __future__ import annotations

from fractions import Fraction

from intervals import EMPTY, IntervalSet
from line import GEN, USER, LineState, changed_region
from modes import ModeKnowledge
from rules import Rule


def mode_region(rule: Rule, known: ModeKnowledge, certain: bool = False) -> IntervalSet:
    if rule.lhs_mode is None:
        return IntervalSet.universe()
    return known.region(rule.lhs_mode) if certain else known.possible(rule.lhs_mode)


def possible_violations(state: LineState, rules: list[Rule], known: ModeKnowledge) -> list[IntervalSet]:
    return [r.violations(state, mode_region(r, known)) for r in rules]


def adds_violation(before: LineState, after: LineState, rules: list[Rule], known: ModeKnowledge) -> bool:
    wb = possible_violations(before, rules, known)
    wa = possible_violations(after, rules, known)
    return any(not a.issubset(b) for a, b in zip(wa, wb))


def free_types(rules: list[Rule]) -> set[str]:
    """Types written by a continuous edit must not be in any left side."""
    lhs = set()
    for r in rules:
        lhs |= r.lhs_types
    return {it.etype for r in rules for it in r.rhs} - lhs


def forbidden_zone(state: LineState, rules: list[Rule], known: ModeKnowledge, etype: str, value: Fraction) -> IntervalSet:
    """Positions where writing `value` for a type that is in no left side would add a
    possible violation: ⋃ over items on that type that do not allow the value of
    (positions where the left side may be met) ⊕ offsets."""
    out = EMPTY
    for r in rules:
        items = [it for it in r.rhs if it.etype == etype and not it.allowed.ok(value)]
        if items:
            met = r.met(state, mode_region(r, known))
            for it in items:
                out = out | (met + it.rel)
    return out


def check_edit(before: LineState, after: LineState, chip: Fraction, rules: list[Rule], known: ModeKnowledge) -> str | None:
    """None if going from `before` to `after` respects N1, N2 and N3, else the reason."""
    frozen = IntervalSet.up_to(chip)
    for t in set(before.types()) | set(after.types()):
        ch = changed_region(before, after, t)
        if ch & frozen:
            return f"N1: would change {t} on {ch & frozen}, at or left of the chip {chip}"
        if ch & before.owned(t, USER):
            return f"N2: would change the user's {t} on {ch & before.owned(t, USER)}"
        if after.owned(t, USER) != before.owned(t, USER):
            return f"N2: would add a user-owned {t}"
    if adds_violation(before, after, rules, known):
        return "N3: would add a violation of an element rule"
    return None


def apply_items(state: LineState, items) -> LineState:
    out = state.copy()
    for t, x, v in items:
        if v:
            out.assign(t, x, Fraction(v), GEN)
        else:
            out.remove_owned(t, x, GEN)
    return out
