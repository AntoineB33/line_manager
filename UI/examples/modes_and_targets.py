"""The chip moves through alternating modes; the generator aims at presence ratios
and minimum lengths, the mode rules act at the chip. Try:
    run 4               the time runs for 4: the chip moves 4 * speed
    status              the time, the chip, its mode, the problems
    script              what the generator decided
    speed 1/2           slower
    move 20             until the chip is at 20
    add A 50% on [24, 26]
    claims              who placed what

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/modes_and_targets.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import MinimumLengthProblem, PresenceRatioProblem, start  # noqa: E402

# The element rules and the mode rules ("mode 1 → ..." acts only at the chip, in mode 1).
RULES = [
    "A → B or C",
    "B → D 1 unit on the left",
    "B → no D",
    "mode 1 → E",
    "mode 2 → no E",
]

# The modes: (position, mode) gives the mode from that position on. They are given
# externally: the generator only knows those the chip has reached.
MODES = [(2 * i, "1" if i % 2 == 0 else "2") for i in range(40)]

# The chip: where it starts, and its speed (> 0, positions per unit of time).
CHIP = 0
SPEED = 1

# The initial state of the line: the user's first edit.
LINE = [
    "A on [1, 3]",
]

# What the generator aims at, each problem with its weight; the horizon is how far
# after the chip it plans.
PROBLEMS = [
    (PresenceRatioProblem({"A": "1/4", "E": "1/2"}, horizon=8), 1),
    (MinimumLengthProblem({"A": 3}, horizon=8), 1),
]

# The allocated resources quantity, in (0, 1): lower is faster, higher optimizes more.
Q = 0.2

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q)
