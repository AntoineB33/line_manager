"""Public entry point.  Semantics and proofs: SEMANTICS.md."""
from __future__ import annotations

from fractions import Fraction as F

from engine import (
    Add,
    ContradictionError,
    DependencyError,
    Drag,
    Extend,
    LineSystem,
    MissingElementError,
    Move,
    Remove,
    Retract,
    inverse,
)
from region import EMPTY, FULL, Region
from rules import RuleCycleError, RuleSet, forbids, implies, implies_any

__all__ = [
    "EMPTY",
    "FULL",
    "Add",
    "ContradictionError",
    "DependencyError",
    "Drag",
    "Extend",
    "LineSystem",
    "MissingElementError",
    "Move",
    "Region",
    "Remove",
    "Retract",
    "RuleCycleError",
    "RuleSet",
    "forbids",
    "implies",
    "implies_any",
    "inverse",
]


if __name__ == "__main__":
    import io
    import sys
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # Region reprs use "∪"

    print("--- Example 1 of requirements.md, with x1..x8 = 1..8 ---")
    x1, x2, x3, x4, x5, x6, x7, x8 = range(1, 9)
    line = LineSystem([forbids("D", "C")])
    line.apply([Add("A", Region.closed(x1, x2)),
                Add("B", Region.closed(x1, x4)),
                Add("D", Region.closed(x6, x7))])
    print("initial         ", line.state())

    edit_a = line.apply([Move.to("A", Region.interval(x1, x2, True, False), x3),
                         Move.to("B", Region.point(x4), x5),
                         Add("C", Region.closed(x6, x7))])
    print("after edit A    ", line.state())

    edit_b = line.apply([Add("D", Region.closed(x6, x7))])
    print("after edit B    ", line.state())

    line.reverse(edit_b)
    print("edit B reversed ", line.state())

    line.reverse(edit_a)
    print("edit A reversed ", line.state())

    try:
        line.apply([Add("C", Region.point(x8)), Add("D", Region.point(x8))])
        print("ERROR: edit C was accepted")
    except ContradictionError as e:
        print("edit C rejected:", e)

    for name, rules, start, block in (
            ("Example 2", [forbids("A", "B")], ("A", Region.closed(0, 2)), ("B", Region.closed(0, 1))),
            ("Example 3", [forbids("D", "C")], ("D", Region.point(2)), ("C", Region.closed(0, 1)))):
        print(f"\n--- {name}, with x1..x5 = 0..4: drag {block[0]} from [0, 1] to [3, 4] ---")
        line = LineSystem(rules)
        line.apply([Add(*start)])
        line.apply([Add(*block)])
        drag = line.drag([block])
        for i in range(13):
            drag.step(F(i, 4))
            print(f"  {block[0]} moved by {F(i, 4)}: {line.state()}")
        drag.drop()
