"""Generated scripts: events that trigger edits of the line or a new generator run.

Triggers
- ChipIn(lo, hi, mode): true while the chip is in [lo, hi) and (if mode is not
  None) the mode at the chip is `mode`. Its actions fire at each instant it
  becomes true (at installation if it is already true); a Follow action is active
  while it is true.
- TransitionCount(k): fires once, when the k-th mode transition after the
  installation of the script is revealed.

Actions
- SetAction(items): an atomic edit, (type, region, value) items, value 0 meaning
  "remove the generated element". It is applied entirely or not at all.
- FollowAction(type, value, offsets): a continuous edit. While active, at every
  chip position c it writes `value` on c + offsets (offsets > 0, so always strictly
  right of the chip).
- RerunAction(): runs the rules generator again; its new script replaces this one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from intervals import INF, IntervalSet, Num, _fmt


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
class SetAction:
    items: tuple[tuple[str, IntervalSet, Fraction], ...]

    def __str__(self) -> str:
        parts = [f"set {t} on {x} to {v}%" if v else f"remove generated {t} on {x}" for t, x, v in self.items]
        return ", ".join(parts)


@dataclass(frozen=True)
class FollowAction:
    etype: str
    value: Fraction
    offsets: IntervalSet

    def __post_init__(self) -> None:
        if not self.offsets or not self.offsets.is_bounded():
            raise ValueError("follow offsets must be a non-empty bounded set")
        lo = self.offsets.intervals[0]
        if lo.lo < 0 or (lo.lo == 0 and lo.lo_closed):
            raise ValueError("follow offsets must be strictly positive")

    def __str__(self) -> str:
        what = f"{self.etype} at {self.value}%" if self.value else f"no generated {self.etype}"
        return f"keep {what} on chip + {self.offsets}"


@dataclass(frozen=True)
class RerunAction:
    def __str__(self) -> str:
        return "run the generator again"


@dataclass(frozen=True)
class Event:
    trigger: ChipIn | TransitionCount
    actions: tuple

    def __str__(self) -> str:
        verb = "while" if any(isinstance(a, FollowAction) for a in self.actions) else "if"
        return f"{verb} {self.trigger}, then " + "; ".join(map(str, self.actions)) + "."


@dataclass
class Script:
    events: list[Event] = field(default_factory=list)

    def text(self) -> str:
        return "\n".join(map(str, self.events))

    def rerun_triggers(self) -> list[ChipIn | TransitionCount]:
        return [e.trigger for e in self.events if any(isinstance(a, RerunAction) for a in e.actions)]
