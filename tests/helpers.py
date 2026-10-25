"""Shared test helpers: import path, random objects, brute-force oracles, log checker."""

import os
import random
import sys
from fractions import Fraction as F

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from guards import possible_violations  # noqa: E402
from intervals import Interval, IntervalSet  # noqa: E402
from line import USER  # noqa: E402

GRID = [F(i, 16) for i in range(-16 * 8, 16 * 8 + 1)]  # [-8, 8] by 1/16


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


def check_state(state) -> None:
    """Uniqueness of a type at a position, percentages in (0, 100]."""
    for t in state.types():
        ps = state.pieces(t)
        for i, p in enumerate(ps):
            assert 0 < p.value <= 100
            for q in ps[i + 1:]:
                assert not (IntervalSet.of(p.interval) & IntervalSet.of(q.interval)), (t, p, q)


def user_pieces(state):
    return {t: tuple(p for p in state.pieces(t) if p.owner == USER) for t in state.types()}


def check_log(log, rules, modes) -> int:
    """Every change made by a script respects N1, N2, N3. Returns how many were checked."""
    n = 0
    for ch in log.changes:
        check_state(ch.after)
        if ch.source == "user":
            continue
        n += 1
        frozen = IntervalSet.up_to(ch.chip)
        for t in set(ch.before.types()) | set(ch.after.types()):
            assert ch.before.restricted(t, frozen) == ch.after.restricted(t, frozen), ("N1", ch.source, t)
        assert {k: v for k, v in user_pieces(ch.before).items() if v} == {k: v for k, v in user_pieces(ch.after).items() if v}, "N2"
        if ch.source == "follow":
            first = ch.offsets.intervals[0]
            assert first.lo > 0 or (first.lo == 0 and not first.lo_closed)
            assert ch.written.issubset(ch.swept + ch.offsets)
            assert ch.swept.inf() >= ch.chip
        else:
            assert ch.written.issubset(IntervalSet.after(ch.chip))
        known = modes.known(ch.chip)
        wb = possible_violations(ch.before, rules, known)
        wa = possible_violations(ch.after, rules, known)
        assert all(a.issubset(b) for a, b in zip(wa, wb)), "N3"
    return n
