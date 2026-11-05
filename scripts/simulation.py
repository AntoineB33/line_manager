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

An iteration of the script's exact loop is one `advance` (script.py): whatever the steps,
the instants it stops at are the same, so the line is too. With `apart` (the window), the
other loops are left to their own threads (`iterate`); without it, they iterate at each
instant.

A simulation can also go step by step (the interactive program, UI/app.py):
`start()` is the first instant, `advance(t)` runs it up to time t (starting it if
needed), the chip's trajectory being extended as it goes (`Chip.extend`); `edit_now`
before the start is an edit of the first instant, whose triggers wait for `start()`;
In the background mode (`background`, the interactive program), a run of the generator
takes time: a call to run it only records it (`request`), the script in place goes on,
and each script of the run replaces it when it is found (`publish`, and `complete` for
the last, at that time), unless a newer call was recorded meanwhile (the older run is
then cancelled).
`edit_now` and `set_generator` act at
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
from solver import Line, Undetermined, limited
from resolve import Refused, apply_edit, flattened, mode_won, owned_by_others, rebuild
from watch import due, look
from script import SHOW, ChangeAction, ConstraintEditAction, ChipIn, ChipOn, EditAction, Event, PresenceAbove, RerunAction, ScoreBelow, Script, ShowLine, Start, TransitionCount, Uncovered, Edited, score


EDIT_LIMIT = 2  # the seconds an edit of the user can take: beyond, it is refused
LOG_KEPT = 512  # changes of the line kept in the log, at least
MEMORY_LIMIT = 2 * 1024 ** 3  # bytes a state can take: beyond, its far left is flattened, then removed
LAST_RESORT = PACE_TIME - EDIT_LIMIT  # (so that an edit going on never delays the pace)


def _reach(ev: Event):
    """Its trigger, a ChipOn with no `within` taking it from the event's own edit written
    from the chip: as far after the chip as that edit puts its kind (its first position),
    so that a piece closer to the chip than the edit puts it is pushed (0: the chip in it)."""
    trig = ev.trigger
    if not isinstance(trig, ChipOn) or trig.within is not None:
        return trig
    for a in ev.actions:
        if isinstance(a, EditAction) and a.from_chip:
            for o in a.ops:
                if o.action == "put" and o.kind == trig.kind and not o.region.is_empty():
                    lo = o.region.inf()
                    return replace(trig, within=max(Fraction(0), lo.x if isinstance(lo, Pos) else Fraction(0)))
    return replace(trig, within=Fraction(0))


def against_constraints(rules, state: LineState, c: Fraction, constraints) -> str | None:
    """Why the constraints the script must satisfy cannot hold on this line, if so."""
    ops = [o for k in constraints for o in k.ops(state, c)]
    if not ops:
        return None
    try:
        why = check_script_edit(state, apply_edit(rules, state, ops, GEN), c)
    except Refused as e:
        why = str(e)
    return f"it contradicts the constraints: {why}" if why else None


def user_edit(rules, state: LineState, ops, owner, c: Fraction, constraints) -> tuple:
    """(the state after an edit of the user, why it is refused, if so)."""
    try:
        after = apply_edit(rules, state, ops, owner)
        return after, against_constraints(rules, after, c, constraints)
    except (Refused, Undetermined) as e:
        return None, str(e)


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
        t1, x1 = self.points[-1]
        if not t > t1 or x < x1:
            raise ValueError("the chip goes on later, and never to the left")
        if len(self.points) > 1:
            t0, x0 = self.points[-2]
            if (x1 - x0) * (t - t1) == (x - x1) * (t1 - t0):  # the same speed: one segment, whatever the time
                self.points[-1] = (t, x)
                return
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
    refused_gone: int = 0  # how many were dropped from the start of `refused`
    runs_gone: int = 0  # and of `runs`

    def changed(self, change: Change) -> None:
        """The last changes only: each one keeps two states, which the memory limit must
        be able to free."""
        self.changes.append(change)
        if len(self.changes) > 2 * LOG_KEPT:
            del self.changes[:LOG_KEPT]

    def refuse(self, entry: tuple) -> None:
        self.refused.append(entry)
        if len(self.refused) > 2 * LOG_KEPT:
            del self.refused[:LOG_KEPT]
            self.refused_gone += LOG_KEPT

    def ran(self, run: tuple) -> None:
        """The last runs only. Only the newest keeps what its script compiled: the rest keep
        older states too, which the memory limit could not free."""
        if self.runs:
            t, c, why, script, stats = self.runs[-1]
            self.runs[-1] = (t, c, why, replace(script, stretches=[], base=None), stats)
        self.runs.append(run)
        if len(self.runs) > 2 * LOG_KEPT:
            del self.runs[:LOG_KEPT]
            self.runs_gone += LOG_KEPT

    @property
    def refused_count(self) -> int:
        return self.refused_gone + len(self.refused)

    @property
    def run_count(self) -> int:
        return self.runs_gone + len(self.runs)

    def refused_since(self, count: int) -> list:
        return self.refused[max(0, count - self.refused_gone):]

    def runs_since(self, count: int) -> list:
        return self.runs[max(0, count - self.runs_gone):]


