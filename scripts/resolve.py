"""Edits: the result of every edit is decided by fixed rules.

An edit is a list of operations (line.Op). Its result:
1. Its own operations apply: a put replaces what was claimed of that kind there, a
   take removes the claims of that kind there and requires the kind to be absent.
2. If the edit contradicts the rules by itself, it is refused.
3. Otherwise its elements are kept: edits from the user win over what is on the
   line. If older elements conflict with them, some older elements are removed, one
   conflict after the other, from left to right. An element is a largest interval of
   one claim ("K p%" or "no K", one owner), removed whole.
   - A conflict is two requirements that contradict on a region X: K present and
     "no K" (a claim, or a rule applied there), or two percentages. A requirement
     that comes from a rule r applied on X gives way by making r's left side false
     there: the combinations of the elements its terms rest on (a kind that a rule
     places rests on that rule's left side, and so on). A requirement that comes
     from an older claim gives way by removing it. The edit's own never gives way.
   - Among the combinations that remove the conflict from X: only what is necessary
     (none strictly contains another one that works), the one that loses the fewest
     elements (the elements of the line, the largest intervals of one kind and
     percentage, that do not stay whole, plus the "no K" elements removed), and on a
     tie the one that keeps the leftmost elements: at the leftmost position where
     two combinations differ, the one that keeps the element there, the kinds at one
     position in the order they first appear in the rules.
   - With "A and B → C", A and B at x, then "no C" at x: removing A loses A and C,
     removing B loses B and C; the tie keeps A, so B and C are removed.
   - The choice is made conflict by conflict, not over all of them at once: that
     takes a few combinations per conflict, however many elements there are.
   - A conflict these requirements do not explain (through the "or" choices), or
     that no combination removes: the older elements are removed one after the
     other, those near the edit first, then from left to right, until the claims
     are consistent. The edit alone is consistent, so this ends. Then each element
     removed that way is put back if the claims stay consistent with it.
4. The operands of "or" are then chosen as in solver.py.

So an edit is refused only when it has no result on its own (step 2), never because
of what is on the line.

(Removing a part of an element is not an option: with rules relative to l, the left
edge of an interval, removing [0, a) of an element for every a > 1/2 can work while
a = 1/2 does not, and then no removal would be the smallest.)
"""

from __future__ import annotations

import bisect
import itertools
from fractions import Fraction

from intervals import Interval, IntervalSet
from line import USER, Claims, LineState, Op
from region import IncompatiblePeriods, Region
from rules import And, Atom, Not, Or, RuleSet
from solver import EMPTY, Conflict, Line, Problem, Undetermined, _values, kinds_of, lhs_region, solve, target, union

MAX_OPTIONS = 64  # combinations kept per requirement (the smallest first)


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
        return _Resolution(rs, state.line, c1, mand_pos, mand_neg, temp, footprint).run()
    except Undetermined as e:
        raise Refused(f"the rules do not decide the result: {e}")
    except IncompatiblePeriods as e:
        raise Refused(f"two results repeat with periods that cannot be compared: {e}")


# ---------------------------------------------------------------------------------


def _older_elements(c1: Claims, mand_pos, mand_neg) -> list[tuple[str, Region, Fraction | None]]:
    """(name, region, percentage): the older elements, "K" or "no K", each a largest
    interval of a claim."""
    out = []
    for k, d in c1.pos.items():
        new = union(mand_pos.get(k, {}).values())
        for (v, _), reg in d.items():
            out += [(k, e, v) for e in (reg - new).components()]
    for k, d in c1.neg.items():
        for reg in d.values():
            out += [("no " + k, e, None) for e in (reg - mand_neg.get(k, EMPTY)).components()]
    return out


