"""Rerun rules, context 3: a target, and no mode: the script watches the score.

A should be on half of the next 100 units. Nothing is planned for a position: what asks
for a run is the line moving away from the target:
    if the optimization score becomes less than ..., then run the generator again.
    if A 100% exceeds a presence ratio of ... when the chip is in ..., then run the
    generator again.
A lower Q waits for a lower score and a higher ratio.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import PresenceRatioProblem, start  # noqa: E402

Q = 0.5

start(problems=[(PresenceRatioProblem({"A": 1 / 2}, horizon=100), 1)], q=Q)
