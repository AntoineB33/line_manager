"""The 20s break of OmniApp (the "look-away", docs/scheduler_requirements.md of OmniApp),
with nothing written for it: only rules and constraints, which the rules generator works
with whatever they are. One unit is one second, and the chip is the now line.

The requirements, and the rule or constraint that says each:
- a 20s break lasts 20 seconds and comes with "no screen":
      "Break20s → NoScreen", "Break20s is always 100%"
- never more than 20 minutes without a 20s break (the obligation):
      MaximumGap("Break20s", 20 minutes, length=20)
- the breaks are placed as early as possible (the preference: without it, the
  obligation alone places each as late as it allows, here the same place):
      AsEarlyAsPossible("Break20s", length=20)
- after the end of a 20s break, no 20s break in the next 20 minutes (only a 20s break
  does: "no screen", however long, bars none):
      MinimumGap("Break20s", 20 minutes, pull="NoScreen")
- a break that would start inside a "no screen" period starts at its start: its `pull`
- the now line must be in mode 1 or 3 before entering a 20s break, so one it reaches in
  mode 2 is dragged, (now line, now line + 20s]:
      "mode 2 → NoScreen & NoComputerUnlocked & NoPhoneUnlocked & no Break20s"
- the now line cannot be in mode 1 in a 20s break: a mode-1 line enters it and is as in
  mode 3 until it leaves it (it crosses it, the break stays where it is):
      "mode 1 & not Break20s → no NoScreen"
      "mode 1 & Break20s → NotOnComputer or NotOnPhone"
So a break every 20min20s (1220) from the chip, which the line crosses; locked, the
next one waits at the line, and is crossed where the line comes back to a screen.

Not expressed: the 5min and 15min breaks (screen_break_15_rules.py has the 15min one);
which device the user looks away from: the line has what the "or" chooses.

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/screen_break_20s_rules.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import AsEarlyAsPossible, MaximumGap, MinimumGap, start  # noqa: E402

RULES = [
    # A break comes with "no screen"; "no screen" is each device locked or not used.
    "Break20s → NoScreen",
    "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)",
    "NotOnComputer → no NoComputerUnlocked",
    "NotOnPhone → no NoPhoneUnlocked",

    # A break is whole.
    "Break20s is always 100%",

    # The now line's modes: in mode 1 a 20s break is crossed as in mode 3; mode 2 drags it.
    "mode 1 & not Break20s → no NoScreen",
    "mode 1 & Break20s → NotOnComputer or NotOnPhone",
    "mode 2 → NoScreen & NoComputerUnlocked & NoPhoneUnlocked & no Break20s",
    "mode 3 → NoScreen & (NotOnComputer or NotOnPhone)",
]
MINUTE = 60  # units
MODES = [(0, "1"), (3000, "2"), (6000, "1")]
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
    # breaks of 20 seconds, each as early as allowed
    AsEarlyAsPossible("Break20s", length=20),
    # no 20s break within 20 minutes after one; one that would start inside a "no screen"
    # period starts at its start
    MinimumGap("Break20s", 20 * MINUTE, pull="NoScreen"),
    # never more than 20 minutes without one
    MaximumGap("Break20s", 20 * MINUTE, length=20),
]
PROBLEMS = []  # a break is no task: no target
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q, constraints=CONSTRAINTS,
      selectors=SELECTORS, selector_modes=SELECTOR_MODES)
