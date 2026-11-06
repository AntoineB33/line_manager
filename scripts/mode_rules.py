"""The mode rules acting at the chip, one position after the other.

A mode rule can only modify what is at the chip's position, a single point. When the
chip is at x (a position x + kε: it passes x+ε, x+2ε, ... as it leaves x), the mode
rules whose mode is the mode at x are decided at x, in their order, on the line as it
is at that moment:
- a rule whose left side is not met at x does nothing;
- for each item, the leftmost operand that can be chosen: one that already holds at x
  (nothing to do), else the first whose edit on {x} is accepted: it is resolved like
  any edit (resolve.py) and must respect M1 (guards.py) with the chip at x: only the
  claims at x change, nothing changes on (-inf, x). The chip gets to x after the user's
  edits made before, so it wins over them there (resolve.mode_won);
- if no operand is accepted, the item does nothing at x.
So what the chip has gone through is decided, and nothing on its right.

How it is computed exactly. Between two positions the chip goes through infinitely
many, but the decision only changes at a few. From a position m where it was decided,
the same decision goes on (a run) over [m, x) as x moves right. The claims and the
line then have fixed ends, and ends that move with x, at x + d (d a sum of rule
distances along the rules). Whether the decision at x is the same changes only where
a moving end crosses a fixed one: at e - d + jε, e an end of the state or m, d a
multiple of g (the gcd of the rule distances) with |d| <= 2 * depth * reach, |j| <= 2.
The run is checked at each of those positions and at the first, middle and last
position between two of them; a check that fails between them refuses the rest of
the positions rather than guessing.

A run is compiled ahead (`_Plan`): decided once, it is kept from one step of the chip
to the next, its positions being those of the state where it started, and each stretch
between two of them is checked once, up to its end, before the chip gets there. On
such a stretch the line has the same ends, each fixed or moving with the chip (above):
the first and middle checks give which ones move, and the last check must be that
line exactly. A step of the chip inside a checked stretch then only moves those ends,
with no solve (`_affine`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

from constraints import ChipPush, pushes_in
from guards import check_mode_edit, same_dict, user_claims
from intervals import EPS, INF, Interval, IntervalSet, Pos
from line import GEN, MODE, LineState, Op
from region import IncompatiblePeriods, Region
from resolve import Refused, apply_edit
from rules import RuleSet, _fgcd
from solver import Conflict, Line, Undetermined, checkpoint, lhs_region, solve

HOLDS, PUT, NONE = "holds", "put", "none"
PUSHING = "pushing"  # (the last item of a decision, where the mode rules keep pushes: `decide`)


@dataclass
class Run:
    """A change of the mode rules: one decision on the positions `at`, the first being
    `start`. It is continuous: each position changes when the chip is there."""
    start: Pos
    at: Region
    before: LineState
    after: LineState
    end: Pos | None = None  # the last position of `at`
    plan: object = None  # the run compiled ahead that makes it (a stretch), or None (a decision at `start`)


@dataclass
class Stretch:
    """The mode rules compiled by a generator run: with the chip at a position p of
    [a, b] in `mode`, the line having been `before` when the chip was at a, the state is
    `state_at(p)` (a decision at a = b, or a stretch of a compiled run)."""
    mode: str
    a: Pos
    b: Pos
    before: LineState
    after: LineState  # at b
    plan: object = None

    def state_at(self, p: Pos) -> LineState:
        return self.after if self.plan is None or p == self.b else self.plan.state_at(p)


def compile_runs(rs: RuleSet, state: LineState, segments, constraints=(), pushes=()) -> tuple[list[Stretch], list[tuple[Pos, str]]]:
    """The mode rules along the chip's positions [lo, hi] of each (lo, hi, mode) of
    `segments`, from `state`, the constraints kept after the chip, and the script's
    pushes (constraints.ChipPush) in their modes: the stretches they make, and the
    operands refused."""
    out, refused, mrs = [], [], {}
    for lo, hi, mode in segments:
        mr = mrs.get(mode) or mrs.setdefault(mode, ModeRules(rs, mode, tuple(constraints) + pushes_in(pushes, mode)))
        for ev in mr.act(state, lo, hi):
            if isinstance(ev, Run):
                plan = ev.plan.frozen() if ev.plan is not None else None
                out.append(Stretch(mode, ev.start, ev.end, ev.before, ev.after, plan))
                state = ev.after
            else:
                refused.append(ev)
    return out, refused


class ModeRules:
    def __init__(self, rs: RuleSet, mode: str, constraints=()) -> None:
        self.rs, self.mode = rs, mode
        self.constraints = tuple(constraints)  # kept after the chip (constraints.py): `constrain`
        # the runs after `plan`, decided and checked ahead in another process (`take_ahead`),
        # each starting right after the one before: taken where the chip gets to its start
        # on the line it was decided on, with nothing to solve (`act`)
        self.ready: list[_Plan] = []
        self.pushes = tuple(k for k in self.constraints if isinstance(k, ChipPush))  # (constraints.ChipPush)
        self.rules = [r for r in rs.mode_rules if r.mode == mode]
        self.items = [(r, it) for r in self.rules for it in r.rhs]
        dists = [abs(e.x) for r in rs.element for o in r.lits() for e in (o.where.lo, o.where.hi) if type(e) is Pos]
        g = None
        for d in dists:
            if d:
                g = d if g is None else _fgcd(g, d)
        self.g = g
        # how far a moving end can be from x
        self.span = 2 * (len(rs.order) + 1) * rs.reach
        self.plan: _Plan | None = None  # the run being compiled ahead
        self.kept: Pos | None = None  # the position where the user edited after the chip got there
        # the kinds whose ends can change a decision: those of the mode rules and the
        # constraints, and the kinds linked to them through the rules (rs.linked); None:
        # every kind (a constraint that does not tell its kinds)
        read = {k for r in self.rules for k in r.lhs.kinds()} | {o.kind for r in self.rules for o in r.written_lits()}
        for k in self.constraints:
            read = None if read is None or not hasattr(k, "kinds") else read | set(k.kinds())
        linked = getattr(rs, "linked", {})
        groups = None if read is None else {linked[k] for k in read if k in linked}
        self.reads = None if read is None else (lambda k: k in read or linked.get(k) in groups)

    # ------------------------------------------------------------ one position

    def decide(self, state: LineState, x: Pos) -> tuple[tuple, LineState, list[str]]:
        """The decision at x on this state, the state after it, and why operands were refused."""
        s, out, why = state, [], []
        pushing = self.pushing(state, x)  # (on the line as the chip gets to x: before the rules act there)

        def item(r, it, s):
            """(the choice of an item at x on s, the state after it)."""
            for oi, o in enumerate(it.options):
                if _holds(o, s, x):
                    return (HOLDS, oi), s
                at = Region.point(x)
                op = Op.put_no(o.kind, at) if o.negated else Op.put(o.kind, at, o.value or 100)
                try:
                    after = apply_edit(self.rs, s, [op], MODE)
                except Refused as e:
                    why.append(f"{r}: {o} at {x}: {e}")
                    continue
                reason = check_mode_edit(s, after, at, x)
                if x == self.kept and not same_dict(user_claims(s.claims), user_claims(after.claims)):
                    reason = "N2: the user edited this position after the chip got there"
                if reason:
                    why.append(f"{r}: {o} at {x}: {reason}")
                    continue
                return (PUT, oi, _gone(s, after, x, o.kind)), after
            return (NONE,), s
        for r in self.rules:
            met = lhs_region(r.lhs, s.line.vals).contains(x)
            for it in r.rhs:
                if not met:
                    out.append(None)
                    continue
                choice, s = item(r, it, s)
                out.append(choice)
        # an item found already true that a later one made false ("mode 2 → NoScreen & no
        # Break20s" with the chip in a 20s break: "no screen" held by the break, which the
        # second item takes off): made true again, in order, until each holds
        for _ in range(len(self.items)):
            redo = [i for i, ((r, it), choice) in enumerate(zip(self.items, out))
                    if choice and choice[0] == HOLDS and not _holds(it.options[choice[1]], s, x)]
            if not redo:
                break
            for i in redo:
                r, it = self.items[i]
                out[i], s = item(r, it, s)
        if self.pushes:
            out.append((PUSHING, pushing))  # (a run that starts pushing a kind pushes it all along)
            return tuple(out), self._constrained(s, x, pushing), why
        return tuple(out), self._constrained(s, x), why

    def pushing(self, state: LineState, p: Pos) -> frozenset:
        """The kinds the chip at p pushes on this state (constraints.ChipPush.on)."""
        return frozenset(k.kind for k in self.pushes if k.on(state, p))

    @staticmethod
    def pushing_of(decision: tuple) -> frozenset | None:
        """The kinds a decision pushes (`decide`); None if the mode rules push nothing."""
        last = decision[-1] if decision else None
        return last[1] if last and last[0] == PUSHING else None

    def with_run(self, state: LineState, decision: tuple, at: Region, pushing: frozenset | None = None) -> LineState:
        """The state with the decision's edits on `at`: the operands placed, and the older
        claims the edit removed at its position (M1: at that position only) removed on
        `at` too. `pushing`: the kinds the chip pushes all along the run (where it started:
        each position of the run pushes them again, so the chip is still on them at the
        last, whatever the run removed of them behind it); None: read on the state."""
        p = at.bounded().sup()
        claims = state.claims
        if pushing:
            claims = self.pushed_behind(claims, pushing, at.bounded().inf() - EPS, p)
            state = LineState(claims, state.line, state.history)  # (its line: solved below)
        c = self.run_claims(state, decision, at)
        if c is None:
            if not pushing:
                return state
            c = claims
        return self._constrained(LineState(c, solve(self.rs, c.problem()), state.history), p, pushing)

    def pushed_behind(self, claims, pushing: frozenset, m: Pos, p: Pos):
        """The claims with what the chip pushing these kinds from m leaves behind it on
        (m, p] (constraints.ChipPush.behind), before the run's own edits there: each
        position of it was after the chip, pushed, until the chip went through it."""
        c = None
        went = Region.of(Interval(m, p, False, True))
        for k in self.constraints:
            if isinstance(k, ChipPush) and k.kind in pushing:
                c = c or claims.copy()
                d = c.pos.setdefault(k.kind, {})
                for key in [key for key in d if key[1] == GEN]:
                    d[key] = d[key] - went
                    if not d[key]:
                        del d[key]
                if k.behind():
                    key = (Fraction(100), GEN)
                    d[key] = d[key] | went if key in d else went
        return claims if c is None else c

    def _targets(self, k, st: LineState, p: Pos, pushing: frozenset | None) -> dict:
        if pushing is not None and isinstance(k, ChipPush):
            return k.from_chip(p) if k.kind in pushing else {}
        return k.targets(st, p)

    def constrain(self, claims, line: Line, p: Pos, history=(), give_way: bool = False, pushing: frozenset | None = None):
        """The claims with the constraints' elements after p where they want them, as the
        generator's (GEN; its script keeps them as the chip goes): with the chip at p in
        mode 1, the break it would eat moves on after it. `give_way`: the generator's other
        elements are taken off where those go (the next run places them again). `pushing`:
        as `with_run`."""
        if not self.constraints:
            return claims
        st, c = LineState(claims, line, history), claims.copy()
        later = Region.of(Interval(p, INF, False, False))
        targets = [self._targets(k, st, p, pushing) for k in self.constraints]
        wanted = {kind: want for t in targets for kind, want in t.items()}
        if give_way:
            moved = Region.empty()
            for want in wanted.values():
                moved = moved | (want or Region.empty())
            for kind, d in c.pos.items():
                if kind not in wanted:
                    for key in [key for key in d if key[1] == GEN]:
                        d[key] = d[key] - moved
                        if not d[key]:
                            del d[key]
        for t in targets:
            for kind, want in t.items():
                d = c.pos.setdefault(kind, {})
                for key in list(d):
                    if key[1] == GEN:
                        d[key] = d[key] - later
                        if not d[key]:
                            del d[key]
                if want:
                    key = (Fraction(100), GEN)
                    d[key] = d[key] | want if key in d else want
        return c

    def _constrained(self, state: LineState, p: Pos, pushing: frozenset | None = None) -> LineState:
        """The state with the constraints kept after p (`constrain`), solved."""
        if not self.constraints:
            return state
        c = self.constrain(state.claims, state.line, p, state.history, pushing=pushing)
        if c.same(state.claims) is True:
            return state
        try:
            return LineState(c, solve(self.rs, c.problem()), state.history)
        except Conflict:  # the generator's other elements are in the way
            c = self.constrain(state.claims, state.line, p, state.history, give_way=True, pushing=pushing)
            return LineState(c, solve(self.rs, c.problem()), state.history)

    def run_claims(self, state: LineState, decision: tuple, at: Region):
        """The claims of `with_run`, or None if the decision places nothing."""
        if not any(choice and choice[0] == PUT for choice in decision):
            return None
        c = state.claims.copy()
        for (r, it), choice in zip(self.items, decision):
            if choice and choice[0] == PUT:
                for kind, positive in choice[2]:
                    (c.remove_pos if positive else c.remove_neg)(kind, at)
                o = it.options[choice[1]]
                if o.negated:
                    c.add_neg(o.kind, at, MODE)
                else:
                    c.add_pos(o.kind, at, o.value or 100, MODE)
        return c

    # ------------------------------------------------------------ the positions of a sweep

    def candidates(self, state: LineState, m: Pos, lo: Pos, hi: Pos, pushing: frozenset | None = None) -> list[Pos]:
        """The positions of [lo, hi] where a run started at m can change its decision: the
        ends of the kinds it reads (`reads`; the others cannot change it). A kind the chip
        pushes from m (constraints.ChipPush) is made again from the chip at each position:
        its ends after m in this state, and those of the line there (what comes with it),
        move with the chip and are not where the decision changes; the claims of the user
        and of the mode rules stay where they are, and their ends are looked at."""
        keep = self.reads or (lambda k: True)
        span = self.span
        a, b = lo.x - span - 1, hi.x + span + 1
        pushed = pushing or frozenset()
        moving = set()
        for kind in pushed:
            for reg in [r for (_, owner), r in state.claims.pos.get(kind, {}).items() if owner == GEN] + \
                    list(state.line.vals.get(kind, {}).values()):
                for iv in reg.on(a, b).intervals:
                    moving.update(e for e in (iv.lo, iv.hi) if type(e) is Pos and e > m)
        regions = [r for k, d in state.claims.pos.items() if keep(k) for (_, owner), r in d.items()
                   if not (k in pushed and owner == GEN)]
        regions += [r for k, d in state.claims.neg.items() if keep(k) for r in d.values()]
        regions += [r for k, d in state.claims.taken.items() if keep(k) for r in d.values()]
        fixed = [r for k, d in state.line.vals.items() if keep(k) for r in d.values()]
        ends = {m}
        for reg in regions:
            for iv in reg.on(a, b).intervals:
                ends.update(e for e in (iv.lo, iv.hi) if type(e) is Pos)
        for reg in fixed:
            for iv in reg.on(a, b).intervals:
                ends.update(e for e in (iv.lo, iv.hi) if type(e) is Pos and e not in moving)
        for k in self.pushes:  # (a push that starts once its trigger has lasted: no end of the line there)
            ends.update(k.flips(state.line, a, b))
        out = set()
        for e in ends:
            if self.g is None:
                ks = [0]
            else:
                n = int(span // self.g)
                ks = range(max(-n, math.ceil((e.x - hi.x - 1) / self.g)), min(n, math.floor((e.x - lo.x + 1) / self.g)) + 1)
            for k in ks:
                base = e - (k * self.g if self.g else 0)
                for j in range(-self.rs.eps_reach, self.rs.eps_reach + 1):
                    c = base + EPS * j
                    if lo <= c <= hi:
                        out.add(c)
        return sorted(out)

    def act(self, state: LineState, lo: Pos, hi: Pos):
        """The chip goes through [lo, hi]: yields Run (a change) and (position, reason)
        (an operand refused, or the rest of the positions refused); returns at the end.
        A run compiled at an earlier step goes on if the line is still the one it made."""
        x = lo
        while x <= hi:
            plan = self.plan
            if plan is not None and plan.last is state and plan.next == x:
                nxt = x
            elif plan is not None and plan.pending is not None and plan.before is state and plan.m == x:
                why, s1 = plan.pending  # decided ahead (`prepare`): it happens now
                plan.pending = None
                for w in why:
                    yield x, w
                if s1 is not state:
                    yield Run(x, Region.point(x), state, s1, x)
                state = plan.last = s1
                nxt = x + EPS
                if nxt > hi:
                    return
            elif self._take_ready(state, x):
                continue  # (decided ahead, elsewhere: it happens now, as decided ahead here)
            else:
                decision, s1, why = self.decide(state, x)
                for w in why:
                    yield x, w
                if s1 is not state:
                    yield Run(x, Region.point(x), state, s1, x)
                state = s1
                plan = self.plan = _Plan(self, s1, decision, x)
                nxt = x + EPS
                if nxt > hi:
                    return
            try:
                end = plan.extend(nxt, hi)
            except _Changes as e:
                self.plan = None
                yield e.at, "mode rules: the decision changes at a position that is not looked at; the rest is refused"
                return
            if end is None:  # the decision changes at nxt
                self.plan = None
                x = nxt
                continue
            state2 = plan.state_at(end)
            if state2 is not state:
                yield Run(nxt, Region.of(_iv(nxt, end)), state, state2, end, plan)
            state = plan.last = state2
            plan.next = end + EPS
            x = end + EPS
        return

    def prepare(self, state: LineState, x: Pos, ahead=Fraction(1)) -> None:
        """Compile ahead the run the chip at x (not gone through yet) starts, checked up to
        x + ahead at least: the line is not changed, the decision at x happens when the
        chip goes through x (`act`)."""
        plan = self.plan
        if plan is not None and ((plan.last is state and plan.next == x) or
                                 (plan.pending is not None and plan.before is state and plan.m == x)):
            return
        decision, s1, why = self.decide(state, x)
        plan = _Plan(self, s1, decision, x)
        plan.before, plan.pending = state, (why, s1)
        self.plan = plan
        try:
            plan.extend(x + EPS, Pos(x.x + ahead))
        except _Changes:
            pass  # the chip will refuse there when it gets there

    # ------------------------------------------------------------ checked ahead elsewhere

    def _take_ready(self, state: LineState, x: Pos) -> bool:
        """The run decided ahead at x on this line (`ready`), if there is one: it becomes
        `plan`, its decision to happen at x (`act`). The runs decided ahead for positions
        the chip went past are dropped; one decided on another line is not taken."""
        while self.ready and self.ready[0].m < x:
            self.ready.pop(0)
        if not self.ready or self.ready[0].m != x or not _same_state(self.ready[0].before, state):
            return False
        plan = self.plan = self.ready.pop(0)
        why, s1 = plan.pending
        if _same_state(s1, state):  # (nothing decided there: the line as it is, the same object)
            plan.state0 = plan.last = s1 = state
            plan.pending = (why, s1)
        plan.before = state
        return True

    def ahead_target(self):
        """The run to check further ahead elsewhere: the last of `plan` and `ready`."""
        return self.ready[-1] if self.ready else self.plan

    def frontier(self) -> Pos | None:
        """How far the chip's runs are known: the last position checked of the last one."""
        t = self.ahead_target()
        return None if t is None else t.checked

    def take_ahead(self, target: _Plan, base: Pos | None, answer) -> bool:
        """The answer of `prepare_ahead` for `target` (a run of `plan` and `ready`), asked
        when it was checked up to `base`: its new stretches, and the runs after it. False
        if that run is gone or was checked further meanwhile (the answer is then of no use)."""
        if target is not self.plan and target not in self.ready:
            return False
        if target.checked != base:
            return False
        more, after = answer
        target.pieces += more["pieces"]
        target.checked, target.over, target.queue, target.top = more["checked"], more["over"], more["queue"], more["top"]
        if target is self.plan:
            self.ready = []
        else:
            self.ready = self.ready[:self.ready.index(target) + 1]
        self.ready += [_Plan.of_data(self, d) for d in after]
        return True

    def _same(self, state: LineState, decision: tuple, lo: Pos, p: Pos,
              pushing: frozenset | None = None) -> tuple[bool, LineState | None]:
        """With the run on [lo, p), is the decision at p the same? And the state with the
        run on [lo, p]. `pushing`: as `with_run`."""
        try:
            before = self.with_run(state, decision, Region.of(_iv(lo, p - EPS)), pushing) if lo < p else state
            d, after, _ = self.decide(before, p)
        except (Conflict, Undetermined, IncompatiblePeriods):
            return False, None
        return d == decision, after


