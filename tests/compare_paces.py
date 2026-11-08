"""The two paces compared on random small cases: what the instant schedule shows at once
(instant.Instant) against what a step of the scheduler finds with its time (pace.Pace), on the
same line, scored by the same score (score.Score.evaluate). OmniApp's requirements (§ Two paces)
ask the instant one never to be obviously wrong; here, obviously wrong is:

- "worse": its J more than WORSE times the step's (the step had the time to find better, and
  by that much: something the instant layer does not see);
- "own side": a panel of a task against a block of the user's of that same task (the block
  looks longer than the user made it), where the step keeps that side to another task;
- "short": a panel shorter than its task's minimum with nothing that cuts it there (not the
  window's end, a night, a block of the user's or a task's forbidden place);
- "step worse": the step's J above the instant one's (it starts from it: it may only improve).

    python tests/compare_paces.py [first seed] [how many] [budget s]

Prints one line per case and, for a finding, the case and how to rerun it. test_compare_paces.py
runs fixed seeds.
"""

from __future__ import annotations

import os
import random
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
from ratio_plan import cycle  # noqa: E402
from region import Region  # noqa: E402
from resolve import apply_edit  # noqa: E402
from rules import parse_rules  # noqa: E402
from score import Score, Task  # noqa: E402

MIN, HOUR, DAY = 60, 3600, 86400
WORSE = 1.5
REFINE = os.environ.get("REFINE", "1") == "1"  # the instant layer's rolling pass (Instant.refine)


def scenario(seed: int) -> dict:
    """2 to 4 tasks that exclude each other, random priorities and minimums (a maximum
    sometimes), a night or not, a past (the instant schedule's, one task's hours, or none),
    0 to 2 blocks of the user's in the window."""
    r = random.Random(seed)
    names = "ABCD"[:r.choice([2, 2, 3, 4])]
    night = r.random() < 0.5
    tasks = {}
    for k in names:
        M = r.choice([5, 10, 15, 30, 45]) * MIN
        X = r.choice([2, 3, 4]) * M if r.random() < 0.2 else None
        tasks[k] = dict(priority=r.choice([1, 1, 2, 3, 4]), minimum=M, maximum=X)
    chip = 10 * DAY + r.randint(0, 23) * HOUR + r.choice([0, 7, 30, 45]) * MIN
    past = r.choice(["instant", "instant", "one task", "none"])
    heavy = r.choice(names)
    a = chip + Pace.STEP
    end = a + Pace.STEP + Pace.AHEAD
    blocks, taken = [], []
    for _ in range(r.choice([0, 1, 1, 2])):
        L = r.choice([20, 30, 60, 90, 120]) * MIN
        s = r.randint(int(a - 30 * MIN), int(end - 20 * MIN)) // 60 * 60
        if all(s + L <= x or s >= y for x, y in taken):
            blocks.append((r.choice(names), s, s + L))
            taken.append((s, s + L))
    return dict(seed=seed, tasks=tasks, night=night, chip=chip, past=past, heavy=heavy, blocks=blocks)


def build(sc: dict):
    """(rules, score, state) of a case."""
    names = sorted(sc["tasks"])
    rules = parse_rules([f"{x} → " + " & ".join(f"no {y}" for y in names[i + 1:]) for i, x in enumerate(names[:-1])])
    res = {"Sleep": 0} if sc["night"] else {}
    score = Score({k: Task(v["priority"], v["minimum"], v["maximum"] if v["maximum"] else float("inf"), resilience=dict(res))
                   for k, v in sc["tasks"].items()})
    st = LineState.empty()
    if sc["night"]:
        st = apply_edit(rules, st, [Op.put("Sleep", cycle(F(4920), F(DAY), F(0), F(8 * HOUR)))], "user")
    chip = F(sc["chip"])
    if sc["past"] == "instant":
        got = Instant(rules, score).schedule(st, chip - 2 * DAY, chip)
        st = apply_edit(rules, st, [Op.put(k, _region(v)) for k, v in got.items()], "generated")
    elif sc["past"] == "one task":
        st = apply_edit(rules, st, [Op.put(sc["heavy"], Region.of(Iv(chip - 6 * HOUR, chip, False, True)))], "generated")
    if sc["blocks"]:
        st = apply_edit(rules, st, [Op.put(k, Region.of(Iv(F(x), F(y), False, True))) for k, x, y in sc["blocks"]], "user")
    return rules, score, st


def _region(v) -> Region:
    return Region.of(IntervalSet([Iv(a, b, False, True) for a, b in v]))


def _x(p) -> F:
    return F(getattr(p, "x", p))


