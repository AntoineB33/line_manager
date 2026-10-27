"""The 15min break of OmniApp (docs/scheduler_requirements.md), placed by rules. One
unit is one minute. The break lasts 15 minutes, always comes with "no screen", starts
as early as possible, and after "no screen" there is no 15min break in the next
2 hours: an infinite pattern, a break every 135 minutes. Type in the command box:
    add NoScreen on [200, 260]     an hour without screen: the next break waits until
                                   2 hours after its end (380)
    remove Break15 on [135, 150]   the break of 135 gives way: it starts right after
                                   the removal (150), and the pattern follows it
    no Awake on [500, 600]         asleep: no break there
    undo
Press Play to move the chip (the now line).

The rules:
- "Awake" is where a break may be placed; at each position the "or" takes the leftmost
  operand that can be chosen, Start15 before no Start15, so a break starts as early as
  the rules allow.
- A break needs "Ready" where it starts; a break and the 2 hours after "no screen"
  forbid it, so no break starts inside another one, nor in the 2 hours after.

Not expressed: OmniApp's rule asks for at least 15 minutes of "no screen" before the
2 hours apply. Its exact form is EXACT_COOLDOWN below (slower: the reach of "Long"
bounds the "no screen" lengths it handles, here up to 2 hours 15). The rules that move
a break with the now line and its modes, and the chains of 5min and 15min breaks, are
not here either.

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/fifteen_minute_break.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import start  # noqa: E402

RULES = [
    "Awake → Start15 or no Start15",
    "Start15 → Ready",
    "Start15 → Break15 on [0, 15] of p",
    "Start15 → no Ready on (0, 15] of p",
    "Break15 → NoScreen",
    "NoScreen → no Ready on (0, 120] of p",
]

# The exact "after ≥ 15 minutes of no screen": put these two in place of the last rule.
EXACT_COOLDOWN = [
    "NoScreen → Long on [15, 120] of l",
    "NoScreen & Long → no Ready on (0, 120] of p",
]

MODES = [(0, "1")]
CHIP = 0
SPEED = 10  # minutes per second

LINE = [
    "Awake on [0, inf)",
]

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE)
