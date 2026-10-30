"""Generated scripts: a text of events that trigger edits of the line or a new generator
run. What the text says is what the script does, nothing else: the user can change it
(script_text.py, Simulation.replace_script), and what is written then applies.

At the start of the program the script is only an instruction to run the rules
generator once, as soon as possible (`Script.initial`).

Lines
- keep ...: a constraint of the script (constraints.py), written with its parameters.
- if <trigger>, then <actions>.
- if the chip is in [a, b] and mode is m, then apply the mode rules: the mode rules, the
  constraints kept, as the chip goes through these positions in that mode, on the line
  as it is (each edit at the chip, continuous).
- elsewhere, apply the mode rules: the same at all the other positions and modes.

Triggers
- Start(): true from the start of the program; it fires once, at the first instant.
- ChipIn(lo, hi, mode): true while the chip is in [lo, hi) and (if mode is not
  None) the mode at the chip is `mode`. Its actions fire at each instant it
  becomes true (at installation if it is already true).
- TransitionCount(k): fires once, when the k-th mode transition after the
  installation of the script is revealed.
- Edited(): fires at each edit of the user (undo and clear included).
- Uncovered(): true while the chip is where the script does not apply the mode rules.

Actions
- EditAction(ops): an edit of the line on exact intervals (continuous: any interval
  of the real line, not a grid), resolved by the fixed rules of resolve.py and applied
  only if it respects N1 and N2 (guards.py).
- ConstraintEditAction(): the edits the script's constraints ask for where it fires, on
  the line as it is. Then as EditAction.
- RerunAction(): runs the rules generator again; its new script replaces this one.

Where the run compiled the mode rules ahead (`stretches`, mode_rules.Stretch), the
simulation goes through what it compiled instead of computing it again: only a
shortcut, used if the line is the one compiled for it, and not part of the text. The
rules are the same at every position, so when the script is put in place with the chip
d after `origin` and the line is `base` moved by d, every stretch is used moved by d.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from intervals import INF, Interval, Num, _fmt
from line import Op
from region import Region

MODE_RULES = "apply the mode rules"


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
class TransitionCount:
    k: int

    def __str__(self) -> str:
        return f"{self.k} mode transition{'s' if self.k > 1 else ''} happened"


@dataclass(frozen=True)
class Edited:
    def __str__(self) -> str:
        return "the user edits the line"


@dataclass(frozen=True)
class Uncovered:
    def __str__(self) -> str:
        return "the chip is where this script does not apply the mode rules"


@dataclass(frozen=True)
class EditAction:
    ops: tuple[Op, ...]

    def __str__(self) -> str:
        return ", ".join(map(str, self.ops))


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
class Event:
    trigger: Start | ChipIn | TransitionCount | Edited | Uncovered
    actions: tuple

    def __str__(self) -> str:
        return f"if {self.trigger}, then " + "; ".join(map(str, self.actions)) + "."


@dataclass
class Script:
    events: list[Event] = field(default_factory=list)
    stretches: list = field(default_factory=list)  # mode_rules.Stretch, in the order of the chip
    covered: list = field(default_factory=list)  # (lo, hi, mode): the positions and modes the run compiled
    origin: object = None  # the chip's position the run compiled from
    base: object = None  # the line it compiled from (line.LineState), its own edits made
    live: bool = False  # it applies the mode rules everywhere
    constraints: tuple = ()  # what it keeps

    @staticmethod
    def initial() -> Script:
        """The script at the start of the program: run the generator once, as soon as possible."""
        return Script([Event(Start(), (RerunAction(first=True),))])

    def text(self) -> str:
        return "\n".join([str(k) for k in self.constraints] + [str(e) for e in self.events] +
                         [f"if the chip is in [{lo}, {hi}] and mode is {m}, then {MODE_RULES}." for lo, hi, m in self.covered] +
                         ([f"elsewhere, {MODE_RULES}."] if self.live else []))

    def rerun_triggers(self) -> list[Start | ChipIn | TransitionCount | Edited | Uncovered]:
        return [e.trigger for e in self.events if any(isinstance(a, RerunAction) for a in e.actions)]
