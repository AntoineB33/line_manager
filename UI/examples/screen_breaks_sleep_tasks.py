"""The three screen breaks of OmniApp (docs/scheduler_requirements.md of OmniApp),
"sleep" every day from 23:00 to 07:00 and three tasks, with the now line's 3 modes given
by the state of each device. One unit is one second, 0 is a midnight, and the chip is
the now line. As screen_breaks_sleep.py, with the tasks.

The tasks are elements, one at a time (rules), that the generator places (PROBLEMS):
    task         share   minimum   during "no screen"
    Homework     40%     30min     allowed
    VideoGames   30%     30min     forbidden ("NoScreen → no VideoGames")
    Reading      30%     15min     allowed
None during "sleep", a 20s break, or the first minute of a 5min break. The shares are
of the time a task can be at: the presence ratios of PresenceRatioProblem are of the
whole window, so they are the shares times the part of it that is not a break or
"sleep" (about 9/10 of a day awake). The minimum lengths weigh 1/10000 of the ratios:
a run makes its edits a few seconds at a time (its stages), and a piece of a few
seconds falls short of 15 or 30 minutes by more than its presence brings, so with the
same weight no task would ever start. The generator plans the tasks a few minutes
ahead of the chip at each run (with mode rules, a run's later stages do not edit
further), so they appear as the chip goes.

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
Press Play to move the chip, and use the selectors (or "mode 3" in the command box): a
break the chip reaches in mode 1 moves on with it.

Not expressed: as screen_breaks.py, and:
- "no screen" is each device locked or not used: the rule
  "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)"
  of screen_breaks.py is not here, because with a "no screen" every day forever the
  solver takes longer than an edit may (simulation.EDIT_LIMIT). The modes still put
  the devices at the chip.
- the resilience of a task to a period, when it is neither 0 nor 1 (Homework 0.5 to
  "no screen"): a task is allowed there or not, and its share is the same.
- the exponential decay of the compensation: the generator only looks at its window
  (4 hours from the chip).
- "before bed" is only the "no screen" hour before sleep.

Copy this file to make your own session, change the definitions, and run it:
    python UI/examples/screen_breaks_sleep_tasks.py
"""

import os
import sys
from fractions import Fraction as F_

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

    # The tasks: one at a time, and none where they are not allowed.
    "Homework → no VideoGames & no Reading",
    "VideoGames → no Reading",
    "NoScreen → no VideoGames",
    "Sleep → no Homework & no Reading",
    "Break20s → no Homework & no Reading",
    "Break5 → no Homework on [0, 60) of l & no Reading on [0, 60) of l",

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
AWAKE = F_(9, 10)  # the part of the window a task can be at
PROBLEMS = [
    (PresenceRatioProblem({"Homework": F_(40, 100) * AWAKE, "VideoGames": F_(30, 100) * AWAKE,
                           "Reading": F_(30, 100) * AWAKE}, horizon=240 * MINUTE), 1),
    (MinimumLengthProblem({"Homework": 30 * MINUTE, "VideoGames": 30 * MINUTE, "Reading": 15 * MINUTE},
                          horizon=240 * MINUTE), F_(1, 10000)),
]
Q = 0.5

start(rules=RULES, modes=MODES, chip=CHIP, speed=SPEED, line=LINE, problems=PROBLEMS, q=Q, constraints=CONSTRAINTS,
      selectors=SELECTORS, selector_modes=SELECTOR_MODES)
