"""Edits: the result of every edit is decided by fixed rules.

An edit is a list of operations (line.Op). Its result:
1. Its own operations apply: a put replaces what was claimed of that kind there, a
   take removes the claims of that kind there and requires the kind to be absent.
2. If the edit contradicts the rules by itself, it is refused.
3. Otherwise its elements are kept (the newest edit wins) and, if older elements
   conflict with them, some older elements are removed. An element is a largest
   interval of one claim ("K p%" or "no K", one owner), removed whole. The removal:
   only what is necessary (no removal set strictly contains another that works), the
   combination that loses the fewest elements (the elements of the line, the largest
   intervals of one kind and percentage, that do not stay whole, plus the "no K"
   elements removed), and on a tie the one that keeps the leftmost elements: at the
   leftmost position where two combinations differ, the one that keeps the element
   there, the kinds at one position in the order they first appear in the rules.
   With "A and B → C", A and B at x, then "no C" at x: removing A loses A and C,
   removing B loses B and C; the tie keeps A, so B and C are removed.
4. The operands of "or" are then chosen as in solver.py.

(Removing a part of an element is not an option: with rules relative to l, the left
edge of an interval, removing [0, a) of an element for every a > 1/2 can work while
a = 1/2 does not, and then no removal would be the smallest.)

How it is computed. A rule acts within `reach` of its left side (a change of the left
side on a set E moves the left edges only within the closure of E), so a change can
only matter within depth * reach (depth: the number of kinds) of where it is made.
The candidates are the older elements within that distance (+1) of the edit;
candidates farther apart than twice that distance are decided separately (each
counting its losses near itself), from left to right. A group of more than
MAX_CANDIDATES elements is refused rather than guessed.
"""

from __future__ import annotations

import itertools
from fractions import Fraction

from intervals import Interval
from line import USER, Claims, LineState, Op
from region import IncompatiblePeriods, Region
from rules import RuleSet
from solver import EMPTY, Conflict, Line, Problem, Undetermined, solve, union

MAX_CANDIDATES = 12


class Refused(Exception):
    pass


def initial_state(rs: RuleSet, entries, owner: str = USER) -> LineState:
    """A state from (kind, interval or set, percentage) entries, one edit."""
    ops, seen = [], {}
    for kind, where, pct in entries:
        r = Region.of(where)
        if seen.get(kind, EMPTY) & r:
            raise ValueError(f"{kind} is present several times on {seen[kind] & r}")
        seen[kind] = seen.get(kind, EMPTY) | r
        ops.append(Op.put(kind, r, pct))
    return apply_edit(rs, LineState.empty(), ops, owner)


def apply_edit(rs: RuleSet, state: LineState, ops: list[Op], owner: str) -> LineState:
    """The state after the edit; raises Refused."""
    c1 = state.claims.copy()
    mand_pos: dict[str, dict[Fraction, Region]] = {}
    mand_neg: dict[str, Region] = {}
    temp: dict[str, Region] = {}
    for op in ops:
        if op.action == "put":
            c1.add_pos(op.kind, op.region, op.value, owner)
            for v in list(mand_pos.get(op.kind, {})):
                mand_pos[op.kind][v] = mand_pos[op.kind][v] - op.region
            mand_pos.setdefault(op.kind, {})[op.value] = mand_pos.get(op.kind, {}).get(op.value, EMPTY) | op.region
            mand_neg[op.kind] = mand_neg.get(op.kind, EMPTY) - op.region
        elif op.action == "put_no":
            c1.add_neg(op.kind, op.region, owner)
            mand_neg[op.kind] = mand_neg.get(op.kind, EMPTY) | op.region
            for v in list(mand_pos.get(op.kind, {})):
                mand_pos[op.kind][v] = mand_pos[op.kind][v] - op.region
        elif op.action == "take":
            c1.remove_pos(op.kind, op.region)
            temp[op.kind] = temp.get(op.kind, EMPTY) | op.region
        elif op.action == "take_no":
            c1.remove_neg(op.kind, op.region)
        else:
            raise ValueError(op.action)
    own_neg = {k: mand_neg.get(k, EMPTY) | temp.get(k, EMPTY) for k in set(mand_neg) | set(temp)}
    try:
        try:
            solve(rs, Problem(mand_pos, own_neg))
        except Conflict as e:
            raise Refused(f"the edit contradicts the rules: {e}")
        try:
            return LineState(c1, solve(rs, c1.problem(temp)))
        except Conflict:
            pass
        footprint = union(op.region for op in ops)
        return _resolve(rs, state.line, c1, mand_pos, mand_neg, temp, footprint)
    except Undetermined as e:
        raise Refused(f"the rules do not decide the result: {e}")
    except IncompatiblePeriods as e:
        raise Refused(f"two results repeat with periods that cannot be compared: {e}")


