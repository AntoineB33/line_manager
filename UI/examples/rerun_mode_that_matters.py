"""Rerun rules, context 4: only the modes that touch the target run the generator again.

The target is on A. Mode 2 removes A, so the script has a rule for it; modes 1 and 3 work
on other kinds, so they have none, however often they come:
    if the chip is at or after ... and mode is 2, then run the generator again.
Change TARGET to "B": the rule is then on mode 3, and mode 2 has none.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import PresenceRatioProblem, start  # noqa: E402

TARGET = "A"
RULES = ["mode 1 → C", "mode 2 → no A", "mode 3 → B"]
MODES = [(0, "1"), (30, "3"), (40, "1"), (60, "2"), (70, "1")]

start(rules=RULES, modes=MODES, problems=[(PresenceRatioProblem({TARGET: 1 / 2}, horizon=100), 1)])
