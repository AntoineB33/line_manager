"""Generated scripts. A script is an object with a state and three elements:
- S_f (`frame` and `loops`): scripts executed in a loop by different threads at different
  frequencies. `frame` is s_f, executed at every frame: a flat list of what to place,
  which depends on the window, so `on_view` fills it where the window is. `loops` are the
  others: `STEP` is the loop of the simulation's thread, the exact one (below), and any
  other one has a thread of its own in the window (gui.App._looper).
- s_u (`on_edit`): executed at each edit of the user.
- s_v (`on_view`): executed when the view is moved or resized.
Each can change the state, which includes the others but not itself (ChangeAction). The
other fields of `Script` are the rest of the state.

The STEP loop is exact: an iteration goes through the positions the chip went through
since the last one, in order, each trigger at its exact position. So as long as the same
script is in place, how often it iterates changes when the line adapts, not what it
becomes (requirement.md). The other loops look at the line as it is when they iterate:
what the generator writes goes in the STEP loop.

What the text says is what the script does, nothing else: the user can change it
(script_text.py, Simulation.replace_script), and what is written then applies. s_f is not
in the text: `on_view` makes it.

At the start of the program the script is only an instruction to run the rules
generator once, as soon as possible (`Script.initial`).

Lines
- every p s: the lines under it are the loop of period p. Lines written before any such
  line are in the STEP loop.
- keep ...: a constraint of the script (constraints.py), written with its parameters.
- if <trigger>, then <actions>.
- if the chip is in [a, b] and mode is m, then apply the mode rules: the mode rules, the
  constraints kept, as the chip goes through these positions in that mode, on the line
  as it is (each edit at the chip, continuous). Without a mode: in any mode.
- elsewhere, apply the mode rules: the same at all the other positions and modes.

Triggers
- Start(): true from the start of the program; it fires once, at the first instant.
- ChipIn(lo, hi, mode): true while the chip is in [lo, hi) and (if mode is not
  None) the mode at the chip is `mode`. Its actions fire at each instant it
  becomes true (at installation if it is already true).
- TransitionCount(k): fires once, when the k-th mode transition after the
  installation of the script is revealed.
- Edited(only): fires at each edit of the user; with `only`, of
  an element that a rule, a constraint or a problem of the generator reads.
- Viewed(): the view moves or is resized.
- Uncovered(): true while the chip is where the script does not apply the mode rules.
- ChipOn(K, mode): true while the chip is in an interval of K (not where it starts) and (if mode is not None) the mode at the chip is `mode`; its actions fire at each
  instant it is true (e.g. a break the chip pushes: placed again from the chip at each).
  Where the script applies the mode rules everywhere, "if the chip is on K, then from the
  chip, take K off (0, inf), put K on R" is the same line kept by the mode rules as the
  chip goes, compiled ahead, with no edit at each instant (`Script.pushes`).
- PresenceAbove(K, v, y, H, lo, hi, mode): true while the presence ratio of K at v% on
  (chip, chip + H] is above y, the chip in [lo, hi) and the mode at the chip `mode`.
- ScoreBelow(z): true while the optimization score (`score`) is below z.
  Both are looked at at each instant.
A line with Edited is in s_u and one with Viewed in s_v, wherever it is written.

Actions
- EditAction(ops, from_chip): an edit of the line on exact intervals (continuous: any
  interval of the real line, not a grid), resolved by the fixed rules of resolve.py and
  applied only if it respects N1 and N2 (guards.py); with `from_chip`, its intervals are
  written from the chip (0: the chip's position where it fires).
- ConstraintEditAction(): the edits the script's constraints ask for where it fires, on
  the line as it is. Then as EditAction.
- RerunAction(): runs the rules generator again; its new script replaces this one.
- ShowLine(): s_f is made again: what the view shows of the line.
- ChangeAction(add, target, event): adds this event to a part of the script, or removes
  it from it: `every p s`, `the user edit script` or `the view script`, never the part it
  is in (script_text.py refuses it).

Where the run compiled the mode rules ahead (`stretches`, mode_rules.Stretch), the
simulation goes through what it compiled instead of computing it again: only a
shortcut, used if the line is the one compiled for it, and not part of the text. The
rules are the same at every position, so when the script is put in place with the chip
d after `origin` and the line is `base` moved by d, every stretch is used moved by d.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from fractions import Fraction

from intervals import INF, Interval, IntervalSet, Num, _fmt
from line import Op

MODE_RULES = "apply the mode rules"
STEP = Fraction(1, 20)  # the seconds between two iterations of the exact loop
ON_EDIT, ON_VIEW = "the user edit script", "the view script"


class _Now:
    """What a problem's error reads, on the line as it is (nothing predicted)."""
    weights_full = None

    def __init__(self, c) -> None:
        self.c = c

    def expected(self, s, kind, weights, Wset):
        return s.line.integral(kind, Wset)