# ---------------------------------------------------------------------------------


def _older_elements(c1: Claims, mand_pos, mand_neg) -> list[tuple[str, Region]]:
    """(name, region): the older elements, "K" or "no K", each a largest interval of a claim."""
    out = []
    for k, d in c1.pos.items():
        new = union(mand_pos.get(k, {}).values())
        for reg in d.values():
            out += [(k, e) for e in (reg - new).components()]
    for k, d in c1.neg.items():
        for reg in d.values():
            out += [("no " + k, e) for e in (reg - mand_neg.get(k, EMPTY)).components()]
    return out


def _resolve(rs: RuleSet, old_line: Line, c1: Claims, mand_pos, mand_neg, temp, footprint: Region) -> LineState:
    """Remove older elements so that the claims are consistent again."""
    R = (len(rs.order) + 1) * rs.reach + 1
    near = lambda reg, d: reg.dilate(Interval.closed(-d, d))
    zone = near(footprint, R)
    cands = [c for c in _older_elements(c1, mand_pos, mand_neg) if c[1] & zone]
    key = lambda c: (c[1].inf(), rs.rank.get(c[0].removeprefix("no "), 10 ** 6))
    cands.sort(key=key)
    # groups: candidates closer than 2R stay together
    groups: list[list] = []
    for c in cands:
        reach = near(c[1], 2 * R)
        hit = [g for g in groups if any(reach & e[1] for e in g)]
        merged = [c] + [e for g in hit for e in g]
        groups = [g for g in groups if not any(g is h for h in hit)] + [sorted(merged, key=key)]
    groups.sort(key=lambda g: key(g[0]))
    chosen: list = []
    for gi, group in enumerate(groups):
        if len(group) > MAX_CANDIDATES:
            raise Refused(f"more than {MAX_CANDIDATES} older elements are in conflict at once")
        later = [c for g in groups[gi + 1:] for c in g]
        local = near(union(c[1] for c in group), R)
        best, minimal = None, []
        for n in range(len(group) + 1):
            for sub in itertools.combinations(range(len(group)), n):
                s = set(sub)
                if any(m <= s for m in minimal):
                    continue
                drop = chosen + [group[i] for i in sub] + later
                try:
                    line = solve(rs, _without(c1, drop).problem(temp))
                except Conflict:
                    continue
                minimal.append(s)
                lost = _lost(old_line, line, local) + sum(1 for i in sub if group[i][0].startswith("no "))
                cand = (lost, _dropped([group[i] for i in sub]))
                if best is None or _better(cand, best[0], rs):
                    best = (cand, [group[i] for i in sub])
        if best is None:
            raise Refused("no removal of older elements resolves the conflict")
        chosen += best[1]
    c2 = _without(c1, chosen)
    try:
        return LineState(c2, solve(rs, c2.problem(temp)))
    except Conflict as e:
        raise Refused(f"no removal of older elements resolves the conflict: {e}")


def _without(c1: Claims, drop) -> Claims:
    c = c1.copy()
    for name, reg in drop:
        if name.startswith("no "):
            c.remove_neg(name[3:], reg)
        else:
            c.remove_pos(name, reg)
    return c


def _dropped(drop) -> dict[str, Region]:
    out: dict[str, Region] = {}
    for name, reg in drop:
        out[name] = out.get(name, EMPTY) | reg
    return out


def _lost(old: Line, new: Line, where: Region):
    """The elements of the old line (largest intervals of one kind and percentage)
    that meet `where` and do not stay whole."""
    n = 0
    for k, d in old.vals.items():
        for v, reg in d.items():
            diff = (reg - new.vals.get(k, {}).get(v, EMPTY)) & where
            if diff:
                n += reg.components_meeting(diff)
    return n


def _better(a, b, rs) -> bool:
    """a, b = (loss, dropped {name: Region}); fewer lost, then leftmost kept."""
    if a[0] != b[0]:
        return a[0] < b[0]
    return _keeps_leftmost(a[1], b[1], rs)


def _keeps_leftmost(da: dict, db: dict, rs) -> bool:
    """True if dropping da keeps the leftmost element that only one of da, db keeps."""
    first = None
    for k in set(da) | set(db):
        for diff, a_drops in ((da.get(k, EMPTY) - db.get(k, EMPTY), True), (db.get(k, EMPTY) - da.get(k, EMPTY), False)):
            if diff:
                key = (diff.inf(), rs.rank.get(k.removeprefix("no "), 10 ** 6))
                if first is None or key < first[0]:
                    first = (key, a_drops)
    return first is not None and not first[1]
