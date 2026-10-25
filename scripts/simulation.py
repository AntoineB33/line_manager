"""The world: the chip moving, modes being revealed, the user editing, scripts running.

Time t >= 0 is exact. The chip's position is a continuous, non-decreasing,
piecewise-linear function of time. Everything happens at exact instants, in
this order at one instant: user edits, then revealed mode transitions, then a
run of the generator if the user edited (the state changed under the script),
then the script's triggers in script order (a rerun installs the new script,
whose triggers are evaluated at once).

Between two instants nothing changes but the chip's position, so continuous
edits are applied exactly for that period: the chip goes through the positions
P = chip([t0, t1)) and a continuous edit with offsets J writes on P ⊕ J, minus
the positions that would break N2 or N3 (the user's elements of that type and
the forbidden zone of guards.py, both constant during the period).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from generator import RulesGenerator, RunStats
from guards import apply_items, check_edit, forbidden_zone
from intervals import INF, Interval, IntervalSet, Num
from line import GEN, USER, LineState
from modes import ModeTimeline
from script import ChipIn, FollowAction, RerunAction, Script, SetAction, TransitionCount


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
    etype: str
    region: IntervalSet
    value: Fraction  # 0 removes the type (whoever added it)


@dataclass
class Change:
    """One change of the line, for the checks."""
    time: Fraction
    chip: Fraction  # chip at the instant (for a continuous edit: at the start of its period)
    source: str  # "user", "set", "follow"
    before: LineState
    after: LineState
    written: IntervalSet = field(default_factory=IntervalSet)
    swept: IntervalSet = field(default_factory=IntervalSet)  # follow: chip positions of the period
    offsets: IntervalSet = field(default_factory=IntervalSet)


@dataclass
class Log:
    changes: list[Change] = field(default_factory=list)
    refused: list[tuple[Fraction, str]] = field(default_factory=list)
    runs: list[tuple[Fraction, Fraction, str, Script, RunStats]] = field(default_factory=list)  # (time, chip, why, script, stats)


class Simulation:
    def __init__(self, state: LineState, generator: RulesGenerator, chip: Chip, modes: ModeTimeline, user_edits: list[UserEdit] = ()) -> None:
        if modes.start != chip.position(Fraction(0)):
            raise ValueError("modes start where the chip starts")
        self.state = state.copy()
        self.gen = generator
        self.chip = chip
        self.modes = modes
        self.user_edits = sorted(user_edits, key=lambda e: e.time)
        self.log = Log()
        self.script = Script()
        self.revealed = 0  # number of transitions revealed
        self._since_install = 0
        self._was_true: list[bool] = []
        self._fired: list[bool] = []

    # --------------------------------------------------------------- helpers
    def known(self, t: Fraction):
        return self.modes.known(self.chip.position(t))

    def _install(self, t: Fraction, why: str) -> None:
        c = self.chip.position(t)
        script, stats = self.gen.run(self.state.copy(), c, self.known(t))
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

    def _apply_set(self, t: Fraction, action: SetAction) -> None:
        c = self.chip.position(t)
        after = apply_items(self.state, action.items)
        why = check_edit(self.state, after, c, self.gen.rules, self.known(t))
        if why:
            self.log.refused.append((t, why))
            return
        self.log.changes.append(Change(t, c, "set", self.state, after, _union(x for _, x, _ in action.items)))
        self.state = after

    # ----------------------------------------------------------------- main
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
        return min(cands, default=INF)

    def _instant(self, t: Fraction, first: bool = False) -> None:
        edited = False
        for e in self.user_edits:
            if e.time == t:
                before, self.state = self.state, self.state.copy()  # states in the log are never mutated
                self.state.assign(e.etype, e.region, e.value, USER)
                self.log.changes.append(Change(t, self.chip.position(t), "user", before, self.state, e.region))
                edited = True
        c = self.chip.position(t)
        revealed = sum(1 for p, _ in self.modes.transitions if p <= c)
        self._since_install += revealed - self.revealed
        self.revealed = revealed
        if first:
            self._install(t, "start")
        elif edited:
            self._install(t, "user edit")
        for _ in range(3):  # a rerun installs a script whose own reruns cannot fire at once
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
                    if isinstance(a, SetAction):
                        self._apply_set(t, a)
                    elif isinstance(a, RerunAction):
                        rerun = True
                if rerun:
                    break
            if not rerun:
                return
            self._install(t, "script")
        raise AssertionError("reruns did not settle")

    def _sweep(self, t0: Fraction, t1: Fraction) -> None:
        """Apply the active continuous edits for the chip positions of [t0, t1)."""
        if t1 <= t0:
            return
        c0, c1 = self.chip.position(t0), self.chip.position(t1)
        if c1 == c0:
            swept = IntervalSet.of(Interval.point(c0))
        else:
            swept = IntervalSet.of(Interval(c0, c1, True, self.chip.first_time(c1) < t1))
        known = self.known(t0)
        for i, ev in enumerate(self.script.events):
            if not self._was_true[i]:
                continue
            for a in ev.actions:
                if not isinstance(a, FollowAction):
                    continue
                if any(a.etype in r.lhs_types for r in self.gen.rules):
                    # its forbidden zone would not be exact: refused
                    self.log.refused.append((t0, f"continuous edit of {a.etype}, which is in a left side"))
                    continue
                target = swept + a.offsets
                allowed = (target - self.state.owned(a.etype, USER)
                           - forbidden_zone(self.state, self.gen.rules, known, a.etype, a.value))
                after = self.state.copy()
                if a.value:
                    after.assign(a.etype, allowed, a.value, GEN)
                else:
                    after.remove_owned(a.etype, allowed, GEN)
                if after != self.state:
                    self.log.changes.append(Change(t0, c0, "follow", self.state, after, allowed, swept, a.offsets))
                    self.state = after


def _union(sets) -> IntervalSet:
    out = IntervalSet()
    for s in sets:
        out = out | s
    return out
