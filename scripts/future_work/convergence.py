"""Does "the leftmost operand that can be chosen" settle as the grid gets finer, with
R1, R2, R3 lifted? (README.md, "Evidence".)

    python scripts/future_work/convergence.py cases            the hand-picked cases
    python scripts/future_work/convergence.py random SEED SECS random rule sets

A case is flagged if a valid assignment exists on one grid and not on another, or if
the grids of step 1/8 and 1/16 disagree at a point more than 1/4 from every boundary."""

import random
import sys
import time
from fractions import Fraction as F

from grid import Grid, runs
from rules import RuleError

CASES = {
    "R1": (["A → B or C", "A & not B → E on [0, 1] of p", "E → no C"], [("A", (0, 2))]),
    "R1, X at 1": (["A → B or C", "A & not B → E on [0, 1] of p", "E → no C", "X → no B"], [("A", (0, 2)), ("X", (1, 1))]),
    "R1, runs of C": (["A → B or C", "A & not B → E on (0, 1] of l", "E → no C", "X → no B"], [("A", (0, 3)), ("X", (1, 2))]),
    "R1, not C ahead": (["A → B or C", "A & not C → D on (0, 1] of p", "D → no B", "X → no B"], [("A", (0, 3)), ("X", (1, 1))]),
    "R2": (["A → B or C on [0, 1] of l", "X → no B"], [("A", (0, 2)), ("X", (1, 1))]),
    "R2, C on l blocks B": (["A → B or C on [0, 1] of l", "C → no B on [0, 1/2] of p", "X → no B"], [("A", (0, 3)), ("X", (2, 2))]),
    "R3": (["A → B or C", "B → E on [-1, 0] of l", "E → no F on (0, 1] of l"], [("A", (0, 2)), ("F", (1, 3))]),
    "R3, B forbids C behind": (["A → B or C", "B → D on [-1, 0] of p", "D → no C", "X → no B"], [("A", (0, 3)), ("X", (1, 1))]),
    "R3, C needs room behind": (["A → B or C", "C → D on [-1, 0) of p", "D → no B", "X → no B"], [("A", (0, 3)), ("X", (2, 2))]),
}

POOLS = {
    "R1": ["A & not B → E on [0, 1] of p", "E → no C", "A & not B → E on (0, 1] of l", "not B & A → no C on (0, 1/2] of p",
           "A & not C → D on (0, 1] of p", "D → no B", "A & not C → D on [0, 1/2] of l", "X → no B", "X → no C",
           "D → no C 1 unit on the right"],
    "R2": ["X → no B", "X → no C", "B → no C on [0, 1/2] of p", "C → no B on (0, 1] of p", "B → E", "C → no E on [0, 1] of l",
           "B → no B2 on (0, 1] of l"],
    "R3": ["B → D on [-1, 0] of p", "D → no C", "C → D on [-1, 0) of p", "D → no B", "X → no B", "X → no C",
           "B → E on [-1/2, 0] of l", "E → no C on [0, 1/2] of p"],
}
BASE = {"R1": [["A → B or C"]], "R2": [["A → B or C on [0, 1] of l"], ["A → B on [0, 1/2] of l or C"]], "R3": [["A → B or C"]]}


def show_cases():
    for name, (rules, claims) in CASES.items():
        print("==", name, "|", " ; ".join(rules), "|", claims)
        for h in (F(1, 2), F(1, 4), F(1, 8), F(1, 16)):
            g = Grid(rules, claims, h, -2, 5, limit=60000)
            try:
                T = g.solve()
            except TimeoutError:
                print(f"   h={str(h):5} search too long")
                continue
            if T is None:
                print(f"   h={str(h):5} no valid assignment")
                continue
            got = {k: [(str(a), str(b)) for a, b in runs(T, k, g)] for k in g.order if k not in ("A", "X", "F") and runs(T, k, g)}
            print(f"   h={str(h):5}", got)


def search(seed: int, seconds: int):
    rng = random.Random(seed)
    deadline = time.time() + seconds
    stats = {}
    while time.time() < deadline:
        kind = rng.choice(list(POOLS))
        rules = rng.choice(BASE[kind]) + rng.sample(POOLS[kind], rng.randint(1, 4))
        a = F(rng.randint(0, 2))
        claims = [("A", (a, a + F(rng.randint(2, 6), 2)))]
        claims += [("X", (x, x)) for x in {F(rng.randint(0, 8), 2) for _ in range(rng.randint(0, 2))}]
        res = {}
        try:
            for h in (F(1, 4), F(1, 8), F(1, 16)):
                g = Grid(rules, claims, h, -2, 6, limit=40000)
                res[h] = (g.solve(), g)
        except (RuleError, TimeoutError):
            continue
        st = stats.setdefault(kind, [0, 0])
        st[0] += 1
        exists = [T is not None for T, _ in res.values()]
        if len(set(exists)) > 1:
            st[1] += 1
            print("EXISTENCE FLIPS", kind, rules, claims, exists)
            continue
        if not exists[0]:
            continue
        (T8, g8), (T16, g16) = res[F(1, 8)], res[F(1, 16)]
        kinds = [k for k in g16.order if k not in ("A", "X")]
        bounds = [e for k in kinds for T, g in ((T8, g8), (T16, g16)) for r in runs(T, k, g) for e in r]
        for k in kinds:
            for i in range(33):
                x = F(-2) + i * F(1, 4)
                if any(abs(x - e) <= F(1, 4) for e in bounds):
                    continue
                if T8[k][int((x - g8.lo) / g8.h)] != T16[k][int((x - g16.lo) / g16.h)]:
                    st[1] += 1
                    print("DISAGREE", kind, rules, claims, k, x)
                    break
    print({k: f"{v[0]} cases, {v[1]} flagged" for k, v in stats.items()})


if __name__ == "__main__":
    if sys.argv[1:2] == ["random"]:
        search(int(sys.argv[2]), int(sys.argv[3]))
    else:
        show_cases()
