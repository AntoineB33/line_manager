"""The user's edits are kept: an older edit gives way only where, and as long as, it
contradicts newer ones. No modes and no optimization problem: only the rules and the
edits. Type in the command box:
    add X at 0          A and B are at 0: B gives way to X (D would contradict it)
    remove A at 0       B comes back
    remove X at 0
    add A at 0          A, B, C and D again
    undo                as if the last edit had never been made

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/edit_history.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import start  # noqa: E402

# The element rules and mode rules, as in requirement.md ("->" works for "→").
RULES = [
    "A and B and C → D",
    "X → no D",
    "A and B → C",
]

# The initial state of the line: the user's first edit.
LINE = [
    "A at 0",
    "B at 0",
]

start(rules=RULES, line=LINE)
