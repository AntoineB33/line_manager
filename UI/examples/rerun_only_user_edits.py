"""Rerun rules, context 1: nothing can improve, so the script asks for nothing by itself.

No mode rule and no target, and the breaks repeat forever: once they are on the line, a
new run would change nothing. "Generated script" shows one rerun rule only:
    if the user edits what the rules, the constraints or the problems read, then run the
    generator again.
Type "add NoScreen on [40, 50]" (after Play): that edit is the only thing that runs it.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import FifteenMinuteBreaks, PresenceRatioProblem, start  # noqa: E402

start(rules=["Break15 → NoScreen"],
      problems=[(PresenceRatioProblem({}, horizon=100), 1)],
      constraints=[FifteenMinuteBreaks("Break15", "NoScreen", length=5, pause=20)])
