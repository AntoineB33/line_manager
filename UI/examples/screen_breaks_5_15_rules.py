"""The 5min and 15min screen breaks of OmniApp, as screen_breaks_5_15.py, with nothing
written for them: only rules and constraints, which the rules generator works with
whatever they are. One unit is one second, and the chip is the now line.

The requirements, and the rule or constraint that says each:
- a break lasts as long as its name says and comes with "no screen":
      "Break5 → NoScreen", "Break15 → NoScreen", "Break5 is always 100%", ...
- the breaks are placed as early as possible:
      AsEarlyAsPossible("Break15", length=15 minutes), AsEarlyAsPossible("Break5", ...)
- after 5 minutes or more of "no screen", no 5min break in the next hour; after 15
  minutes or more, no 15min break in the next 2 hours; a break that would start inside
  a "no screen" period starts at its start:
      MinimumGap("Break5", 1 hour, after="NoScreen", at_least=5 minutes, pull="NoScreen")
      MinimumGap("Break15", 2 hours, after="NoScreen", at_least=15 minutes, pull="NoScreen")
- two breaks that could be at the same position: the longer
      "Break15 → no Break5"
From an empty line, the chip at 0:
    15min (0, 900]    5min (4500, 4800]    15min (8100, 9000]    5min (12600, 12900] ...
The constraints are placed in their order, each on the line the ones before it made
(constraints.constraint_ops): the 5min breaks where the 15min ones leave room.

Not expressed (gaps_to_solve_later.md): the chain. A 5min break the line drags in mode 1
until it touches a 15min one should become that 15min break, brought to the line; here
the script pushes the 5min break with a pattern of its own from the line, which runs
into the 15min breaks that stay where they are.

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/screen_breaks_5_15_rules.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import AsEarlyAsPossible, MinimumGap, start  # noqa: E402

MINUTE = 60  # units
RULES = [
    # A break comes with "no screen"; "no screen" is each device locked or not used.
    "Break5 → NoScreen",
    "Break15 → NoScreen",
    "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
    "NotOnComputer → no NoComputerUnlocked",
    "NotOnPhone → no NoPhoneUnlocked",

    # A break is whole; where both could be, the longer.
    "Break5 is always 100%",
    "Break15 is always 100%",
    "Break15 → no Break5",

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
    AsEarlyAsPossible("Break15", length=15 * MINUTE),
    MinimumGap("Break15", 2 * 60 * MINUTE, after="NoScreen", at_least=15 * MINUTE, pull="NoScreen"),
    AsEarlyAsPossible("Break5", length=5 * MINUTE),
    MinimumGap("Break5", 60 * MINUTE, after="NoScreen", at_least=5 * MINUTE, pull="NoScreen"),
]
PROBLEMS = []  # a break is no task: no target
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q, constraints=CONSTRAINTS,
      selectors=SELECTORS, selector_modes=SELECTOR_MODES)