class Ahead:
    """The state as a function of the chip's position (`Simulation.ahead`). key: the same
    while one formula gives it; until(p): where that formula stops, from p (None: none)."""

    def __init__(self, at, key, until) -> None:
        self.at, self.key, self.until = at, key, until

    def __call__(self, p):
        return self.at(p)


class Simulation:
    def __init__(self, state: LineState, generator: RulesGenerator, chip: Chip, modes: ModeTimeline, user_edits: list[UserEdit] = ()) -> None:
        if modes.start != chip.position(Fraction(0)):
            raise ValueError("modes start where the chip starts")
        self._settled = None  # (position, mode, script, the state before, after): see `_settle`
        self._kept = None  # the position where the mode rules leave the user's elements as they are
        self._frozen = None  # while a script of a run is put in place: where it is definitive
        self._looks = None  # (script, {the text of an event: its last watch.look or None})
        self.state = state
        self.gen = generator
        self.rules = generator.rules
        self.chip = chip
        self.modes = modes
        self.user_edits = sorted(user_edits, key=lambda e: e.time)
        self.log = Log()
        self.script = Script.initial()
        self.revealed = 0
        self.t = Fraction(0)
        self.started = False
        h = self.rules.smallest_offset
        self.grid = h / 2 if h else None
        self._stretch = None  # (stretch, the state it made last, the next position): the one the chip goes through
        self._live_rules: dict[str, ModeRules] = {}  # the mode rules the script applies itself, per mode
        self._shift = None
        self._missed = None  # (script, state): no stretch of that script takes over on that line
        self.background = False  # runs of the generator complete later (`complete`)
        self.apart = False  # the loops other than the exact one have threads of their own
        self.shown = 0  # how many times a script asked to show the line (script.ShowLine)
        self.request: RunRequest | None = None  # the run asked for and not completed
        self.definitive = None  # how far the run's scripts are definitive
        self.calculate = None  # (job, *args) -> its answer, the user's edits computed elsewhere
        self.memory_limit = MEMORY_LIMIT
        self.flat_to = self.gone_to = None  # the line is simple on the left of the first, empty of the second
        self._edited = False  # the user edited at this instant
        self._read_edited = False  # something that the rules, the constraints or the problems read
        self.generator_off = False  # switched off by the user (switch_generator): no run
        self._requests = 0

    def snapshot(self) -> tuple:
        """What a step changes, to go back to it (`restore`) if the step is interrupted."""
        return (self.state, len(self.log.changes), self.log.refused_count, self.log.run_count, self.script,
                self.revealed, self.script.since, set(self.script.true), set(self.script.fired), self.t, self.started,
                len(self.chip.points), self.chip.points[-1], self._stretch, self.request, self._shift, self.definitive,
                self.flat_to, self.gone_to, self._settled)

    def restore(self, snap: tuple) -> None:
        (self.state, nc, nr, nu, self.script, self.revealed, self.script.since, self.script.true, self.script.fired,
         self.t, self.started, npts, last, self._stretch, self.request, self._shift, self.definitive,
         self.flat_to, self.gone_to, self._settled) = snap
        del self.log.changes[nc:], self.log.refused[max(0, nr - self.log.refused_gone):], self.log.runs[max(0, nu - self.log.runs_gone):]
        self.chip.points[npts - 1:] = [last]  # (a step of the same speed moves the last point)
        self._looks = None

    def known(self, t: Fraction):
        return self.modes.known(self.chip.position(t))

    def _install(self, t: Fraction, why: str) -> None:
        c = self.chip.position(t)
        if self.generator_off:  # (switched off by the user: the script in place goes on as it is)
            self.log.refuse((t, "script", f"the generator is switched off: not run ({why})"))
            return
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
        frozen = self.definitive if req.published else None
        self._set_script(self.t, req.chip, req.why, self._fit(script) if req.published else script, stats, keep=req.published)
        self.request = None
        self._frozen = frozen
        self._after_edits(self.t, False, False, req.why)
        self._frozen = None
        return True

    def first_script(self) -> None:
        """At the start of a run, at once and whatever the run does: the script in place,
        changed a little, definitive on the PACE after the chip: the mode rules there in any
        mode, the constraints of the generator, and the rest as it is."""
        req, s = self.request, self.script
        x = req.chip + PACE
        constraints = tuple(getattr(self.gen, "constraints", ())) or s.constraints
        events = [e for e in s.events if not isinstance(e.trigger, Start)]
        hidden = [k for k in constraints if not getattr(k, "explicit", False)]
        if hidden and not any(isinstance(a, ConstraintEditAction) for e in events for a in e.actions):
            events.insert(0, Event(ChipIn(req.chip, INF), (ConstraintEditAction(),)))
        written = getattr(self.gen, "constraint_events", lambda *_: [])(self.state, req.chip)
        events = [e for e in written if str(e) not in {str(x) for x in events}] + events
        if not any(isinstance(e.trigger, Edited) and any(isinstance(a, RerunAction) for a in e.actions) for e in events):
            # an edit of the user while this run goes on runs the generator again (the run,
            # from the line before it, is cancelled): the start-up script had no such rule
            events.append(Event(Edited(True), (RerunAction(),)))
        # published before the run's last one, it calls for a new run where it stops being
        # definitive (with the user's rule that a script calling for none is the perfect one):
        # the run going on replaces it before, else the chip has
        # gone past what is decided and a run catches up from there
        events.append(Event(ChipIn(x, INF), (RerunAction(),)))
        covered = [(lo, hi, m) for lo, hi, m in s.covered if m is not None]
        if self.rules.mode_rules:
            covered.insert(0, (Pos(req.chip), Pos(x), None))
        base = None
        at = sum(1 for e in events if isinstance(e.trigger, ChipIn) and isinstance(e.actions[0], ConstraintEditAction))
        for ops in getattr(self.gen, "first_edits", lambda *_: [])(self.state, req.chip):
            try:  # on top of what the constraints ask for, and they must still hold after it
                if base is None:
                    asked = [o for k in constraints for o in k.ops(self.state, req.chip)]
                    base = apply_edit(self.rules, self.state, asked, GEN) if asked else self.state
                after = apply_edit(self.rules, base, ops, GEN)
                why = check_script_edit(base, after, req.chip) or \
                    ("it goes against the constraints" if any(k.ops(after, req.chip) for k in constraints) else None)
            except (Refused, Undetermined) as e:
                why = str(e)
            if not why:
                base = after
                events.insert(at, Event(ChipIn(req.chip, req.chip + self.gen.window), (EditAction(tuple(ops)),)))
                at += 1
        script = replace(s.with_events(events), on_view=s.on_view or SHOW, covered=covered, constraints=constraints,
                         live=s.live or bool(constraints and self.rules.mode_rules))
        req.published, req.pace_at, req.previous = True, req.time + LAST_RESORT, script
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
            was = self.definitive
            self.definitive += PACE
            req.pace_at += LAST_RESORT
            self.script = self._moved(self._fit(self.script), was, self.definitive)
            if self.definitive >= req.chip + getattr(self.gen, "window", 0) and getattr(self.gen, "q", 0) != 1:
                self.request = None
                self.log.ran((t, req.chip, req.why, self.script, RunStats(chosen="the script in place, as a last resort")))
                return

    @staticmethod
    def _moved(script: Script, was, now) -> Script:
        """The script whose call for a new run where it stopped being definitive (at `was`,
        first_script) is where it stops now: definitive further, it calls for it further."""
        events, moved = [], False
        for e in script.events:
            tr = e.trigger
            if (isinstance(tr, ChipIn) and tr.lo == was and tr.hi == INF and tr.mode is None
                    and len(e.actions) == 1 and isinstance(e.actions[0], RerunAction)):
                e, moved = Event(ChipIn(now, INF), e.actions), True
            events.append(e)
        return replace(script.with_events(events)) if moved else script

    def _fit(self, script: Script) -> Script:
        """The script with the frontier of the run: its edits that are new after it, and the
        mode rules up to it in any mode."""
        x, now = Pos(self.definitive), {str(e) for e in self.script.events}
        far = getattr(self.gen, "margin", lambda: Fraction(0))()
        before = Region.universe() if far is None else Region.of(Interval(-INF, x + far, False, True))
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
        return replace(script.with_events(events), covered=covered)

    def publish(self, request_id: int, script: Script, stats: RunStats) -> bool:
        """The run asked for by `request` found a script and goes on: it replaces the one in
        place, the events that stay stay as they were. False if a newer run was asked for."""
        req = self.request
        if req is None or req.id != request_id:
            return False
        script = self._fit(script)  # (up to the frontier as it was: this script goes further)
        frozen = self.definitive
        if stats.definitive is not None and stats.definitive > self.definitive:
            self.definitive, req.pace_at = stats.definitive, self.t + LAST_RESORT
            script = replace(script, covered=[(lo, Pos(stats.definitive), m) if m is None else (lo, hi, m) for lo, hi, m in script.covered])
        self._carry(script)
        self.script = script
        self._reset()
        self._frozen = frozen
        self._after_edits(self.t, False, False, req.why)
        self._frozen = None
        return True

    def _reset(self) -> None:
        self._stretch = None
        self._live_rules = {}
        self._shift = None  # how far the script's stretches are moved: decided when they are first needed
        self.script.since = 0

    def _carry(self, script: Script, same_constraints: bool = True) -> None:
        """The events of `script` that are in the one in place stay as they were (what they
        did is not done again), unless they satisfy constraints that changed."""
        kept = {str(e) for e in script.events if same_constraints or not any(isinstance(a, ConstraintEditAction) for a in e.actions)}
        script.true, script.fired = self.script.true & kept, self.script.fired & kept

    def _set_script(self, t: Fraction, c: Fraction, why: str, script: Script, stats: RunStats, keep: bool = False) -> None:
        if keep:
            self._carry(script)
        else:
            script.true, script.fired = set(), set()
        self.script = script
        self._reset()
        self.definitive = None if stats.definitive is None else max(stats.definitive, self.definitive or stats.definitive)
        self.log.ran((t, c, why, script, stats))

    def replace_script(self, script: Script) -> None:
        """The user's script (script_text.py) replaces the one in place, and the run going
        on is cancelled (its script would replace the user's). An event that was already
        there stays as it was (what it did is not done again), the others are evaluated
        at once, as for a published script; with other constraints, those that satisfy
        them too."""
        self._carry(script, list(map(str, script.constraints)) == list(map(str, self.script.constraints)))
        self.script = script
        self.request = None
        self.definitive = None
        self._reset()
        if self.started:
            self._after_edits(self.t, False, False, "user edit")

    def _cond(self, trig, t: Fraction) -> bool:
        if isinstance(trig, Start):
            return True
        if isinstance(trig, Edited):
            return self._edited and (self._read_edited or not trig.only)
        if isinstance(trig, Uncovered):
            c = self.chip.position(t)
            return self._uncovered_at(self.modes.mode_at(c), Pos(c))
        if isinstance(trig, ChipIn):
            c = self.chip.position(t)
            return trig.holds(c, self.modes.mode_at(c))
        if isinstance(trig, (PresenceAbove, ChipOn)):
            c = self.chip.position(t)
            return trig.holds(self.state, c, self.modes.mode_at(c))
        if isinstance(trig, ScoreBelow):
            return score(self.gen.problems, self.state, self.chip.position(t)) < trig.z
        return self.script.since >= trig.k

    def _edit(self, t: Fraction, ops: list[Op], owner: str, source: str, frozen=None) -> bool:
        """`frozen`: the line up to there is definitive, the edit cannot change it."""
        before = self.state
        c = self.chip.position(t)
        if source == "user":
            alone = self._alone(ops)  # nothing reads it: nothing to compute, or to run
            job = (self.rules, before, ops, owner, c, () if alone else self._constraints())
            self._read_edited = self._read_edited or not alone
            if alone:
                after, why = user_edit(*job)
            elif self.calculate is not None:
                after, why = self.calculate("edit", *job)
            else:
                with limited(EDIT_LIMIT if self.background else None):
                    after, why = user_edit(*job)
        else:
            try:
                after = apply_edit(self.rules, before, ops, owner)
                why = check_script_edit(before, after, c)
                if not why and frozen is not None and not before.line.same_on(after.line, Region.of(Interval(-INF, frozen, False, True))):
                    why = f"it would change the line where the script is definitive (up to {frozen})"
            except Refused as e:
                why = str(e)
        if why:
            self.log.refuse((t, source, why))
            return False
        self.log.changed(Change(t, self.chip.position(t), source, before, after))
        self.state = after
        self._within_memory(c)
        return True

    def _within_memory(self, c: Fraction) -> None:
        """Past the limit, half of what is left of the chip and not flat yet is flattened,
        the furthest first, then half of what is flat is removed, and so on."""
        while self.state.memory() > self.memory_limit:
            del self.log.changes[:]  # they keep older states
            starts = self.state.starts_before(c)
            layered = [x for x in starts if self.flat_to is None or x >= self.flat_to]
            flat = [x for x in starts if self.flat_to is not None and x < self.flat_to and (self.gone_to is None or x >= self.gone_to)]
            if len(layered) >= 2:
                self.flat_to = layered[len(layered) // 2]
                self.state = flattened(self.rules, self.state, self.flat_to, self.gone_to)
            elif len(flat) >= 2:
                self.gone_to = flat[len(flat) // 2]
                self.state = flattened(self.rules, self.state, self.flat_to, self.gone_to)
            else:
                break  # all there is is on the right of the chip, or being used

    def _alone(self, ops) -> bool:
        kinds = getattr(self.gen, "kinds_read", lambda: None)()
        return kinds is not None and not any(o.kind in kinds for o in ops)

    def switch_generator(self, on: bool) -> None:
        """Off: no call runs the generator (the script's, the user's edits, new rules), the run
        going on is dropped, and the script in place goes on as it is. On again: it runs once,
        from the line as it is (it may have changed meanwhile)."""
        if on == (not self.generator_off):
            return
        self.generator_off = not on
        if self.generator_off:
            self.request = None
        else:
            self._install(self.t, "generator on")

    def _constraints(self) -> tuple:
        return self.script.constraints or getattr(self.gen, "constraints", ())

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
            self._within_memory(self.chip.position(t1))  # (the mode rules add to the line too)
            self.t = t1
            self._instant(self.t)

    def start(self, edits: list[list[Op]] | None = None) -> None:
        """The first instant, with these edits of the user (else those scheduled at 0)."""
        self.started = True
        self._instant(self.t, first=True, edits=edits)

    def edit_now(self, ops: list[Op]) -> str | None:
        """An edit of the user at the current time: None, or why it is refused."""
        n = self.log.refused_count
        if not self.started:  # an edit of the first instant: the rest of it at start()
            self._edit(self.t, ops, USER, "user")
        else:
            self._instant(self.t, edits=[ops])
        refused = [why for _, src, why in self.log.refused_since(n) if src == "user"]
        return refused[0] if refused else None

    def modes_changed(self) -> None:
        """The modes changed from the chip's position on (given externally): revealed
        now, and the script's triggers see them."""
        self._after_edits(self.t, False, False, "user edit")

    def set_generator(self, generator: RulesGenerator) -> None:
        """New rules or problems: the line is computed again from the user's edits, and the
        generator runs again."""
        self.gen, self.rules = generator, generator.rules
        self._looks = None
        h = self.rules.smallest_offset
        self.grid = h / 2 if h else None
        self._replace(self.state.history, why="rules or problems changed", run=True)

    def _replace(self, history: tuple, why: str = "user edit", run: bool = False) -> None:
        before = self.state
        after = rebuild(self.rules, history, owned_by_others(before.claims), before.line)
        self.log.changed(Change(self.t, self.chip.position(self.t), "user", before, after))
        self.state = after
        self._read_edited = True
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
        if self._looks is not None and self._looks[0] is self.script:
            cands += [self.chip.first_time(e[0]) for e in self._looks[1].values() if e is not None and e[0] != INF and e[0] > c]
        later = [x for x in cands if x > t]
        return min(later, default=INF)

    def _instant(self, t: Fraction, first: bool = False, edits: list[list[Op]] | None = None) -> None:
        edited = self._read_edited = False
        for ops in ([e.ops for e in self.user_edits if e.time == t] if edits is None else edits):
            edited |= self._edit(t, ops, USER, "user")
        self._after_edits(t, first, edited, "user edit")

    def _after_edits(self, t: Fraction, first: bool, edited: bool, why: str, run: bool = False) -> None:
        self.started = True
        self.keep_pace(t)
        c = self.chip.position(t)
        if self.background and self.definitive is not None and c > self.definitive and \
                any(not isinstance(k, Edited) for k in self.script.rerun_triggers()):
            self._install(t, "catch up")  # (the program was put to sleep; a script that asks only
            # for the user's edits holds by itself: nothing to catch up)
        c = self.chip.position(t)
        revealed = sum(1 for p, _ in self.modes.transitions if p <= c)
        self.script.since += revealed - self.revealed
        self.revealed = revealed
        if run:  # a new generator: its run replaces the script
            self._install(t, why)
        self._edited = edited and not first
        self._execute(t, None)

    def iterate(self, period: Fraction) -> None:
        """An iteration of the loop of this period, on its own thread (`apart`)."""
        if self.started:
            self._execute(self.t, period)

    def _events(self, period: Fraction | None) -> list[Event]:
        if period is not None:
            return [e for loop in self.script.loops if loop.period == period for e in loop.events]
        return self.script.in_step() if self.apart else self.script.events

    def _execute(self, t: Fraction, period: Fraction | None) -> None:
        """The events of that loop (None: of what goes with the chip), each when it is true
        and was not. An event that changes the script or runs the generator ends the pass,
        and the script in place is executed in turn."""
        problems = getattr(self.gen, "problems", ())
        for _ in range(32):
            again = rerun = False
            script, events = self.script, self._events(period)
            looks = self._looks[1] if self._looks is not None and self._looks[0] is script else {}
            self._looks = (script, looks)
            for ev in events:
                key = str(ev)
                c = self.chip.position(t)
                mode = self.modes.mode_at(c)
                if not due(looks.get(key), ev.trigger, self.state, c, mode, problems):
                    continue
                now = self._cond(_reach(ev), t)
                looks[key] = look(ev.trigger, self.state, c, mode, problems)
                rising = now and key not in script.true
                (script.true.add if now else script.true.discard)(key)
                if isinstance(ev.trigger, TransitionCount):
                    rising = now and key not in script.fired
                elif isinstance(ev.trigger, (Edited, ChipOn)):
                    rising = now  # at each edit; at each instant the chip is on it
                if not rising:
                    continue
                script.fired.add(key)
                if isinstance(ev.trigger, Edited):
                    self._edited = False  # this edit is answered
                for a in ev.actions:
                    if isinstance(a, EditAction):
                        ops = list(a.ops)
                        if a.from_chip:  # (written from the chip: where it fires)
                            c = self.chip.position(t)
                            ops = [Op(o.action, o.kind, o.region.shift(c), o.value) for o in ops]
                        self._edit(t, ops, GEN, "script", self._frozen)
                    elif isinstance(a, ConstraintEditAction):
                        c = self.chip.position(t)
                        hidden = [k for k in script.constraints if not getattr(k, "explicit", False)]
                        self._edit(t, [o for k in hidden for o in k.ops(self.state, c)], GEN, "script")
                    elif isinstance(a, RerunAction):
                        rerun = ("start" if a.first else "user edit" if isinstance(ev.trigger, Edited)
                                 else "uncovered" if isinstance(ev.trigger, Uncovered) else "script")
                    elif isinstance(a, ShowLine):
                        self.shown += 1
                    elif isinstance(a, ChangeAction):
                        if a.target == script.part_of(ev):
                            self.log.refuse((t, "script", f"a script does not change itself: {a.target}"))
                        else:
                            self.script, again = self.script.changed(a), True
                if rerun or again:
                    break
            if rerun:
                self._install(t, rerun)
            elif not again:
                if period is None:
                    self._settle(t)
                return
        raise AssertionError("reruns did not settle")

    def _settle(self, t: Fraction) -> None:
        """The mode rules at the chip's position, now: they hold there at every instant, not
        only once the chip has moved on. Then `_sweep` goes on right after it. If the mode
        changed at this very position, what the old mode did there is undone first."""
        if not (self.script.covered or self.script.live):
            return
        c = self.chip.position(t)
        x, mode = Pos(c), self.modes.mode_at(c)
        was = self._settled
        if was is not None and was[0] == x:
            if was[1] == mode and was[2] is self.script and was[4] is self.state:
                return
            if was[1] != mode and was[4] is self.state and was[3] is not self.state:
                self.log.changed(Change(t, x, "mode", self.state, was[3], Region.point(x)))
                self.state = was[3]
        before = self.state
        if was is not None and was[0] == x and was[1] == mode and self.chip.first_time(c) < t:
            self._kept = x  # the chip stands here: what the user just did here is newer than it
            self._apply(t, mode, x, x)
            self._kept = None
        else:
            self._through(t, mode, x, x)
        self._settled = (x, mode, self.script, before, self.state)

    def _sweep(self, t0: Fraction, t1: Fraction) -> None:
        """The script's stretches act on the chip positions of [t0, t1), the first one
        unless `_settle` went through it."""
        if t1 <= t0 or not (self.script.covered or self.script.live):
            return
        c0, c1 = self.chip.position(t0), self.chip.position(t1)
        mode = self.modes.mode_at(c0)
        x = Pos(c0)
        hi = x if c1 == c0 else (Pos(c1) if self.chip.first_time(c1) < t1 else Pos(c1) - EPS)
        was = self._settled
        if was is not None and was[0] == x and was[1] == mode and was[2] is self.script and was[4] is self.state:
            x += EPS
        self._through(t0, mode, x, hi)

    def _through(self, t0: Fraction, mode: str, x: Pos, hi: Pos) -> None:
        """The chip goes through [x, hi] in this mode."""
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
                after = mode_won(self.state, after)
                self.log.changed(Change(t, x, "mode", self.state, after, Region.of(Interval._mk(x, end))))
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
        mr.kept = self._kept
        made = None  # the last state of the run, as the rules made it
        for ev in mr.act(self.state, x, hi):
            if isinstance(ev, Run):
                t = max(t0, self.chip.first_time(ev.start.x))
                made, after = ev.after, mode_won(self.state, ev.after)
                self.log.changed(Change(t, ev.start, "mode", self.state, after, ev.at))
                self.state = after
            else:
                self.log.refuse((t0, "mode", ev[1]))
        if made is not None and mr.plan is not None and mr.plan.last is made:
            mr.plan.last = self.state  # (the same line: the run goes on from it)

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
        was = self._settled
        if was is not None and was[0] == x and was[1] == mode and was[4] is self.state:
            x += EPS  # (`_settle` went through the chip's position)
        state, history = self.state, self.state.history
        cur = self._stretch
        if cur is not None and cur[1] is state and cur[2] == x and cur[0].mode == mode and cur[0].plan is not None:
            plan, d = cur[0].plan, self._shift
            def at(p):
                st = plan.exact(p - d) if p - d <= cur[0].b else None
                return None if st is None else _moved(st, d, history)

            def until(p):
                end = plan.formula_until(p - d)
                return None if end is None else min(end, cur[0].b) + d
            return Ahead(at, (id(plan), d), until)
        mr = self._live_rules.get(mode)
        plan = mr.plan if mr is not None else None
        if plan is not None and plan.last is state and plan.next == x:
            return Ahead(plan.exact, id(plan), plan.formula_until)
        return Ahead(lambda p: None, None, lambda p: None)

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