def compare(sc: dict, budget: float = 2.0) -> tuple:
    """(findings, (J instant, J step), seconds)."""
    rules, score, st = build(sc)
    chip = F(sc["chip"])
    pace = Pace(rules, score)
    a, b = pace.slice(chip, 0)
    end = b + pace.AHEAD
    inst = Instant(rules, score)
    inst.refine = REFINE
    began = time.perf_counter()
    got = inst.schedule(st, a, end, start=a)  # (as the window shows it where the plan ends at a)
    quick = apply_edit(rules, st, [Op.put(k, _region(v)) for k, v in got.items()], "generated") if got else st
    t_inst = time.perf_counter() - began
    ops, info = pace.step(st, chip, 0, budget=budget)
    deep = apply_edit(rules, st, ops, "generated") if ops else st
    from score import ScoreProblem
    pre = ScoreProblem(score, window=end - a)._preplaced(st)  # (the user's blocks: as the score reads them)
    ji, jd = score.evaluate(quick.line, a, end, pre), score.evaluate(deep.line, a, end, pre)
    out = []
    if (ji.D, ji.J) > (jd.D, jd.J * WORSE) and ji.J > jd.J + 60:
        out.append(f"worse: instant (D {ji.D:.0f}, J {ji.J:.0f}) against the step's (D {jd.D:.0f}, J {jd.J:.0f})")
    if (jd.D, jd.J) > (ji.D, ji.J * 1.001) and jd.J > ji.J + 1:
        out.append(f"step worse: the step's (D {jd.D:.0f}, J {jd.J:.0f}) against instant (D {ji.D:.0f}, J {ji.J:.0f})")
    # the instant line's panels, read from what it put
    users = {k: st.claims.owned(k, "user").on(a - DAY, end + DAY) for k in sc["tasks"]}
    night = st.line.presence("Sleep").on(a - DAY, end + DAY) if sc["night"] else IntervalSet()
    for k, v in got.items():
        M = sc["tasks"][k]["minimum"]
        line_k = quick.line.presence(k).on(a - DAY, end + DAY)
        for iv in line_k.intervals:
            x, y = _x(iv.lo), _x(iv.hi)
            if y <= a or x >= end:
                continue
            mine = [(p, q) for p, q in v if p >= x and q <= y]
            if not mine:
                continue  # (the user's block alone)
            deep_k = deep.line.presence(k).on(a - DAY, end + DAY)
            for blk in users[k].intervals:
                bx, by = _x(blk.lo), _x(blk.hi)
                left, right = x < bx, y > by
                if x <= bx and by <= y and (left or right) and not (
                        (not left or deep_k.contains(bx - F(1, 2))) and (not right or deep_k.contains(by + F(1, 2)))):
                    # (the step's too: the room there too short for another task, the score's answer)
                    out.append(f"own side: {k}'s panel against the user's block of {k} "
                               f"[{float((bx - a) / MIN):.0f}, {float((by - a) / MIN):.0f}] min: the stretch "
                               f"[{float((x - a) / MIN):.0f}, {float((y - a) / MIN):.0f}]")
            if y - x < M:
                def cut(t):
                    if t <= a or t >= end:
                        return True
                    if any(_x(n.lo) <= t <= _x(n.hi) for n in night.intervals):
                        return True
                    return any(any(_x(u.lo) <= t <= _x(u.hi) for u in users[j].intervals) for j in users)
                if not (cut(x) or cut(y)):
                    out.append(f"short: {k}'s panel [{float((x - a) / MIN):.1f}, {float((y - a) / MIN):.1f}] min, "
                               f"its minimum {M // MIN} min")
    return out, (ji.J, jd.J), (t_inst, info.get("seconds"))


def describe(sc: dict) -> str:
    t = ", ".join(f"{k}: priority {v['priority']} min {v['minimum'] // MIN}min" + (f" max {v['maximum'] // MIN}min" if v["maximum"] else "")
                  for k, v in sorted(sc["tasks"].items()))
    a = sc["chip"] + Pace.STEP
    bl = ", ".join(f"{k} [{(x - a) // MIN}, {(y - a) // MIN}] min" for k, x, y in sc["blocks"]) or "none"
    return (f"seed {sc['seed']}: {t}; night {sc['night']}; past {sc['past']}"
            + (f" ({sc['heavy']})" if sc["past"] == "one task" else "") + f"; user's blocks {bl}"
            + f"; chip at {(sc['chip'] % DAY) / HOUR:.2f}h")


if __name__ == "__main__":
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    budget = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0
    bad, ratios = 0, []
    for seed in range(first, first + count):
        sc = scenario(seed)
        found, (ji, jd), (ti, td) = compare(sc, budget)
        ratios.append(ji / jd if jd > 0 else 1.0)
        if found:
            bad += 1
            print(describe(sc), flush=True)
            for f in found:
                print("   ", f, flush=True)
        else:
            print(f"seed {seed}: fine (J instant {ji:.0f}, step {jd:.0f}; instant {ti * 1000:.0f} ms)", flush=True)
    ratios.sort()
    import math
    geo = math.exp(sum(math.log(max(x, 1e-9)) for x in ratios) / len(ratios))
    print(f"{bad} of {count} with a finding; J instant / step: median {ratios[len(ratios) // 2]:.2f}, "
          f"mean (geometric) {geo:.3f}, worst {ratios[-1]:.2f}")
