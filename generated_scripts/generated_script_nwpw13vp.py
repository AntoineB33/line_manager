# Generated script at time 0, chip at 0 (mode 1).
# No generator run has completed yet.
# This script is the one published by the generator run going on, from chip 0, asked by the start-up script (run the generator once) at time 0; the run goes on, and its next script replaces this one.
# Definitive up to 10.

import sys

sys.path.insert(0, 'c:\\code\\line_manager\\scripts')  # (scripts/: this file runs on its own too)

from fractions import Fraction as F

from constraints import AsEarlyAsPossible, Breaks, KeepOn, MinimumGap, ScreenBreaks
from intervals import INF, Interval, IntervalSet, Pos
from line import Op
from ratio_plan import cycle, turns
from region import Region
from resolve import Record
from solver import Line
from script import (ChangeAction, ChipIn, ChipOn, ConstraintEditAction, Edited, EditAction, Event, Loop, Place, PresenceAbove,
                    RerunAction, ScoreBelow, Script, ShowLine, Start, TransitionCount, Uncovered, Viewed)
from script_python import claims, line_state

CONSTRAINTS = (AsEarlyAsPossible('Break15', length=F(900)), MinimumGap('Break15', F(7200), after='NoScreen', at_least=F(900)))
R1 = cycle(F(0), F(8100), F(0), F(900))

E1 = Event(trigger=ChipIn(lo=F(0), hi=INF), actions=(EditAction(ops=(Op.put('Break15', R1),)),))
E2 = Event(trigger=ChipOn(kind='Break15', mode='1'), actions=(EditAction(ops=(Op.take('Break15', Region.of(Interval(Pos(0, 1), INF, True, False))), Op.put('Break15', R1)), from_chip=True),))
E3 = Event(trigger=ChipIn(lo=F(10), hi=INF), actions=(RerunAction(),))
E4 = Event(trigger=Edited(only=True), actions=(RerunAction(),))
E5 = Event(trigger=Viewed(), actions=(ShowLine(),))

SCRIPT = Script(
    loops=[  # S_f (with s_f, `frame` below): each loop runs its events every `period` seconds
        Loop(F(1, 20), (
            E1,
            E2,
            E3,
        )),
    ],
    on_edit=(  # s_u: at each edit of the user
        E4,
    ),
    on_view=(  # s_v: when the view moves or is resized
        E5,
    ),
    frame=[  # s_f: at each frame (s_v makes it again)
        Place(item='world', shape='line'),
    ],
    covered=[(Pos(0), Pos(10), None)],  # (from, to, mode): where the mode rules are compiled ahead
    live=True,  # the mode rules apply at the chip elsewhere too
    constraints=CONSTRAINTS,  # what the script keeps after the chip
    origin=None,  # the chip's position the run compiled from
    true={str(E1)},  # the events true when last read
    fired={str(E1)},  # and those that fired
    since=0,  # the mode transitions since it was put in place
)
