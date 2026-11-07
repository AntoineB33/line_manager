"""The rules generator.

A run takes the state of the line, the chip's position c and the revealed modes, and
returns a script. It is given optimization problems, each with a weight (problems.py),
defined on the whole line. When the definitions are simple enough it finds the best line
at once (ratio_plan.py); otherwise it searches, an approximation, on the window
W = (c, c + H], H its own choice (`search_window`).

0. Constraints (`constraints`, constraints.py): each gives the edits that make it hold
   after the chip at c, on the line as it is (e.g. Breaks: the 15-minute
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
3. The script applies the best edit at once, and asks for a new run only where one can
   help (`_reruns`), so its rerun rules depend on the context:
   - always at each edit of the user that the run reads (script.Edited);
   - mode rules: at a position, so that a run compiles what comes next in its own process
     (past what is compiled the chip computes them itself, and waits for it), and when the
     chip is where the script does not apply them (script.Uncovered);
   - a constraint that does not hold by itself as the chip goes: at a position;
   - targets: the score falling below what the run left, too much of a kind, and the
     chip in a mode whose rules reach what the problems read (the other modes do not
     matter).
   With no mode rule, nothing to optimise and everything holding by itself, only the
   user's edits do.
4. With mode rules, it compiles them (mode_rules.compile_runs) from the line after
   the best edit, as the chip goes from c to the next run's position D(q) through
   the predicted modes: the mode at the chip, then the most probable scenario of the
   probability finder where it predicts one (else the mode goes on). Each compiled
   stretch is in the script (script.Script.stretches); other mode sequences are not.

The hyperparameter q in [0, 1]:
    budget      B(q) = 2 + floor(36 q)           candidates evaluated (non-decreasing)
    distance    D(q) = H (1 - q / 2)             rerun when the chip has moved D, where a position is
                                                 needed (decreasing); with mode rules, they are compiled
                                                 up to c + D / 6 (a run compiles about a minute of the chip
                                                 in a second) and the next run is asked for at c + D / 18,
                                                 so that it can complete before the chip leaves what this
                                                 one compiled
    modes       c + D (1 - q) / 2                from there, a mode that matters runs the generator again
    margin      (1 - q) / 2                      how far the score falls first
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field, replace
from fractions import Fraction

from constraints import constraint_ops, expand, push_of
from guards import check_script_edit
from intervals import EPS, INF, Interval, IntervalSet, Pos
from line import GEN, LineState, Op
from mode_rules import compile_runs
from modes import ModeKnowledge
from probability import ProbabilityFinder, Scenario
from problems import MinimumLengthProblem, PresenceRatioProblem, check_sum  # noqa: F401 (the problems, for the callers)
from region import Region
from ratio_plan import Fill, NoBestScript, Plan, check_best_exists  # noqa: F401 (NoBestScript: for the callers)
from resolve import Refused, apply_edit
from rules import RuleSet
from script import SHOW, ConstraintEditAction, ChipIn, ChipOn, EditAction, Event, PresenceAbove, RerunAction, ScoreBelow, Script, Uncovered, Edited, score
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
    final: bool = True  # the last script of the run
    definitive: object = None  # the next scripts of the run change nothing before this position


PACE = Fraction(10)  # a run makes the script definitive this far further ...
PACE_TIME = Fraction(10)  # ... every this many seconds (Simulation.keep_pace sees to it)
SEARCH_TIME = Fraction(6)  # the seconds a stage searches at q = 1: what the pace allows


def _within(o: Op, where: Region) -> Op:
    """The op on `where`; the op itself where it is already within it (a region built from a
    pattern keeps it: ratio_plan.Built, which the script's text gives as its call)."""
    return o if o.region.issubset(where) else Op(o.action, o.kind, o.region & where, o.value)


def budget(q: float) -> int:
    return 2 + math.floor(36 * q)


def rerun_distance(q: float, window: Fraction) -> Fraction:
    # the shortest decimal of a float keeps the order of floats: 0.2 -> 1/5
    return window * (1 - Fraction(str(q)) / 2)


SMALLEST_WINDOW = 24 * PACE  # the search's window when nothing in the definitions is longer


def search_window(problems, constraints=()) -> Fraction:
    """How far a run's search looks after the chip: far enough for what the definitions
    are made of, 4 times the longest minimum length and twice the longest cycle of a
    constraint (a break and its pause), at least SMALLEST_WINDOW."""
    out = SMALLEST_WINDOW
    for p, _ in problems:
        for L in getattr(p, "lengths", {}).values():
            out = max(out, 4 * Fraction(L))
    for k in constraints:
        out = max(out, 2 * Fraction(getattr(k, "cycle", 0)))
    return out


class RulesGenerator:
    MAX_SCENARIOS = 8

    def __init__(self, rules: RuleSet, problems, q: float, use_probability: bool = True, constraints=(), seed=None,
                 window=None, shortcuts: bool = True) -> None:
        """problems: a problem, or [(problem, weight)] with weights >= 0; constraints:
        constraints.py's (anything with `ops(state, chip)`); seed: the random choices
        (ratio_plan.py), drawn once here if None; window: the search's (`search_window`
        if None, or the largest window of problems already bound to one); shortcuts: the
        best line at once where ratio_plan.py finds it (False: the search alone). Raises
        NoBestScript (a ValueError) when the definitions have no best script."""
        if not 0 <= q <= 1:
            raise ValueError("the allocated resources quantity must be in [0, 1]")
        self.rules = rules
        problems = [(problems, Fraction(1))] if hasattr(problems, "error") else [(p, Fraction(w)) for p, w in problems]
        if any(w < 0 for _, w in problems):
            raise ValueError("the weights of the problems are >= 0")
        self.constraints = expand(constraints)  # (what places the pieces of an obligation alone)
        targets = {}
        for p, _ in problems:
            targets.update(getattr(p, "targets", {}))
        check_sum(targets)  # all the problems' together
        bound = [p.window for p, _ in problems if getattr(p, "window", None)]
        self.window = Fraction(window) if window is not None else max(bound) if bound else search_window(problems, self.constraints)
        self.problems = [(p.with_window(self.window) if hasattr(p, "with_window") else p, w) for p, w in problems]
        self.q = q
        self.use_probability = use_probability
        for k in self.constraints:  # (ScreenBreaks reads what the rules put)
            if hasattr(k, "bind"):
                k.bind(rules)
            if hasattr(k, "bind_all"):  # (AsEarlyAsPossible reads the other constraints)
                k.bind_all(self.constraints)
        self.paced = False  # in a process of its own: the instant script holds PACE after the chip
        self.endless = False  # q = 1 in a process of its own: the run goes on until it proves it is done
        self.speed = Fraction(1)  # the chip's, for how far it goes during a run
        self.seed = random.randrange(2 ** 32) if seed is None else seed
        check_best_exists(rules, self.problems)  # whatever the rest of the definitions
        self.plan = Plan(rules, self.problems, self.constraints, self.seed)
        self.fill = Fill(self.problems)
        self.cycles = Plan(rules, self.problems, self.constraints, self.seed, strict=False)
        if self.fill.ok and self._modes_add_targets():
            self.fill.ok = False  # the mode rules add to the targets' share: the search predicts them
        if not shortcuts:  # the search alone, whatever the definitions (its tests)
            self.plan.ok = self.fill.ok = self.cycles.ok = False

    def kinds_read(self):
        """What the rules, the constraints and the problems read; None if that is not known."""
        out = set(self.rules.ruled)
        for k in self.constraints:
            if not hasattr(k, "kinds"):
                return None
            out |= k.kinds()
        for p, _ in self.problems:
            out |= set(getattr(p, "targets", ())) | set(getattr(p, "lengths", ()))
        return out

    def margin(self) -> Fraction | None:
        """How far after a position the mode rules acting there can reach, through the rules
        their elements trigger; None if that has no bound. An edit further than that from
        the frontier cannot change what they do before it, whatever the modes."""
        reached = {o.kind for r in self.rules.mode_rules for o in r.written_lits()}
        far, rules = Fraction(0), [r for r in self.rules.rules if not r.is_mode_rule]
        used, grew = set(), bool(reached)
        while grew:
            grew = False
            for i, r in enumerate(rules):
                if i not in used and reached & set(r.lhs.kinds()):
                    used.add(i)
                    grew = True
                    reached |= {o.kind for o in r.written_lits()}
                    ends = [e.x for o in r.lits() for e in (o.where.lo, o.where.hi) if type(e) is Pos]
                    if any(o.anchor == "l" or type(o.where.lo) is not Pos or type(o.where.hi) is not Pos for o in r.lits()):
                        return None
                    far += max(map(abs, ends), default=Fraction(0))
        return far

    def first_edits(self, state: LineState, c: Fraction) -> list[list[Op]]:
        """What the script at the start of a run can do at once, on the PACE after the chip:
        - ratio targets: the kind with the biggest target not reached yet;
        - minimum lengths: each kind at the chip that is not there for its length L on the
          L before the chip goes on;
        - with a perfect script (ratio_plan.py): its whole edit."""
        plan = self.plan.ops(state, c) if self.plan.ok else None
        if plan is not None:
            return [plan]
        fill = self._fill(state, c)[0]
        if fill:
            return [fill]
        best, short = None, []
        cp = Pos(c)
        for p, _ in self.problems:
            for kind, r in sorted(getattr(p, "targets", {}).items()):
                w = IntervalSet.of(Interval(c, c + p.window, False, True))
                if state.line.integral(kind, w) / p.window < r and (best is None or r > best[0]):
                    best = (r, kind)
            for kind, L in sorted(getattr(p, "lengths", {}).items()):
                pres = state.line.presence(kind)
                if (pres.contains(cp) or pres.contains(cp - EPS)) and pres.measure_on(IntervalSet.of(Interval(c - L, c, False, True))) < L:
                    short.append(kind)
        on = Region.of(Interval(c, c + PACE, False, True))
        return [[Op.put(k, on)] for k in ([best[1]] if best else []) + [k for k in short if not best or k != best[1]]]

    def frontiers(self, c: Fraction) -> list:
        """Where the stages of a run from c make the script definitive: c + PACE, then steps
        of at least PACE up to c + H, as many as q allows (the lower q, the shorter the run),
        at least one: with q = 0 too a run reaches c + H, or it would run again sooner than
        with a higher q. None for a run that reaches c + H at once."""
        first = c + PACE
        rest = c + self.window - first
        if rest <= 0:
            return [None]
        more = max(1, math.ceil(Fraction(str(self.q)) * rest / PACE))
        return [first + rest * i / more for i in range(more + 1)]

    def run(self, state: LineState, chip, known: ModeKnowledge) -> tuple[Script, RunStats]:
        """The last script of the run."""
        for out in self.stages(state, chip, known):
            pass
        return out

    def stages(self, state: LineState, chip, known: ModeKnowledge, previous: Script | None = None):
        """The scripts of a run, as they are found, each with its stats. Each stage only adds
        an edit on the positions after the last frontier and up to its own, so the lines of
        the script for the positions before a frontier never change (the score, the mode
        rules and the constraints of the script are decided at the first stage).

        `paced`: the run has published its instant script (Simulation.first_script): up to
        c + PACE it says what that script says, the mode rules in any mode and the edits of
        `previous` that are still to come, and the stages go on from there."""
        c = Fraction(chip)
        if self.plan.ok:
            perfect = self._perfect(state, c)
            if perfect is not None:
                yield perfect
                return
        H = self.window
        ctx = _Context(self, state, c, known)
        stats = RunStats(budget=budget(self.q), probability_rules=ctx.probability_rules)
        base, keep = state, None
        ops = constraint_ops(self.rules, state, c, self.constraints)
        written = self.constraint_events(state, c)  # (the explicit ones: their edits in the script)
        if ops:
            keep = ConstraintEditAction() if any(not getattr(k, "explicit", False) for k in self.constraints) else None
            try:
                base = apply_edit(self.rules, state, ops, GEN)
                why = check_script_edit(state, base, c)
            except Refused as e:
                why = str(e)
            if why:
                base, keep, written, stats.constraints = state, None, [], why
        frontiers = self.frontiers(c)
        ctx.forced = []
        if self.fill.ok:  # the best line at once, on the whole line: one stage
            fill = self._at_once(base, c)
            try:
                valid = bool(fill) and check_script_edit(state, apply_edit(self.rules, base, fill, GEN), c) is None
            except Refused:
                valid = False
            if valid:
                ctx.forced, frontiers = [("the targets at once: each where it can be", tuple(fill))], [None]
        D = rerun_distance(self.q, H)
        predicted = self.predicted(ctx, c + D / 6) if self.rules.mode_rules else []
        covered, carried = predicted, []
        lo = c
        if self.paced:
            lo = c + PACE
            if self.rules.mode_rules:
                covered = [(Pos(c), Pos(lo), None)] + predicted
            carried = [e for e in (previous.events if previous else ()) if any(isinstance(a, EditAction) for a in e.actions)
                       and (not isinstance(e.trigger, ChipIn) or e.trigger.hi > c)]
            frontiers = frontiers[1:] or [lo]
            for e in carried:  # (they fire before the chip goes on: the stages start from them)
                for a in e.actions:
                    if isinstance(a, EditAction):
                        try:
                            base = apply_edit(self.rules, base, list(a.ops), GEN)
                        except Refused:
                            pass
        live = bool(self.constraints and self.rules.mode_rules)  # a constraint holds whatever a run had time for
        best_key, best_label, current = ctx.score(base), "no-op", base
        stats.work, stats.valid = 1, 1
        edits: list[Event] = []  # one for each stage that edits
        began = time.perf_counter()
        after_edits: list[Event] | None = None
        published = None
        for i, hi in enumerate(frontiers):
            ctx.state = current
            until = time.perf_counter() + float(SEARCH_TIME) if self.endless else None
            # the first stage chooses over the whole window, with the whole budget, and keeps
            # all of its edit: the later ones only make the script definitive further, and
            # improve on it where they can (cut to its stage, a plan of the window kept only
            # its first few units, and the budget ran out a few stages on)
            whole = i == 0
            # an interval reaching this stage's frontier can still grow at the next one (problems.py)
            ctx.open_end = hi if not whole and hi is not None and hi < c + H else None
            ops, key, label, after = self._stage(ctx, state, current, lo, hi, stats, best_key, len(frontiers) - i, until,
                                                 whole=whole)
            ctx.open_end = None
            if ops:
                best_key, best_label, current = key, label, after
                edits.append(Event(ChipIn(c, INF), (EditAction(tuple(ops)),)))
            if after_edits is None:
                next_run = D / 18 if self.rules.mode_rules else D
                if self.paced:  # after this run, or it would cancel it: twice the time of the longest
                    # run, whatever q is, so that a higher q never asks for its next run later
                    # (per candidate: a stage of a lower q does less, each costs the same)
                    each = Fraction(time.perf_counter() - began).limit_denominator(1000) / max(1, stats.work)
                    next_run = max(next_run, 2 * each * budget(1) * self.speed)
                after_edits = self._reruns(state, current, c, c + next_run, D, known.current_mode(), bool(ops) or len(frontiers) > 1)
                after_edits.append(Event(Edited(True), (RerunAction(),)))
                if covered and not live:
                    after_edits.append(Event(Uncovered(), (RerunAction(),)))
            # a script that calls for no new run but at the user's edits is the perfect one
            # (the user's rule): the run ends with it, definitive everywhere
            perfect = all(isinstance(e.trigger, Edited) for e in after_edits)
            final = i == len(frontiers) - 1 or perfect
            stretches = []
            if final and predicted:
                # the text does not depend on what is compiled ahead, a shortcut: a lower q
                # compiles less (q D(q) grows with q), and the chip computes the rest
                end = c + D / 6 * Fraction(str(self.q))
                ahead = [(a, min(b, Pos(end)), m) for a, b, m in predicted if a <= end]
                pushes = [k for k in map(push_of, written + carried) if k is not None] if live else []
                stretches, stats.refused = compile_runs(self.rules, current, ahead, self.constraints, pushes)
            # (nothing left to improve: the error, not the stretches', which turns of a given
            # length cannot bring to 0)
            done = final and (not self.endless or perfect)
            stats.chosen, stats.key, stats.final, stats.definitive = best_label, best_key, done, INF if perfect else hi
            if published is None or ops or final:  # (a stage that edits nothing publishes the same script)
                events = ([Event(ChipIn(c, INF), (keep,))] if keep is not None else []) + written + carried + edits + after_edits
                published = Script.of(events, on_view=SHOW, stretches=stretches, covered=covered, origin=c, base=current,
                                      live=live, constraints=self.constraints)
            yield published, replace(stats)
            if perfect:
                return
            lo = hi
            if final and not done:  # nothing proves it is the best: requirement.md, the run never ends
                lo = c + max(H, PACE) if hi is None else hi
                while True:  # at the pace, each stage on the window as far as the frontier went
                    hi = lo + PACE
                    until = time.perf_counter() + float(SEARCH_TIME)
                    further = _Context(self, current, lo - PACE, known)
                    ops, key, label, after = self._stage(further, state, current, lo, hi, stats, further.score(current), 1, until)
                    if ops:
                        best_label, current = label, after
                        edits.append(Event(ChipIn(c, INF), (EditAction(tuple(ops)),)))
                    time.sleep(max(0, until - time.perf_counter()))
                    stats.chosen, stats.definitive = best_label, hi
                    events = ([Event(ChipIn(c, INF), (keep,))] if keep is not None else []) + written + carried + edits + after_edits
                    yield Script.of(events, on_view=SHOW, stretches=stretches, covered=covered, origin=c, base=current, live=live,
                                    constraints=self.constraints), replace(stats)
                    lo = hi

    def constraint_events(self, state: LineState, c) -> list[Event]:
        """The edits of the constraints written out in the script (those `explicit`), not
        called while it runs: what they ask for from the chip at c, an edit; and for each
        AsEarlyAsPossible with a length, in each mode where it cannot be at the chip, what
        the chip does when it is on one: the pieces from the chip again, written from the
        chip (the user can change them: (10, 910] instead of (0, 900] moves them on 10)."""
        from ratio_plan import cycle
        c = Fraction(c)
        out = []
        ops = constraint_ops(self.rules, state, c, [k for k in self.constraints if getattr(k, "explicit", False)])
        if ops:
            out.append(Event(ChipIn(c, INF), (EditAction(tuple(ops)),)))
        for k in self.constraints:
            if getattr(k, "explicit", False) and getattr(k, "length", None) is not None and hasattr(k, "spacing"):
                again = (Op.take(k.kind, Region.of(Interval(0, INF, False, False))),
                         Op.put(k.kind, cycle(0, k.spacing(), 0, k.length)))
                # (no `within`: as far after the chip as the edit puts it, so changing the edit
                # alone moves where the chip pushes it: simulation._reach)
                out += [Event(ChipOn(k.kind, m), (EditAction(again, from_chip=True),)) for m in k.pushed_in(self.rules)]
                # in each mode that puts a gap's kind at the chip ("mode 2 → NoScreen"), once it has
                # lasted long enough to open the gap and the chip is out of a piece: the gap starts
                # at the chip at each position, so the pieces go on from it
                for g in k.counted_gaps():
                    later = (Op.take(k.kind, Region.of(Interval(0, INF, False, False))),
                             Op.put(k.kind, cycle(g.gap, k.spacing(), 0, k.length)))
                    trig = dict(lasted=g.at_least or None, unless=k.kind)
                    if trig["lasted"] is None:
                        continue  # (a gap after any of its kind: at once, nothing to wait for)
                    out += [Event(ChipOn(g.after, m, **trig), (EditAction(later, from_chip=True),))
                            for m in k.puts_at_chip(self.rules, g.after)]
        return out

    def _fill(self, state: LineState, c: Fraction):
        """(the fill's edit or None, the state with the constraints' elements) (ratio_plan.Fill)."""
        if not self.fill.ok:
            return None, state
        ops = constraint_ops(self.rules, state, c, self.constraints)
        try:
            base = apply_edit(self.rules, state, ops, GEN) if ops else state
        except Refused:
            return None, state
        return self._at_once(base, c), base

    def _at_once(self, base: LineState, c: Fraction) -> list[Op] | None:
        """The best line's edit on this line (the constraints' elements on it), if it is
        found at once: each target where it can be (ratio_plan.Fill), the targets that
        exclude each other in their cycles (ratio_plan.Plan.group_ops); None if not."""
        grouped = self.cycles.grouped if self.cycles.ok else set()
        ops = self.fill.ops(self.rules, base, c, self.window, skip=grouped)
        if ops is None:
            return None
        if grouped:
            more = self.cycles.group_ops(self.rules, base, c, self.constraints)
            if more is None:
                return None
            ops = ops + more
        return ops or None

    def _perfect(self, state: LineState, c: Fraction):
        """The perfect script (ratio_plan.py) and its stats, at once: definitive everywhere,
        and only the user's edits run the generator again. None if there is none here."""
        ops = self.plan.ops(state, c)
        if ops is None:
            return None
        try:
            after = apply_edit(self.rules, state, ops, GEN)
            why = check_script_edit(state, after, c)
        except Refused as e:
            why = str(e)
        if why:
            return None
        events = [Event(ChipIn(c, INF), (EditAction(tuple(ops)),)), Event(Edited(True), (RerunAction(),))]
        stats = RunStats(work=1, budget=budget(self.q), valid=1, chosen="perfect script", final=True, definitive=INF)
        return Script.of(events, on_view=SHOW, origin=c, base=after), stats

    def _stage(self, ctx: _Context, state: LineState, base: LineState, lo, hi, stats: RunStats, best_key, left: int, until=None,
               whole: bool = False):
        """The best edit on (lo, hi] of the candidates this stage has budget and time for
        (the budget left, shared with the `left` stages to come; with `until`, all it finds
        up to that time): (ops, key, label, state). `whole`: on everything after lo, with
        the whole budget left."""
        if until is None and not whole:
            # the first stage chose over the whole window: this one only makes the script
            # definitive further (a run that goes on, `until`, still improves on it)
            return (), best_key, "no-op", base
        # (scored here: what counts depends on the stage's frontier, problems.MinimumLengthProblem.error)
        best = ((), ctx.score(base), "no-op", base)
        far = self.margin()
        if lo > ctx.c and self.rules.mode_rules:  # (what the mode rules do before lo stays)
            lo = hi if far is None or hi is None else min(lo + far, hi)
        if whole and hi is not None:
            where = Region.of(Interval(lo, INF, False, False)) if lo < hi else Region.empty()
            limit = stats.budget
        else:
            where = None if hi is None else Region.of(Interval(lo, hi, False, True)) if lo < hi else Region.empty()
            limit = stats.work + max(1, (stats.budget - stats.work) // left)
        held = not any(k.ops(base, ctx.c) for k in self.constraints)
        seen = {""}
        # the best line found at once (ratio_plan) is the run's edit: no candidate of the search
        # competes with it (their score does not follow the stretch rule as it does)
        forced = getattr(ctx, "forced", ())
        for label, ops in (iter(forced) if forced else ctx.candidates(until is not None)):
            if where is not None:
                ops = tuple(o for o in (_within(o, where) for o in ops) if not o.region.is_empty())
            key = " ; ".join(map(str, ops))
            if key in seen:
                continue  # the no-op, or an edit already built
            seen.add(key)
            if (stats.work >= min(limit, stats.budget)) if until is None else time.perf_counter() >= until:
                break
            stats.work += 1
            try:
                after = apply_edit(self.rules, base, list(ops), GEN)
            except Refused:
                continue
            if check_script_edit(state, after, ctx.c) is not None:
                continue
            if lo > ctx.c and not base.line.same_on(after.line, Region.of(Interval(-INF, lo, False, True))):
                continue  # a rule reaches back: the script is definitive up to lo
            if held and any(k.ops(after, ctx.c) for k in self.constraints):
                continue  # an edit never breaks a constraint
            stats.valid += 1
            key = ctx.score(after)
            if key < best[1]:
                best = (ops, key, label, after)
        return best

    def _reruns(self, state: LineState, after: LineState, c: Fraction, at: Fraction, D: Fraction, mode: str, edits: bool) -> list[Event]:
        """The events that ask for a new run, only where one can help (see the top of this file)."""
        rerun = (RerunAction(),)
        out = []
        if self.rules.mode_rules or not all(getattr(k, "holds_by_itself", lambda: False)() for k in self.constraints):
            out.append(Event(ChipIn(at, INF), rerun))
        if any(p.has_targets() for p, _ in self.problems) and not self._futile(state, edits):
            lo = c + D * (1 - Fraction(str(self.q))) / 2
            out += [Event(ChipIn(lo, INF, m), rerun) for m in self._modes_that_matter()]
            out += self._watches(after, c, D, mode)
        return out

    def _modes_add_targets(self) -> bool:
        """Whether a mode rule can put a target kind on the line, directly or through the
        element rules ("mode 1 → D", D a target). A mode rule that can only forbid them at
        the chip ("mode 2 → NoScreen", "NoScreen → no A") does not change what the best
        line is from what is known: a run after the mode acts makes it again."""
        targets = {k for p, _ in self.problems for k, r in getattr(p, "targets", {}).items() if r > 0}
        written = [o for r in self.rules.mode_rules for o in r.written_lits()]
        put = {o.kind for o in written if not o.negated}
        reached = put | {o.kind for o in written}
        grew = True
        while grew:
            grew = False
            for r in self.rules.rules:
                if not r.is_mode_rule and reached & set(r.lhs.kinds()):
                    more = {o.kind for o in r.written_lits() if not o.negated} - put
                    if more:
                        put |= more
                        reached |= more
                        grew = True
        return bool(put & targets)

    def _modes_that_matter(self) -> list[str]:
        """The modes whose rules reach what the problems read, directly or through the
        element rules."""
        read = {k for p, _ in self.problems for k in (*getattr(p, "targets", ()), *getattr(p, "lengths", ()))}
        out = []
        for m in sorted({r.mode for r in self.rules.mode_rules}):
            reached = {o.kind for r in self.rules.mode_rules if r.mode == m for o in r.written_lits()}
            grew = True
            while grew:
                grew = False
                for r in self.rules.rules:
                    written = {o.kind for o in r.written_lits()}
                    if not r.is_mode_rule and reached & set(r.lhs.kinds()) and not written <= reached:
                        reached |= written
                        grew = True
            if reached & read:
                out.append(m)
        return out

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
                w = IntervalSet.of(Interval(c, c + p.window, False, True))
                now = state.line.integral(kind, w) / p.window
                y = max(now, r) + margin / 2
                if y < 1:
                    out.append(Event(PresenceAbove(kind, Fraction(100), y, p.window, c, c + D, mode), (RerunAction(),)))
        return out

    def predicted(self, ctx: _Context, end: Fraction) -> list[tuple[Pos, Pos, str]]:
        """The modes the mode rules are compiled for (`_Context.predicted`)."""
        return ctx.predicted(end)

    def _futile(self, state: LineState, edits: bool) -> bool:
        """The line is empty, no mode rule exists and this run does nothing: every later run,
        until a user edit, sees the same problem translated."""
        empty = not state.claims.kinds() and not state.line.kinds()
        return empty and not self.rules.mode_rules and not edits


class _Context:
    def __init__(self, gen: RulesGenerator, state: LineState, c: Fraction, known: ModeKnowledge) -> None:
        self.gen, self.state, self.c, self.known = gen, state, c, known
        H = gen.window
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
                    else:  # at the rule's percentage (requirement.md: the ratio uses them)
                        adj += w * Fraction(100 if o.value is None else o.value) / 100 * (met - pres).measure_on(Wset)
        return base + adj

    def score(self, s: LineState) -> tuple:
        """(what the problems' quantities by a date miss, before anything else (score.ScoreProblem:
        a shortfall outweighs every other goal), then the weighted error of the problems, then
        how far the shorter stretches from the chip are from the ratio targets:
        problems.PresenceRatioProblem.stretch_error)."""
        return (sum((p.shortfall(self, s) for p, w in self.gen.problems if w and hasattr(p, "shortfall")), 0),
                sum((w * p.error(self, s) for p, w in self.gen.problems if w), Fraction(0)),
                sum((w * p.stretch_error(self, s) for p, w in self.gen.problems if w and hasattr(p, "stretch_error")),
                    Fraction(0)))

    def candidates(self, deep: bool = False):
        """The candidates in their order; `deep`: then finer and finer placements."""
        yield from self._candidates(STRATEGIES + self._by_length())
        if deep:
            # up to 1024 parts: a placement spread over more costs too much to build (it is
            # built before the run's time is checked) for parts 1/1024 of the window wide
            yield from self._candidates(tuple(f"spread{2 ** n}" for n in range(5, 11)))

    def _by_length(self) -> tuple:
        """The placement that spreads each kind in pieces of its minimum length: over as many
        parts of the window as its share there allows (turns of A and B of 60, not blocks),
        when that is finer than the other placements (STRATEGIES: 16 parts at most)."""
        targets, lengths = {}, {}
        for p, w in self.gen.problems:
            if w:
                targets.update(getattr(p, "targets", {}))
                lengths.update(getattr(p, "lengths", {}))
        parts = [int(self.gen.window * targets[k] / L) for k, L in lengths.items() if targets.get(k)]
        n = min(parts, default=0)
        return (f"spread{min(n, 64)}",) if n > 16 else ()  # (more parts cost too much to build and score)

    def _candidates(self, strategies):
        preds = [p for p in PREDICTIONS if p != "scenario" or self.weights_best]
        problems = [p for p, w in self.gen.problems if w]
        for pred in preds:
            for strategy in strategies:
                label = f"prediction={pred}, placement={strategy}"
                parts = [p.proposals(self, pred, strategy) for p in problems]
                yield label, tuple(op for ops in parts for op in ops)
                if len(problems) > 1:
                    for p, ops in zip(problems, parts):
                        yield f"{p.name} alone, {label}", ops
        if strategies[:len(STRATEGIES)] == STRATEGIES:
            for p in problems:
                yield from p.extra(self)
