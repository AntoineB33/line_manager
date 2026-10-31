"""The 15min break of OmniApp (docs/scheduler_requirements.md of OmniApp), with the now
line's 3 modes given by the state of each device. One unit is one minute, and the chip
is the now line.

The line has OmniApp's periods only: Break15, NoScreen, NoComputerUnlocked,
NoPhoneUnlocked, NotOnComputer, NotOnPhone. Its element rules are the relations
between them (a break comes with "no screen"; "no screen" is each device locked or not
used), and its mode rules what each mode needs at the chip.

The window has a selector for each device: unlocked (U), fake locked (F: unlocked, but
not used, "not on a computer"/"not on a phone") or locked (L: "no computer unlocked"/
"no phone unlocked"). A mode is their two letters, the computer's first:
    UU UF UL FU LU   a device is unlocked: OmniApp's mode 1, no "no screen" at the chip
    LL               OmniApp's mode 2
    FF FL LF         OmniApp's mode 3

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
Press Play to move the chip, and use the selectors (or "mode FL" in the command box).
MODES below locks both devices at 200 and has the computer fake locked and the phone
locked at 300 for a while: after 15 minutes with both locked, the next break waits
until 2 hours after the chip leaves it.

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

    # The now line's modes: the computer's state, then the phone's.
    "mode UU → no NoScreen",
    "mode UF → no NoScreen & NotOnPhone",
    "mode UL → no NoScreen & NoPhoneUnlocked",
    "mode FU → no NoScreen & NotOnComputer",
    "mode LU → no NoScreen & NoComputerUnlocked",
    "mode LL → NoScreen & NoComputerUnlocked & NoPhoneUnlocked",
    "mode FF → NoScreen & NotOnComputer & NotOnPhone",
    "mode FL → NoScreen & NotOnComputer & NoPhoneUnlocked",
    "mode LF → NoScreen & NoComputerUnlocked & NotOnPhone",
]
STATES = {"U": "unlocked", "F": "fake locked", "L": "locked"}
SELECTORS = {"computer": STATES, "phone": STATES}
MODES = [(0, "UU"), (200, "LL"), (240, "UU"), (300, "FL"), (310, "UU")]
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

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q, constraints=CONSTRAINTS,
      selectors=SELECTORS)
