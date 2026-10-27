"""Minimum length: each interval of A should be at least 10 long. Intervals close to
one another count for each other, by how close they are; an interval further than 10
from every other one needs the full 10 on its own. The generator has already grown
the short intervals after the chip. Type in the command box:
    remove A on [6, 7]  a hole: the two pieces fall short, and grow to make up for it
    claims              what the generated script placed
Press Play to move the chip.

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/minimum_length.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import MinimumLengthProblem, start  # noqa: E402

RULES = []
MODES = [(0, "1")]
CHIP = 0
SPEED = 1
LINE = [
    "A on [2, 4]",
    "A on [5, 7]",
    "A on [30, 31]",
]
PROBLEMS = [
    (MinimumLengthProblem({"A": 10}, horizon=40), 1),
]
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q)
