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
The script carries out the mode rules where its text says so: between two instants the
chip goes through the positions chip([t0, t1)), all with one mode, and the mode rules
change the line as the chip goes through them (logged as a continuous change, source
"mode"), through the stretches its run compiled ahead where the line is the one
compiled for them (script.Script.stretches, mode_rules.Stretch), or else computed on the
line as it is. Where the text does not say so, the mode rules do nothing. Sweeps are cut
at the multiples of half the smallest rule offset.

A simulation can also go step by step (the interactive program, UI/app.py):
`start()` is the first instant, `advance(t)` runs it up to time t (starting it if
needed), the chip's trajectory being extended as it goes (`Chip.extend`); `edit_now`
before the start is an edit of the first instant, whose triggers wait for `start()`;
In the background mode (`background`, the interactive program), a run of the generator
takes time: a call to run it only records it (`request`), the script in place goes on,
and each script of the run replaces it when it is found (`publish`, and `complete` for
the last, at that time), unless a newer call was recorded meanwhile (the older run is
then cancelled).
`edit_now`, `undo_now`, `clear_now` and `set_generator` act at
the current time, as a user edit at that instant would; `modes_changed` after the
modes from the chip on were changed (ModeTimeline.replace).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from fractions import Fraction

from generator import PACE, PACE_TIME, RulesGenerator, RunStats
from guards import check_script_edit
from intervals import EPS, INF, Interval, Num, Pos
from line import GEN, USER, Claims, LineState, Op
from mode_rules import ModeRules, Run
from modes import ModeTimeline
from region import Region
from solver import Line
from resolve import Refused, owned_by_others, apply_edit, rebuild
from script import ConstraintEditAction, ChipIn, EditAction, Event, PresenceAbove, RerunAction, ScoreBelow, Script, Start, TransitionCount, Uncovered, Edited, score


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
    published: bool = False  # a script of this run is in place
    previous: object = None  # the script in place when it started
    pace_at: Fraction = Fraction(0)  # the time at which the script must be definitive further


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
        self._live_rules: dict[str, ModeRules] = {}  # the mode rules the script applies itself, per mode
        self._shift = None
        self._missed = None  # (script, state): no stretch of that script takes over on that line
        self.background = False  # runs of the generator complete later (`complete`)
        self.request: RunRequest | None = None  # the run asked for and not completed
        self.definitive = None  # how far the run's scripts are definitive
        self._edited = False  # the user edited at this instant
        self._requests = 0

    def snapshot(self) -> tuple:
        """What a step changes, to go back to it (`restore`) if the step is interrupted."""
        return (self.state, len(self.log.changes), len(self.log.refused), len(self.log.runs), self.script,
                self.revealed, self._since_install, list(self._was_true), list(self._fired), self.t, self.started,
                len(self.chip.points), self._stretch, self.request, self._shift, self.definitive)

    def restore(self, snap: tuple) -> None:
        (self.state, nc, nr, nu, self.script, self.revealed, self._since_install, self._was_true, self._fired,
         self.t, self.started, npts, self._stretch, self.request, self._shift, self.definitive) = snap
        del self.log.changes[nc:], self.log.refused[nr:], self.log.runs[nu:], self.chip.points[npts:]

    def known(self, t: Fraction):
        return self.modes.known(self.chip.position(t))

    def _install(self, t: Fraction, why: str) -> None:
        c = self.chip.position(t)
        if self.background:
            self._requests += 1
            self.request = RunRequest(self._requests, t, c, why, self.state, self.known(t), self.gen, previous=self.script)
            self.first_script()
            return
        script, stats = self.gen.run(self.state, c, self.known(t))
        self._set_script(t, c, why, script, stats)

    def complete(self, request_id: int, script: Script, stats: RunStats) -> bool:
        """The run asked for by `request` completed now: its script replaces the one in
        place, and its triggers are evaluated at once. False if a newer run was asked for."""
        req = self.request
        if req is None or req.id != request_id:
            return False
        self._set_script(self.t, req.chip, req.why, self._fit(script) if req.published else script, stats, keep=req.published)
        self.request = None
        self._after_edits(self.t, False, False, req.why)
        return True

    def first_script(self) -> None:
        """At the start of a run, at once and whatever the run does: the script in place,
        changed a little, definitive on the PACE after the chip: the mode rules there in any
        mode, the constraints of the generator, and the rest as it is."""
        req, s = self.request, self.script
        x = req.chip + PACE
        constraints = tuple(getattr(self.gen, "constraints", ())) or s.constraints
        events = [e for e in s.events if not isinstance(e.trigger, Start)]
        if constraints and not any(isinstance(a, ConstraintEditAction) for e in events for a in e.actions):
            events.insert(0, Event(ChipIn(req.chip, INF), (ConstraintEditAction(),)))
        covered = [(lo, hi, m) for lo, hi, m in s.covered if m is not None]
        if self.rules.mode_rules:
            covered.insert(0, (Pos(req.chip), Pos(x), None))
        script = replace(s, events=events, covered=covered, constraints=constraints, live=s.live or bool(constraints and self.rules.mode_rules))
        req.published, req.pace_at = True, req.time + PACE_TIME
        self._carry(script)
        self.script = script
        self._reset()
        self.definitive = x

    def keep_pace(self, t: Fraction) -> None:
        """A run that did not get further in time is overtaken by the script in place, its
        last resort: definitive PACE further, whatever the run does. At the end of the run
        that way, the run is over."""
        req = self.request
        while req is not None and req.published and t >= req.pace_at:
            self.definitive += PACE
            req.pace_at += PACE_TIME
            self.script = self._fit(self.script)
            if self.definitive >= req.chip + getattr(self.gen, "horizon", 0):
                self.request = None
                self.log.runs.append((t, req.chip, req.why, self.script, RunStats(chosen="the script in place, as a last resort")))
                return

    def _fit(self, script: Script) -> Script:
        """The script with the frontier of the run: its edits that are new after it, and the
        mode rules up to it in any mode."""
        x, now = Pos(self.definitive), {str(e) for e in self.script.events}
        before = Region.of(Interval(-INF, x, False, True))
        events = []
        for e in script.events:
            actions = []
            for a in e.actions:
                if isinstance(a, EditAction) and str(e) not in now:
                    ops = tuple(o for o in (Op(o.action, o.kind, o.region - before, o.value) for o in a.ops) if not o.region.is_empty())
                    if ops:
                        actions.append(EditAction(ops))
                else:
                    actions.append(a)
            if actions:
                events.append(Event(e.trigger, tuple(actions)))
        covered = [(lo, hi, m) for lo, hi, m in script.covered if m is not None]
        if self.rules.mode_rules and self.request is not None:
            covered.insert(0, (Pos(self.request.chip), x, None))
        return replace(script, events=events, covered=covered)

    def publish(self, request_id: int, script: Script, stats: RunStats) -> bool:
        """The run asked for by `request` found a script and goes on: it replaces the one in
        place, the events that stay stay as they were. False if a newer run was asked for."""
        req = self.request
        if req is None or req.id != request_id:
            return False
        script = self._fit(script)  # (up to the frontier as it was: this script goes further)
        if stats.definitive is not None and stats.definitive > self.definitive:
            self.definitive, req.pace_at = stats.definitive, self.t + PACE_TIME
            script = replace(script, covered=[(lo, Pos(stats.definitive), m) if m is None else (lo, hi, m) for lo, hi, m in script.covered])
        self._carry(script)
        self.script = script
        self._reset()
        self._after_edits(self.t, False, False, req.why)
        return True

    def _reset(self) -> None:
        self._stretch = None
        self._live_rules = {}
        self._shift = None  # how far the script's stretches are moved: decided when they are first needed
        self._since_install = 0

    def _carry(self, script: Script, same_constraints: bool = True) -> None:
        """The events of `script` that are in the one in place stay as they were (what they
        did is not done again), unless they satisfy constraints that changed."""
        seen = {str(e): i for i, e in enumerate(self.script.events)}
        at = [seen.get(str(e)) if same_constraints or not any(isinstance(a, ConstraintEditAction) for a in e.actions) else None
              for e in script.events]
        self._was_true = [i is not None and self._was_true[i] for i in at]
        self._fired = [i is not None and self._fired[i] for i in at]

    def _set_script(self, t: Fraction, c: Fraction, why: str, script: Script, stats: RunStats, keep: bool = False) -> None:
        if keep:
            self._carry(script)
        else:
            self._was_true = [False] * len(script.events)
            self._fired = [False] * len(script.events)
        self.script = script
        self._reset()
        if stats.definitive is not None:
            self.definitive = max(stats.definitive, self.definitive or stats.definitive)
        self.log.runs.append((t, c, why, script, stats))

    def replace_script(self, script: Script) -> None:
        """The user's script (script_text.py) replaces the one in place, and the run going
        on is cancelled (its script would replace the user's). An event that was already
        there stays as it was (what it did is not done again), the others are evaluated
        at the next instant; with other constraints, those that satisfy them too."""
        self._carry(script, list(map(str, script.constraints)) == list(map(str, self.script.constraints)))
        self.script = script
        self.request = None
        self.definitive = None
        self._reset()

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
        if isinstance(trig, PresenceAbove):
            c = self.chip.position(t)
            return trig.holds(self.state, c, self.modes.mode_at(c))
        if isinstance(trig, ScoreBelow):
            return score(self.gen.problems, self.state, self.chip.position(t)) < trig.z
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
        elif source == "user":
            why = self._against_constraints(after, self.chip.position(t))
            if why:
                self.log.refused.append((t, source, why))
                return False
        self.log.changes.append(Change(t, self.chip.position(t), source, before, after))
        self.state = after
        return True

    def _against_constraints(self, state: LineState, c: Fraction) -> str | None:
        """Why the constraints the script must satisfy cannot hold on this line, if so."""
        ops = [o for k in self.script.constraints or getattr(self.gen, "constraints", ()) for o in k.ops(state, c)]
        if not ops:
            return None
        try:
            why = check_script_edit(state, apply_edit(self.rules, state, ops, GEN), c)
        except Refused as e:
            why = str(e)
        return f"it contradicts the constraints: {why}" if why else None

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
        if self.request is not None and self.request.published:
            cands.append(self.request.pace_at)
        later = [x for x in cands if x > t]
        return min(later, default=INF)

    def _instant(self, t: Fraction, first: bool = False, edits: list[list[Op]] | None = None) -> None:
        edited = False
        for ops in ([e.ops for e in self.user_edits if e.time == t] if edits is None else edits):
            edited |= self._edit(t, ops, USER, "user")
        self._after_edits(t, first, edited, "user edit")

    def _after_edits(self, t: Fraction, first: bool, edited: bool, why: str, run: bool = False) -> None:
        self.started = True
        self.keep_pace(t)
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
                        c = self.chip.position(t)
                        self._edit(t, [o for k in self.script.constraints for o in k.ops(self.state, c)], GEN, "script")
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
        if t1 <= t0 or not (self.script.covered or self.script.live):
            return
        c0, c1 = self.chip.position(t0), self.chip.position(t1)
        mode = self.modes.mode_at(c0)
        x = Pos(c0)
        hi = x if c1 == c0 else (Pos(c1) if self.chip.first_time(c1) < t1 else Pos(c1) - EPS)
        while x <= hi:
            st = self._stretch_at(mode, x, hi)
            if st is None:  # no stretch acts on the rest
                self._apply(t0, mode, x, hi)
                return
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

    def _apply(self, t0: Fraction, mode: str, x: Pos, hi: Pos) -> None:
        """The mode rules on [x, hi], the constraints kept, on the line as it is, where the
        script says so (each part of its text on its positions)."""
        s = self.script
        if s.live:
            self._apply_on(t0, mode, x, hi)
            return
        last = None  # (they may overlap, and a position is gone through once)
        for a, b in sorted((max(lo, x), min(b, hi)) for lo, b, m in s.covered if m in (mode, None) and lo <= hi and b >= x):
            a = a if last is None else max(a, last + EPS)
            if a <= b:
                self._apply_on(t0, mode, a, b)
                last = b

    def _apply_on(self, t0: Fraction, mode: str, x: Pos, hi: Pos) -> None:
        mr = self._live_rules.get(mode) or self._live_rules.setdefault(mode, ModeRules(self.rules, mode, self.script.constraints))
        for ev in mr.act(self.state, x, hi):
            if isinstance(ev, Run):
                t = max(t0, self.chip.first_time(ev.start.x))
                self.log.changes.append(Change(t, ev.start, "mode", ev.before, ev.after, ev.at))
                self.state = ev.after
            else:
                self.log.refused.append((t0, "mode", ev[1]))

    def ahead(self):
        """The state as a function of the chip's position p, from where it is, for the
        window to show them together: p -> the state, or None where the formula of the
        run the chip goes through does not say it. None if the script does not change the
        line as the chip goes (the state stays)."""
        c = self.chip.position(self.t)
        mode, x = self.modes.mode_at(c), Pos(c)
        s = self.script
        if not (s.live or any(lo <= x <= hi and m in (mode, None) for lo, hi, m in s.covered)):
            return None
        state, history = self.state, self.state.history
        cur = self._stretch
        if cur is not None and cur[1] is state and cur[2] == x and cur[0].mode == mode and cur[0].plan is not None:
            plan, d = cur[0].plan, self._shift
            def at(p):
                st = plan.exact(p - d) if p - d <= cur[0].b else None
                return None if st is None else _moved(st, d, history)
            return at
        mr = self._live_rules.get(mode)
        plan = mr.plan if mr is not None else None
        if plan is not None and plan.last is state and plan.next == x:
            return plan.exact
        return lambda p: None

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
        """The chip at x in this mode is where the script does not apply the mode rules
        (past the end of what it says, the run it asked for is the one to wait for)."""
        covered = self.script.covered
        if not covered:
            return False
        d = self._align(x)
        y = x - d  # where the run compiled it
        if y > max(hi for _, hi, _ in covered):
            return False  # past what the run compiled: the run it asked for compiles that
        return not any(lo <= y <= hi and m in (mode, None) for lo, hi, m in covered) and not self.script.live

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