LOOK_AHEAD = 256  # how many reaches a stretch looks ahead for where a decision can change


class _Plan:
    """A run compiled ahead: decided at m on `state0`, the same decision on [m + ε,
    checked], `checked` growing one stretch at a time (between two positions where the
    decision can change, or one such position). Each stretch keeps its checked states,
    and the line as an affine function of the chip's position when one is found."""

    def __init__(self, mr: ModeRules, state0: LineState, decision: tuple, m: Pos) -> None:
        self.mr, self.state0, self.decision, self.m = mr, state0, decision, m
        self.a0 = m + EPS
        self.checked: Pos | None = None
        self.over = False  # the decision differs right after `checked`
        self.pieces: list[tuple[Pos, Pos, object, dict]] = []  # (a, b, line of p or None, {p: state})
        self.queue: list[Pos] = []  # the positions where the decision can change, ahead
        self.top: Pos | None = None  # how far they were looked for
        self.last, self.next = state0, self.a0  # the state the run made last, and where it goes on
        self.before, self.pending = None, None  # decided ahead of the chip (`prepare`): the state before, (why, after)
        self.pushing = mr.pushing_of(decision)  # the kinds the chip pushes all along the run (`with_run`)

    FIELDS = ("state0", "decision", "m", "a0", "checked", "over", "pieces", "queue", "top", "last", "next", "before",
              "pending", "pushing")

    def data(self) -> dict:
        """What it is, without its mode rules: for another process (`prepare_ahead`)."""
        return {f: getattr(self, f) for f in self.FIELDS}

    @staticmethod
    def of_data(mr: ModeRules, d: dict) -> _Plan:
        out = object.__new__(_Plan)
        out.mr = mr
        for f in _Plan.FIELDS:
            setattr(out, f, d[f])
        out.pieces, out.queue = list(out.pieces), list(out.queue)
        return out

    def extend(self, nxt: Pos, hi: Pos):
        """The last position of [nxt, hi] the run reaches (None: not nxt), checking the
        stretches up to hi that are not yet."""
        self.pieces = [pc for pc in self.pieces if pc[1] >= nxt]
        while not self.over and (self.checked is None or self.checked < hi):
            checkpoint()
            self._stretch(hi)
        if self.checked is None or self.checked < nxt:
            return None
        return self.checked if self.checked < hi else hi

    def _stretch(self, hi: Pos) -> None:
        cur = self.a0 if self.checked is None else self.checked + EPS
        if not self.queue and (self.top is None or self.top < cur):
            # (with no lattice, finding them costs the same however far: the longer the stretch,
            # the fewer are checked, and a run that goes on is what mode rules mostly do)
            g = self.mr.g or LOOK_AHEAD * (self.mr.rs.reach or Fraction(1))
            self.top = Pos(max(hi.x, cur.x) + g)
            self.queue = self.mr.candidates(self.state0, self.m, cur, self.top, self.pushing)
        self.queue = [c for c in self.queue if c >= cur]
        if self.queue and self.queue[0] == cur:
            a = b = self.queue.pop(0)
        else:
            a, b = cur, (self.queue[0] - EPS if self.queue else self.top)
        reps = [a] if a == b else [a] + ([Pos((a.x + b.x) / 2)] if a.x < b.x else []) + [b]
        states = {}
        for i, p in enumerate(reps):
            ok, st = self.mr._same(self.state0, self.decision, self.a0, p, self.pushing)
            if not ok:
                if i == 0:
                    self.over = True
                    return
                raise _Changes(p)
            states[p] = st
        line = None
        if len(reps) == 3:
            line = _affine(states[reps[0]].line, states[reps[1]].line, reps[0], reps[1] - reps[0])
            if line is not None:
                try:
                    if not _same_line(line(b), states[b].line):
                        line = None
                except _NotAffine:
                    line = None
        self.pieces.append((a, b, line, states))
        self.checked = b

    def frozen(self) -> _Plan:
        """A copy that keeps its checked stretches (the run goes on without it)."""
        out = object.__new__(_Plan)
        out.__dict__.update(self.__dict__)
        out.pieces = list(self.pieces)
        return out

    def state_at(self, p: Pos) -> LineState:
        """The state with the run on [m + ε, p], p checked."""
        s = self.exact(p)
        return s if s is not None else self.mr.with_run(self.state0, self.decision, Region.of(_iv(self.a0, p)), self.pushing)

    def formula_until(self, p: Pos) -> Pos | None:
        """Where the formula of the line at p stops; None if there is none at p."""
        for a, b, line, _ in list(self.pieces):
            if a <= p <= b:
                return b if line is not None else None
        return None

    def exact(self, p: Pos) -> LineState | None:
        """That state, where the run's formula gives it with nothing to solve; else None."""
        for a, b, line, states in list(self.pieces):
            if a <= p <= b:
                if p in states:
                    return states[p]
                if line is not None:
                    try:
                        s0 = self.state0
                        if self.pushing:
                            s0 = LineState(self.mr.pushed_behind(s0.claims, self.pushing, self.m, p), s0.line, s0.history)
                        c = self.mr.run_claims(s0, self.decision, Region.of(_iv(self.a0, p)))
                        if c is None:
                            if not self.pushing:
                                return self.state0
                            c = s0.claims
                        lp = line(p)
                        return LineState(self.mr.constrain(c, lp, p, self.state0.history, pushing=self.pushing), lp,
                                         self.state0.history)
                    except _NotAffine:
                        pass
                break
        return None

