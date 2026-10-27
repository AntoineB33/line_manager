"""Shared test helpers: import path, random objects, independent checks."""

import itertools
import os
import random
import sys
from fractions import Fraction as F

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from guards import claims_on, left_side, same_dict, user_claims  # noqa: E402
from intervals import INF, Interval, IntervalSet, Pos  # noqa: E402
from region import Region  # noqa: E402
from solver import EMPTY, lhs_region, solve, union  # noqa: E402

GRID = [F(i, 16) for i in range(-16 * 8, 16 * 8 + 1)]  # [-8, 8] by 1/16
POSITIONS = [Pos(x, k) for x in GRID for k in (-1, 0, 1)]  # and the positions next to them


def rand_interval(rng: random.Random, lo=-4, hi=4) -> Interval:
    while True:
        a, b = sorted(F(rng.randint(2 * lo, 2 * hi), 2) for _ in range(2))
        if rng.random() < 0.15:
            b = a
        iv = Interval.make(a, b, rng.random() < 0.5 or a == b, rng.random() < 0.5 or a == b)
        if iv:
            return iv


def rand_set(rng: random.Random, n=3, lo=-4, hi=4) -> IntervalSet:
    return IntervalSet(rand_interval(rng, lo, hi) for _ in range(rng.randint(0, n)))


def rules_hold(rs, line, claims=None) -> list[str]:
    """Independent check, with region algebra only: every element rule is applied where
    its left side is met, one percentage per kind and position, the claims hold.
    Returns the failures."""
    bad = []
    kinds = set(line.kinds()) | set(rs.order)
    pres = {k: line.presence(k) for k in kinds}
    for k in line.kinds():
        vs = list(line.vals[k].values())
        for a, b in itertools.combinations(vs, 2):
            if a & b:
                bad.append(f"{k} has two percentages on {a & b}")
    for r in rs.element:
        anchors = lhs_region(r.lhs, line.vals)
        if not anchors:
            continue
        edges = anchors.left_edges()
        for it in r.rhs:
            if it.is_or:
                ok = union(holds(o, line, pres) for o in it.options)
                base = anchors if it.options[0].anchor == "p" else edges
                if not base.issubset(ok):
                    bad.append(f"{r}: no operand on {base - ok}")
                continue
            o = it.options[0]
            base = anchors if o.anchor == "p" else edges
            tgt = base.shift(o.rel) if o.is_point else base.dilate(o.where)
            if o.anchor == "l" and o.where.hi == INF and anchors.inf() == -INF:
                tgt = Region.universe()  # l = -inf (unbound) and the interval reaches +inf
            if o.negated:
                if tgt & pres.get(o.kind, EMPTY):
                    bad.append(f"{r}: {o.kind} present on {tgt & pres[o.kind]}")
            elif o.value is not None:
                if not tgt.issubset(line.vals.get(o.kind, {}).get(o.value, EMPTY)):
                    bad.append(f"{r}: {o.kind} not {o.value}%")
            elif not tgt.issubset(pres.get(o.kind, EMPTY)):
                bad.append(f"{r}: {o.kind} missing on {tgt - pres.get(o.kind, EMPTY)}")
    if claims is not None:
        for k in claims.kinds():
            for v, reg in claims.by_value(k).items():
                if not reg.issubset(line.vals.get(k, {}).get(v, EMPTY)):
                    bad.append(f"claim {k} {v}% not on the line")
            if claims.negative(k) & pres.get(k, EMPTY):
                bad.append(f"claim no {k} violated")
    return bad


def holds(o, line, pres) -> Region:
    """The anchors a where the literal o holds: a + where inside the kind (at its
    percentage), or outside it for "no"."""
    have = line.vals.get(o.kind, {}).get(o.value, EMPTY) if o.value is not None else pres.get(o.kind, EMPTY)
    back = -o.where
    if o.negated:
        return have.dilate(back).complement()
    return have.complement().dilate(back).complement()


def check_log(log, rs, modes=None) -> int:
    """Every state is consistent and is the line of its claims; every change made by a
    script respects N1 and N2, every change made by the mode rules M1 and N2. Returns
    how many script changes were checked."""
    n = 0
    for ch in log.changes:
        assert not rules_hold(rs, ch.after.line, ch.after.claims), rules_hold(rs, ch.after.line, ch.after.claims)
        assert ch.after.line == solve(rs, ch.after.claims.problem())
        if ch.source == "mode":
            left = left_side(ch.chip, closed=False)
            assert ch.before.line.same_on(ch.after.line, left), "M1 line"
            assert same_dict(claims_on(ch.before.claims, left), claims_on(ch.after.claims, left)), "M1 claims"
            away = ch.at.complement()
            assert same_dict(claims_on(ch.before.claims, away), claims_on(ch.after.claims, away)), "M1 away from the chip"
            assert same_dict(user_claims(ch.before.claims), user_claims(ch.after.claims)), "N2 (mode rules)"
        if ch.source != "script":
            continue
        n += 1
        left = left_side(ch.chip)
        assert ch.before.line.same_on(ch.after.line, left), "N1 line"
        assert same_dict(claims_on(ch.before.claims, left), claims_on(ch.after.claims, left)), "N1 claims"
        assert same_dict(user_claims(ch.before.claims), user_claims(ch.after.claims)), "N2"
    return n