class _Lines:
    """The line of some claims as far as region algebra computes it (the kinds that no
    "or" choice changes), with each conflict and the two requirements behind it:
    (region, side, side). A side is ("rule", i, lit): element rule i applied there;
    ("claim", K, v): the claims of K at v%; ("no", K): the "no K" claims; ("taken", K):
    the edit takes K off there; ("present", K): K present there."""

    def __init__(self, rs: RuleSet, c: Claims, temp: dict) -> None:
        self.rs = rs
        prob = c.problem(temp)
        self.kinds_in = set(rs.choice_dependent) if rs.or_items else set()
        self.static = [i for i, r in enumerate(rs.element) if not set(r.lhs.kinds()) & self.kinds_in]
        self.vals: dict[str, dict[Fraction, Region]] = {}
        self._met: dict[int, Region] = {}
        self.conflicts: list[tuple[Region, tuple, tuple]] = []
        for K in kinds_of(rs, prob):
            if K in self.kinds_in:
                continue
            claims = prob.pos.get(K, {})
            plain, exact, srcs = EMPTY, {}, []
            for i, o in self.placing(K):
                t = target(o, self.met(i))
                srcs.append((i, o, t))
                if o.value is None:
                    plain = plain | t
                else:
                    exact[o.value] = exact.get(o.value, EMPTY) | t
            self.vals[K] = _values(K, claims, plain, exact, check=False)
            for n, (i, o, t) in enumerate(srcs):
                if o.value is None:
                    continue
                for v, reg in claims.items():
                    if v != o.value:
                        self._add(t & reg, ("rule", i, o), ("claim", K, v))
                for j, p, u in srcs[n + 1:]:
                    if p.value is not None and p.value != o.value:
                        self._add(t & u, ("rule", i, o), ("rule", j, p))
        pres = {k: union(d.values()) for k, d in self.vals.items()}
        for K in set(c.neg) | set(temp):
            if K not in self.kinds_in:
                self._add(pres.get(K, EMPTY) & c.negative(K), ("no", K), ("present", K))
                self._add(pres.get(K, EMPTY) & temp.get(K, EMPTY), ("taken", K), ("present", K))
        for i in self.static:
            for it in rs.element[i].rhs:
                o = it.options[0]
                if not it.is_or and o.negated and o.kind not in self.kinds_in:
                    self._add(pres.get(o.kind, EMPTY) & target(o, self.met(i)), ("rule", i, o), ("present", o.kind))

    def _add(self, where: Region, a: tuple, b: tuple) -> None:
        if where:
            self.conflicts.append((where, a, b))

    def met(self, i: int) -> Region:
        """Where the left side of element rule i is met (it only reads kinds computed before)."""
        if i not in self._met:
            self._met[i] = lhs_region(self.rs.element[i].lhs, self.vals)
        return self._met[i]

    def anchors(self, i: int, o, where: Region) -> Region:
        """The anchors of rule i whose literal o applies somewhere on `where`."""
        base = self.met(i) if o.anchor == "p" else self.met(i).left_edges()
        return base & where.dilate(-o.where)

    def placing(self, K: str) -> list:
        """(i, literal): the rules that place K present, always (no "or")."""
        return [(i, it.options[0]) for i in self.static for it in self.rs.element[i].rhs
                if not it.is_or and not it.options[0].negated and it.options[0].kind == K]

    def line(self) -> Line:
        return Line(self.vals)