class _NotAffine(Exception):
    pass


def _affine(l1: Line, l2: Line, p1: Pos, s):
    """The line at p as a function of p, from l1 at p1 and l2 at p1 + s: each end of each
    region fixed or moving with p (`_affine_region`); None if they are not so."""
    if set(l1.vals) != set(l2.vals) or any(set(l1.vals[k]) != set(l2.vals[k]) for k in l1.vals):
        return None
    parts = {}
    for k, d in l1.vals.items():
        parts[k] = {}
        for v, r1 in d.items():
            f = _affine_region(r1, l2.vals[k][v], s)
            if f is None:
                return None
            parts[k][v] = f
    return _AffineLine(parts, p1)


class _AffineLine:
    """The line at p: each region an `_AffineRegion` of p - p1."""

    def __init__(self, parts: dict, p1: Pos) -> None:
        self.parts, self.p1 = parts, p1

    def __call__(self, p: Pos) -> Line:
        return Line({k: {v: f(p - self.p1) for v, f in d.items()} for k, d in self.parts.items()})


def _affine_region(r1: Region, r2: Region, s):
    """r(d) with r(0) = r1 and r(s) = r2, each end fixed or moving by d, the left part
    fixed and the right one fixed or moving; None if r1 and r2 are not so."""
    try:
        if r1 == r2:
            return _AffineRegion(r1, [], r1, False)
        A = r1.lo if r1.lo <= r2.lo else r2.lo
        X = r1.hi if r1.hi >= r2.hi else r2.hi
        left = Region.of(Interval(-INF, A, False, False))
        if (r1 & left) != (r2 & left):
            return None
        right = Region.of(Interval(X, INF, True, False))
        tail = r1 & right
        moves = (r2 & right) != tail
        if moves and (r2 & Region.of(Interval(X + s, INF, True, False))) != tail.shift(s):
            return None
    except IncompatiblePeriods:
        return None
    i1 = r1.on(A, X).intervals
    i2 = r2.on(A, X + s if moves else X).intervals
    if len(i1) != len(i2):
        return None
    ends = []
    for a, b in zip(i1, i2):
        lo, hi = _moved(a.lo, b.lo, s), _moved(a.hi, b.hi, s)
        if lo is None or hi is None:
            return None
        ends.append((a.lo, lo, a.hi, hi))
    return _AffineRegion(r1 & left, ends, tail, moves)


