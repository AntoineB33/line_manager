"""The 15min break of OmniApp, as screen_break_15.py, with one task: Task, with a
presence ratio target of 1 (all the time it can be) and a minimum length of 60 (one
minute). One unit is one second, and the chip is the now line.

The task stops during a break ("Break15 → no Task"); its best line is everywhere else,
from the end of a break to the start of the next. The generator plans it a few minutes
ahead of the chip at each run (with mode rules, a run's later stages do not edit
further), so it appears as the chip goes: the first run puts it right after the first
break, for about 6 minutes from 900.

Type in the command box, as in screen_break_15.py:
    add NoScreen on [18000, 19800]   30 minutes without screen: the next break moves, and
                                     the task with it
Press Play to move the chip, and use the selectors (or "mode 3" in the command box).

Not expressed: as screen_break_15.py, and the task's relation to "no screen" (it is
there during "no screen" that is not a break).

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/screen_break_15_task.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import FifteenMinuteBreaks, MinimumLengthProblem, PresenceRatioProblem, start  # noqa: E402

RULES = [
    # A break comes with "no screen"; "no screen" is each device locked or not used.
    "Break15 → NoScreen",
    "Break15 → no Task",  # the task stops during a break
    "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
    "NotOnComputer → no NoComputerUnlocked",
    "NotOnPhone → no NoPhoneUnlocked",

    # The now line's modes.
    "mode 1 → no NoScreen",
    "mode 2 → NoScreen & NoComputerUnlocked & NoPhoneUnlocked",
    "mode 3 → NoScreen & (NotOnComputer or NotOnPhone)",
]
MINUTE = 60  # units
MODES = [(0, "1"), (200 * MINUTE, "2"), (240 * MINUTE, "1"), (300 * MINUTE, "3"), (310 * MINUTE, "1")]
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
    FifteenMinuteBreaks("Break15", "NoScreen", length=15 * MINUTE, pause=120 * MINUTE),
]
PROBLEMS = [
    (PresenceRatioProblem({"Task": 1}), 1),
    (MinimumLengthProblem({"Task": 60}), 1),
]
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q, constraints=CONSTRAINTS,
      selectors=SELECTORS, selector_modes=SELECTOR_MODES)
