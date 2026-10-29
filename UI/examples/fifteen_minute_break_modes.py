"""The 15min break of OmniApp (docs/scheduler_requirements.md of OmniApp) with the now
line's 3 modes. One unit is one minute, and the chip is the now line.

The line has OmniApp's periods only: Break15, NoScreen, NoComputerUnlocked,
NoPhoneUnlocked, NotOnComputer, NotOnPhone. Its element rules are the relations
between them (a break comes with "no screen"; "no screen" is each device locked or not
used), and its mode rules what each mode needs at the chip.

The breaks are a constraint of the rules generator (constraints.FifteenMinuteBreaks):
a break lasts 15 minutes, starts as early as possible, and after 15 minutes or more of
"no screen" (a break included) no break starts in the next 2 hours, so one every 135
minutes; a break that would start inside a "no screen" period starts at its start (or
right after the chip). The line starts empty: the breaks appear when the generator's
first run completes, put there by its script. Type in the command box:
    add NoScreen on [300, 330]   30 minutes without screen: no break until 2 hours
                                 after its end, the next one at 450
    add NoScreen on [300, 310]   10 minutes only: nothing changes
    add NoScreen on [400, 410]   a break would start inside it: it starts at 400
    mode 2                       the chip is in "no screen", "no computer unlocked"
                                 and "no phone unlocked"
    mode 3                       in "no screen", and "not on a computer" (else "not
                                 on a phone")
    mode 1                       no "no screen" at the chip
    undo
Press Play to move the chip. MODES below switches to mode 2 at 200 and to mode 3 at
300 for a while: after 15 minutes in mode 2, the next break waits until 2 hours after
the chip leaves it.

Not expressed:
- A break removed from the past to satisfy its rules (the exception to the frozen
  past): nothing changes left of the chip but the user's edits.
- The 20s and 5min breaks and what only concerns them (the mode switches around a 20s
  break, the chains of 5min and 15min breaks), the tasks, and the default periods
  "sleep" and "before bed".

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/fifteen_minute_break_modes.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import FifteenMinuteBreaks, MinimumLengthProblem, PresenceRatioProblem, start  # noqa: E402

RULES = [
    # A break comes with "no screen"; "no screen" is each device locked or not used.
    "Break15 → NoScreen",
    "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
    "NotOnComputer → no NoComputerUnlocked",
    "NotOnPhone → no NoPhoneUnlocked",

    # The now line's modes.
    "mode 1 → no NoScreen",
    "mode 2 → NoScreen & NoComputerUnlocked & NoPhoneUnlocked",
    "mode 3 → NoScreen & (NotOnComputer or NotOnPhone)",
]
MODES = [(0, "1"), (200, "2"), (240, "1"), (300, "3"), (310, "1")]
CHIP = 0
SPEED = 1  # minutes per second
LINE = []  # the line starts empty
CONSTRAINTS = [
    FifteenMinuteBreaks("Break15", "NoScreen", length=15, pause=120),
]
PROBLEMS = [
    (PresenceRatioProblem({}, horizon=240), 1),
    (MinimumLengthProblem({}, horizon=240), 1),
]
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q, constraints=CONSTRAINTS)
