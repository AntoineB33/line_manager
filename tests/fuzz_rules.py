"""Any rule set: random break-like rule sets and scenarios, each run twice, the fast way
(the stretches the generator compiled, the pushes the mode rules keep) and the slow
reference (the mode rules decided at the chip at each step, each push an edit at each
instant, as the script's text says), the two compared at each step on the line ahead of
the chip. The fast run is also checked to keep every MinimumGap, and to raise nothing.

    python tests/fuzz_rules.py [first seed] [how many] [steps]

Prints one line per scenario and, for a failing one, how to rerun it. test_fuzz.py runs a
few fixed seeds.
"""

from __future__ import annotations

import os
import random
import sys
import time
import traceback
from fractions import Fraction as F
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "UI"))
from helpers import Interval  # noqa: E402,F401 (the paths of scripts/)

import gui  # noqa: E402
import simulation  # noqa: E402
from app import AsEarlyAsPossible, MinimumGap, Session  # noqa: E402
from constraints import MinimumGap as _Gap  # noqa: E402
from intervals import Interval as Iv  # noqa: E402
from line import Op  # noqa: E402
from region import Region  # noqa: E402
from script import Script  # noqa: E402


def scenario(seed: int, steps: int = 24) -> dict:
    """A random rule set of 1 or 2 break kinds that come with N, and what happens to it."""
    r = random.Random(seed)
    n = r.choice([1, 1, 2])
    kinds = ["A", "B"][:n]
    lengths = sorted(r.sample([10, 20, 30, 60], n))  # (the longer one is B)
    rules = ["N → X or Y"]  # (an "or" N brings: what a device does)
    constraints = []
    for k, L in zip(kinds, lengths):
        rules += [f"{k} → N", f"{k} is always 100%"]
    if n == 2:
        rules.append("B → no A")  # where both could be, the longer
    for k, L in sorted(zip(kinds, lengths), key=lambda kl: -kl[1]):  # (the longer first)
        gap = r.choice([100, 200, 400])
        after, at_least = r.choice([(k, 0), ("N", L)])
        pull = r.choice([None, "N"])
        constraints += [AsEarlyAsPossible(k, length=L),
                        MinimumGap(k, gap, after=after if after != k else None, at_least=at_least, pull=pull)]
    # the modes: 1 has no N at the chip (but a kind it crosses), 2 puts N and drags a kind, 3 puts N
    crossed = r.choice([None] + kinds)
    if crossed:
        rules += [f"mode 1 & not {crossed} → no N", f"mode 1 & {crossed} → X"]
    else:
        rules.append("mode 1 → no N")
    dragged = r.choice([None] + kinds)
    items = ["N", "Y"] + ([f"no {dragged}"] if dragged else [])
    r.shuffle(items)  # (the order of a mode rule's items must not matter)
    rules.append("mode 2 → " + " & ".join(items))
    rules.append("mode 3 → N & X")
    modes, t = [(0, "1")], 0
    for _ in range(r.randint(0, 4)):
        t += r.choice([50, 150, 300, 700])
        m = r.choice([x for x in "123" if x != modes[-1][1]])
        modes.append((t, m))
    edits = []
    if r.random() < 0.5:
        a = r.randint(50, 1500)
        edits.append((r.randint(1, steps // 2), (a, a + r.choice([5, 40, 120]))))
    ticks = [F(r.choice([1, 3, 10, 25, 60])) for _ in range(steps)]
    return dict(seed=seed, rules=rules, constraints=constraints, modes=modes, edits=edits, ticks=ticks)


def session(sc: dict) -> Session:
    s = Session(sc["rules"], sc["modes"], 0, 1, [], [], F(1, 2), [_copy(k) for k in sc["constraints"]])
    s.begin()
    req = s.sim.request
    s.sim.complete(req.id, *list(req.gen.stages(req.state, req.chip, req.known, req.previous))[-1])
    s.sim.background = False
    return s


def _copy(k):
    if isinstance(k, AsEarlyAsPossible):
        return AsEarlyAsPossible(k.kind, length=k.length)
    return _Gap(k.kind, k.gap, after=k.after if k.after != k.kind else None, at_least=k.at_least, pull=k.pull)


AHEAD = 3000  # how far ahead of the chip the two runs are compared


def run(sc: dict, reference: bool) -> list:
    """The line ahead of the chip after each step: [(position, {kind: region})]; and an
    error string at the end if the run raised or broke a MinimumGap."""
    patches = []
    if reference:
        patches = [mock.patch.object(simulation.Simulation, "_stretch_at", lambda self, mode, x, hi: None),
                   mock.patch.object(Script, "pushes", lambda self: {})]
    for p in patches:
        p.start()
    out = []
    try:
        s = session(sc)
        kinds = sorted({k.kind for k in sc["constraints"]} | {"N"})
        gaps = [k for k in s.sim.script.constraints if isinstance(k, _Gap)]
        for i, dt in enumerate(sc["ticks"]):
            for at, (a, b) in sc["edits"]:
                if at == i:
                    s.sim.edit_now([Op.put("N", Region.of(Iv(s.position + a, s.position + b, True, True)))])
            s.tick(dt)
            c = s.position
            ahead = Region.of(Iv(c, c + AHEAD, False, True))
            line = s.sim.state.line
            out.append((c, {k: line.presence(k) & ahead for k in kinds}))
            if not reference:
                for g in gaps:
                    ops = g.ops(s.sim.state, c)
                    if ops:
                        out.append(f"after the step to {c}, {g} does not hold: {ops[0].action} {ops[0].region}")
                        return out
    except Exception:  # noqa: BLE001 (any error of the machine is a finding)
        out.append("raised: " + traceback.format_exc(limit=6))
    finally:
        for p in patches:
            p.stop()
    return out


def compare(sc: dict) -> str | None:
    """None if the fast run is the reference's, else what differs first."""
    fast, ref = run(sc, False), run(sc, True)
    for i, (a, b) in enumerate(zip(fast, ref)):
        if isinstance(a, str):
            return "fast " + a
        if isinstance(b, str):
            return "reference " + b
        if a != b:
            c = a[0]
            diff = [k for k in a[1] if a[1][k] != b[1].get(k)]
            return (f"step {i} (chip {c}): {', '.join(diff)} differ\n"
                    + "\n".join(f"    {k} fast {a[1][k]}\n    {k} ref  {b[1][k]}" for k in diff))
    if len(fast) != len(ref):
        return "fast " + str(fast[-1]) if len(fast) > len(ref) else "reference " + str(ref[-1])
    return None


def describe(sc: dict) -> str:
    return (f"seed {sc['seed']}\n  rules {sc['rules']}\n  constraints {[str(k) for k in sc['constraints']]}\n"
            f"  modes {sc['modes']}\n  edits {sc['edits']}\n  ticks {[str(t) for t in sc['ticks']]}")


if __name__ == "__main__":
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    steps = int(sys.argv[3]) if len(sys.argv) > 3 else 24
    bad = 0
    with mock.patch.object(gui, "run"):
        for seed in range(first, first + count):
            sc = scenario(seed, steps)
            t = time.perf_counter()
            why = compare(sc)
            took = time.perf_counter() - t
            if why is None:
                print(f"seed {seed}: same ({took:.1f}s)", flush=True)
            else:
                bad += 1
                print(f"seed {seed}: DIFFERENT ({took:.1f}s)\n{describe(sc)}\n  {why}", flush=True)
    print(f"{bad} of {count} different")