def score(problems, state, chip) -> Fraction:
    """The optimization score of the line, in (0, 1]: 1 when the targets are reached, lower
    as the weighted error of the problems grows."""
    return 1 / (1 + sum((w * p.error(_Now(chip), state) for p, w in problems if w), Fraction(0)))


@dataclass(frozen=True)
class Start:
    def __str__(self) -> str:
        return "the program starts"


@dataclass(frozen=True)
class ChipIn:
    lo: Num
    hi: Num
    mode: str | None = None

    def holds(self, chip: Fraction, mode: str) -> bool:
        return self.lo <= chip < self.hi and (self.mode is None or self.mode == mode)

    def __str__(self) -> str:
        s = f"the chip is in [{_fmt(self.lo)}, {_fmt(self.hi)})" if self.hi != INF else f"the chip is at or after {_fmt(self.lo)}"
        return s + (f" and mode is {self.mode}" if self.mode is not None else "")


@dataclass(frozen=True)
class ChipOn:
    """True while the chip is in an interval of `kind`, or one is less than `within` after
    it, i.e. on [chip, chip + within) (within 0: the chip in it, not where it starts), and
    (if `mode` is not None) the mode at the chip is `mode`. Its actions fire at each
    instant it is true. With no `within`: as far after the chip as its event's edit
    written from the chip puts `kind` (simulation._reach): with that edit putting the
    piece at (chip + 10, chip + 910], the chip keeps it 10 ahead as it goes.

    With `lasted`: instead of the chip in it, `kind` has been on the chip for `lasted`, on
    [chip - lasted, chip) (what is behind it: the same before and after the mode rules act
    at the chip). With `unless`: and the chip is not on `unless`. "If the chip has been on
    NoScreen for 900 and is not on Break15, and mode is 2": locked for 15 minutes, out of a
    break."""
    kind: str
    mode: str | None = None
    within: Num | None = None  # (None: 0)
    lasted: Num | None = None
    unless: str | None = None

    def holds(self, state, chip, mode: str) -> bool:
        if self.mode is not None and self.mode != mode:
            return False
        from intervals import Pos
        from region import Region
        p = chip if isinstance(chip, Pos) else Pos(chip)
        if self.unless is not None and state.line.presence(self.unless).contains(p):
            return False
        pres = state.line.presence(self.kind)
        if self.lasted is not None:
            return Region.of(Interval(p - Fraction(self.lasted), p, True, False)).issubset(pres)
        d = Fraction(self.within or 0)
        if not d:
            return pres.contains(p)
        return pres.contains(p) or not (pres & Region.of(Interval(p, p + d, False, False))).is_empty()

    def __str__(self) -> str:
        d = Fraction(self.within or 0)
        if self.lasted is not None:
            where = f"the chip has been on {self.kind} for {_fmt(Fraction(self.lasted))}"
        else:
            where = f"the chip is on {self.kind}" if not d else f"{self.kind} is less than {_fmt(d)} after the chip"
        where += f" and is not on {self.unless}" if self.unless is not None else ""
        return where + (f" and mode is {self.mode}" if self.mode is not None else "")


@dataclass(frozen=True)
class TransitionCount:
    k: int

    def __str__(self) -> str:
        return f"{self.k} mode transition{'s' if self.k > 1 else ''} happened"


@dataclass(frozen=True)
class Edited:
    only: bool = False

    def __str__(self) -> str:
        return "the user edits what the rules, the constraints or the problems read" if self.only else "the user edits the line"


@dataclass(frozen=True)
class Viewed:
    def __str__(self) -> str:
        return "the view moves or is resized"


@dataclass(frozen=True)
class Uncovered:
    def __str__(self) -> str:
        return "the chip is where this script does not apply the mode rules"


