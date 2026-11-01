"""Rerun rules, context 5: a mode can reach the target through the element rules.

Mode 2 does not name A, but it puts X, X brings Y, and Y removes A: the script has a rule
for mode 2. Mode 3 puts B, which leads nowhere, and has none:
    if the chip is at or after ... and mode is 2, then run the generator again.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import PresenceRatioProblem, start  # noqa: E402

RULES = ["mode 2 → X", "X → Y", "Y → no A", "mode 3 → B"]
MODES = [(0, "1"), (30, "3"), (40, "1"), (60, "2"), (70, "1")]

start(rules=RULES, modes=MODES, problems=[(PresenceRatioProblem({"A": 1 / 2}, horizon=100), 1)])
