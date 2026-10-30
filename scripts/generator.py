"""The rules generator.

A run takes the state of the line, the chip's position c and the revealed modes, and
returns a script. It is given optimization problems, each with a weight (problems.py),
and plans the window W = (c, c + H], H the longest horizon of its problems.

0. Constraints (`constraints`, constraints.py): each gives the edits that make it hold
   after the chip at c, on the line as it is (e.g. FifteenMinuteBreaks: the 15-minute
   breaks, where OmniApp's rules put them). A run makes them first, and every
   candidate edit comes on top of them; the script makes them where it is put in
   place (script.ConstraintEditAction).
1. It builds candidate edits in a fixed order that does not depend on the
   hyperparameter q: the no-op first, then for each (prediction, placement) the
   edits all the problems propose together, then (with several problems) each
   problem's alone, then the other edits of each problem; an edit already built
   is skipped:
   - prediction: how much of each kind the mode rules will add or remove in W when
     the chip goes through it: from the most probable scenario of the probability
     finder ("scenario"), every mode equally likely ("uniform"), or ignored ("none");
   - placement: where to put what is missing (or which generated elements to remove
     when there is too much).
2. It evaluates the first B(q) candidates: each is resolved like any edit
   (resolve.py); it is valid if it is not refused and respects N1 and N2 (guards.py).
   The best valid one minimises the weighted sum of the errors of the problems,
   expected over the probability finder's scenarios (or uniform modes) where a
   problem predicts the mode rules; the earliest wins ties, so the no-op wins when
   nothing is better.
3. The script applies the best edit at once and schedules the next run.
   It also runs again at each edit of the user (script.Edited) and, with mode
   rules, when the chip is where none of its compiled stretches applies
   (script.Uncovered).
4. With mode rules, it compiles them (mode_rules.compile_runs) from the line after
   the best edit, as the chip goes from c to the next run's position D(q) through
   the predicted modes: the mode at the chip, then the most probable scenario of the
   probability finder where it predicts one (else the mode goes on). Each compiled
   stretch is in the script (script.Script.stretches); other mode sequences are not.

The hyperparameter q in (0, 1):
    budget      B(q) = 2 + floor(36 q)           candidates evaluated (non-decreasing)
    distance    D(q) = H (1 - q / 2)             rerun when the chip has moved D (decreasing); with mode
                                                 rules, they are compiled up to c + D / 6 (a run compiles
                                                 about a minute of the chip in a second) and the next run
                                                 is asked for at c + D / 18, so that it can complete
                                                 before the chip leaves what this one compiled
    transitions K(q) = 1 + floor(4 (1 - q))      or after K revealed transitions (non-increasing)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction

from guards import check_script_edit
from intervals import EPS, INF, Interval, IntervalSet, Pos
from line import GEN, LineState, Op
from mode_rules import compile_runs
from modes import ModeKnowledge
from probability import ProbabilityFinder, Scenario
from problems import MinimumLengthProblem, PresenceRatioProblem  # noqa: F401 (the problems, for the callers)
from region import Region
from resolve import Refused, apply_edit
from rules import RuleSet
from script import ConstraintEditAction, ChipIn, EditAction, Event, PresenceAbove, RerunAction, ScoreBelow, Script, TransitionCount, Uncovered, Edited, score
from solver import lhs_region

PREDICTIONS = ("scenario", "uniform", "none")
STRATEGIES = ("spread4", "left", "right", "spread2", "spread8", "spread16")


@dataclass
class RunStats:
    work: int = 0  # candidates built and checked (the no-op included)
    budget: int = 0
    valid: int = 0
    chosen: str = "no-op"
    key: tuple = ()
    probability_rules: list[str] = field(default_factory=list)
    refused: list = field(default_factory=list)  # (position, why): operands of the mode rules refused while compiling
    constraints: str = ""  # why the constraints could not be put, if they could not


def budget(q: float) -> int:
    return 2 + math.floor(36 * q)


def rerun_distance(q: float, horizon: Fraction) -> Fraction:
    # the shortest decimal of a float keeps the order of floats: 0.2 -> 1/5
    return horizon * (1 - Fraction(str(q)) / 2)


def rerun_transitions(q: float) -> int:
    return 1 + math.floor(4 * (1 - q))


class RulesGenerator:
    MAX_SCENARIOS = 8

    def __init__(self, rules: RuleSet, problems, q: float, use_probability: bool = True, constraints=()) -> None:
        """problems: a problem, or [(problem, weight)] with weights >= 0; constraints:
        constraints.py's (anything with `ops(state, chip)`)."""
        if not 0 < q < 1:
            raise ValueError("the allocated resources quantity must be in (0, 1)")
        self.rules = rules
        self.problems = [(problems, Fraction(1))] if hasattr(problems, "error") else [(p, Fraction(w)) for p, w in problems]
        if any(w < 0 for _, w in self.problems):
            raise ValueError("the weights of the problems are >= 0")
        self.horizon = max((p.horizon for p, _ in self.problems), default=Fraction(1))
        self.q = q
        self.use_probability = use_probability
        self.constraints = tuple(constraints)

    def run(self, state: LineState, chip, known: ModeKnowledge) -> tuple[Script, RunStats]:
        c = Fraction(chip)
        H = self.horizon
        ctx = _Context(self, state, c, known)
        stats = RunStats(budget=budget(self.q), probability_rules=ctx.probability_rules)
        base, keep = state, None
        ops = [o for k in self.constraints for o in k.ops(state, c)]
        if ops:
            keep = ConstraintEditAction()
            try:
                base = apply_edit(self.rules, state, ops, GEN)
                why = check_script_edit(state, base, c)
            except Refused as e:
                why = str(e)
            if why:
                base, keep, stats.constraints = state, None, why
        best_ops, best_key, best_label, best_after = (), ctx.score(base), "no-op", base
        stats.work, stats.valid = 1, 1
        seen = {""}
        for label, ops in ctx.candidates():
            key = " ; ".join(map(str, ops))
            if key in seen:
                continue  # the no-op, or an edit already built
            seen.add(key)
            if stats.work >= stats.budget:
                break
            stats.work += 1
            try:
                after = apply_edit(self.rules, base, list(ops), GEN)
            except Refused:
                continue
            if check_script_edit(state, after, c) is not None:
                continue
            stats.valid += 1
            key = ctx.score(after)
            if key < best_key:
                best_ops, best_key, best_label, best_after = ops, key, label, after
        stats.chosen, stats.key = best_label, best_key
        events: list[Event] = []
        if keep is not None:
            events.append(Event(ChipIn(c, INF), (keep,)))
        stretches, covered = [], []
        D = rerun_distance(self.q, H)
        if self.rules.mode_rules:
            covered = self.predicted(ctx, c + D / 6)
            # what the text covers does not depend on what is compiled ahead, a shortcut: a
            # lower q compiles less (q D(q) grows with q), and the chip computes the rest
            end = c + D / 6 * Fraction(str(self.q))
            ahead = [(lo, min(hi, Pos(end)), m) for lo, hi, m in covered if lo <= end]
            stretches, stats.refused = compile_runs(self.rules, best_after, ahead, self.constraints)
        if best_ops:
            events.append(Event(ChipIn(c, c + H), (EditAction(tuple(best_ops)),)))
        if not self._futile(state, best_ops):
            events.append(Event(ChipIn(c + (D / 18 if self.rules.mode_rules else D), INF), (RerunAction(),)))
            events.append(Event(TransitionCount(rerun_transitions(self.q)), (RerunAction(),)))
            events += self._watches(best_after, c, D, known.current_mode())
        events.append(Event(Edited(), (RerunAction(),)))
        if covered:
            events.append(Event(Uncovered(), (RerunAction(),)))
        live = bool(self.constraints and self.rules.mode_rules)  # a constraint holds whatever a run had time for
        return Script(events, stretches, covered, c, best_after, live, self.constraints), stats

    def _watches(self, state: LineState, c: Fraction, D: Fraction, mode: str) -> list[Event]:
        """Run again if the line goes away from what this run left: the score gets lower than
        it is, or a presence ratio above its target and what it is, by a margin that a
        lower q makes wider (not true on the line this run leaves, so no run asks for itself)."""
        if not any(p.has_targets() for p, _ in self.problems):
            return []
        margin = (1 - Fraction(str(self.q))) / 2
        out = [Event(ScoreBelow(score(self.problems, state, c) * (1 - margin)), (RerunAction(),))]
        for p, _ in self.problems:
            for kind, r in sorted(getattr(p, "targets", {}).items()):
                w = IntervalSet.of(Interval(c, c + p.horizon, False, True))
                now = state.line.integral(kind, w) / p.horizon
                y = max(now, r) + margin / 2
                if y < 1:
                    out.append(Event(PresenceAbove(kind, Fraction(100), y, p.horizon, c, c + D, mode), (RerunAction(),)))
        return out

    def predicted(self, ctx: _Context, end: Fraction) -> list[tuple[Pos, Pos, str]]:
        """The modes the mode rules are compiled for (`_Context.predicted`)."""
        return ctx.predicted(end)

    def _futile(self, state: LineState, ops) -> bool:
        """A run can tell that later runs would do nothing until the user edits the line:
        - there is nothing to optimise (no target, no rule); or
        - the line is empty, no mode rule exists and this run does nothing: every later
          run, until a user edit, sees the same problem translated."""
        if not any(p.has_targets() for p, _ in self.problems) and not self.rules.rules:
            return True
        empty = not state.claims.kinds() and not state.line.kinds()
        return empty and not self.rules.mode_rules and not ops


