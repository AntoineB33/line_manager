"""OmniApp's three screen breaks (docs/scheduler_requirements.md of OmniApp), as
elements with rules, spanning infinitely. One unit is one minute, and the chip is
OmniApp's now line.

Each break comes with "no screen", starts as early as possible (the leftmost operand
of an "or"), and never inside another break. No 20s break in the 20 minutes after a
20s break, no 5min break in the hour after a 5min or 15min break, no 15min break in
the 2 hours after a 15min break. A 15min break wins over a 5min break that would start
with it.

The now line's modes are the chip's: in mode 1 "no screen" is not at the chip, in
mode 2 "no screen", "no computer unlocked" and "no phone unlocked" are. A mode rule
acts only at the chip: a break the chip reaches in mode 1 gives way there and starts
after it, so the chip drags it. Switch to mode 2 on a 5min or 15min break, and back to
mode 1 after it:
    mode 2
    mode 1
Press Play to move the chip.

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/OmniApp.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import start  # noqa: E402

RULES = [
    # The 15min break: 2 hours after a 15min break.
    "Timeline → Start15 or no Start15",
    "Start15 → Ready15",
    "Start15 → Break15 on [0, 15] of p",
    "Start15 → no Ready15 on (0, 15] of p",
    "Break15 → no Ready15 on (0, 120] of p",

    # The 5min break: 1 hour after a 5min or 15min break; a 15min break starting there
    # wins.
    "Timeline → Start5 or no Start5",
    "Start5 → Ready5",
    "Start5 → Break5 on [0, 5] of p",
    "Start5 → no Ready5 on (0, 5] of p",
    "Break5 → no Ready5 on (0, 60] of p",
    "Break15 → no Ready5 on [0, 60] of p",
    "Start5 → no Ready15 on (0, 5] of p",

    # The 20s break: 20 minutes after a 20s break, never inside another break.
    "Timeline → Start20s or no Start20s",
    "Start20s → Ready20s",
    "Start20s → Break20s on [0, 1/3] of p",
    "Break20s → no Ready20s on (0, 20] of p",
    "Break15 → no Ready20s",
    "Break5 → no Ready20s",

    # Every screen break comes with "no screen".
    "Break15 → NoScreen",
    "Break5 → NoScreen",
    "Break20s → NoScreen",

    # The now line's modes.
    "mode 1 → no NoScreen",
    "mode 2 → NoScreen & NoComputerUnlocked & NoPhoneUnlocked",
]

MODES = [(0, "1")]
CHIP = 0
SPEED = 2  # minutes per second

LINE = [
    "Timeline on [0, inf)",
]

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE)
