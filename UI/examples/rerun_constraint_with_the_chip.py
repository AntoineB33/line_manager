"""Rerun rules, context 2: a constraint that goes with the chip needs new runs.

"F on (1, 5)" is written from the chip: F must be on the 1 to 5 units ahead of it, wherever
it is, so the positions of the line that hold it change as the chip goes. The script asks
for a run at a position, unlike rerun_only_user_edits.py, whose breaks repeat forever:
    if the chip is at or after 75, then run the generator again.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import PresenceRatioProblem, start  # noqa: E402

start(problems=[(PresenceRatioProblem({}, horizon=100), 1)],
      constraints=["F on (1, 5)"])
