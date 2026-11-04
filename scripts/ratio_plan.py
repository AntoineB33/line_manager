"""The best line of the presence ratio targets and minimum lengths, found at once
(the examples of tests/test_ratio_plan.py), when the definitions are simple enough:
- the rules are only exclusions "B → no A" (no mode rule, no percentage rule);
- no constraint;
- the problems are presence ratios and minimum lengths, a kind having one target, at
  least one target above 0, and a minimum length only for a kind with a target;
- after the chip, the target kinds are only where the generator put them.
Otherwise there is no plan, and the generator searches its window as before.

The kinds that exclude each other form groups; each group must be a clique (every two
of its kinds exclude each other), else there is no plan.
- A kind alone in its group: at its ratio everywhere after the chip, e.g. A and B at
  50% each: both 50% on (x, inf).
- A group of several kinds K with ratios r_K (sum <= 1) and minimum lengths L_K: a
  cycle of period P = max L_K / r_K, each kind once in it for r_K P, so its shortest
  piece is its minimum length. A then B, ratios 1/2, lengths 1:
      A on (x + 2n, x + 2n + 1], B on (x + 2n + 1, x + 2n + 2], for every n >= 0
  The order in the cycle is drawn at random once per generator (its seed), except
  that a kind at the chip comes last: first, it would give more of it to the intervals
  ending just after the chip. Before the cycle, the interval of that kind at the chip
  goes on until it is its minimum length long. With no minimum length (P = 0) every
  cycle is beaten by a faster one, so no line is the best: the definitions are refused
  (NoBestScript).
The pieces start after the chip (the generated script does not change the line up to
it), so they are open on the left.
"""

from __future__ import annotations

import random
from fractions import Fraction

from intervals import INF, Interval, IntervalSet, Pos
from line import GEN, LineState, Op
from problems import MinimumLengthProblem, PresenceRatioProblem
from region import Region
from rules import Atom, RuleSet


class NoBestScript(ValueError):
    pass


def _exclusion(r) -> frozenset | None:
    """The two kinds of a rule "B → no A" (everywhere B is, no A), else None."""
    lits = r.lits()
    if r.is_mode_rule or r.internal or not (type(r.lhs) is Atom and r.lhs.op == "present" and len(r.rhs) == 1 and len(lits) == 1):
        return None
    o = lits[0]
    if not (o.negated and o.anchor == "p" and o.value is None and o.where.is_point and o.where.lo == 0):
        return None
    return frozenset((r.lhs.kind, o.kind))


def exclusions(rs: RuleSet) -> set[frozenset] | None:
    """The pairs of kinds that exclude each other, if the rules are only "B → no A"; None
    otherwise."""
    if rs.mode_rules or rs.percent:
        return None
    pairs = {_exclusion(r) for r in rs.rules}
    return None if None in pairs else pairs


def check_best_exists(rs: RuleSet, problems) -> None:
    """Raises NoBestScript when two kinds with a target exclude each other ("B → no A")
    and none of the kinds they are linked to that way has a minimum length: every
    alternation of them is beaten by a faster one, whatever the other rules, the modes
    and the constraints."""
    found = _targets(problems)
    if not found:
        return
    targets, lengths = found
    kinds = {k for k, r in targets.items() if r > 0}
    near = {k: set() for k in kinds}
    for r in rs.rules:
        pair = _exclusion(r)
        if pair and len(pair) == 2 and pair <= kinds:
            a, b = sorted(pair)
            near[a].add(b)
            near[b].add(a)
    seen = set()
    for k in sorted(kinds):
        if k in seen or not near[k]:
            continue
        g, todo = set(), [k]
        while todo:
            a = todo.pop()
            if a not in g:
                g.add(a)
                todo.extend(near[a] - g)
        seen |= g
        if not any(lengths.get(a, 0) for a in g):
            raise NoBestScript(f"{', '.join(sorted(g))} exclude each other and none has a minimum length: every "
                               "alternation of them is beaten by a faster one, so no script is the best")


def _targets(problems) -> tuple[dict, dict] | None:
    targets: dict[str, Fraction] = {}
    lengths: dict[str, Fraction] = {}
    for p, w in problems:
        if isinstance(p, PresenceRatioProblem):
            for k, r in p.targets.items():
                if targets.setdefault(k, r) != r:
                    return None
        elif isinstance(p, MinimumLengthProblem):
            for k, L in p.lengths.items():
                lengths[k] = max(lengths.get(k, Fraction(0)), Fraction(L))
        else:
            return None
    return targets, lengths


def _cycle(c: Fraction, P: Fraction, lo: Fraction, hi: Fraction) -> Region:
    """(c + lo + nP, c + hi + nP] for every n >= 0, 0 <= lo < hi <= P."""
    if hi < P:
        pattern = IntervalSet.of(Interval(lo, hi, False, True))
    else:  # the end of each piece is the start of the next period
        pattern = IntervalSet.of(Interval.point(0)) | IntervalSet.of(Interval(lo, P, False, False))
    return Region(c, c, IntervalSet(), Pos(1), IntervalSet(), Pos(P), pattern) & Region.of(Interval(c, INF, False, False))


