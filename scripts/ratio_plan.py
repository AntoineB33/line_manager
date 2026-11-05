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
  The order in the cycle: a kind the constraints put as early as possible first
  (constraints.AsEarlyAsPossible), then the largest target (a stretch from the chip all of
  it is the closest to the targets), at random between equal targets once per generator
  (its seed), except that a kind at the chip comes last: first, it would give more of it to the intervals
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

from intervals import EPS, INF, Interval, IntervalSet, Pos
from line import GEN, LineState, Op
from problems import MinimumLengthProblem, PresenceRatioProblem
from region import Region
from rules import Atom, RuleSet


class NoBestScript(ValueError):
    pass


def _exclusions(r) -> set[frozenset] | None:
    """The pairs of kinds of a rule "B → no A" or "B → no A & no C" (everywhere B is, none
    of them), else None."""
    if r.is_mode_rule or r.internal or not (type(r.lhs) is Atom and r.lhs.op == "present"):
        return None
    out = set()
    for it in r.rhs:
        if len(it.options) != 1:
            return None
        o = it.options[0]
        if not (o.negated and o.anchor == "p" and o.value is None and o.where.is_point and o.where.lo == 0):
            return None
        out.add(frozenset((r.lhs.kind, o.kind)))
    return out or None


def _pairs(rules) -> set[frozenset]:
    """The pairs of two kinds that exclude each other, in the rules that only do that."""
    return {p for r in rules for p in (_exclusions(r) or ()) if len(p) == 2}


def exclusions(rs: RuleSet) -> set[frozenset] | None:
    """The pairs of kinds that exclude each other, if the rules are only "B → no A"; None
    otherwise."""
    if rs.mode_rules or rs.percent or any(_exclusions(r) is None for r in rs.rules):
        return None
    return _pairs(rs.rules)


def check_best_exists(rs: RuleSet, problems) -> None:
    """Raises NoBestScript when two kinds with a target exclude each other ("B → no A")
    and neither has a minimum length: every alternation of them is beaten by a faster
    one, whatever the other rules, the modes and the constraints (A, B, C at 1/3 that
    exclude each other, A at least 60: between two pieces of A, B and C alternate ever
    faster). One of them with a minimum length: the other's pieces come between its own."""
    found = _targets(problems)
    if not found:
        return
    targets, lengths = found
    kinds = {k for k, r in targets.items() if r > 0}
    free = sorted({tuple(sorted(pair)) for pair in _pairs(rs.rules)
                   if pair <= kinds and not any(lengths.get(k) for k in pair)})
    if free:
        a, b = free[0]
        raise NoBestScript(f"{a}, {b} exclude each other and neither has a minimum length: every alternation of them "
                           "is beaten by a faster one, so no script is the best")


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


class Built(Region):
    """A region and the Python call that built it (`src`): the generated script's text
    gives the call, not its intervals (script_python.py). An operation that changes nothing
    gives it back; another one gives the call with the operation."""
    __slots__ = ("src",)  # the call's text, or (operator, left, right): the operation on two regions

    def __and__(self, other):
        if self.issubset(other):
            return self
        return _built(Region.__and__(self, other), ("&", self, other))

    def __sub__(self, other):
        if (Region.__and__(self, other)).is_empty():
            return self
        return _built(Region.__sub__(self, other), ("-", self, other))

    def __or__(self, other):
        return _built(Region.__or__(self, other), ("|", self, other))


def _built(r: Region, src) -> Built:
    out = Built.__new__(Built)
    for s in Region.__slots__:
        setattr(out, s, getattr(r, s))
    out.src = src
    return out


def cycle(c, P, lo, hi) -> Region:
    """(c + lo + nP, c + hi + nP] for every n >= 0, 0 <= lo < hi <= P: a kind's turn in a
    cycle of period P from c."""
    from script_python import py
    return _built(_cycle(Fraction(c), Fraction(P), Fraction(lo), Fraction(hi)), f"cycle({py(Fraction(c))}, {py(Fraction(P))}, "
                  f"{py(Fraction(lo))}, {py(Fraction(hi))})")