@dataclass(frozen=True)
class PresenceAbove:
    """The ratio of K at this value on (chip, chip + H], (1/H) ∫ 1 dx, is above y, the chip
    in [lo, hi) and (if not None) the mode at the chip `mode`."""
    kind: str
    value: Fraction
    y: Fraction
    H: Fraction
    lo: Num
    hi: Num
    mode: str | None = None

    def holds(self, state, chip, mode: str) -> bool:
        if not (self.lo <= chip < self.hi and (self.mode is None or self.mode == mode)):
            return False
        r = state.line.vals.get(self.kind, {}).get(self.value)
        return r is not None and r.measure_on(IntervalSet.of(Interval(chip, chip + self.H, False, True))) / self.H > self.y

    def __str__(self) -> str:
        return (f"{self.kind} {_fmt(self.value)}% exceeds a presence ratio of {_fmt(self.y)} over the next {_fmt(self.H)} "
                f"when the chip is in [{_fmt(self.lo)}, {_fmt(self.hi)})" + (f" and mode is {self.mode}" if self.mode is not None else ""))


@dataclass(frozen=True)
class ScoreBelow:
    """The optimization score (`score`) is below z."""
    z: Fraction

    def __str__(self) -> str:
        return f"the optimization score becomes less than {_fmt(self.z)}"


@dataclass(frozen=True)
class EditAction:
    ops: tuple[Op, ...]
    from_chip: bool = False  # the intervals are written from the chip where it fires

    def __str__(self) -> str:
        return ("from the chip, " if self.from_chip else "") + ", ".join(map(str, self.ops))


@dataclass(frozen=True)
class ConstraintEditAction:
    def __str__(self) -> str:
        return "satisfy the constraints"


@dataclass(frozen=True)
class RerunAction:
    first: bool = False  # the initial script's run

    def __str__(self) -> str:
        return "run the generator once, as soon as possible" if self.first else "run the generator again"


@dataclass(frozen=True)
class ShowLine:
    def __str__(self) -> str:
        return "show the line"


@dataclass(frozen=True)
class Event:
    trigger: Start | ChipIn | PresenceAbove | ScoreBelow | TransitionCount | Edited | Viewed | Uncovered
    actions: tuple

    def __str__(self) -> str:
        return f"if {self.trigger}, then " + "; ".join(map(str, self.actions)) + "."


@dataclass(frozen=True)
class ChangeAction:
    add: bool
    target: str  # `loop_name(p)`, ON_EDIT or ON_VIEW
    event: Event

    def __str__(self) -> str:
        return f'{"add to" if self.add else "remove from"} {self.target}: "{str(self.event).removesuffix(".")}"'


def loop_name(period: Fraction) -> str:
    return f"every {_fmt(period)} s"


@dataclass(frozen=True)
class Loop:
    """A script of S_f: these events, executed every `period` seconds."""
    period: Fraction
    events: tuple = ()


@dataclass(frozen=True)
class Place:
    """An instruction of s_f. With the chip d further than when it was written: `item`, a
    group of what stays on the line (no coords), moves back with the chip; or it is at
    `coords`, each point going with the chip at its rate."""
    item: object
    shape: str
    coords: tuple = ()
    rates: tuple = ()

    def run(self, canvas, scale: float, d: float, moved: float) -> None:
        if not self.coords:
            canvas.move(self.item, -scale * (d - moved), 0)
            return
        out = list(self.coords)
        for i, r in enumerate(self.rates):
            out[2 * i] += (r - 1) * scale * d
        canvas.coords(self.item, *out)

    def __str__(self) -> str:
        return f"place {self.shape} at " + (", ".join(map(str, self.coords)) or "the line")


SHOW = (Event(Viewed(), (ShowLine(),)),)


