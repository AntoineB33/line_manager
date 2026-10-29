"""Generated scripts: events that trigger edits of the line or a new generator run.

At the start of the program the script is only an instruction to run the rules
generator once, as soon as possible (`Script.initial`).

Triggers
- Start(): true from the start of the program; it fires once, at the first instant.
- ChipIn(lo, hi, mode): true while the chip is in [lo, hi) and (if mode is not
  None) the mode at the chip is `mode`. Its actions fire at each instant it
  becomes true (at installation if it is already true).
- TransitionCount(k): fires once, when the k-th mode transition after the
  installation of the script is revealed.
- Edited(): fires at each edit of the user (undo and clear included).
- Uncovered(): true while the chip is where none of the script's stretches applies,
  within the positions the run compiled (`covered`): a mode the run did not compile
  there, or a line other than the one it compiled.

Actions
- EditAction(ops): an edit of the line on exact intervals (continuous: any interval
  of the real line, not a grid), resolved by the fixed rules of resolve.py and applied
  only if it respects N1 and N2 (guards.py).
- ChipEditAction(ops): an edit whose regions are written from the chip: at the instant
  it fires, each region is shifted by the chip's position (`Future on (0, inf)` puts
  Future on (chip, inf)); then as EditAction.
- RerunAction(): runs the rules generator again; its new script replaces this one.

Compiled mode rules (`stretches`, mode_rules.Stretch): what the mode rules do as the
chip goes through the modes the run predicted, each stretch a continuous edit at the
chip that applies only if the chip is there in that mode on the line compiled for it
(simulation.py). The rules are the same at every position, so when the script is put
in place with the chip d after `origin` and the line is `base` moved by d, every
stretch applies moved by d (the run took that long; nothing changed meanwhile). Nothing else carries out the mode rules: where the script has no
stretch, they do nothing until a run compiles one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from intervals import INF, Num, _fmt
from line import Op


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
        return "the chip is where no compiled stretch applies (another mode or another line)"


@dataclass(frozen=True)
class EditAction:
    ops: tuple[Op, ...]

    def __str__(self) -> str:
        return ", ".join(map(str, self.ops))


@dataclass(frozen=True)
class ChipEditAction:
    ops: tuple[Op, ...]

    def at(self, chip) -> list[Op]:
        """The edit with the chip at `chip`."""
        return [Op(o.action, o.kind, o.region.shift(chip), o.value) for o in self.ops]

    def __str__(self) -> str:
        return ", ".join(str(o).replace(" on ", " on chip + ", 1).replace(" off ", " off chip + ", 1) for o in self.ops)


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

    @staticmethod
    def initial() -> Script:
        """The script at the start of the program: run the generator once, as soon as possible."""
        return Script([Event(Start(), (RerunAction(first=True),))])

    def text(self) -> str:
        return "\n".join([str(e) for e in self.events] + [st.text() for st in self.stretches])

    def rerun_triggers(self) -> list[Start | ChipIn | TransitionCount | Edited | Uncovered]:
        return [e.trigger for e in self.events if any(isinstance(a, RerunAction) for a in e.actions)]
