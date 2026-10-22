"""Public entry point.  Requirements, semantics and proofs: SEMANTICS.md."""
from __future__ import annotations

import io
import sys
import time
from fractions import Fraction

from chip import Chip, Mode
from machine import (
    Deadline,
    Machine,
    Optimizer,
    ProcessRunner,
    Resources,
    Run,
    View,
    usable_cpus,
)
from reverse_logic import (
    Add,
    Extend,
    Move,
    Remove,
    Retract,
    forbids,
    implies,
)
from scheduler import TendencyPlanner
from track import (
    Follow,
    Ratio,
    Refused,
    Region,
    Rel,
    Rule,
    RulesText,
    Text,
    Track,
    TrackManager,
    ahead,
    at_or_behind,
    restrict,
)

__all__ = [
    "Add",
    "Chip",
    "Deadline",
    "Extend",
    "Follow",
    "Machine",
    "Mode",
    "Move",
    "Optimizer",
    "ProcessRunner",
    "Ratio",
    "Refused",
    "Region",
    "Rel",
    "Remove",
    "Resources",
    "Retract",
    "Rule",
    "RulesText",
    "Run",
    "TendencyPlanner",
    "Text",
    "Track",
    "TrackManager",
    "View",
    "ahead",
    "at_or_behind",
    "forbids",
    "implies",
    "restrict",
    "usable_cpus",
]


# ==========================================
# MOCK GENERATIONS & PROOF
# ==========================================

def alpha_rules(position: Fraction, mode: Mode, state: Track):
    # Put an A point 2 ahead of the chip.
    yield Add("A", Region.point(position + 2))


def _wait(machine: Machine) -> None:
    while machine.running:
        machine.poll()
        time.sleep(0.01)


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # Region reprs use "∪"

    manager = TrackManager(modes=(1, 2), initial_mode=1, rules=[implies("A", ("B", 1))])

    print("--- 1. First text (Alpha): an A 2 ahead of the chip, once, at the install ---")
    manager.switch_rules_text(Text("Text_Alpha", rules=[Rule(alpha_rules)]))
    manager.print_track()

    print("\n--- 2. Chip advances to 2.5, the user switching to mode 2 at 2.2: the A at 2 is reached ---")
    manager.update_chip(2.5, transitions=[(2.2, 2)])
    manager.print_track()

    print("\n--- 3. New text (Beta): in mode 2, X on [chip + 1, chip + 2]; Alpha's edits are reversed on the right ---")
    manager.switch_rules_text(Text("Text_Beta", follow=[Follow("X", Rel(1), Rel(2), modes={2})]))
    manager.print_track()

    print("\n--- 4. Chip advances to 5, back to mode 1 at 4: X follows the chip, and nothing is left behind ---")
    manager.update_chip(5, transitions=[(4, 1)])
    manager.print_track()

    print("\n--- 5. The machine: S placed where the modes the user tends to follow earn from it ---")
    # In mode "busy", S earns 1 per unit of length; in "idle" it costs 1. D tends to make the user busy.
    optimizer = TendencyPlanner({"busy": {"S": 1}, "idle": {"S": -1}}, controlled=["S"], cost=Fraction(1, 2),
                                tendency=[("D", "busy"), ("Q", "idle")])
    manager = TrackManager(modes=("idle", "busy"), initial_mode="idle",
                           initial_elements=[("D", Region.closed(2, 6)), ("Q", Region.closed(6, 7)),
                                             ("D", Region.closed(Fraction(15, 2), 8))])
    machine = Machine(manager, optimizer, performance=Fraction(1, 10))  # a long horizon, a fine grid
    _wait(machine)
    print(f"run: {machine.runs[-1].outcome}")
    manager.print_track()
    for x, mode in ((1, "idle"), (3, "busy"), (5, "busy"), (Fraction(13, 2), "idle")):
        machine.update_chip(x, [(x, mode)] if mode != manager.chip.mode else [])
    print("chip moved through 3 (busy) and 13/2 (idle), as the tendency said: still", len(machine.runs), "run")
    manager.print_track()
    machine.update_chip(10, [(9, "busy")])
    print("the user became busy at 9, where nothing incites it: the text reacts by itself")
    manager.print_track()
    machine.close()
