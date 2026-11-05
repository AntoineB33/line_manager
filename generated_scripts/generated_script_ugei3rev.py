# Generated script at time 0, chip at 0 (mode 1).
# No generator run has completed yet.
# This script is the one published by the generator run going on, from chip 0, asked by the start-up script (run the generator once) at time 0; the run goes on, and its next script replaces this one.
# Definitive up to 1310581/81.

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
BASE = line_state(claims(pos={'Break15': {(F(100), 'generated'): R1}}), Line({'NoComputerUnlocked': {F(100): Region(Pos(0, 1), Pos(900, 1), IntervalSet([Interval(Pos(0, 1), Pos(900), True, True)]), Pos(1), IntervalSet(), Pos(8100), IntervalSet([Interval(Pos(7200), Pos(8100, -1), True, True)]))}, 'Break15': {F(100): (R1 | Region.of(IntervalSet()))}, 'NoScreen': {F(100): Region(Pos(0, 1), Pos(1, 1), IntervalSet([Interval(Pos(0, 1), Pos(1), True, True)]), Pos(1), IntervalSet(), Pos(8100), IntervalSet([Interval(Pos(0), Pos(899, -1), True, True), Interval(Pos(8099), Pos(8100, -1), True, True)]))}, 'NoPhoneUnlocked': {F(100): Region(Pos(0, 1), Pos(900, 1), IntervalSet([Interval(Pos(0, 1), Pos(900), True, True)]), Pos(1), IntervalSet(), Pos(8100), IntervalSet([Interval(Pos(7200), Pos(8100, -1), True, True)]))}}))  # the line the run compiled from, its own edits made

E1 = Event(trigger=ChipIn(lo=F(0), hi=INF), actions=(EditAction(ops=(Op.put('Break15', R1),)),))
E2 = Event(trigger=ChipOn(kind='Break15', mode='1', within=F(0)), actions=(EditAction(ops=(Op.take('Break15', Region.of(Interval(Pos(0, 1), INF, True, False))), Op.put('Break15', R1)), from_chip=True),))
E3 = Event(trigger=ChipIn(lo=F(0), hi=INF), actions=(EditAction(ops=(Op.put('Break15', R1),)),))
E4 = Event(trigger=ChipOn(kind='Break15', mode='1', within=F(0)), actions=(EditAction(ops=(Op.take('Break15', Region.of(Interval(Pos(0, 1), INF, True, False))), Op.put('Break15', R1)), from_chip=True),))
E5 = Event(trigger=ChipIn(lo=F(675), hi=INF), actions=(RerunAction(),))
E6 = Event(trigger=Edited(only=True), actions=(RerunAction(),))
E7 = Event(trigger=Viewed(), actions=(ShowLine(),))

SCRIPT = Script(
    loops=[  # S_f (with s_f, `frame` below): each loop runs its events every `period` seconds
        Loop(F(1, 20), (
            E1,
            E2,
            E3,
            E4,
            E5,
        )),
    ],
    on_edit=(  # s_u: at each edit of the user
        E6,
    ),
    on_view=(  # s_v: when the view moves or is resized
        E7,
    ),
    frame=[  # s_f: at each frame (s_v makes it again)
        Place(item='world', shape='line'),
    ],
    covered=[(Pos(0), Pos(F(1310581, 81)), None), (Pos(0), Pos(2025), '1')],  # (from, to, mode): where the mode rules are compiled ahead
    live=True,  # the mode rules apply at the chip elsewhere too
    constraints=CONSTRAINTS,  # what the script keeps after the chip
    origin=F(0),  # the chip's position the run compiled from
    base=BASE,
    true={str(E1)},  # the events true when last read
    fired={str(E1)},  # and those that fired
    since=0,  # the mode transitions since it was put in place
)