class _Resolution:
    """Remove older elements, one conflict after the other, so that the claims are
    consistent again (see the top of this file)."""

    def __init__(self, rs: RuleSet, old_line: Line, c1: Claims, mand_pos, mand_neg, temp, footprint: Region) -> None:
        self.rs, self.old_line, self.c1, self.temp = rs, old_line, c1, temp
        self.mand_pos, self.mand_neg = mand_pos, mand_neg
        self.older = _older_elements(c1, mand_pos, mand_neg)
        R = (len(rs.order) + 1) * rs.reach + 1
        self.near = footprint.dilate(Interval.closed(-R, R))
        self._lines: dict[frozenset, Line | None] = {}

    def run(self) -> LineState:
        removed: list[int] = []
        while not self.consistent(removed):
            lines = _Lines(self.rs, self.claims(removed), self.temp)
            step = self.step(lines, removed) if lines.conflicts else None
            if not step:
                removed = self.prune(self.fallback(removed), len(removed))
                break
            removed += step
        return LineState(self.claims(removed), self._lines[frozenset(removed)])

    def claims(self, removed) -> Claims:
        return _without(self.c1, [self.older[i] for i in removed])

    def consistent(self, removed) -> bool:
        key = frozenset(removed)
        if key not in self._lines:
            try:
                self._lines[key] = solve(self.rs, self.claims(removed).problem(self.temp))
            except (Conflict, Undetermined, IncompatiblePeriods):
                self._lines[key] = None
        return self._lines[key] is not None

    def live(self, removed, name: str, where: Region, value=None) -> frozenset:
        """The older elements still there, of this name, that meet `where`."""
        gone = set(removed)
        idx = [i for i, (n, _, v) in enumerate(self.older) if i not in gone and n == name and (value is None or v == value)]
        if not idx:
            return frozenset()
        meets = _Meets(where, [self.older[i][1] for i in idx])
        return frozenset(i for i in idx if meets(self.older[i][1]))

    # ------------------------------------------------------------ one conflict

    def step(self, lines: _Lines, removed) -> list[int] | None:
        """The removal that resolves the leftmost conflict, or None."""
        X, a, b = min(lines.conflicts, key=lambda c: c[0].inf())
        opts = set(self.give_way(lines, a, X, removed)) | set(self.give_way(lines, b, X, removed))
        opts.discard(frozenset())
        works = []
        for opt in sorted(opts, key=lambda o: (len(o), sorted(o))):
            if any(w <= opt for w, _ in works):
                continue  # only what is necessary
            after = _Lines(self.rs, self.claims(removed + sorted(opt)), self.temp)
            if any(w & X and {a2, b2} == {a, b} for w, a2, b2 in after.conflicts):
                continue
            works.append((opt, after))
        best = None
        for opt, after in works:
            lost = _lost(self.old_line, after.line(), Region.universe()) + sum(1 for i in opt if self.older[i][0].startswith("no "))
            cand = (lost, _dropped([self.older[i] for i in opt]))
            if best is None or _better(cand, best[0], self.rs):
                best = (cand, opt)
        return sorted(best[1]) if best else None

    def give_way(self, lines: _Lines, side: tuple, X: Region, removed) -> list[frozenset]:
        """The combinations of older elements whose removal drops this requirement on X."""
        if side[0] == "rule":
            _, i, o = side
            A = lines.anchors(i, o, X)
            return self.falsify(lines, self.rs.element[i].lhs, A, removed) if A else []
        if side[0] == "claim":
            _, K, v = side
            if self.mand_pos.get(K, {}).get(v, EMPTY) & X:
                return []  # the edit's own
            s = self.live(removed, K, X, v)
            return [s] if s else []
        if side[0] == "no":
            K = side[1]
            if self.mand_neg.get(K, EMPTY) & X:
                return []
            s = self.live(removed, "no " + K, X)
            return [s] if s else []
        if side[0] == "present":
            return self.absent(lines, side[1], X, removed)
        return []  # "taken": the edit's own

    def absent(self, lines: _Lines, K: str, Q: Region, removed) -> list[frozenset]:
        """Combinations that make K absent on Q: its older claims there go, and each rule
        that places K there stops applying."""
        if K in lines.kinds_in or any(reg & Q for reg in self.mand_pos.get(K, {}).values()):
            return []
        choices = [[self.live(removed, K, Q)]]
        for i, o in lines.placing(K):
            A = lines.anchors(i, o, Q)
            if A:
                sub = self.falsify(lines, self.rs.element[i].lhs, A, removed)
                if not sub:
                    return []
                choices.append(sub)
        return _combine(choices)

    def falsify(self, lines: _Lines, f, Q: Region, removed) -> list[frozenset]:
        """Combinations that make the left side f false on Q, by removing elements only:
        a term that holds where a kind is absent (not, K 0%, K < p%) cannot be made false."""
        if isinstance(f, Atom):
            if f.test(Fraction(0)) or f.kind in lines.kinds_in:
                return []
            true = Q & f.region(lines.vals, Region.universe(), EMPTY)
            return self.absent(lines, f.kind, true, removed) if true else [frozenset()]
        if isinstance(f, Not):
            return []
        if isinstance(f, Or):
            return _combine([self.falsify(lines, p, Q, removed) for p in f.parts])
        if isinstance(f, And):  # one term or more made false: the combinations of the requirement
            out = []
            for n in range(1, len(f.parts) + 1):
                for some in itertools.combinations(f.parts, n):
                    out += _combine([self.falsify(lines, p, Q, removed) for p in some])
            return _cap(out)
        return []

    # ------------------------------------------------------------ otherwise

    def fallback(self, removed) -> list[int]:
        """Older elements removed one after the other, near the edit first, then from left
        to right, until the claims are consistent (with none left, only the edit stays,
        which is consistent: step 2)."""
        rank = lambda i: self.rs.rank.get(self.older[i][0].removeprefix("no "), 10 ** 6)
        order = sorted((i for i in range(len(self.older)) if i not in removed),
                       key=lambda i: (not (self.older[i][1] & self.near), self.older[i][1].inf(), rank(i)))
        cur = list(removed)
        for i in order:
            cur.append(i)
            if self.consistent(cur):
                return cur
        raise Refused("no removal of older elements resolves the conflict")

    def prune(self, removed, start: int) -> list[int]:
        """An element the fallback removed (removed[start:]) is put back if the claims
        stay consistent with it, the last removed first. (Each conflict's own removal is
        already only what is necessary.)"""
        cur = list(removed)
        for i in reversed(removed[start:]):
            trial = [j for j in cur if j != i]
            if self.consistent(trial):
                cur = trial
        return cur


