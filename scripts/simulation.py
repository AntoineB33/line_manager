"""The world: the chip moving, modes being revealed, the user editing, scripts running.

Time t >= 0 is exact. The chip's position is a continuous, non-decreasing,
piecewise-linear function of time. Everything happens at exact instants, in this
order at one instant: user edits, then revealed mode transitions, then a run of the
generator if the user edited, then the script's triggers in script order (a rerun
installs the new script, whose triggers are evaluated at once). The first script is
`Script.initial()`: the generator runs at the first instant, after the user's edits
of that instant, through its trigger (logged as "start").

Only the user and the generated script modify the line; the script carries out the
mode rules (script.ModeRulesAction). They act at the chip's position, one position
after the other as the chip goes through them (mode_rules.py): each position is
decided when the chip is there, on the line as it is then, never ahead of the chip.
Between two instants the chip goes through the positions chip([t0, t1)), all with
one mode; they are logged as continuous changes, each changing its positions as the
chip goes through them. Sweeps are cut at the multiples of half the smallest rule
offset.

The mode rules of each mode are kept from one sweep to the next: a run they compiled
ahead goes on while the line is the one it made (mode_rules._Plan).

A simulation can also go step by step (the interactive program, UI/app.py):
`start()` is the first instant, `advance(t)` runs it up to time t (starting it if
needed), the chip's trajectory being extended as it goes (`Chip.extend`); `edit_now`
before the start is an edit of the first instant, whose triggers wait for `start()`;
`edit_now`, `undo_now`, `clear_now` and `set_generator` act at
the current time, as a user edit at that instant would; `modes_changed` after the
modes from the chip on were changed (ModeTimeline.replace).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from generator import RulesGenerator, RunStats
from guards import check_script_edit
from intervals import EPS, INF, Num, Pos
from line import GEN, USER, LineState, Op
from modes import ModeTimeline
from region import Region
from resolve import Refused, owned_by_others, apply_edit, rebuild
from script import ChipIn, EditAction, RerunAction, Script, Start, TransitionCount
from mode_rules import ModeRules, Run


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
        self.mode_rules: dict[str, ModeRules] = {}

    def snapshot(self) -> tuple:
        """What a step changes, to go back to it (`restore`) if the step is interrupted."""
        plans = {m: (mr.plan, mr.plan and (mr.plan.last, mr.plan.next, mr.plan.pending)) for m, mr in self.mode_rules.items()}
        return (self.state, len(self.log.changes), len(self.log.refused), len(self.log.runs), self.script,
                self.revealed, self._since_install, list(self._was_true), list(self._fired), self.t, self.started,
                len(self.chip.points), plans)

    def restore(self, snap: tuple) -> None:
        (self.state, nc, nr, nu, self.script, self.revealed, self._since_install, self._was_true, self._fired,
         self.t, self.started, npts, plans) = snap
        del self.log.changes[nc:], self.log.refused[nr:], self.log.runs[nu:], self.chip.points[npts:]
        for m in list(self.mode_rules):
            if m not in plans:
                del self.mode_rules[m]
        for m, (plan, at) in plans.items():
            self.mode_rules[m].plan = plan
            if plan is not None:
                plan.last, plan.next, plan.pending = at

    def _chip_rules(self):
        """The mode rules acting at the chip now (the script applies them), or None."""
        c = self.chip.position(self.t)
        mode = self.modes.mode_at(c)
        if not self.rules.mode_rules or not self.script.applies_mode_rules(c, mode):
            return None
        if mode not in self.mode_rules:
            self.mode_rules[mode] = ModeRules(self.rules, mode)
        return self.mode_rules[mode]

    def prepared(self) -> bool:
        """The run of the mode rules at the chip is compiled (or there is none)."""
        mr = self._chip_rules()
        return mr is None or mr.prepared(self.state, Pos(self.chip.position(self.t)))

    def prepare(self) -> None:
        """Compile ahead the run of the mode rules at the chip, before the chip moves."""
        mr = self._chip_rules()
        if mr is not None:
            mr.prepare(self.state, Pos(self.chip.position(self.t)))

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
        if isinstance(trig, Start):
            return True
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
        self.mode_rules = {}
        self._replace(self.state.history, why="rules or problems changed")

    def _replace(self, history: tuple, why: str = "user edit") -> None:
        before = self.state
        after = rebuild(self.rules, history, owned_by_others(before.claims), before.line)
        self.log.changes.append(Change(self.t, self.chip.position(self.t), "user", before, after))
        self.state = after
        if not self.started:
            self.started = True
            self._instant(self.t, first=True, edits=[])
        else:
            self._after_edits(self.t, False, True, why)

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

    def _after_edits(self, t: Fraction, first: bool, edited: bool, why: str) -> None:
        self.started = True
        c = self.chip.position(t)
        revealed = sum(1 for p, _ in self.modes.transitions if p <= c)
        self._since_install += revealed - self.revealed
        self.revealed = revealed
        if edited and not first:  # at the first instant, the initial script runs the generator
            self._install(t, why)
        for _ in range(3):
            rerun = None
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
                        rerun = "start" if a.first else "script"
                if rerun:
                    break
            if not rerun:
                return
            self._install(t, rerun)
        raise AssertionError("reruns did not settle")

    def _sweep(self, t0: Fraction, t1: Fraction) -> None:
        """The mode rules act on the chip positions of [t0, t1)."""
        if t1 <= t0 or not self.rules.mode_rules:
            return
        c0, c1 = self.chip.position(t0), self.chip.position(t1)
        mode = self.modes.mode_at(c0)
        if not self.script.applies_mode_rules(c0, mode):
            return
        lo = Pos(c0)
        hi = lo if c1 == c0 else (Pos(c1) if self.chip.first_time(c1) < t1 else Pos(c1) - EPS)
        if mode not in self.mode_rules:
            self.mode_rules[mode] = ModeRules(self.rules, mode)
        for ev in self.mode_rules[mode].act(self.state, lo, hi):
            t = max(t0, self.chip.first_time(ev.start.x if isinstance(ev, Run) else ev[0].x))
            if isinstance(ev, Run):
                self.log.changes.append(Change(t, ev.start, "mode", ev.before, ev.after, ev.at))
                self.state = ev.after
            else:
                self.log.refused.append((t, "mode", ev[1]))