def turns(free: Region, start, order, lengths: dict, minimum: dict, forbid: dict | None = None) -> dict | None:
    """The kinds of `order` taking their turns, one after the other, in the stretches of
    `free` from `start`, a turn going on from one stretch to the next: each for its length
    (`lengths`), less at the end of a stretch if that is still its minimum length
    (`minimum`), else nothing there; the last piece of a stretch stops just before its
    end (what comes after it, e.g. a break, starts there). `forbid`: {kind: region, with
    an end} where that kind alone cannot be (e.g. no break within 2 hours after the last
    one): when its turn comes there, the kind before it goes on until it can be. What
    `free` repeats, the turns repeat once a period starts with the same kind, past what is
    forbidden. {kind: region}; None if `free` repeats with no position outside it to cut
    its periods at, or what is forbidden has no end."""
    from script_python import py
    forbid = {k: r for k, r in (forbid or {}).items() if not r.is_empty()}
    out = _turns(free, Fraction(start), list(order), lengths, minimum, forbid)
    if out is None:
        return None
    more = f", {py(forbid)}" if forbid else ""
    src = f"turns({py(free)}, {py(Fraction(start))}, {list(order)!r}, {py(dict(lengths))}, {py(dict(minimum))}{more})"
    return {k: _built(r, f"{src}[{k!r}]") for k, r in out.items()}


def _turns(free: Region, start: Fraction, order: list, lengths: dict, minimum: dict, forbid: dict) -> dict | None:
    n = len(order)
    pieces = {k: [] for k in order}
    if any(not r.is_bounded() for r in forbid.values()):
        return None
    fend = max([Pos(start)] + [r.bounded().sup() for r in forbid.values()])  # past it, nothing is forbidden

    def lay(ivs, i, until=None):
        """Turns on these stretches (finite), from the turn i: (the turn after them, None);
        with `until`, stops at the first end of a turn past it: (that turn, where)."""
        for iv in ivs:
            x, end, closed = iv.lo, iv.hi, True  # (a stretch's first position is in it)
            while True:
                if until is not None and x >= until and not closed:
                    return i, x
                k = order[i % n]
                length = lengths[k]
                room = (end - x).x if end != INF else length + 1  # (no end: room for a whole turn)
                f = forbid.get(k)
                ahead = f.on(x, end + 1).intervals if f is not None and end != INF else \
                    (f.on(x, f.bounded().sup() + 1).intervals if f is not None else [])
                ahead = [g for g in ahead if g.hi >= x]
                if ahead and ahead[0].lo <= x:  # k cannot be here: the kind before it goes on
                    y = min(ahead[0].hi, end) if end != INF else ahead[0].hi
                    before = order[(i - 1) % n]
                    pieces[before].append(Interval(x, y, closed, True))
                    x, closed = y, False
                    if y == end:
                        break
                    continue
                if ahead and (ahead[0].lo - x).x < room:  # forbidden further in its turn: it ends before
                    room = (ahead[0].lo - x).x
                    stop = ahead[0].lo - EPS
                else:
                    stop = end
                if room < length and not (room > 0 and room >= minimum.get(k, Fraction(0))):
                    if ahead and stop != end:  # too short before what forbids it: the one before goes on
                        before = order[(i - 1) % n]
                        pieces[before].append(Interval(x, stop, closed, True))
                        x, closed = stop, False
                        continue
                    break  # too short for its turn: nothing there
                b = stop if room <= length else x + length  # (the ε of x: not past the end)
                pieces[k].append(Interval(x, b, closed, room > length))
                x, i, closed = b, i + 1, False
                if room <= length and b == end:
                    break
        return i, None

    free = free & Region.of(Interval(start, INF, False, False))
    pr, right = free.pr, free.right
    if not right or right == IntervalSet.of(Interval(0, pr.x, True, False)):
        # free is a finite union of intervals, and everything from free.hi on if `right`
        hi = max(free.hi, Pos(start))
        ivs = list(free.on(start, hi).intervals) if hi > Pos(start) else []
        x0 = None
        if right:
            if ivs and ivs[-1].hi + EPS >= hi:  # the last one goes on forever
                x0 = ivs.pop().lo
            else:
                x0 = hi
        i, _ = lay(ivs, 0)
        if x0 is not None and x0 < fend:  # turns there, while something is forbidden
            i, x0 = lay([Interval(x0, INF, True, False)], i, until=fend)
        out = {k: Region.of(IntervalSet(v)) for k, v in pieces.items()}
        if x0 is not None:
            P = sum(lengths.values(), Fraction(0))
            at = Fraction(0)
            for j in range(n):
                k = order[(i + j) % n]
                out[k] = out[k] | _cycle(Fraction(x0.x), P, at, at + lengths[k])
                at += lengths[k]
        return out
    gaps = IntervalSet.of(Interval(0, pr.x, True, False)) - right
    if not gaps:
        return None
    q = free.hi + gaps.intervals[0].lo  # outside free, every pr from there
    while q < Pos(start):
        q = q + pr
    i, _ = lay(free.on(start, q).intervals, 0)
    j = 0
    while q + pr * j < fend:  # (the periods with something forbidden: before the pattern)
        i, _ = lay(free.on(q + pr * j, q + pr * (j + 1)).intervals, i)
        j += 1
    seen = {}
    while i % n not in seen:
        seen[i % n] = j
        i, _ = lay(free.on(q + pr * j, q + pr * (j + 1)).intervals, i)
        j += 1
    j1 = seen[i % n]
    lo, hi = q + pr * j1, q + pr * j
    out = {}
    for k in order:
        ivs = IntervalSet(pieces[k])
        core = ivs & IntervalSet.of(Interval(start, lo, False, False))
        tail = (ivs & IntervalSet.of(Interval(lo, hi, True, False))).shift(-lo)
        out[k] = Region(start, lo, core, Pos(1), IntervalSet(), hi - lo, tail)
    return out