class _Meets:
    """reg & where != {} for many bounded elements reg, by binary search on the
    intervals of `where` where they lie (each test is then logarithmic, not linear)."""

    def __init__(self, where: Region, regs: list[Region]) -> None:
        self.where = where
        ends = [e for reg in regs if reg.is_bounded() for iv in reg.bounded().intervals for e in (iv.lo, iv.hi)]
        if ends:
            lo, hi = min(ends), max(ends)
            self.ivs = where.on(lo, hi + 1).intervals
            self.los = [iv.lo for iv in self.ivs]

    def __call__(self, reg: Region) -> bool:
        if not reg.is_bounded():
            return bool(reg & self.where)
        for iv in reg.bounded().intervals:
            j = bisect.bisect_right(self.los, iv.hi) - 1
            if j >= 0 and self.ivs[j].hi >= iv.lo:
                return True
        return False


def _combine(choices: list[list[frozenset]]) -> list[frozenset]:
    """One combination from each list, together; none if a list has none."""
    if any(not c for c in choices):
        return []
    out = [frozenset()]
    for c in choices:
        out = _cap([a | b for a in out for b in c])
    return out


def _cap(opts) -> list[frozenset]:
    return sorted(set(opts), key=lambda o: (len(o), sorted(o)))[:MAX_OPTIONS]


def _without(c1: Claims, drop) -> Claims:
    c = c1.copy()
    for name, reg in _dropped(drop).items():
        if name.startswith("no "):
            c.remove_neg(name[3:], reg)
        else:
            c.remove_pos(name, reg)
    return c


def _dropped(drop) -> dict[str, Region]:
    """{name: the union of its elements} (bounded ones in one step: they are many)."""
    bounded: dict[str, list] = {}
    out: dict[str, Region] = {}
    for name, reg, *_ in drop:
        if reg.is_bounded():
            bounded.setdefault(name, []).extend(reg.bounded().intervals)
        else:
            out[name] = out.get(name, EMPTY) | reg
    for name, ivs in bounded.items():
        out[name] = out.get(name, EMPTY) | Region.of(IntervalSet(ivs))
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
