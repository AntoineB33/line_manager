"""One element, A, with a presence ratio target of 100%, and nothing else: no rules, no
constraint, no mode rule, and an empty line.

Its perfect generated script (scripts/ratio_plan.py) is found at once, on the whole
line:
    A on (0, inf)   (the chip at 0)
Only an edit of the user runs the generator again. Type in the command box:
    no A on [20, 30]   a hole: the line has more than the perfect script, so the
                       generator searches instead, an approximation on a window of its
                       own after the chip (generator.search_window)

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/single_element_full_presence.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import PresenceRatioProblem, start  # noqa: E402

RULES = []
MODES = [(0, "1")]
CHIP = 0
SPEED = 1  # units per second
LINE = []  # the line starts empty
PROBLEMS = [
    (PresenceRatioProblem({"A": 1}), 1),
]
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q)