class _Context:
    def __init__(self, gen: RulesGenerator, state: LineState, c: Fraction, known: ModeKnowledge) -> None:
        self.gen, self.state, self.c, self.known = gen, state, c, known
        H = gen.horizon
        self.Wset = IntervalSet.of(Interval(c, c + H, False, True))
        self.W = Region.of(self.Wset)
        modes = set(known.modes_seen()) | {r.mode for r in gen.rules.mode_rules}
        self.scenarios: list[Scenario] = []
        self.residual = Fraction(1)
        self.probability_rules: list[str] = []
        if gen.use_probability:
            model = ProbabilityFinder().fit(known)
            self.probability_rules = model.rules()
            self.scenarios, self.residual = model.scenarios(known, c + H, gen.MAX_SCENARIOS)
            modes |= set(model.next_counts) | set(model.length_counts)
        self.modes = sorted(modes)
        unknown = Region.of(IntervalSet.of(Interval(max(c, known.frontier), c + H, False, True)))
        # weights: list of (mode, region, weight) giving P(mode(x) = m) on W's unknown part
        self.weights_full = []
        u = Fraction(1, len(self.modes)) if self.modes else Fraction(0)
        for sc in self.scenarios:
            for m in self.modes:
                self.weights_full.append((m, Region.of(sc.mode_region(m)) & unknown, sc.probability))
                self.weights_full.append((m, Region.of(sc.uncovered) & unknown, sc.probability * u))
        for m in self.modes:
            self.weights_full.append((m, unknown, (self.residual if gen.use_probability else Fraction(1)) * u))
        self.weights_uniform = [(m, unknown, u) for m in self.modes]
        best = self.scenarios[0] if self.scenarios else None
        self.weights_best = ([(m, Region.of(best.mode_region(m)) & unknown, Fraction(1)) for m in self.modes]
                             + [(m, Region.of(best.uncovered) & unknown, u) for m in self.modes]) if best else None

    def predicted(self, end: Fraction) -> list[tuple[Pos, Pos, str]]:
        """The chip's positions from c to end, (lo, hi, mode) where the modes are
        predicted: the mode at the chip, then the most probable scenario where it
        predicts one; elsewhere the mode goes on."""
        starts = {self.c: self.known.current_mode()}
        if self.scenarios:
            for m in self.modes:
                for iv in self.scenarios[0].mode_region(m).intervals:
                    x = iv.lo.x if isinstance(iv.lo, Pos) else iv.lo
                    if self.c < x < end:
                        starts[x] = m
        points = []
        for x in sorted(starts):
            if not points or points[-1][1] != starts[x]:
                points.append((x, starts[x]))
        return [(Pos(x), Pos(points[i + 1][0]) - EPS if i + 1 < len(points) else Pos(end), m)
                for i, (x, m) in enumerate(points)]

    # expected integral of a kind on Wset (W by default), with what the mode rules will do there
    def expected(self, s: LineState, kind: str, weights, Wset: IntervalSet | None = None) -> Fraction:
        Wset = self.Wset if Wset is None else Wset
        base = s.line.integral(kind, Wset)
        if not weights:
            return base
        pres = s.line.presence(kind)
        adj = Fraction(0)
        for m, region, w in weights:
            if not w or not region:
                continue
            for r in self.gen.rules.mode_rules:
                if r.mode != m:
                    continue
                met = region & lhs_region(r.lhs, s.line.vals)
                for it in r.rhs:
                    o = it.options[0]
                    if o.kind != kind:
                        continue
                    if o.negated:
                        for v, reg in s.line.vals.get(kind, {}).items():
                            adj -= w * v / 100 * (reg & met).measure_on(Wset)
                    else:
                        adj += w * ((met - pres).measure_on(Wset))
        return base + adj

    def score(self, s: LineState) -> tuple:
        return (sum((w * p.error(self, s) for p, w in self.gen.problems if w), Fraction(0)),)

    def candidates(self):
        preds = [p for p in PREDICTIONS if p != "scenario" or self.weights_best]
        problems = [p for p, w in self.gen.problems if w]
        for pred in preds:
            for strategy in STRATEGIES:
                label = f"prediction={pred}, placement={strategy}"
                parts = [p.proposals(self, pred, strategy) for p in problems]
                yield label, tuple(op for ops in parts for op in ops)
                if len(problems) > 1:
                    for p, ops in zip(problems, parts):
                        yield f"{p.name} alone, {label}", ops
        for p in problems:
            yield from p.extra(self)