@dataclass
class Script:
    loops: list[Loop] = field(default_factory=list)  # S_f, but for s_f
    on_edit: tuple = ()  # s_u
    on_view: tuple = ()  # s_v
    frame: list[Place] = field(default_factory=list, compare=False)  # s_f
    # s_f written by the user (script_python.from_python): it stays until s_v makes it again,
    # that is until the count of ShowLine (Simulation.shown) is past this one; None: s_v's
    frame_given: int | None = field(default=None, compare=False)
    stretches: list = field(default_factory=list)  # mode_rules.Stretch, in the order of the chip
    covered: list = field(default_factory=list)  # (lo, hi, mode): the positions and modes the run compiled
    origin: object = None  # the chip's position the run compiled from
    base: object = None  # the line it compiled from (line.LineState), its own edits made
    live: bool = False  # it applies the mode rules everywhere
    constraints: tuple = ()  # what it keeps
    true: set = field(default_factory=set, compare=False)  # the text of the events true when last read
    fired: set = field(default_factory=set, compare=False)  # and of those that fired
    since: int = field(default=0, compare=False)  # the mode transitions since it was put in place
    _pushes: dict | None = field(default=None, init=False, compare=False, repr=False)  # (`pushes`)

    def pushes(self) -> dict:
        """{the text of an event: constraints.ChipPush} for each event of the chip pushing a
        kind ("if the chip is on K, then from the chip ... K", constraints.push_of), where
        the script applies the mode rules everywhere (`live`): those the mode rules keep as
        the chip goes, compiled ahead, instead of an edit at each instant (the same line)."""
        if self._pushes is None:
            from constraints import push_of
            out = {}
            if self.live:
                for e in self.events:
                    k = push_of(e)
                    if k is not None:
                        out[str(e)] = k
            self._pushes = out
        return self._pushes

    @staticmethod
    def of(events=(), **fields) -> Script:
        """The script of these events: each in the part of the script its trigger says, the
        STEP loop if it says none."""
        return Script(**fields).with_events(events)

    @staticmethod
    def initial() -> Script:
        """The script at the start of the program: run the generator once, as soon as possible."""
        return Script.of([Event(Start(), (RerunAction(first=True),))])

    @property
    def events(self) -> list[Event]:
        """What the simulation executes (not s_v, which the window does)."""
        return [e for loop in self.loops for e in loop.events] + list(self.on_edit)

    def with_events(self, events) -> Script:
        """The same script with these events; those of another loop stay in it."""
        where = {str(e): loop.period for loop in self.loops for e in loop.events}
        parts: dict = {STEP: []} | {loop.period: [] for loop in self.loops}
        on_edit = []
        for e in events:
            (on_edit if isinstance(e.trigger, Edited) else parts[where.get(str(e), STEP)]).append(e)
        return replace(self, loops=[Loop(p, tuple(evs)) for p, evs in parts.items() if evs or p == STEP], on_edit=tuple(on_edit))

    def part_of(self, e: Event) -> str:
        """The name of the part of the script this event is in."""
        for name, events in ((ON_EDIT, self.on_edit), (ON_VIEW, self.on_view)):
            if any(str(x) == str(e) for x in events):
                return name
        return next((loop_name(loop.period) for loop in self.loops if any(str(x) == str(e) for x in loop.events)), loop_name(STEP))

    def changed(self, a: ChangeAction) -> Script:
        """The script after this action."""
        def edit(events: tuple) -> tuple:
            return (*events, a.event) if a.add else tuple(e for e in events if str(e) != str(a.event))
        if a.target == ON_EDIT:
            return replace(self, on_edit=edit(self.on_edit))
        if a.target == ON_VIEW:
            return replace(self, on_view=edit(self.on_view))
        loops = list(self.loops)
        for i, loop in enumerate(loops):
            if loop_name(loop.period) == a.target:
                loops[i] = replace(loop, events=edit(loop.events))
                return replace(self, loops=loops)
        period = Fraction(a.target.removeprefix("every ").removesuffix(" s"))
        return replace(self, loops=loops + [Loop(period, (a.event,))]) if a.add else self

    def in_step(self) -> list[Event]:
        """The events the simulation's thread executes when the other loops have threads."""
        return [e for loop in self.loops if loop.period == STEP for e in loop.events] + list(self.on_edit)

    def shows_line(self) -> bool:
        return any(isinstance(a, ShowLine) for e in self.on_view for a in e.actions)

    def text(self) -> str:
        applied = [f"if the chip is in [{lo}, {hi}]" + (f" and mode is {m}" if m is not None else "") + f", then {MODE_RULES}."
                   for lo, hi, m in self.covered] + ([f"elsewhere, {MODE_RULES}."] if self.live else [])
        out = [str(k) for k in self.constraints]
        for loop in sorted(self.loops, key=lambda loop: loop.period != STEP):
            body = [str(e) for e in loop.events] + (applied if loop.period == STEP else [])
            if body:
                out += [f"{loop_name(loop.period)}:"] + [f"  {line}" for line in body]
        return "\n".join(out + [str(e) for e in self.on_edit + self.on_view])

    def rerun_triggers(self) -> list:
        return [e.trigger for e in self.events if any(isinstance(a, RerunAction) for a in e.actions)]
