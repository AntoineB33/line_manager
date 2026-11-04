"""The 5min and 15min screen breaks of OmniApp (docs/scheduler_requirements.md of
OmniApp), with the now line's 3 modes given by the state of each device. One unit is
one second, and the chip is the now line. As fifteen_minute_break_modes.py, with the
5min break too.

The breaks are a constraint of the rules generator (constraints.ScreenBreaks):
- a 5min break, then none in the hour after 5 minutes or more of "no screen";
- a 15min break, then none in the 2 hours after 15 minutes or more of "no screen";
- a 5min break followed right at its end by a 15min one is one 15min break;
- a break that would start inside a "no screen" period starts at its start.
From an empty line, the chip at 0: the 15min break at 0 (the chain "5min, then 15min"),
then a 5min break 1 hour after it, a 15min break 2 hours after it, and so on every
2h15 (8100):
    15min (0, 900]    5min (4500, 4800]    15min (8100, 9000]    5min (12600, 12900] ...
Type in the command box:
    add NoScreen on [1800, 3600]   30 minutes without screen: no 5min break until 1 hour
                                   after its end, no 15min one until 2 hours after it
Press Play to move the chip, and use the selectors (or "mode 3" in the command box): a
break the chip reaches in mode 1 moves on with it.

Not expressed: as fifteen_minute_break_modes.py; the 20s break, "sleep", and the tasks
(so nothing tells the first minute of a 5min break from the 4 others: see
screen_breaks_sleep_tasks.py).

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/screen_breaks_5_15.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import MinimumLengthProblem, PresenceRatioProblem, ScreenBreaks, start  # noqa: E402

MINUTE = 60  # units
RULES = [
    # A break comes with "no screen"; "no screen" is each device locked or not used.
    "Break5 → NoScreen",
    "Break15 → NoScreen",
    "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
    "NotOnComputer → no NoComputerUnlocked",
    "NotOnPhone → no NoPhoneUnlocked",

    # The now line's modes.
    "mode 1 → no NoScreen",
    "mode 2 → NoScreen & NoComputerUnlocked & NoPhoneUnlocked",
    "mode 3 → NoScreen & (NotOnComputer or NotOnPhone)",
]
MODES = [(0, "1")]
U, F, L = "unlocked", "fake locked", "locked"
SELECTORS = {"computer": (U, F, L), "phone": (U, F, L)}
SELECTOR_MODES = {  # the first choice of a mode is what the selectors show when MODES gives it
    (U, U): "1", (U, F): "1", (U, L): "1", (F, U): "1", (L, U): "1",
    (L, L): "2",
    (F, L): "3", (L, F): "3", (F, F): "3",
}
CHIP = 0
SPEED = 1  # seconds per second: real time
LINE = []  # the line starts empty
CONSTRAINTS = [
    ScreenBreaks([("Break5", 5 * MINUTE, 60 * MINUTE), ("Break15", 15 * MINUTE, 120 * MINUTE)], "NoScreen",
                 chain=("Break5", "Break15")),
]
PROBLEMS = [
    (PresenceRatioProblem({}), 1),
    (MinimumLengthProblem({}), 1),
]
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q, constraints=CONSTRAINTS,
      selectors=SELECTORS, selector_modes=SELECTOR_MODES)
