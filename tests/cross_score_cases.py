"""line_manager's score against OmniApp's (ScoreModel, Kotlin) on the same cases, to keep the two
implementations of docs/scheduler_score.md one score (2026-11-08: 40 of 40 agree to 0.1, once the panel
ending at the chip was charged here and the window kept from ending in a night after a block, below).

    python tests/cross_score_cases.py <cases.json> <how many>
    (then in OmniApp, tests/omniapp_score_cases.kt.txt as shared/src/jvmTest/.../ScratchScoreCases.kt, its DIR
    the directory of cases.json: ./gradlew :shared:jvmTest --tests "*ScratchScoreCases" --rerun; it writes
    results.txt next to it, both scores per case; delete it afterwards)

Known apart: a window ending inside a night whose last schedulable stretch is a block of the user's:
ScoreModel takes the last schedulable stretch as going on forever past the window (the block depriving the
others forever), line_manager reads the line ahead (the night). And what ScoreModel does not have yet:
each task's own compensation length, rare tasks, a maximum, quantities (scheduler_score.md, 2026-11-07).

Cases both scorers read: tasks, periods, blocks, history, a plan, the window [a, end), in millis. The
common ground of the two documents: every task's period tau = M / pi under 4h (one compensation length,
nothing rare), no maximum. line_manager's J (without the tail past end) written with each case."""
import sys, json, random; sys.path.insert(0, "C:/code/line_manager/tests")
from fractions import Fraction as F
from helpers import Interval  # noqa
from intervals import Interval as Iv, IntervalSet
from line import LineState, Op
from region import Region
from resolve import apply_edit
from rules import parse_rules
from score import Score, ScoreProblem, Task

MIN, HOUR, DAY = 60, 3600, 86400
def reg(v): return Region.of(IntervalSet([Iv(F(a), F(b), False, True) for a, b in sorted(v) if b > a]))

def case(seed, tail=False):
    r = random.Random(seed)
    names = "ABCD"[:r.choice([1, 2, 2, 3, 4])]
    feats = set(r.sample(["night", "past", "block", "idle", "short"], r.randint(0, 5)))
    pr = {k: r.choice([1, 2, 3]) for k in names}
    tot = sum(pr.values())
    tasks = {}
    for k in names:
        pi = pr[k] / tot
        Mmax = int(4 * HOUR * pi / MIN)  # (tau = M / pi under 4h)
        tasks[k] = Task(pr[k], r.choice([m for m in (5, 10, 15, 20, 30) if m <= Mmax] or [1]) * MIN,
                        resilience={"Sleep": 0} if "night" in feats else {})
    rules = parse_rules([f"{x} → " + " & ".join(f"no {y}" for y in names[i + 1:]) for i, x in enumerate(names[:-1])] if len(names) > 1 else [])
    score = Score(tasks)
    a = 10 * DAY + r.randint(0, 23) * HOUR
    end = a + r.choice([2, 4, 6]) * HOUR
    st = LineState.empty()
    periods = []
    if "night" in feats:
        n0 = (a // DAY) * DAY + r.choice([-2, 1, 3]) * HOUR
        for d in (-1, 0, 1):
            periods.append(("Sleep", n0 + d * DAY, n0 + d * DAY + 6 * HOUR))
        st = apply_edit(rules, st, [Op.put("Sleep", reg([(x, y) for _, x, y in periods]))], "user")
    def fill(lo, hi, idle):
        out, t = [], lo
        while t < hi:
            k = r.choice(names)
            h = r.choice([5, 10, 20, 30, 45]) * MIN
            if "short" in feats and r.random() < 0.3:
                h = r.choice([1, 2, 3]) * MIN
            if idle and r.random() < 0.15:
                k = None
            out.append((k, t, min(t + h, hi)))
            t += h
        return out
    history = [x for x in fill(a - 16 * HOUR, a, False) if x[0]] if "past" in feats else []
    blocks = []
    if "block" in feats:
        s0 = a + r.randint(10, 60) * MIN
        blocks.append((r.choice(names), s0, s0 + r.choice([20, 45, 90]) * MIN))
    plan = []
    for k, x, y in fill(a, end, "idle" in feats):
        for bk, bx, by in blocks:  # (a plan does not cover the user's block)
            if x < by and y > bx:
                if x < bx:
                    plan.append((k, x, bx))
                if y > by:
                    plan.append((k, by, y))
                break
        else:
            plan.append((k, x, y))
    plan = [p for p in plan if p[0] and p[2] > p[1]]
    if history:
        st = apply_edit(rules, st, [Op.put(k, reg([(x, y) for kk, x, y in history if kk == k])) for k in names if any(h[0] == k for h in history)], "generated")
    if blocks:
        st = apply_edit(rules, st, [Op.put(k, reg([(x, y)])) for k, x, y in blocks], "user")
    pre = ScoreProblem(score, window=end - a)._preplaced(st)
    line = apply_edit(rules, st, [Op.put(k, reg([(x, y) for kk, x, y in plan if kk == k])) for k in names if any(p[0] == k for p in plan)], "generated") if plan else st
    score._no_tail = not tail
    res = score.evaluate(line.line, a, end, pre)
    ms = lambda x: int(x) * 1000
    return dict(seed=seed, feats=sorted(feats),
                tasks=[dict(id=k, priority=float(t.priority), minimumMillis=ms(t.minimum), resilience=t.resilience) for k, t in tasks.items()],
                periods=[dict(kind="sleep", start=ms(x), end=ms(y)) for _, x, y in periods],
                blocks=[dict(task=k, start=ms(x), end=ms(y)) for k, x, y in blocks],
                history=[dict(task=k, start=ms(x), end=ms(y)) for k, x, y in history],
                plan=[dict(task=k, start=ms(x), end=ms(y)) for k, x, y in plan],
                a=ms(a), end=ms(end), lookback=ms(16 * HOUR),
                lm=dict(J=res.J, J1=res.J1, J2=res.J2))

if __name__ == "__main__":
    n = int(sys.argv[2])
    json.dump([case(s) for s in range(n)], open(sys.argv[1], "w"))
    print("wrote", n, "cases")
