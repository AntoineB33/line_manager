"""The three screen breaks of OmniApp (docs/scheduler_requirements.md of OmniApp) and
"sleep" every day from 23:00 to 07:00, with the now line's 3 modes given by the state
of each device. One unit is one second, 0 is a midnight, and the chip is the now line.
As screen_breaks.py, with "sleep".

"Sleep" is one single edit of the user, that repeats every day from the night before
the chip's start on (LINE):
    add Sleep on [-3600, 25200) every 86400
It comes with "no screen", and so does the hour before it (the rule
"Sleep → NoScreen on [-3600, 0] of l"): "no screen" every day from 22:00 to 07:00.

The breaks are a constraint of the rules generator (constraints.ScreenBreaks):
- a 20s break, then none in the 20 minutes after 20 seconds or more of "no screen"
  (so a 5min or 15min break puts off the 20s ones too);
- a 5min break, then none in the hour after 5 minutes or more of "no screen";
- a 15min break, then none in the 2 hours after 15 minutes or more of "no screen";
- a 5min break followed right at its end by a 15min one is one 15min break;
- two breaks that could start at the same position: the longer;
- a break that would start inside a "no screen" period starts at its start.
The chip starts at 07:00, when the night's "no screen" ends: it puts off every break,
the 20s ones until 07:20, the 5min ones until 08:00 and the 15min ones until 09:00.
Then every day:
    20s   07:20:00, 07:40:20
    5min  08:00
    20s   08:25:00, 08:45:20
    15min 09:00
    5min  10:15, 12:30, 14:45, 17:00, 19:15, 21:30   (20s breaks between them)
    15min 11:15, 13:30, 15:45, 18:00, 20:15
    15min 22:00: it would start at 22:30, inside the "no screen" before sleep, so it
          starts at its start; that "no screen" then gets no other break
Type in the command box:
    remove Sleep on [82800, 111600)   no sleep tonight: the breaks go on through the
                                      night (the other nights stay)
Press Play to move the chip, and use the selectors (or "mode 3" in the command box): a
break the chip reaches in mode 1 moves on with it.

Not expressed: as screen_breaks.py, and:
- "no screen" is each device locked or not used: the rule
  "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)"
  of screen_breaks.py is not here, because with a "no screen" every day forever the
  solver takes longer than an edit may (simulation.EDIT_LIMIT). The modes still put
  the devices at the chip.
- the tasks, that "sleep" allows none of (see screen_breaks_sleep_tasks.py).
- "before bed" is only the "no screen" hour before sleep.

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/screen_breaks_sleep.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from app import MinimumLengthProblem, PresenceRatioProblem, ScreenBreaks, start  # noqa: E402

MINUTE = 60  # units
RULES = [
    # A break and "sleep" come with "no screen".
    "Break20s → NoScreen",
    "Break5 → NoScreen",
    "Break15 → NoScreen",
    "Sleep → NoScreen",
    "Sleep → NoScreen on [-3600, 0] of l",
    "NotOnComputer → no NoComputerUnlocked",
    "NotOnPhone → no NoPhoneUnlocked",

    # The now line's modes.
    "mode 1 → no NoScreen",
    "mode 2 → NoScreen & NoComputerUnlocked & NoPhoneUnlocked",
    "mode 3 → NoScreen & (NotOnComputer or NotOnPhone)",
]
U, F, L = "unlocked", "fake locked", "locked"
SELECTORS = {"computer": (U, F, L), "phone": (U, F, L)}
SELECTOR_MODES = {  # the first choice of a mode is what the selectors show when MODES gives it
    (U, U): "1", (U, F): "1", (U, L): "1", (F, U): "1", (L, U): "1",
    (L, L): "2",
    (F, L): "3", (L, F): "3", (F, F): "3",
}
HOUR = 60 * MINUTE
CHIP = 7 * HOUR
MODES = [(CHIP, "1")]
SPEED = 1  # seconds per second: real time
LINE = ["Sleep on [-3600, 25200) every 86400"]  # 23:00 to 07:00, every day: one edit
CONSTRAINTS = [
    ScreenBreaks([("Break20s", 20, 20 * MINUTE), ("Break5", 5 * MINUTE, 60 * MINUTE), ("Break15", 15 * MINUTE, 120 * MINUTE)],
                 "NoScreen",
                 chain=("Break5", "Break15")),
]
PROBLEMS = [
    (PresenceRatioProblem({}), 1),
    (MinimumLengthProblem({}), 1),
]
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q, constraints=CONSTRAINTS,
      selectors=SELECTORS, selector_modes=SELECTOR_MODES)
