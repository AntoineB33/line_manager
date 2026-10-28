"""An infinite pattern placed by a cycle: A for 5 units, then 5 units without A, again
and again. "A → A 10 units on the right" places each A again 10 units later, so A on
[0, 5] gives A on [10, 15], [20, 25], ... without end. Type in the command box:
    remove A on [20, 21]   A there only repeats the first A, so that one gives way:
                           A on (1, 5], (11, 15], (21, 25], ... without end
    add A at 7             one more A, repeated every 10 units: at 17, 27, ...
    no A on [42, 43]       the same through 4 repetitions: A on [0, 2) ∪ (3, 5],
                           [10, 12) ∪ (13, 15], ...
    line                   the line, its repeating part written once
    undo
Press Play to move the chip.

The generator has both optimization problems, with no target: it has nothing to aim
at, so it leaves the line to the rules and the edits.

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/infinite_pattern.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import MinimumLengthProblem, PresenceRatioProblem, start  # noqa: E402

RULES = [
    "A → A 10 units on the right",
]
MODES = [(0, "1")]
CHIP = 0
SPEED = 1
LINE = [
    "A on [0, 5]",
]
PROBLEMS = [
    (PresenceRatioProblem({}, horizon=20), 1),
    (MinimumLengthProblem({}, horizon=20), 1),
]
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q)
