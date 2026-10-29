"""The 15min break of OmniApp (docs/scheduler_requirements.md of OmniApp) with the now
line's 3 modes, its periods as elements with rules. One unit is one minute, and the
chip is the now line. The break lasts 15 minutes, comes with "no screen", starts as
early as possible, and after 15 minutes or more of "no screen" there is no 15min break
in the next 2 hours: an infinite pattern, a break every 135 minutes. The line starts
empty: the generator's constraint keeps Future on the positions after the chip, and
the rules place the breaks there, so they appear when its first run completes. Type in
the command box:
    add Away on [300, 330]   30 minutes without screen: no break until 2 hours after
                             its end, the next one at 450
    add Away on [300, 310]   10 minutes only: nothing changes
    mode 2                   the chip is in "no screen", "no computer unlocked" and
                             "no phone unlocked"
    mode 3                   in "no screen", and "not on a computer" (else "not on a
                             phone")
    mode 1                   no "no screen" at the chip: a break it reaches gives way
                             and starts right after it, so the chip drags it
    undo
Press Play to move the chip. MODES below switches to mode 2 at 200 and to mode 3 at
300 for a while: after 15 minutes in mode 2, the next break waits until 2 hours after
the chip leaves it.

The elements:
- Future: where breaks are placed, the positions after the chip, kept there by the
  generated script (CONSTRAINTS).
- Start15: where a break starts. At each position the "or" takes the leftmost operand
  that can be chosen, Start15 before no Start15: as early as the rules allow.
- Ready15: a break may start here; a break and the 2 hours after it remove it, so do
  the 2 hours after 15 minutes of Away.
- Break15, the break, and NoScreen, which comes with it.
- Away: "no screen" that is not a 15min break: the modes 2 and 3, and the user's (type
  "add Away on [a, b]"). Fresh is its first 15 minutes: Away without Fresh is 15
  minutes or more of "no screen".
- NoComputerUnlocked, NoPhoneUnlocked, NotOnComputer, NotOnPhone: the periods of the
  modes 2 and 3. "Not on a computer" can't be with "no computer unlocked", nor "not on
  a phone" with "no phone unlocked": the newer one wins.
The generator has both optimization problems, with no target, and one constraint:
Future on (chip, inf). It compiles the mode rules for the modes it predicts, so the
generated script drags the break ("Break15 on chip + ((0, 15] ...)").

Not expressed:
- A break whose start falls in a "no screen" period does not move back to the start
  of that period (or to the now line): a rule can only place what an "or" chooses
  after the position where it is decided.
- A break and the Away right after it count as two "no screen" periods: if the Away
  lasts less than 15 minutes, the 2 hours count from the end of the break, up to 15
  minutes too early. A "no screen" typed as NoScreen instead of Away has no 2 hours.
- A break removed from the past to satisfy its rules (the exception to the frozen
  past): nothing changes left of the chip but the user's edits.
- The 20s and 5min breaks and what only concerns them (the mode switches around a 20s
  break, the chains of 5min and 15min breaks), the tasks, and the default periods
  "sleep" and "before bed" ("Sleep → Sleep 1440 units on the right" repeats it every
  day, but each edit then takes seconds and the chip far longer).
A generator run takes a minute or two here (it compiles the mode rules over the next 3
hours): it goes on while the chip moves, and its script replaces the one in place when
it completes.

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/fifteen_minute_break_modes.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import MinimumLengthProblem, PresenceRatioProblem, start  # noqa: E402

RULES = [
    # The 15min break, as early as possible, never within 2 hours after another one.
    "Future → Start15 or no Start15",
    "Start15 → Ready15",
    "Start15 → Break15 on [0, 15] of p",
    "Start15 → no Ready15 on (0, 135] of p",
    "Break15 → NoScreen",

    # After 15 minutes or more of "no screen", no 15min break in the next 2 hours.
    "Away → NoScreen",
    "Away → Fresh on [0, 15) of l",
    "Away & not Fresh → no Ready15 on (0, 120] of p",

    # The periods of the modes.
    "NotOnComputer → no NoComputerUnlocked",
    "NotOnPhone → no NoPhoneUnlocked",

    # The now line's modes.
    "mode 1 → no NoScreen",
    "mode 2 → Away & NoComputerUnlocked & NoPhoneUnlocked",
    "mode 3 → Away & (NotOnComputer or NotOnPhone)",
]
MODES = [(0, "1"), (200, "2"), (240, "1"), (300, "3"), (310, "1")]
CHIP = 0
SPEED = 1  # minutes per second
LINE = []  # the line starts empty
CONSTRAINTS = [
    "Future on (0, inf)",  # from the chip: Future on (chip, inf)
]
PROBLEMS = [
    (PresenceRatioProblem({}, horizon=240), 1),
    (MinimumLengthProblem({}, horizon=240), 1),
]
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q, constraints=CONSTRAINTS)
