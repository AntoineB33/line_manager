"""The rules generator.

A run takes the line, the chip's position and the revealed modes, and returns a
script. It plans the window W = (c, c + H] (H: the problem's horizon):

1. It builds candidate states in a fixed order that does not depend on the
   hyperparameter q: the current state (no-op) first, then variants combining
   - prefill: where to satisfy, in advance, mode rules that cannot react in time
     ("scenario": where the most probable scenario of the probability finder puts
     the mode, "none", "everywhere" the mode is possible);
   - a placement strategy for the presence ratio targets;
   - with or without repair of the violations that are certain.
2. It evaluates the first B(q) candidates. A candidate is valid iff going from the
   current state to it respects N1, N2 and N3 (guards.py). The best valid one
   minimises (expected violation measure, expected violation points, ratio
   error), the earliest wins ties. The no-op is always valid.
3. The script sets the difference between the current state and the best
   candidate, adds a continuous edit for each mode rule that can react to the
   mode at the chip, and schedules the next run.

The hyperparameter q in (0, 1):
    budget      B(q) = 2 + floor(36 q)           candidates evaluated (non-decreasing)
    distance    D(q) = H (1 - q / 2)             rerun when the chip has moved D (decreasing)
    transitions K(q) = 1 + floor(4 (1 - q))      or after K revealed transitions (non-increasing)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction

from guards import check_edit, forbidden_zone, free_types, mode_region
from intervals import EMPTY, INF, Interval, IntervalSet
from line import GEN, USER, LineState, changed_region
from modes import ModeKnowledge
from probability import ProbabilityFinder, Scenario
from rules import ABSENT, EXACT, PRESENT, Item, Rule
from script import ChipIn, Event, FollowAction, RerunAction, Script, SetAction, TransitionCount

PREFILLS = ("scenario", "none", "everywhere")
STRATEGIES = ("spread4", "left", "right", "spread2", "spread8", "skip")
REPAIR_ROUNDS = 6


@dataclass
class PresenceRatioProblem:
    """Targets r_T in [0, 1] for the presence ratio (1/H) ∫_W s_T(x)/100 dx of each type."""
    targets: dict[str, Fraction]
    horizon: Fraction

    def __post_init__(self) -> None:
        self.targets = {t: Fraction(r) for t, r in self.targets.items()}
        self.horizon = Fraction(self.horizon)
        if self.horizon <= 0 or any(not 0 <= r <= 1 for r in self.targets.values()):
            raise ValueError("horizon > 0 and targets in [0, 1]")


@dataclass
class RunStats:
    work: int = 0  # candidates built and checked
    budget: int = 0
    valid: int = 0
    chosen: str = "no-op"
    key: tuple = ()
    probability_rules: list[str] = field(default_factory=list)


def budget(q: float) -> int:
    return 2 + math.floor(36 * q)


def rerun_distance(q: float, horizon: Fraction) -> Fraction:
    # the shortest decimal of a float keeps the order of floats: 0.2 -> 1/5
    return horizon * (1 - Fraction(str(q)) / 2)


def rerun_transitions(q: float) -> int:
    return 1 + math.floor(4 * (1 - q))


class RulesGenerator:
    MAX_SCENARIOS = 8

    def __init__(self, rules: list[Rule], problem: PresenceRatioProblem, q: float, use_probability: bool = True) -> None:
        if not 0 < q < 1:
            raise ValueError("the allocated resources quantity must be in (0, 1)")
        self.rules = list(rules)
        self.problem = problem
        self.q = q
        self.use_probability = use_probability
        self.free = free_types(self.rules)

    # ------------------------------------------------------------------ run
    def run(self, state: LineState, chip, known: ModeKnowledge) -> tuple[Script, RunStats]:
        c = Fraction(chip)
        H = self.problem.horizon
        ctx = _Context(self, state, c, known)
        stats = RunStats(budget=budget(self.q), probability_rules=ctx.probability_rules)

        best_state, best_key, best_label = state, ctx.score(state), "no-op"
        stats.work, stats.valid = 1, 1
        for label, build in ctx.candidates():
            if stats.work >= stats.budget:
                break
            stats.work += 1
            cand = build()
            if check_edit(state, cand, c, self.rules, known) is not None:
                continue
            stats.valid += 1
            key = ctx.score(cand)
            if key < best_key:
                best_state, best_key, best_label = cand, key, label
        stats.chosen, stats.key = best_label, best_key

        events: list[Event] = []
        items = _diff_items(state, best_state)
        if items:
            events.append(Event(ChipIn(c, c + H), (SetAction(items),)))
        for (mode, etype), (value, offsets) in sorted(ctx.follows.items()):
            events.append(Event(ChipIn(c, c + H, mode), (FollowAction(etype, value, offsets),)))
        if not self._futile(state, items):
            events.append(Event(ChipIn(c + rerun_distance(self.q, H), INF), (RerunAction(),)))
            events.append(Event(TransitionCount(rerun_transitions(self.q)), (RerunAction(),)))
        return Script(events), stats

    def _futile(self, state: LineState, items) -> bool:
        """A run can tell that later runs would do nothing until the user edits the line:
        - there is nothing to optimise (no target, no rule); or
        - the line is empty, no rule depends on modes and this run does nothing: every
          later run, until a user edit, sees the same problem translated."""
        if not self.problem.targets and not self.rules:
            return True
        no_modes = all(r.lhs_mode is None for r in self.rules)
        return state.is_empty() and no_modes and not items


class _Context:
    """Everything one run computes once."""

    def __init__(self, gen: RulesGenerator, state: LineState, c: Fraction, known: ModeKnowledge) -> None:
        self.gen, self.state, self.c, self.known = gen, state, c, known
        H = gen.problem.horizon
        self.W = IntervalSet.of(Interval(c, c + H, False, True))
        reach = max((r.reach() for r in gen.rules), default=Fraction(0))
        self.E = IntervalSet.of(Interval(c - reach, c + H, False, True))
        self.editable = IntervalSet.after(c)
        modes = set(known.modes_seen()) | {r.lhs_mode for r in gen.rules if r.lhs_mode is not None}
        self.scenarios: list[Scenario] = []
        self.residual = Fraction(1)
        self.probability_rules: list[str] = []
        if gen.use_probability:
            model = ProbabilityFinder().fit(known)
            self.probability_rules = model.rules()
            self.scenarios, self.residual = model.scenarios(known, c + H, gen.MAX_SCENARIOS)
            for m in list(model.next_counts) + list(model.length_counts):
                modes.add(m)
        self.n_modes = len(modes)
        self._classify_reactive()

    def _classify_reactive(self) -> None:
        """Items of rules 'mode m → U on offsets > 0' with U in no left side are handled by a
        continuous edit following the chip while the mode is m."""
        groups: dict[tuple[str, str], dict[Fraction, IntervalSet]] = {}
        members: dict[tuple[str, str], list[tuple[int, int]]] = {}
        for i, r in enumerate(self.gen.rules):
            if r.lhs_mode is None or r.lhs_types:
                continue
            for j, it in enumerate(r.rhs):
                first = it.rel.intervals[0]
                positive = first.lo > 0 or (first.lo == 0 and not first.lo_closed)
                if it.etype in self.gen.free and positive:
                    key = (r.lhs_mode, it.etype)
                    vals = groups.setdefault(key, {})
                    v = it.allowed.repair_value()
                    vals[v] = vals.get(v, EMPTY) | it.rel
                    members.setdefault(key, []).append((i, j))
        self.follows: dict[tuple[str, str], tuple[Fraction, IntervalSet]] = {}
        self.reactive: set[tuple[int, int]] = set()
        for key, vals in groups.items():
            if len(vals) == 1:  # two values for one type in one mode would conflict
                (v, offs), = vals.items()
                self.follows[key] = (v, offs)
                self.reactive |= set(members[key])

    # ----------------------------------------------------------- scoring
    def score(self, s: LineState) -> tuple:
        meas = Fraction(0)
        pts = Fraction(0)
        for i, r in enumerate(self.gen.rules):
            if r.lhs_mode is None:
                v = r.violations(s, IntervalSet.universe()) & self.E
                meas += v.measure()
                pts += v.points()
                continue
            m = r.lhs_mode
            v = r.violations(s, self.known.region(m)) & self.E
            meas += v.measure()
            pts += v.points()
            items = [it for j, it in enumerate(r.rhs) if (i, j) not in self.reactive]
            base = r.violations(s, self.known.unknown(), items) & self.E
            if not base:
                continue
            for sc in self.scenarios:
                inside = base & sc.mode_region(m)
                unc = base & sc.uncovered
                meas += sc.probability * (inside.measure() + unc.measure() / self.n_modes)
                pts += sc.probability * (inside.points() + Fraction(unc.points(), self.n_modes))
            meas += self.residual * base.measure() / self.n_modes
            pts += self.residual * Fraction(base.points(), self.n_modes)
        H = self.gen.problem.horizon
        err = sum((abs(s.integral(t, self.W) / H - r) for t, r in self.gen.problem.targets.items()), Fraction(0))
        return (meas, pts, err)

    # -------------------------------------------------------- candidates
    def candidates(self):
        prefills = [p for p in PREFILLS if p != "scenario" or self.scenarios]
        for repair in (True, False):
            for prefill in prefills:
                for strategy in STRATEGIES:
                    label = f"prefill={prefill}, placement={strategy}, repair={'yes' if repair else 'no'}"
                    yield label, (lambda p=prefill, st=strategy, rp=repair: self.build(p, st, rp))

    def build(self, prefill: str, strategy: str, repair: bool) -> LineState:
        s = self.state.copy()
        if prefill != "none":
            self._prefill(s, prefill)
        if strategy != "skip":
            self._ratios(s, strategy)
        if repair:
            self._repair(s)
        return s

    def _fix(self, s: LineState, item: Item, region: IntervalSet) -> bool:
        """Make the item hold on region where a generated element can do it."""
        u = item.etype
        y = region & self.editable
        y = y - s.owned(u, USER)
        if not y:
            return False
        before = s.pieces(u)
        a = item.allowed
        if a.kind == PRESENT:
            s.assign(u, y - s.support(u), Fraction(100), GEN)
        elif a.kind == EXACT:
            s.assign(u, y - s.where(u, lambda p: p.value == a.value), a.value, GEN)
        else:
            s.remove_owned(u, y, GEN)
        return s.pieces(u) != before

    def _prefill(self, s: LineState, how: str) -> None:
        best = self.scenarios[0] if self.scenarios else None
        unknown_w = self.known.unknown() & self.W
        for i, r in enumerate(self.gen.rules):
            if r.lhs_mode is None:
                continue
            m = r.lhs_mode
            if how == "scenario" and best is not None:
                region = self.known.region(m) | ((best.mode_region(m) | best.uncovered) & self.W)
            else:
                region = self.known.region(m) | unknown_w
            met = r.met(s, region)
            for j, it in enumerate(r.rhs):
                if (i, j) not in self.reactive:
                    self._fix(s, it, met + it.rel)

    def _repair(self, s: LineState) -> None:
        for _ in range(REPAIR_ROUNDS):
            changed = False
            for r in self.gen.rules:
                met = r.met(s, mode_region(r, self.known, certain=True))
                for it in r.rhs:
                    fail = r.item_violations(s, it, met)
                    if fail:
                        changed |= self._fix(s, it, fail + it.rel)
            if not changed:
                return

    def _ratios(self, s: LineState, strategy: str) -> None:
        H = self.gen.problem.horizon
        for t, target in sorted(self.gen.problem.targets.items()):
            need = target * H - s.integral(t, self.W)
            if need > 0:
                free = self.W - s.support(t) - forbidden_zone(s, self.gen.rules, self.known, t, Fraction(100))
                s.assign(t, _place(free, need, strategy, self.W, self.c, H), Fraction(100), GEN)
            elif need < 0:
                removable = (s.owned(t, GEN) & self.W) - forbidden_zone(s, self.gen.rules, self.known, t, Fraction(0))
                _remove_amount(s, t, removable, -need, from_right=strategy != "right")


def _place(free: IntervalSet, amount: Fraction, strategy: str, W: IntervalSet, c: Fraction, H: Fraction) -> IntervalSet:
    if strategy == "left":
        return free.take(amount)
    if strategy == "right":
        return free.take(amount, from_right=True)
    k = int(strategy.removeprefix("spread"))
    out = EMPTY
    carry = Fraction(0)
    for i in range(k):
        part = IntervalSet.of(Interval(c + H * i / k, c + H * (i + 1) / k, False, True)) & free
        got = part.take(amount / k + carry)
        carry = amount / k + carry - got.measure()
        out = out | got
    return out


def _remove_amount(s: LineState, etype: str, region: IntervalSet, amount: Fraction, from_right: bool) -> None:
    """Remove generated pieces of a type in region until the integral dropped by amount."""
    pieces = [p for p in s.pieces(etype) if p.owner == GEN]
    parts = [(iv, p.value) for p in pieces for iv in (IntervalSet.of(p.interval) & region).intervals]
    parts.sort(key=lambda x: x[0].lo, reverse=from_right)
    cut = []
    left = amount
    for iv, v in parts:
        if left <= 0:
            break
        if iv.is_point:
            continue
        worth = v / 100 * iv.length
        if worth <= left:
            cut.append(iv)
            left -= worth
        else:
            length = left * 100 / v
            cut.append(IntervalSet.of(iv).take(length, from_right=from_right).intervals[0])
            left = Fraction(0)
    s.remove_owned(etype, IntervalSet(cut), GEN)


def _diff_items(before: LineState, after: LineState) -> tuple:
    items = []
    for t in sorted(set(before.types()) | set(after.types())):
        ch = changed_region(before, after, t)
        if not ch:
            continue
        for v in sorted({p.value for p in after.pieces(t) if p.owner == GEN}):
            x = after.where(t, lambda p: p.owner == GEN and p.value == v) & ch
            if x:
                items.append((t, x, v))
        gone = ch - after.support(t)
        if gone:
            items.append((t, gone, Fraction(0)))
    return tuple(items)
