"""Minimal demo: python UI/demo.py"""

import os
import sys
from fractions import Fraction as F

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from generator import PresenceRatioProblem, RulesGenerator  # noqa: E402
from intervals import Interval, IntervalSet  # noqa: E402
from line import LineState  # noqa: E402
from modes import ModeTimeline  # noqa: E402
from rules import parse_rule  # noqa: E402
from simulation import Chip, Simulation, UserEdit  # noqa: E402


def strip(state, etype, lo, hi, step=F(1, 2)):
    cells = []
    x = F(lo)
    while x < hi:
        v = state.value_at(etype, x + step / 2)
        cells.append("#" if v == 100 else "+" if v else ".")
        x += step
    return "".join(cells)


def main():
    print("== Example 1: A → A at -1, chip at 0")
    gen = RulesGenerator([parse_rule("A → A at -1")], PresenceRatioProblem({"A": F(1, 2)}, 10), 0.5)
    script, _ = gen.run(LineState(), 0, ModeTimeline(0, "1").known(0))
    print(f"generated script: {script.text()!r}\n")

    rules = [parse_rule(t) for t in ["A → B", "A → no C", "mode 1 → D", "mode 2 → E at 1"]]
    problem = PresenceRatioProblem({"A": F(1, 4), "C": F(1, 4), "D": F(0)}, F(8))
    modes = ModeTimeline(0, "1", [(F(2 * i), "2" if i % 2 else "1") for i in range(1, 30)])
    edits = [UserEdit(F(12), "C", IntervalSet.of(Interval.closed(20, 22)), F(50))]
    sim = Simulation(LineState(), RulesGenerator(rules, problem, 0.2), Chip([(0, 0), (40, 40)]), modes, edits)
    log = sim.run(40)

    print("== rules:", "; ".join(map(str, rules)))
    print("== modes: 1 and 2 alternate every 2 units; user adds C 50% on [20, 22] at t=12\n")
    for t, c, why, script, stats in log.runs[:4] + log.runs[-1:]:
        print(f"-- run at chip {c} ({why}), work {stats.work}/{stats.budget}, chose: {stats.chosen}")
        for r in stats.probability_rules:
            print(f"   probability rule: {r}")
        print("   " + script.text().replace("\n", "\n   "))
    print(f"\n{len(log.runs)} runs, {len(log.refused)} refused edits\n")
    print("final line, 0..40 (# 100%, + partial, . absent), chip at 40:")
    print("mode " + "".join(modes.mode_at(F(i, 2)) for i in range(80)))
    for t in "ABCDE":
        print(f"{t:4} " + strip(sim.state, t, 0, 40))


if __name__ == "__main__":
    main()