def _cycle(c: Fraction, P: Fraction, lo: Fraction, hi: Fraction) -> Region:
    """(c + lo + nP, c + hi + nP] for every n >= 0, 0 <= lo < hi <= P."""
    if hi < P:
        pattern = IntervalSet.of(Interval(lo, hi, False, True))
    else:  # the end of each piece is the start of the next period
        pattern = IntervalSet.of(Interval.point(0)) | IntervalSet.of(Interval(lo, P, False, False))
    return Region(c, c, IntervalSet(), Pos(1), IntervalSet(), Pos(P), pattern) & Region.of(Interval(c, INF, False, False))


class Plan:
    """The perfect script of these definitions, if they are simple enough (`ok`).
    `strict` False: only the cycles of the targets that exclude each other, whatever the
    other rules and the constraints (`group_ops`; the generator checks the edit)."""

    def __init__(self, rs: RuleSet, problems, constraints=(), seed: int = 0, strict: bool = True) -> None:
        self.ok = False
        self.grouped = set()  # the kinds in a cycle
        if strict:
            found = None if constraints else _targets(problems)
            pairs = exclusions(rs) if found else None
        else:
            found = _targets(problems)
            pairs = _pairs(rs.rules)
        if pairs is None or not found:
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
            # the largest target first: a stretch from the chip that is all of it is the closest
            # to the targets (the user's rule: every stretch as close as possible, the earliest
            # first); at random between equal targets
            drawn = rng.sample(g, len(g))
            # a kind the constraints put as early as possible first (constraints.AsEarlyAsPossible)
            early = {getattr(k, "kind", None) for k in constraints if type(k).__name__ == "AsEarlyAsPossible"}
            self.groups.append((sorted(drawn, key=lambda k: (k not in early, -self.targets[k])), P))
            self.grouped |= set(g)
        self.kinds = sorted(self.targets)
        self.pairs = pairs
        self.ok = True

    def group_ops(self, rules, state: LineState, chip, constraints=()) -> list[Op] | None:
        """The edit of the cycles only (not strict). Where the other rules forbid the kinds
        of a cycle after the chip (its exclusions with the other kinds of its cycle aside),
        the cycle goes on in the stretches they leave (`_laid`). None if a kind of a cycle
        is there after the chip and not put by the generator, or the other rules do not
        forbid the kinds of a cycle at the same positions."""
        c = Fraction(chip)
        after = Region.of(Interval(c, INF, False, False))
        from problems import forbidden  # (problems imports nothing of this module)
        inside = {p for p in self.pairs if p <= self.grouped}  # (the cycles' own exclusions)
        others = [r for r in rules.element if not (_exclusions(r) or {None}) <= inside]
        ops, no, theres = [], {}, {}
        # (what forbids them read on the line without the generator's turns after the chip:
        # they are made again here)
        vals = {k: ({v: r - after for v, r in d.items()} if k in self.grouped else d) for k, d in state.line.vals.items()}
        for k in sorted(self.grouped):
            there = state.line.presence(k) & after
            if not (there - state.claims.owned(k, GEN)).is_empty():
                return None
            no[k] = forbidden(_Lines(others, state), k, vals) & after
            for gap in (g for g in constraints if hasattr(g, "apart") and g.kind == k):  # (constraints.MinimumGap)
                # its `after` intervals but those of the generator's turns after the chip (made
                # again here: "no screen" that comes with a break of its own is not counted)
                source = state.line.presence(gap.after) - (there if gap.after != k else after)
                no[k] = no[k] | (gap.apart(source) & after)
            theres[k] = there
        for g, P in self.groups:
            if P is None:
                continue
            common = no[g[0]]
            for k in g[1:]:
                common = common & no[k]
            alone = {k: no[k] - common for k in g}  # (where a kind alone cannot be: the others go on)
            if common.is_empty() and all(r.is_empty() for r in alone.values()):
                ops += self._cycle_ops(state, c, g, P)
            else:
                laid = self._laid(state, c, g, P, after - common, alone)
                if laid is None:
                    return None
                ops += laid
        # the generator's earlier turns off, where the new ones do not put the same kind ("take":
        # the kind is then absent there, so not where it is put again)
        put = {}
        for o in ops:
            if o.action == "put":
                put[o.kind] = put.get(o.kind, Region.empty()) | o.region
        takes = [Op.take(k, r - put.get(k, Region.empty())) for k, r in sorted(theres.items())
                 if not (r - put.get(k, Region.empty())).is_empty()]
        return takes + ops

    def _order(self, state: LineState, c: Fraction, g) -> tuple[list, list[Op], Fraction]:
        """The kinds of g in their turn (the one at the chip last), the edit that keeps its
        interval at the chip to its minimum length, and where the turns start."""
        ops, start = [], c
        last = [k for k in g if state.line.presence(k).contains(Pos(c))]
        for k in last:
            rest = self.lengths.get(k, Fraction(0)) - _run(state.line.presence(k), c, self.lengths.get(k, Fraction(0)))
            if rest > 0:
                ops.append(Op.put(k, Region.of(Interval(c, c + rest, False, True))))
                start = c + rest
        return [k for k in g if k not in last] + last, ops, start

    def _laid(self, state: LineState, c: Fraction, g, P, free: Region, alone=None) -> list[Op] | None:
        """The kinds of g taking their turns in the stretches of `free` after the chip, each
        for r P (`turns`), a kind's turn waiting where it alone cannot be (`alone`); None if
        there are none to take."""
        order, ops, start = self._order(state, c, g)
        out = turns(free, start, order, {k: self.targets[k] * P for k in order},
                    {k: self.lengths.get(k, Fraction(0)) for k in order}, alone)
        if out is None:
            return None
        return ops + [Op.put(k, r) for k, r in out.items() if not r.is_empty()]

    def _cycle_ops(self, state: LineState, c: Fraction, g, P) -> list[Op]:
        """The kinds of g one after the other every P from the chip, the one at the chip last,
        its interval there kept to its minimum length."""
        order, ops, start = self._order(state, c, g)
        at = Fraction(0)
        for k in order:
            length = self.targets[k] * P
            ops.append(Op.put(k, cycle(start, P, at, at + length)))
            at += length
        return ops

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
            else:
                ops += self._cycle_ops(state, c, g, P)
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
        self.rules = rules if hasattr(rules, "element") else _Element(rules)


class _Element:
    def __init__(self, rules) -> None:
        self.element = list(rules)


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

    def ops(self, rules, state: LineState, chip, window, skip=()) -> list[Op] | None:
        """The edit on this line (the constraints' elements already on it); None if there
        is none: a kind below 1 forbidden somewhere after the chip, or there and not put by
        the generator, or a free stretch shorter than a minimum length to fill."""
        c = Fraction(chip)
        after = Region.of(Interval(c, INF, False, False))
        from problems import forbidden  # (problems imports nothing of this module)
        ops = []
        for k in self.kinds:
            if k in skip:  # (in a cycle: Plan.group_ops)
                continue
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
        return ops