class Plan:
    """The perfect script of these definitions, if they are simple enough (`ok`)."""

    def __init__(self, rs: RuleSet, problems, constraints=(), seed: int = 0) -> None:
        self.ok = False
        found = None if constraints else _targets(problems)
        pairs = exclusions(rs) if found else None
        if pairs is None:
            return
        self.targets, self.lengths = found
        lengths = self.lengths
        kinds = sorted(k for k, r in self.targets.items() if r > 0)
        if not kinds or any(k not in kinds for k in self.lengths):
            return  # nothing to place, or a minimum length with no ratio target: the search
        near = {k: {o for p in pairs if k in p for o in p if o != k and o in kinds} for k in kinds}
        groups, seen = [], set()
        for k in kinds:
            if k in seen:
                continue
            g, todo = set(), [k]
            while todo:
                a = todo.pop()
                if a not in g:
                    g.add(a)
                    todo.extend(near[a] - g)
            seen |= g
            if any(near[a] != g - {a} for a in g):
                return  # not every two of them exclude each other
            groups.append(sorted(g))
        rng = random.Random(seed)
        self.groups = []  # (kinds in their order, period); the period is None for a kind alone
        for g in groups:
            if len(g) == 1:
                self.groups.append((g, None))
                continue
            if sum(self.targets[k] for k in g) > 1:
                return  # the targets cannot all be met
            P = max(lengths.get(k, Fraction(0)) / self.targets[k] for k in g)
            if P == 0:
                raise NoBestScript(f"{', '.join(g)} exclude each other and none has a minimum length: every alternation of "
                                   "them is beaten by a faster one, so no script is the best")
            self.groups.append((rng.sample(g, len(g)), P))
        self.kinds = sorted(self.targets)
        self.ok = True

    def ops(self, state: LineState, chip) -> list[Op] | None:
        """The edit of the perfect script from the chip, on this line; None if the line has
        target kinds after the chip that the generator did not put."""
        c = Fraction(chip)
        after = Region.of(Interval(c, INF, False, False))
        ops = []
        for k in self.kinds:
            there = state.line.presence(k) & after
            if not (there - state.claims.owned(k, GEN)).is_empty():
                return None
            if not there.is_empty():
                ops.append(Op.take(k, after))
        for g, P in self.groups:
            if P is None:
                ops.append(Op.put(g[0], after, 100 * self.targets[g[0]]))
                continue
            last = [k for k in g if state.line.presence(k).contains(Pos(c))]
            start = c
            for k in last:  # its interval at the chip goes on to its minimum length
                rest = self.lengths.get(k, Fraction(0)) - _run(state.line.presence(k), c, self.lengths.get(k, Fraction(0)))
                if rest > 0:
                    ops.append(Op.put(k, Region.of(Interval(c, c + rest, False, True))))
                    start = c + rest
            at = Fraction(0)
            for k in [k for k in g if k not in last] + last:
                length = self.targets[k] * P
                ops.append(Op.put(k, _cycle(start, P, at, at + length)))
                at += length
        return ops


def _run(presence: Region, c: Fraction, L: Fraction) -> Fraction:
    """How long the interval of `presence` at c has been there at c, up to L."""
    if L <= 0:
        return Fraction(0)
    ivs = presence.on(c - L, c + 1).intervals
    here = [iv for iv in ivs if iv.contains(c)]
    if not here:
        return Fraction(0)
    lo = here[0].lo
    return c - Fraction(getattr(lo, "x", lo))


class _Lines:
    """What problems.forbidden reads: the rules and a state."""

    def __init__(self, rules, state) -> None:
        self.gen, self.state = _Rules(rules), state


class _Rules:
    def __init__(self, rules) -> None:
        self.rules = rules


class Fill:
    """The best line of targets that nothing makes compete, whatever the other rules, the
    modes and the constraints: the generator offers it first, on the whole line, instead
    of building it a stage at a time (`ok`; a minimum length only for a kind with a
    target).
    - A kind with the ratio 1: wherever it can be after the chip. E.g. Task with the 15min
      breaks: Task on (x, inf); with "Break15 → no Task", on every position after the chip
      but the breaks.
    - A kind with a ratio r < 1 that nothing forbids after the chip: at r everywhere after
      it, e.g. A and B at 0.5: A 50% and B 50% on (x, inf). Forbidden somewhere, there is
      no such edit (the search decides).
    The rules can still refuse the whole edit (e.g. "A → no B"): the generator checks it.
    No fill when a mode rule reaches a target (e.g. "mode 1 → D", D a target): what it adds
    at the chip changes the share (the generator turns `ok` off)."""

    def __init__(self, problems) -> None:
        found = _targets(problems)
        self.ok = False
        if not found:
            return
        self.targets, self.lengths = found
        self.kinds = sorted(k for k, r in self.targets.items() if r > 0)
        self.ok = bool(self.kinds) and all(k in self.kinds for k in self.lengths)

    def ops(self, rules, state: LineState, chip, window) -> list[Op] | None:
        """The edit on this line (the constraints' elements already on it); None if there
        is none: a kind below 1 forbidden somewhere after the chip, or there and not put by
        the generator, or a free stretch shorter than a minimum length to fill."""
        c = Fraction(chip)
        after = Region.of(Interval(c, INF, False, False))
        from problems import forbidden  # (problems imports nothing of this module)
        ops = []
        for k in self.kinds:
            pres = state.line.presence(k)
            no = forbidden(_Lines(rules, state), k) & after
            if self.targets[k] < 1:
                there = pres & after
                if not no.is_empty() or not (there - state.claims.owned(k, GEN)).is_empty():
                    return None
                if not there.is_empty():
                    ops.append(Op.take(k, after))
                ops.append(Op.put(k, after, 100 * self.targets[k]))
                continue
            free = after - pres - no
            if free.is_empty():
                continue
            L = self.lengths.get(k, Fraction(0))
            if L:
                for iv in free.on(c, c + window).intervals:
                    touches = iv.hi.x >= c + window or pres.contains(iv.lo) or pres.contains(iv.hi + Pos(0, 1))
                    if iv.length < L and not touches:
                        return None
            ops.append(Op.put(k, free))
        return ops or None
