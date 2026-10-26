"""The world: the chip moving, modes being revealed, the user editing, scripts running.

Time t >= 0 is exact. The chip's position is a continuous, non-decreasing,
piecewise-linear function of time. Everything happens at exact instants, in this
order at one instant: user edits, then revealed mode transitions, then a run of the
generator if the user edited, then the script's triggers in script order (a rerun
installs the new script, whose triggers are evaluated at once).

Mode rules act at the chip's position: between two instants the chip goes through
the positions P = chip([t0, t1)), all with one mode; each mode rule whose mode and
left side are met there makes an edit on those positions (owner MODE), resolved like
any edit (the "or" operands tried in order). Sweeps are cut at the multiples of half
the smallest rule offset, so the positions of one sweep never act on each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from generator import RulesGenerator, RunStats
from guards import check_script_edit
from intervals import INF, Interval, Num
from line import GEN, MODE, USER, LineState, Op
from modes import ModeTimeline
from region import Region
from resolve import Refused, apply_edit
from script import ChipIn, EditAction, RerunAction, Script, TransitionCount
from solver import lhs_region


class Chip:
    """Piecewise-linear trajectory through (t, x) points: t strictly increasing from 0,
    x non-decreasing; the chip stays at the last x afterwards."""

    def __init__(self, points: list[tuple[object, object]]) -> None:
        self.points = [(Fraction(t), Fraction(x)) for t, x in points]
        if not self.points or self.points[0][0] != 0:
            raise ValueError("the trajectory starts at time 0")
        for (t0, x0), (t1, x1) in zip(self.points, self.points[1:]):
            if not t1 > t0:
                raise ValueError("times must increase")
            if x1 < x0:
                raise ValueError("the chip never moves left")

    def position(self, t: Fraction) -> Fraction:
        pts = self.points
        if t >= pts[-1][0]:
            return pts[-1][1]
        for (t0, x0), (t1, x1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
        raise ValueError("negative time")

    def first_time(self, x: Num) -> Num:
        """min { t : position(t) >= x } (it exists by continuity), or +inf."""
        pts = self.points
        if x <= pts[0][1]:
            return Fraction(0)
        for (t0, x0), (t1, x1) in zip(pts, pts[1:]):
            if x0 < x <= x1:
                return t0 + (t1 - t0) * (x - x0) / (x1 - x0)
        return INF


@dataclass
class UserEdit:
    time: Fraction
    ops: list[Op]


@dataclass
class Change:
    time: Fraction
    chip: Fraction
    source: str  # "user", "script", "mode"
    before: LineState
    after: LineState


@dataclass
class Log:
    changes: list[Change] = field(default_factory=list)
    refused: list[tuple[Fraction, str, str]] = field(default_factory=list)  # (time, source, reason)
    runs: list[tuple[Fraction, Fraction, str, Script, RunStats]] = field(default_factory=list)


class Simulation:
    def __init__(self, state: LineState, generator: RulesGenerator, chip: Chip, modes: ModeTimeline, user_edits: list[UserEdit] = ()) -> None:
        if modes.start != chip.position(Fraction(0)):
            raise ValueError("modes start where the chip starts")
        self.state = state
        self.gen = generator
        self.rules = generator.rules
        self.chip = chip
        self.modes = modes
        self.user_edits = sorted(user_edits, key=lambda e: e.time)
        self.log = Log()
        self.script = Script()
        self.revealed = 0
        self._since_install = 0
        self._was_true: list[bool] = []
        self._fired: list[bool] = []
        h = self.rules.smallest_offset
        self.grid = h / 2 if h else None

    def known(self, t: Fraction):
        return self.modes.known(self.chip.position(t))

    def _install(self, t: Fraction, why: str) -> None:
        c = self.chip.position(t)
        script, stats = self.gen.run(self.state, c, self.known(t))
        self.script = script
        self._since_install = 0
        self._was_true = [False] * len(script.events)
        self._fired = [False] * len(script.events)
        self.log.runs.append((t, c, why, script, stats))

    def _cond(self, i: int, t: Fraction) -> bool:
        trig = self.script.events[i].trigger
        if isinstance(trig, ChipIn):
            c = self.chip.position(t)
            return trig.holds(c, self.modes.mode_at(c))
        return self._since_install >= trig.k

    def _edit(self, t: Fraction, ops: list[Op], owner: str, source: str) -> bool:
        before = self.state
        try:
            after = apply_edit(self.rules, before, ops, owner)
        except Refused as e:
            self.log.refused.append((t, source, str(e)))
            return False
        if source == "script":
            why = check_script_edit(before, after, self.chip.position(t))
            if why:
                self.log.refused.append((t, source, why))
                return False
        self.log.changes.append(Change(t, self.chip.position(t), source, before, after))
        self.state = after
        return True

    def run(self, end_time) -> Log:
        end = Fraction(end_time)
        t = Fraction(0)
        self._instant(t, first=True)
        while t < end:
            t1 = min(self._next_instant(t), end)
            self._sweep(t, t1)
            t = t1
            self._instant(t)
        return self.log

    def _next_instant(self, t: Fraction) -> Num:
        c = self.chip.position(t)
        cands = [e.time for e in self.user_edits if e.time > t]
        nxt = [p for p, _ in self.modes.transitions if p > c]
        if nxt:
            cands.append(self.chip.first_time(nxt[0]))
        for ev in self.script.events:
            if isinstance(ev.trigger, ChipIn):
                for x in (ev.trigger.lo, ev.trigger.hi):
                    if x != INF and x > c:
                        cands.append(self.chip.first_time(x))
        if self.grid and self.rules.mode_rules:
            cands.append(self.chip.first_time((c // self.grid + 1) * self.grid))
        later = [x for x in cands if x > t]
        return min(later, default=INF)

    def _instant(self, t: Fraction, first: bool = False) -> None:
        edited = False
        for e in self.user_edits:
            if e.time == t:
                edited |= self._edit(t, e.ops, USER, "user")
        c = self.chip.position(t)
        revealed = sum(1 for p, _ in self.modes.transitions if p <= c)
        self._since_install += revealed - self.revealed
        self.revealed = revealed
        if first:
            self._install(t, "start")
        elif edited:
            self._install(t, "user edit")
        for _ in range(3):
            rerun = False
            for i, ev in enumerate(self.script.events):
                now = self._cond(i, t)
                rising = now and not self._was_true[i]
                self._was_true[i] = now
                if isinstance(ev.trigger, TransitionCount):
                    rising = now and not self._fired[i]
                if not rising:
                    continue
                self._fired[i] = True
                for a in ev.actions:
                    if isinstance(a, EditAction):
                        self._edit(t, list(a.ops), GEN, "script")
                    elif isinstance(a, RerunAction):
                        rerun = True
                if rerun:
                    break
            if not rerun:
                return
            self._install(t, "script")
        raise AssertionError("reruns did not settle")

    def _sweep(self, t0: Fraction, t1: Fraction) -> None:
        """The mode rules act on the chip positions of [t0, t1)."""
        if t1 <= t0 or not self.rules.mode_rules:
            return
        c0, c1 = self.chip.position(t0), self.chip.position(t1)
        if c1 == c0:
            swept = Region.point(c0)
        else:
            swept = Region.of(Interval(c0, c1, True, self.chip.first_time(c1) < t1))
        mode = self.modes.mode_at(c0)
        for r in self.rules.mode_rules:
            if r.mode != mode:
                continue
            met = swept & lhs_region(r.lhs, self.state.line.vals)
            if not met:
                continue
            for it in r.rhs:
                for o in it.options:  # the leftmost operand that can be chosen
                    op = Op.put_no(o.kind, met) if o.negated else Op.put(o.kind, met, o.value or 100)
                    if self._edit(t0, [op], MODE, "mode"):
                        break