class _AffineRegion:
    """r(d): the fixed part on the left, the intervals between (each end fixed or moving
    by d), and the part on the right, fixed or moving by d."""

    def __init__(self, fixed: Region, ends: list, tail: Region, moves: bool) -> None:
        self.fixed, self.ends, self.tail, self.moves = fixed, ends, tail, moves

    def __call__(self, d) -> Region:
        if not self.ends and not self.moves:
            return self.tail
        ivs = []
        for lo, lm, hi, hm in self.ends:
            x, y = (lo + d if lm else lo), (hi + d if hm else hi)
            if not x <= y:
                raise _NotAffine
            ivs.append(Interval._mk(x, y))
        return Region.of(IntervalSet(ivs)) | self.fixed | (self.tail.shift(d) if self.moves else self.tail)

def _moved(e1, e2, s):
    """True if the end moved by s, False if it stayed, None otherwise."""
    if e1 == e2:
        return False
    if type(e1) is Pos and type(e2) is Pos and e2 == e1 + s:
        return True
    return None


def _same_line(l1: Line, l2: Line) -> bool:
    if set(l1.vals) != set(l2.vals):
        return False
    try:
        return all(set(l1.vals[k]) == set(l2.vals[k]) and all(l1.vals[k][v] == l2.vals[k][v] for v in l1.vals[k])
                   for k in l1.vals)
    except IncompatiblePeriods:
        return False


