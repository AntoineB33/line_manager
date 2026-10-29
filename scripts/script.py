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

Actions
- EditAction(ops): an edit of the line on exact intervals (continuous: any interval
  of the real line, not a grid), resolved by the fixed rules of resolve.py and applied
  only if it respects N1 and N2 (guards.py).
- RerunAction(): runs the rules generator again; its new script replaces this one.
- ModeRulesAction(): while its trigger holds, the mode rules act at the chip's
  position (simulation.py), checked by M1 and N2 (guards.py). The line is modified
  only by the user and the generated script, so the mode rules act through it.
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
class EditAction:
    ops: tuple[Op, ...]

    def __str__(self) -> str:
        return ", ".join(map(str, self.ops))


@dataclass(frozen=True)
class RerunAction:
    first: bool = False  # the initial script's run

    def __str__(self) -> str:
        return "run the generator once, as soon as possible" if self.first else "run the generator again"


@dataclass(frozen=True)
class ModeRulesAction:
    def __str__(self) -> str:
        return "apply the mode rules at the chip"


@dataclass(frozen=True)
class Event:
    trigger: Start | ChipIn | TransitionCount
    actions: tuple

    def __str__(self) -> str:
        return f"if {self.trigger}, then " + "; ".join(map(str, self.actions)) + "."


@dataclass
class Script:
    events: list[Event] = field(default_factory=list)

    @staticmethod
    def initial() -> Script:
        """The script at the start of the program: run the generator once, as soon as possible."""
        return Script([Event(Start(), (RerunAction(first=True),))])

    def text(self) -> str:
        return "\n".join(map(str, self.events))

    def rerun_triggers(self) -> list[Start | ChipIn | TransitionCount]:
        return [e.trigger for e in self.events if any(isinstance(a, RerunAction) for a in e.actions)]

    def applies_mode_rules(self, chip: Fraction, mode: str) -> bool:
        return any(isinstance(a, ModeRulesAction) and isinstance(e.trigger, ChipIn) and e.trigger.holds(chip, mode)
                   for e in self.events for a in e.actions)
