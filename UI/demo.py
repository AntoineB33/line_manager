"""Minimal demo: python UI/demo.py"""

import os
import sys
from fractions import Fraction as F

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from generator import PresenceRatioProblem, RulesGenerator  # noqa: E402
from intervals import INF, Interval, IntervalSet  # noqa: E402
from line import USER, LineState, Op  # noqa: E402
from modes import ModeTimeline  # noqa: E402
from resolve import Refused, apply_edit  # noqa: E402
from rules import RuleError, parse_rules  # noqa: E402
from simulation import Chip, Simulation, UserEdit  # noqa: E402


def strip(line, kind, lo, hi, step=F(1, 2)):
    out, x = [], F(lo)
    while x < hi:
        v = line.value_at(kind, x + step / 2)
        out.append("#" if v == 100 else "+" if v else ".")
        x += step
    return "".join(out)


def main():
    print("== the requirement's rules; the user adds A on [1, 3] and A 50% on (5, 6]")
    texts = ["A → B 100%", "A & B → C 45% 3 units on the left of p", "A → D 1% 1 unit on the right of l",
             "A >= 50% & not C → E on [0, 1/2] of l"]
    rs = parse_rules(texts)
    print("   " + "\n   ".join(texts))
    s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(1, 3)), Op.put("A", Interval.open_closed(5, 6), 50)], USER)
    print(str(s.line) + "\n")

    print("== a cycle is refused:")
    try:
        parse_rules(["A → B 1% 1 unit on the right of l", "B → A 10%"])
    except RuleError as e:
        print(f"   {e}\n")

    print('== "A → B 100% or C 100%": the leftmost operand that can be chosen; with "X → no B", X on [0, 2], A on [1, 3]:')
    rs = parse_rules(["A → B 100% or C 100%", "X → no B"])
    print(str(apply_edit(rs, LineState.empty(), [Op.put("X", Interval.closed(0, 2)), Op.put("A", Interval.closed(1, 3))], USER).line) + "\n")

    texts = ["A → B or C", "B → E", "B → no E on (0, 1] of l"]
    print("== positions x + kε: " + ", ".join(texts) + "; A on [0, 2]")
    print("   (no B on (0, 1] after the B at 0; after 1, the first position is 1+ε)")
    rs = parse_rules(texts)
    print(str(apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 2))], USER).line) + "\n")
    print("== the same with A on [0, +inf): B at n + nε, repeating every 1+ε")
    print(str(apply_edit(rs, LineState.empty(), [Op.put("A", Interval(F(0), INF, True, False))], USER).line) + "\n")

    print('== looking ahead: the same with "X → no C" and X at 1/2: B must be at 1/2, so no B before it')
    rs = parse_rules(texts + ["X → no C"])
    print(str(apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(0, 2)), Op.put("X", Interval.point(F(1, 2)))], USER).line) + "\n")

    print('== an interval relative to p: "A → B on [0, 1] of p", A at 0 and on [3, 4]')
    rs = parse_rules(["A → B on [0, 1] of p"])
    print(str(apply_edit(rs, LineState.empty(), [Op.put("A", IntervalSet.of(Interval.point(0), Interval.closed(3, 4)))], USER).line) + "\n")

    print('== user edits win: "A → B 100% or C 100%", A and B at [1, 2], then the user removes B at 1.5:')
    rs = parse_rules(["A → B 100% or C 100%"])
    s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.closed(1, 2)), Op.put("B", Interval.closed(1, 2))], USER)
    print(str(apply_edit(rs, s, [Op.take("B", Interval.point(F(3, 2)))], USER).line) + "\n")

    print('== "A → B 0%" is "no B"; "A → B or C", "B → D", "B → D 0% 1 unit on the right" (no B right after a B); A on [0, +inf):')
    rs = parse_rules(["A → B or C", "B → D", "B → D 0% 1 unit on the right"])
    print(str(apply_edit(rs, LineState.empty(), [Op.put("A", Interval(F(0), INF, True, False))], USER).line) + "\n")
    print("== the same with A on (-inf, 0]: no first position, so nothing decides where B starts:")
    try:
        apply_edit(rs, LineState.empty(), [Op.put("A", Interval(-INF, F(0), False, True))], USER)
    except Refused as e:
        print(f"   refused: {e}\n")

    print('== "A and B → C 100%": A and B at 3, then "no C" at 3')
    rs = parse_rules(["A and B → C 100%"])
    s = apply_edit(rs, LineState.empty(), [Op.put("A", Interval.point(3)), Op.put("B", Interval.point(3))], USER)
    print(s.line)
    print("then:\n" + str(apply_edit(rs, s, [Op.put_no("C", Interval.point(3))], USER).line) + "\n")

    rules = parse_rules(["A → B or C", "B → D 1 unit on the left", "B → no D", "mode 1 → E", "mode 2 → no E"])
    problem = PresenceRatioProblem({"A": F(1, 4), "E": F(1, 2)}, F(8))
    modes = ModeTimeline(0, "1", [(F(2 * i), "2" if i % 2 else "1") for i in range(1, 30)])
    edits = [UserEdit(F(12), [Op.put("A", Interval.closed(20, 22), 50)])]
    sim = Simulation(LineState.empty(), RulesGenerator(rules, problem, 0.2), Chip([(0, 0), (30, 30)]), modes, edits)
    log = sim.run(30)
    print("== simulation:", "; ".join(r.text for r in rules.rules))
    print("   modes 1 and 2 alternate every 2 units; the user adds A 50% on [20, 22] at t=12\n")
    for t, c, why, script, stats in log.runs[:3] + log.runs[-1:]:
        print(f"-- run at chip {c} ({why}), work {stats.work}/{stats.budget}, chose: {stats.chosen}")
        for r in stats.probability_rules:
            print(f"   probability rule: {r}")
        print("   " + script.text().replace("\n", "\n   "))
    print(f"\n{len(log.runs)} runs, {len(log.refused)} refused edits")
    print("final line, 0..30 (# 100%, + partial, . absent), chip at 30:")
    print("mode " + "".join(modes.mode_at(F(i, 2)) for i in range(60)))
    for k in "ABCDE":
        print(f"{k:4} " + strip(sim.state.line, k, 0, 30))


if __name__ == "__main__":
    main()