def _same_state(a: LineState, b: LineState) -> bool:
    """The same line and claims (two copies of a state: one from another process)."""
    if a is b:
        return True
    try:
        return _same_line(a.line, b.line) and a.claims.same(b.claims) is True
    except IncompatiblePeriods:
        return False


AHEAD = Fraction(128)  # how far after the chip its runs are checked elsewhere (Simulation.ahead_jobs)
RUNS_AHEAD = 4  # the runs after the chip's decided elsewhere at once, at most


def prepare_ahead(rs: RuleSet, state0: LineState, mode: str, constraints, data: dict, hi: Pos):
    """In another process (edit_worker.py, job "ahead"): the run `data` (a _Plan of these
    mode rules, `state0` apart) checked up to hi, and if it ends before, the runs after it,
    each decided where the one before ends, on the line it makes there, and checked up to
    hi too. Answers (the run's new stretches and frontier, [the data of the runs after])
    for ModeRules.take_ahead. Where a check fails between two positions (_Changes) it
    stops there: the chip refuses the rest itself when it gets there, as it does now."""
    mr = ModeRules(rs, mode, constraints)
    plan = _Plan.of_data(mr, dict(data, state0=state0))
    n = len(plan.pieces)
    after = []
    try:
        while not plan.over and (plan.checked is None or plan.checked < hi):
            plan._stretch(hi)
        last = plan
        while last.over and last.checked is not None and last.checked < hi and len(after) < RUNS_AHEAD:
            x = last.checked + EPS
            mr.plan = None
            mr.prepare(last.state_at(last.checked), x, hi.x - x.x)
            nxt = mr.plan
            if nxt is None or nxt.checked is None:
                break
            after.append(nxt.data())
            last = nxt
    except (_Changes, Conflict, Undetermined, IncompatiblePeriods, Refused):
        pass
    more = {"pieces": plan.pieces[n:], "checked": plan.checked, "over": plan.over, "queue": plan.queue, "top": plan.top}
    return more, after


class _Changes(Exception):
    def __init__(self, at: Pos) -> None:
        self.at = at


def _iv(a: Pos, b: Pos) -> Interval:
    return Interval(a, b)


def _gone(before: LineState, after: LineState, x: Pos, placed: str) -> tuple:
    """(kind, positive): the claims at x the edit removed, besides those of the kind it
    places (which the put replaces anyway)."""
    out = []
    for k in sorted(set(before.claims.pos) | set(before.claims.neg)):
        if k == placed:
            continue
        if before.claims.positive(k).contains(x) and not after.claims.positive(k).contains(x):
            out.append((k, True))
        if before.claims.negative(k).contains(x) and not after.claims.negative(k).contains(x):
            out.append((k, False))
    return tuple(out)


def _holds(o, s: LineState, x: Pos) -> bool:
    if o.negated:
        return not s.line.presence(o.kind).contains(x)
    if o.value is not None:
        return s.line.vals.get(o.kind, {}).get(o.value, Region.empty()).contains(x)
    return s.line.presence(o.kind).contains(x)
