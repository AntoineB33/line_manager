"""The world: the chip moving, modes being revealed, the user editing, scripts running.

Time t >= 0 is exact. The chip's position is a continuous, non-decreasing,
piecewise-linear function of time. Everything happens at exact instants, in this
order at one instant: user edits, then revealed mode transitions, then the script's
triggers in script order (a rerun installs the new script, whose triggers are
evaluated at once). A user edit runs the generator only through the script's events
(script.Edited); a change of rules or problems (`set_generator`) runs the new one. The first script is
`Script.initial()`: the generator runs at the first instant, after the user's edits
of that instant, through its trigger (logged as "start").

Only the user (the edits and the mode) and the generated script modify the line.
The script carries out the mode rules through the stretches its run compiled
(script.Script.stretches, mode_rules.Stretch): between two instants the chip goes
through the positions chip([t0, t1)), all with one mode, and each stretch of the
script for that mode whose line is the one compiled for it changes the line as the
chip goes through it (logged as a continuous change, source "mode"). Where no stretch
applies (another mode, another line), the mode rules do nothing until a run compiles
one. Sweeps are cut at the multiples of half the smallest rule offset.

A simulation can also go step by step (the interactive program, UI/app.py):
`start()` is the first instant, `advance(t)` runs it up to time t (starting it if
needed), the chip's trajectory being extended as it goes (`Chip.extend`); `edit_now`
before the start is an edit of the first instant, whose triggers wait for `start()`;
In the background mode (`background`, the interactive program), a run of the generator
takes time: a call to run it only records it (`request`), the script in place goes on,
and the run's script replaces it when the run completes (`complete`, at that time),
unless a newer call was recorded meanwhile (the older run is then cancelled).
`edit_now`, `undo_now`, `clear_now` and `set_generator` act at
the current time, as a user edit at that instant would; `modes_changed` after the
modes from the chip on were changed (ModeTimeline.replace).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from generator import RulesGenerator, RunStats
from guards import check_script_edit
from intervals import EPS, INF, Interval, Num, Pos
from line import GEN, USER, Claims, LineState, Op
from modes import ModeTimeline
from region import Region
from solver import Line
from resolve import Refused, owned_by_others, apply_edit, rebuild
from script import ConstraintEditAction, ChipIn, EditAction, RerunAction, Script, Start, TransitionCount, Uncovered, Edited


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

    def extend(self, t, x) -> None:
        """The trajectory goes on to (t, x): t after the last time, x not to the left."""
        t, x = Fraction(t), Fraction(x)
        if not t > self.points[-1][0] or x < self.points[-1][1]:
            raise ValueError("the chip goes on later, and never to the left")
        self.points.append((t, x))

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
    chip: Num  # for the mode rules: the first position of `at`
    source: str  # "user", "script", "mode"
    before: LineState
    after: LineState
    at: Region | None = None  # for the mode rules: the positions, each changed when the chip is there


@dataclass
class RunRequest:
    """A call to run the generator, in the background mode: what the run reads."""
    id: int
    time: Fraction
    chip: Fraction
    why: str
    state: LineState
    known: object
    gen: RulesGenerator


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
        self.script = Script.initial()
        self.revealed = 0
        self._since_install = 0
        self._was_true: list[bool] = [False] * len(self.script.events)
        self._fired: list[bool] = [False] * len(self.script.events)
        self.t = Fraction(0)
        self.started = False
        h = self.rules.smallest_offset
        self.grid = h / 2 if h else None
        self._stretch = None  # (stretch, the state it made last, the next position): the one the chip goes through
        self._shift = None
        self._missed = None  # (script, state): no stretch of that script takes over on that line
        self.background = False  # runs of the generator complete later (`complete`)
        self.request: RunRequest | None = None  # the run asked for and not completed
        self._edited = False  # the user edited at this instant
        self._requests = 0

    def snapshot(self) -> tuple:
        """What a step changes, to go back to it (`restore`) if the step is interrupted."""
        return (self.state, len(self.log.changes), len(self.log.refused), len(self.log.runs), self.script,
                self.revealed, self._since_install, list(self._was_true), list(self._fired), self.t, self.started,
                len(self.chip.points), self._stretch, self.request, self._shift)

    def restore(self, snap: tuple) -> None:
        (self.state, nc, nr, nu, self.script, self.revealed, self._since_install, self._was_true, self._fired,
         self.t, self.started, npts, self._stretch, self.request, self._shift) = snap
        del self.log.changes[nc:], self.log.refused[nr:], self.log.runs[nu:], self.chip.points[npts:]

    def known(self, t: Fraction):
        return self.modes.known(self.chip.position(t))

    def _install(self, t: Fraction, why: str) -> None:
        c = self.chip.position(t)
        if self.background:
            self._requests += 1
            self.request = RunRequest(self._requests, t, c, why, self.state, self.known(t), self.gen)
            return
        script, stats = self.gen.run(self.state, c, self.known(t))
        self._set_script(t, c, why, script, stats)

    def complete(self, request_id: int, script: Script, stats: RunStats) -> bool:
        """The run asked for by `request` completed now: its script replaces the one in
        place, and its triggers are evaluated at once. False if a newer run was asked for."""
        req = self.request
        if req is None or req.id != request_id:
            return False
        self.request = None
        self._set_script(self.t, req.chip, req.why, script, stats)
        self._after_edits(self.t, False, False, req.why)
        return True

    def _set_script(self, t: Fraction, c: Fraction, why: str, script: Script, stats: RunStats) -> None:
        self.script = script
        self._stretch = None
        self._shift = None  # how far the script's stretches are moved: decided when they are first needed
        self._since_install = 0
        self._was_true = [False] * len(script.events)
        self._fired = [False] * len(script.events)
        self.log.runs.append((t, c, why, script, stats))

    def _cond(self, i: int, t: Fraction) -> bool:
        trig = self.script.events[i].trigger
        if isinstance(trig, Start):
            return True
        if isinstance(trig, Edited):
            return self._edited
        if isinstance(trig, Uncovered):
            c = self.chip.position(t)
            return self._uncovered_at(self.modes.mode_at(c), Pos(c))
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
        self.advance(end_time)
        return self.log

    def advance(self, end_time) -> None:
        """Run the simulation from its current time up to end_time."""
        end = Fraction(end_time)
        if not self.started:
            self.start()
        while self.t < end:
            t1 = min(self._next_instant(self.t), end)
            self._sweep(self.t, t1)
            self.t = t1
            self._instant(self.t)

    def start(self, edits: list[list[Op]] | None = None) -> None:
        """The first instant, with these edits of the user (else those scheduled at 0)."""
        self.started = True
        self._instant(self.t, first=True, edits=edits)

    def edit_now(self, ops: list[Op]) -> str | None:
        """An edit of the user at the current time: None, or why it is refused."""
        n = len(self.log.refused)
        if not self.started:  # an edit of the first instant: the rest of it at start()
            self._edit(self.t, ops, USER, "user")
        else:
            self._instant(self.t, edits=[ops])
        refused = [why for _, src, why in self.log.refused[n:] if src == "user"]
        return refused[0] if refused else None

    def undo_now(self) -> bool:
        """Cancel the user's last edit, as if it had never been made."""
        if not self.state.history:
            return False
        self._replace(self.state.history[:-1])
        return True

    def modes_changed(self) -> None:
        """The modes changed from the chip's position on (given externally): revealed
        now, and the script's triggers see them."""
        self._after_edits(self.t, False, False, "user edit")

    def clear_now(self) -> None:
        """Cancel all the user's edits."""
        self._replace(())

    def set_generator(self, generator: RulesGenerator) -> None:
        """New rules or problems: the line is computed again from the user's edits, and the
        generator runs again."""
        self.gen, self.rules = generator, generator.rules
        h = self.rules.smallest_offset
        self.grid = h / 2 if h else None
        self._replace(self.state.history, why="rules or problems changed", run=True)

    def _replace(self, history: tuple, why: str = "user edit", run: bool = False) -> None:
        before = self.state
        after = rebuild(self.rules, history, owned_by_others(before.claims), before.line)
        self.log.changes.append(Change(self.t, self.chip.position(self.t), "user", before, after))
        self.state = after
        if not self.started:
            self.started = True
            self._instant(self.t, first=True, edits=[])
        else:
            self._after_edits(self.t, False, True, why, run)

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

    def _instant(self, t: Fraction, first: bool = False, edits: list[list[Op]] | None = None) -> None:
        edited = False
        for ops in ([e.ops for e in self.user_edits if e.time == t] if edits is None else edits):
            edited |= self._edit(t, ops, USER, "user")
        self._after_edits(t, first, edited, "user edit")

    def _after_edits(self, t: Fraction, first: bool, edited: bool, why: str, run: bool = False) -> None:
        self.started = True
        c = self.chip.position(t)
        revealed = sum(1 for p, _ in self.modes.transitions if p <= c)
        self._since_install += revealed - self.revealed
        self.revealed = revealed
        if run:  # a new generator: its run replaces the script
            self._install(t, why)
        self._edited = edited and not first
        for _ in range(3):
            rerun = None
            for i, ev in enumerate(self.script.events):
                now = self._cond(i, t)
                rising = now and not self._was_true[i]
                self._was_true[i] = now
                if isinstance(ev.trigger, TransitionCount):
                    rising = now and not self._fired[i]
                elif isinstance(ev.trigger, Edited):
                    rising = now  # at each edit
                if not rising:
                    continue
                self._fired[i] = True
                if isinstance(ev.trigger, Edited):
                    self._edited = False  # this edit is answered
                for a in ev.actions:
                    if isinstance(a, EditAction):
                        self._edit(t, list(a.ops), GEN, "script")
                    elif isinstance(a, ConstraintEditAction):
                        self._edit(t, a.at(self.chip.position(t)), GEN, "script")
                    elif isinstance(a, RerunAction):
                        rerun = ("start" if a.first else "user edit" if isinstance(ev.trigger, Edited)
                                 else "uncovered" if isinstance(ev.trigger, Uncovered) else "script")
                if rerun:
                    break
            if not rerun:
                return
            self._install(t, rerun)
        raise AssertionError("reruns did not settle")

    def _sweep(self, t0: Fraction, t1: Fraction) -> None:
        """The script's stretches act on the chip positions of [t0, t1)."""
        if t1 <= t0 or not self.script.stretches:
            return
        c0, c1 = self.chip.position(t0), self.chip.position(t1)
        mode = self.modes.mode_at(c0)
        x = Pos(c0)
        hi = x if c1 == c0 else (Pos(c1) if self.chip.first_time(c1) < t1 else Pos(c1) - EPS)
        while x <= hi:
            st = self._stretch_at(mode, x, hi)
            if st is None:
                return  # no stretch acts on the rest: the mode rules do nothing there (script.Uncovered)
            d = self._shift
            x = max(x, st.a + d)
            end = st.b + d if st.b + d < hi else hi
            after = _moved(st.state_at(end - d), d, self.state.history)
            if after is not self.state:
                t = max(t0, self.chip.first_time(x.x))
                self.log.changes.append(Change(t, x, "mode", self.state, after, Region.of(Interval._mk(x, end))))
                self.state = after
            x = end + EPS
            self._stretch = (st, after, x)

    def _align(self, x: Pos) -> Fraction:
        """How far the script's stretches are moved, decided the first time they are needed
        (the chip at x): by how far the chip went while the run computed, if the line is
        the one the run compiled from moved by that much; else not moved."""
        if self._shift is None:
            s = self.script
            d = x.x - s.origin if s.origin is not None and x.k == 0 else Fraction(0)
            self._shift = d if d and _same_state(_moved(s.base, d, self.state.history), self.state) else Fraction(0)
        return self._shift

    def _uncovered_at(self, mode: str, x: Pos) -> bool:
        """The chip at x in this mode is where no stretch of the script applies: the run
        did not compile this mode there, or the line is not the one it compiled (past
        the end of what it compiled, the run it asked for is the one to wait for)."""
        covered = self.script.covered
        if not covered:
            return False
        d = self._align(x)
        y = x - d  # where the run compiled it
        if y > max(hi for _, hi, _ in covered):
            return False  # past what the run compiled: the run it asked for compiles that
        if not any(lo <= y <= hi and m == mode for lo, hi, m in covered):
            return True
        cur = self._stretch
        if cur is not None and cur[2] == x and cur[1] is self.state:
            return False  # going on through a stretch
        if self._inside(mode, x) is not None:
            return False
        if any(st.mode == mode and st.plan is not None and st.a < y <= st.b for st in self.script.stretches):
            return True  # in a stretch, on a line it does not make there
        nxt = next((st for st in self.script.stretches if st.mode == mode and st.a >= y), None)
        return nxt is not None and not _same_state(_moved(nxt.before, d, self.state.history), self.state)

    def _inside(self, mode: str, x: Pos):
        """The stretch of the script whose positions include the chip at x, if the line is
        the one it makes there (a script put in place while the one before did the same):
        the chip takes it over from x. None if there is none; remembered per script and line."""
        if self._missed == (self.script, self.state):
            return None
        d = self._align(x)
        y = x - d
        for st in self.script.stretches:
            if st.mode == mode and st.plan is not None and st.a < y <= st.b:
                if _same_state(_moved(st.state_at(y - EPS), d, self.state.history), self.state):
                    return st
                break
        self._missed = (self.script, self.state)
        return None

    def _stretch_at(self, mode: str, x: Pos, hi: Pos):
        """The stretch of the script the chip goes through from x: the one it is in, or
        the first starting in [x, hi] in this mode on the line compiled for it (the
        compiled mode rules change nothing before it); None if there is none."""
        d = self._align(x)
        cur = self._stretch
        if cur is not None and cur[2] == x and cur[1] is self.state and cur[0].mode == mode and x - d <= cur[0].b:
            return cur[0]
        for st in self.script.stretches:  # in the order of the chip
            if x <= st.a + d <= hi and st.mode == mode and _same_state(_moved(st.before, d, self.state.history), self.state):
                return st
        return self._inside(mode, x)


def _same_state(a: LineState, b: LineState) -> bool:
    return a is b or (a.line == b.line and a.claims.same(b.claims) is True)


def _moved(st: LineState, d, history) -> LineState:
    """The state moved by d (its claims and its line), with this history of the user."""
    if not d:
        return st
    c = Claims()
    c.pos = {k: {key: r.shift(d) for key, r in dd.items()} for k, dd in st.claims.pos.items()}
    c.neg = {k: {key: r.shift(d) for key, r in dd.items()} for k, dd in st.claims.neg.items()}
    c.taken = {k: {key: r.shift(d) for key, r in dd.items()} for k, dd in st.claims.taken.items()}
    return LineState(c, Line({k: {v: r.shift(d) for v, r in dd.items()} for k, dd in st.line.vals.items()}), history)
