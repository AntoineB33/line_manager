"""Public entry point.  Semantics and proofs: SEMANTICS.md."""
from __future__ import annotations

from engine import Add, ContradictionError, LineSystem, Move, Remove, inverse
from region import EMPTY, FULL, Region
from rules import RuleCycleError, RuleSet, forbids, implies, implies_any

__all__ = [
    "EMPTY",
    "FULL",
    "Add",
    "ContradictionError",
    "LineSystem",
    "Move",
    "Region",
    "Remove",
    "RuleCycleError",
    "RuleSet",
    "forbids",
    "implies",
    "implies_any",
    "inverse",
]


if __name__ == "__main__":
    # The example of requirements.md, with x1..x8 = 1..8.
    x1, x2, x3, x4, x5, x6, x7, x8 = range(1, 9)
    line = LineSystem([forbids("D", "C")])
    line.apply([Add("D", Region.closed(x6, x7)),
                Add("A", Region.closed(x1, x2)),
                Add("B", Region.point(x4))])
    print("initial         ", line.state())

    edit_a = line.apply([Move.to("A", Region.closed(x1, x2), x3),
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
