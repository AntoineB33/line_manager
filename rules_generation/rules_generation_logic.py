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
from scheduler import ModeScheduler
from reverse_logic import (
    Add,
    Extend,
    Move,
    Remove,
    Retract,
    forbids,
    implies,
)
from track import (
    FollowText,
    Refused,
    Region,
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
    "FollowText",
    "Machine",
    "Mode",
    "ModeScheduler",
    "Move",
    "Optimizer",
    "ProcessRunner",
    "Refused",
    "Region",
    "Remove",
    "Resources",
    "Retract",
    "RulesText",
    "Run",
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


def beta_follow(position: Fraction, mode: Mode, state: Track):
    # In mode 2, an X interval of length 1 that follows the chip, 1 ahead.
    if mode == 2:
        yield "X", Region.closed(position + 1, position + 2)


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # Region reprs use "∪"

    manager = TrackManager(modes=(1, 2), initial_mode=1, rules=[implies("A", ("B", 1))])

    print("--- 1. First text (Alpha) ---")
    manager.switch_rules_text(Text("Text_Alpha", rules=alpha_rules))
    manager.print_track()

    print("\n--- 2. Chip advances to 2.5: the A at 2 is reached, its B at 3 is not ---")
    manager.update_chip(2.5)
    manager.print_track()

    print("\n--- 3. New text (Beta), mode 2 from 3: Alpha's edits are reversed on the right only ---")
    manager.switch_rules_text(Text("Text_Beta", follow=beta_follow, transitions=((3, 2),)))
    manager.print_track()

    print("\n--- 4. Chip advances to 3.5, then 5: X follows the chip, and the part of it the chip reached stays ---")
    manager.update_chip(3.5)
    manager.print_track()
    manager.update_chip(5)
    manager.print_track()

    print("\n--- 5. The machine and its optimizer: it decides the modes, in a process of its own ---")
    optimizer = ModeScheduler({"on": {"D": 1}}, Fraction(1, 2), base={"off": 0, "on": Fraction(-1, 2)})
    manager = TrackManager(modes=("off", "on"), initial_mode="off",
                           initial_elements=[("D", Region.closed(2, 6)), ("D", Region.closed(7, 8)),
                                             ("D", Region.closed(12, Fraction(25, 2)))])
    machine = Machine(manager, optimizer)
    while machine.running:
        machine.poll()
        time.sleep(0.01)
    print(f"run: {machine.runs[-1].outcome}, transitions: {manager.chip.transitions}")
    manager.print_track()
    for x in (1, 2, 3, 4):
        machine.update_chip(x)
    print(f"chip moved 4 times, nothing else changed: still {len(machine.runs)} run")
    machine.user_edit([Add("D", Region.closed(15, 19))])
    while machine.running:
        machine.poll()
        time.sleep(0.01)
    print(f"demand added: run {machine.runs[-1].outcome}, transitions: {manager.chip.transitions}")
    manager.print_track()
    machine.close()
