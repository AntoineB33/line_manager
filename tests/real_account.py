"""The two paces on a real account: the scheduler's inputs as OmniApp computes them (exported from a COPY of its
database by a throwaway jvmTest: tasks with their priority share, minimum and resilience, the restrictive periods,
the panels), built into a line_manager case, and three schedules scored by the same score on the same windows:
OmniApp's own plan (its panels ahead of the chip), the instant schedule, a step of the scheduler.

    python tests/real_account.py <export.json> [hours between chips] [how many chips] [budget s]

Nothing of the account is written anywhere: it reads the export and prints numbers.
"""

from __future__ import annotations

import json
import os
import sys
import time
from fractions import Fraction as F

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from helpers import Interval  # noqa: E402,F401 (the paths of scripts/)

from instant import Instant  # noqa: E402
from intervals import Interval as Iv, IntervalSet  # noqa: E402
from line import LineState, Op  # noqa: E402
from pace import Pace  # noqa: E402
from region import Region  # noqa: E402
from resolve import apply_edit  # noqa: E402
from rules import parse_rules  # noqa: E402
from score import Score, ScoreProblem, Task  # noqa: E402

DAY = 86400


def _kind(name: str) -> str:
    return "P_" + "".join(w.capitalize() for w in name.split())


def load(path: str):
    """(rules, score, the line with the periods and the user's panels, OmniApp's panels {task: [(a, b)]}, the chip
    of the export, names) on line_manager's clock: seconds from the midnight (UTC) three days before the export."""
    d = json.load(open(path, encoding="utf-8"))
    base = (d["now"] // 1000 // DAY - 3) * DAY

    def s(ms):
        return F(ms // 1000 - base)
    names = {t["id"]: f"T{k}" for k, t in enumerate(d["tasks"])}
    kinds = sorted({p["kind"] for p in d["periods"]})
    tasks = {}
    for t in d["tasks"]:
        res = {_kind(k): float(t["resilience"].get(k, d["defaultResilience"].get(k, 0.0))) for k in kinds}
        tasks[names[t["id"]]] = Task(max(float(t["priority"]), 0.0), max(t["minimumMillis"] // 1000, 60), resilience=res)
    order = sorted(tasks)
    rules = parse_rules([f"{a} → " + " & ".join(f"no {b}" for b in order[i + 1:]) for i, a in enumerate(order[:-1])])
    score = Score(tasks)
    st = LineState.empty()
    by_kind = {}
    for p in d["periods"]:
        a, b = s(p["start"]), s(p["end"])
        if b > a:
            by_kind.setdefault(_kind(p["kind"]), []).append(Iv(a, b, False, True))
    ops = [Op.put(k, Region.of(IntervalSet(v))) for k, v in by_kind.items()]
    if ops:
        st = apply_edit(rules, st, ops, "user")
    app, user = {}, {}
    for p in d["panels"]:
        k = names.get(p["task"])
        if k is None:
            continue
        (user if p["user"] else app).setdefault(k, []).append((s(p["start"]), s(p["end"])))
    if user:
        st = apply_edit(rules, st, [Op.put(k, _region(v)) for k, v in user.items()], "user")
    return rules, score, st, app, s(d["now"]), names


def _region(v) -> Region:
    return Region.of(IntervalSet([Iv(a, b, False, True) for a, b in sorted(v) if b > a]))


def _clip(panels: dict, a, b) -> dict:
    out = {k: [(max(x, a), min(y, b)) for x, y in v if y > a and x < b] for k, v in panels.items()}
    return {k: v for k, v in out.items() if v}


def compare(rules, score, st, app, chip, budget=2.0):
    """{name: (J, seconds)} for OmniApp's plan, the instant schedule and a step, on the step's window from chip."""
    pace = Pace(rules, score)
    a, b = pace.slice(chip, 0)
    end = b + pace.AHEAD
    past = _clip(app, chip - 2 * DAY, a)  # (what OmniApp served and planned before the window: the same for all three)
    base = apply_edit(rules, st, [Op.put(k, _region(v)) for k, v in past.items()], "generated") if past else st
    pre = ScoreProblem(score, window=end - a)._preplaced(base)
    out = {}

    def J(panels):
        line = apply_edit(rules, base, [Op.put(k, _region(v)) for k, v in panels.items()], "generated") if panels else base
        r = score.evaluate(line.line, a, end, pre)
        return r.J, r
    if any(y > a for v in app.values() for _, y in v) and max(y for v in app.values() for _, y in v) >= end:
        out["omniapp"] = (J(_clip(app, a, end))[0], None)
    t0 = time.perf_counter()
    got = Instant(rules, score).schedule(base, a, end, start=a)
    out["instant"] = (J(got)[0], time.perf_counter() - t0)
    t0 = time.perf_counter()
    ops, info = pace.step(base, chip, 0, budget=budget)
    deep = apply_edit(rules, base, ops, "generated") if ops else base
    r = score.evaluate(deep.line, a, end, pre)
    out["step"] = (r.J, time.perf_counter() - t0)
    return out


if __name__ == "__main__":
    path = sys.argv[1]
    every = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    count = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    budget = float(sys.argv[4]) if len(sys.argv) > 4 else 2.0
    rules, score, st, app, now, names = load(path)
    print(f"{len(score.tasks)} tasks; the export's chip at {float(now % DAY) / 3600:.2f}h (UTC)", flush=True)
    for n in range(count):
        chip = now + F(int(n * every * 3600))
        got = compare(rules, score, st, app, chip, budget)
        print(f"chip +{n * every:.0f}h: " + ", ".join(
            f"{k} J {v[0]:.0f}" + (f" ({v[1] * 1000:.0f} ms)" if v[1] is not None else "") for k, v in got.items()), flush=True)
