"""The 15min break of OmniApp, as screen_break_15.py, with nothing written for it: only
rules and constraints, which the rules generator works with whatever they are. One unit
is one second, and the chip is the now line.

The rules are those of screen_break_15.py (a break comes with "no screen", "no screen"
is each device locked or not used, the modes), and "Break15 is always 100%" (a break is
whole). A break is no task: it has no presence ratio target. The constraints of the
rules generator place the breaks:
    AsEarlyAsPossible("Break15", length=15 minutes)
        breaks of 15 minutes, each as early as the rules and the other constraints allow
    MinimumGap("Break15", 2 hours, after="NoScreen", at_least=15 minutes)
        no break within 2 hours after 15 minutes or more of "no screen" (a break included:
        it comes with "no screen"; 30 minutes without screen, or 15 minutes in mode 2, too)
So a break of 15 minutes every 2h15 from the chip; in mode 1 ("no screen" ruled out at
the chip), a break the chip reaches moves on with it.

Not expressed (what the hand-written constraint of screen_break_15.py does and these do
not say):
- a break that would start inside a "no screen" period does not start at its start.

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/screen_break_15_rules.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import AsEarlyAsPossible, MinimumGap, start  # noqa: E402

RULES = [
    # A break comes with "no screen"; "no screen" is each device locked or not used.
    "Break15 → NoScreen",
    "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
    "NotOnComputer → no NoComputerUnlocked",
    "NotOnPhone → no NoPhoneUnlocked",

    # A break is whole.
    "Break15 is always 100%",

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
    # breaks of 15 minutes, each as early as allowed
    AsEarlyAsPossible("Break15", length=15 * MINUTE),
    # no break within 2 hours after 15 minutes or more of "no screen" (a break included)
    MinimumGap("Break15", 2 * 60 * MINUTE, after="NoScreen", at_least=15 * MINUTE),
]
PROBLEMS = []  # a break is no task: no target
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q, constraints=CONSTRAINTS,
      selectors=SELECTORS, selector_modes=SELECTOR_MODES)
