"""The 15min break of OmniApp, as screen_break_15.py, with two tasks: A and B, each
with a presence ratio target of 0.5, that exclude each other ("A → no B"), and A with
a minimum length of 60. One unit is one second, and the chip is the now line.

The best line alternates 60 of A and 60 of B forever, from the chip (the first one at
random): the generator finds it at once (ratio_plan.Plan.group_ops), and the breaks come
and go as in screen_break_15.py. Without the minimum length, every alternation is beaten
by a faster one: the definitions are refused.

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/screen_break_15_task.py
"""

import os
import sys
from fractions import Fraction

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

    "A → no B",
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
    (PresenceRatioProblem({"A": Fraction(1, 2), "B": Fraction(1, 2)}), 1),  # sum: 1
    (MinimumLengthProblem({"A": 60}), 1),
]
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q, constraints=CONSTRAINTS,
      selectors=SELECTORS, selector_modes=SELECTOR_MODES)
