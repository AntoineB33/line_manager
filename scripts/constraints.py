"""Constraints the rules generator satisfies (generator.py).

A constraint gives, for the line as it is and the chip at c, where its elements must be
after the chip, `targets(state, c)` ({kind: region} on (c, inf)), and the edits that
make it hold, `ops(state, c)` (on positions after c only, so nothing left of the chip
changes, N1; none if it already holds). The generator makes the edits
first and scores its candidates on top of them; its script makes them again where it
is put in place (script.ConstraintEditAction). As the chip goes, the mode rules the
generator compiles keep the targets too (mode_rules.ModeRules.constrain): a break the
chip reaches in mode 1 moves on with it.

- KeepOn(op): an edit written from the chip (`Future on (0, inf)`: Future on (c, inf)).
- Breaks: the 15-minute screen breaks of OmniApp
  (docs/scheduler_requirements.md of OmniApp).
- ScreenBreaks: several kinds of screen breaks together (OmniApp's 20s, 5min and 15min
  breaks), each pausing the others by the "no screen" it makes.
"""

from __future__ import annotations

from fractions import Fraction

from intervals import EPS, INF, Interval, IntervalSet, Pos, _fmt
from line import GEN, LineState, Op
from region import ONE, ZERO, Region, plcm
from solver import checkpoint, solve


def _after(c) -> Region:
    """(c, inf)."""
    return Region.of(Interval(c, INF, False, False))


def _upto(c) -> Region:
    """(-inf, c]."""
    return Region.of(Interval(-INF, c, False, True))


class KeepOn:
    """The line keeps `op` (an element put on a region written from the chip)."""

    def __init__(self, op: Op) -> None:
        self.op = op

    def kinds(self) -> set:
        return {self.op.kind}

    def holds_by_itself(self) -> bool:
        """Once made, it holds as the chip goes: no new run is needed for it."""
        return not self.op.region.is_bounded()

    def targets(self, state: LineState, c) -> dict:
        return {self.op.kind: self.op.region.shift(c) & _after(c)} if self.op.action == "put" else {}

    def ops(self, state: LineState, c: Fraction) -> list[Op]:
        o = Op(self.op.action, self.op.kind, self.op.region.shift(c), self.op.value)
        if o.action == "put" and o.value == 100 and (o.region - state.line.presence(o.kind)).is_empty():
            return []
        return [o]

    def __str__(self) -> str:
        return f"keep {str(self.op).replace(' on ', ' on chip + ', 1)}"


class ChipPush:
    """What a script's event "if the chip is on K (and mode is m), then from the chip, take K
    off (0, inf), put K on R" does, as the mode rules keep it: K after the chip is R from
    the chip, at every position the chip goes through in mode m while it is on K (or K is
    less than `within` after it). The event fires at each instant it is true; here it is
    the same, continuous, and the mode rules compile it ahead (mode_rules._Plan): the line
    is a function of the chip's position, with nothing to solve at each step of the chip.
    Not a constraint of the generator: the script's own (`push_of`)."""

    def __init__(self, kind: str, region: Region, mode: str | None, within) -> None:
        self.kind, self.region, self.mode, self.within = kind, region, mode, Fraction(within)

    def kinds(self) -> set:
        return {self.kind}

    def holds_by_itself(self) -> bool:
        return True

    def on(self, state: LineState, c) -> bool:
        """The chip at c is on K, or K is less than `within` after it (script.ChipOn), on
        the line as the chip gets to c (mode_rules.ModeRules.decide: before the mode rules
        act there). A piece that starts right after the chip is not pushed yet: the chip
        sitting before a break leaves the breaks where they are. "Less than `within` after"
        is (c, c + within], its end included: pushed from the position before, K starts
        right at c + within, and the chip still pushes it there (ChipOn's (c, c + within)
        is the same between two instants, ε apart only)."""
        p = c if isinstance(c, Pos) else Pos(c)
        pres = state.line.presence(self.kind)
        if pres.contains(p):
            return True
        return bool(self.within) and not (pres & Region.of(Interval(p, p + self.within, False, True))).is_empty()

    def targets(self, state: LineState, c) -> dict:
        return self.from_chip(c) if self.on(state, c) else {}

    def behind(self) -> bool:
        """What the chip leaves behind it as it pushes: K at each position it went through
        if R starts right after the chip (R from the position before has it there: (0, 900]),
        none if R starts further ((10, 910])."""
        return self.region.contains(EPS)

    def from_chip(self, c) -> dict:
        """K after the chip at c, pushed: R from c (its ε part kept, so that a run of the mode
        rules makes the same decision at each of its positions)."""
        p = c if isinstance(c, Pos) else Pos(c)
        return {self.kind: self.region.shift(p) & _after(p)}

    def ops(self, state: LineState, c: Fraction) -> list[Op]:
        return []  # (it acts as the chip goes, in the mode rules)

    def __str__(self) -> str:
        return f"push {self.kind} on chip + {self.region}" + (f" in mode {self.mode}" if self.mode is not None else "")


def push_of(event) -> ChipPush | None:
    """The ChipPush an event of a script is, if it is one: "if the chip is on K (and mode
    is m), then from the chip, take K off (0, inf) [or more], put K 100% on R ⊆ (0, inf)"
    (what generator.constraint_events writes; the user may change R). None otherwise: the
    event then fires at each instant, as written."""
    from script import ChipOn, EditAction
    trig = event.trigger
    if not isinstance(trig, ChipOn) or len(event.actions) != 1:
        return None
    a = event.actions[0]
    if not isinstance(a, EditAction) or not a.from_chip:
        return None
    if any(o.kind != trig.kind or o.action not in ("take", "put") for o in a.ops):
        return None
    taken, put = Region.empty(), Region.empty()
    for o in a.ops:
        if o.action == "take":
            if not put.is_empty():
                return None  # (a take after a put: not "K is R after the chip")
            taken = taken | o.region
        elif o.value != 100:
            return None
        else:
            put = put | o.region
    if put.is_empty() or not (_after(ZERO) - taken).is_empty() or not (put - _after(ZERO)).is_empty():
        return None
    lo = put.inf()
    within = trig.within if trig.within is not None else max(Fraction(0), lo.x if isinstance(lo, Pos) else Fraction(0))
    return ChipPush(trig.kind, put, trig.mode, within)


def pushes_in(pushes, mode: str) -> tuple:
    """Those of these pushes that act in this mode."""
    return tuple(k for k in pushes if k.mode in (None, mode))


class MinimumGap:
    """No interval of `kind` within `gap` after the end of an interval of `after` (`kind` by
    default) at least `at_least` long: e.g. ("Break15", 7200, after="NoScreen", at_least=900),
    no break within 2 hours after 15 minutes or more of "no screen" (a break included, a
    break coming with "no screen"). Any kinds and lengths: nothing in it is about breaks.
    The generator plans with it (the turns of `kind` wait where it is too close to such an
    interval, ratio_plan) and keeps it: its edits are those that make it hold after the
    chip (`ops`); an edit of the user that it cannot make hold is refused
    (simulation.against_constraints)."""

    explicit = True  # its edits are written in the script (generator.constraint_events)

    def __init__(self, kind: str, gap, after: str | None = None, at_least=0) -> None:
        self.kind, self.gap = kind, Fraction(gap)
        self.after, self.at_least = after or kind, Fraction(at_least)
        if self.gap <= 0 or self.at_least < 0:
            raise ValueError("a gap > 0 and a length >= 0")
        self.cycle = self.gap + self.at_least  # (generator.search_window: far enough to see two of them)

    def kinds(self) -> set:
        return {self.kind, self.after}

    def holds_by_itself(self) -> bool:
        """It holds as the chip goes: nothing moves by itself."""
        return True

    def targets(self, state: LineState, c) -> dict:
        return {}  # (it puts nothing: mode_rules.constrain)

    def long(self, presence: Region) -> Region:
        """The intervals of `presence` at least `at_least` long (each shrunk by it, what is
        left grown back: only those as long remain), whatever repeats in it."""
        if not self.at_least or presence.is_empty():
            return presence
        L = Pos(self.at_least)
        core = presence.complement().dilate(Interval(ZERO, L, True, False)).complement()  # (x - L, x] all in it
        return core.dilate(Interval(-L, ZERO, False, True)) & presence

    def apart(self, presence: Region) -> Region:
        """Where `kind` cannot be after these intervals of `after`: (end, end + gap] of each
        one at least `at_least` long."""
        long = self.long(presence)
        if long.is_empty():
            return Region.empty()
        ends = long.complement().left_edges()  # (the first position after each interval)
        return ends.dilate(Interval(ZERO, Pos(self.gap), True, False))

    def ops(self, state: LineState, c: Fraction) -> list[Op]:
        """What makes it hold after the chip: off, an interval of the generator too close
        after one of `after`, or the part of the generator's `kind` too close before an
        interval of the user (the user's edit wins); an interval of the user too close that
        nothing else makes hold is off too, which the script cannot do: the user's edit is
        refused."""
        pres = state.line.presence(self.kind)
        after = _after(c)
        bad = pres & self.apart(state.line.presence(self.after)) & after
        if bad.is_empty():
            return []
        mine = state.claims.owned(self.kind, GEN)
        off = bad & mine
        theirs = bad - mine
        if not theirs.is_empty():  # what the generator put before them, too close: off
            off = off | (mine & theirs.dilate(Interval(-Pos(self.gap + self.at_least), ZERO, True, True)) & after)
        rest = pres - off
        source = state.line.presence(self.after) - (off if self.after == self.kind else Region.empty())
        off = off | (rest & self.apart(source) & after)
        return [Op.take(self.kind, off)] if not off.is_empty() else []

    def __str__(self) -> str:
        if self.after == self.kind and not self.at_least:
            return f"keep {self.kind} at least {_fmt(self.gap)} apart"
        return f"keep {self.kind} at least {_fmt(self.gap)} after {_fmt(self.at_least)} or more of {self.after}"


class AsEarlyAsPossible:
    """`kind` as early as the rules and the other constraints allow. With `length`: the
    generator places it by itself, no target needed, each piece `length` long, the first
    as early as allowed after the chip, then the next, as many as allowed: what the element
    rules forbid of it and what the other constraints keep it from (MinimumGap: none
    within a gap after a long enough interval of a kind, its own pieces included when they
    are of that kind or come with it). Past what can stop it, one piece every `length` and
    the gaps. A piece the chip is in, begun before it, stays whole. E.g. a break of 15
    minutes from the chip on, none within 2 hours after 15 minutes of "no screen": a break
    every 2h15. Without `length`: in the turns of kinds that exclude each other, it comes
    first (ratio_plan), before the kind with the largest target. Any kind: nothing in it is
    about breaks."""

    explicit = True  # its edits are written in the script (generator.constraint_events)

    def __init__(self, kind: str, length=None) -> None:
        self.kind = kind
        self.length = None if length is None else Fraction(length)
        if self.length is not None and self.length <= 0:
            raise ValueError("a length > 0")
        self.rules = None  # (bind)
        self.siblings: tuple = ()  # the other constraints of the generator (bind_all)

    def bind(self, rules) -> None:
        self.rules = rules

    def bind_all(self, constraints) -> None:
        self.siblings = tuple(k for k in constraints if k is not self)

    @property
    def cycle(self):  # (generator.search_window)
        return (self.length or 0) + max((g.gap + g.at_least for g in self._gaps()), default=0)

    def kinds(self) -> set:
        return {self.kind} | {g.after for g in self._gaps()}

    def holds_by_itself(self) -> bool:
        return True

    def _gaps(self) -> list:
        return [g for g in self.siblings if isinstance(g, MinimumGap) and g.kind == self.kind]

    def _counts(self, gap) -> bool:
        """Whether its own pieces are intervals of the gap's `after` kind: that kind, or a
        rule "kind → after" (a break comes with "no screen")."""
        if gap.after == self.kind:
            return True
        for r in getattr(self.rules, "element", ()):
            kinds = r.lhs.kinds()
            if kinds == [self.kind] and getattr(r.lhs, "op", None) == "present":
                for it in r.rhs:
                    o = it.options[0]
                    if not it.is_or and not o.negated and o.kind == gap.after and o.where.is_point and o.where.lo == ZERO:
                        return True
        return False

    def place(self, state: LineState, c) -> Region:
        """Where its pieces are after the chip at c."""
        L = self.length
        cp = c if isinstance(c, Pos) else Pos(c)
        after = _after(c)
        mine = state.claims.owned(self.kind, GEN) & after  # (its own pieces ahead: placed again here)
        no = Region.empty()
        if self.rules is not None:
            from problems import forbidden
            vals = {k: ({v: r - mine for v, r in d.items()} if k == self.kind else d) for k, d in state.line.vals.items()}
            no = forbidden(_Shim(self.rules, state), self.kind, vals)
        gaps = self._gaps()
        counted = [g for g in gaps if self._counts(g) and L >= g.at_least]
        for g in gaps:
            no = no | g.apart(state.line.presence(g.after) - mine)
        no = no & after
        # a piece the chip is in, begun before it: whole
        placed = []
        now = state.line.presence(self.kind) - mine
        for iv in (state.line.presence(self.kind)).on(cp - L - 1, cp + L + 1).intervals:
            if iv.lo < cp < iv.hi + EPS and iv.lo + L > cp:
                placed.append((iv.lo, iv.lo + L))
        stop = Region.empty() if no.is_bounded() else None
        end = (no.bounded().sup() if not no.is_empty() else cp) if stop is not None else no.hi  # past it: the pattern
        end = max(end, cp)
        t = (placed[-1][1] + EPS) if placed else cp + EPS
        dyn = Region.empty()
        for a, b in placed:
            for g in counted:
                dyn = dyn | Region.of(Interval._mk(b + EPS, b + g.gap))
        while t <= end:
            checkpoint()
            block = (no | dyn) & Region.of(Interval._mk(t, t + L))
            if block.is_empty():
                placed.append((t, t + L))
                for g in counted:
                    dyn = dyn | Region.of(Interval._mk(t + L + EPS, t + L + g.gap))
                t = t + L + EPS
            else:
                first = block.on(t, t + L + 1).intervals[0]
                t = first.hi + EPS
        while not (dyn & Region.of(Interval._mk(t, t + L))).is_empty():  # (past `end`: the gaps of the last ones)
            t = (dyn & Region.of(Interval._mk(t, t + L))).on(t, t + L + 1).intervals[0].hi + EPS
        spacing = L + max((g.gap for g in counted), default=Fraction(0))
        starts = [a for a, _ in placed] + [t]
        if spacing != L and all(b - a == Pos(spacing) for a, b in zip(starts, starts[1:])):
            # one every `spacing` from the first: that pattern, written as such (ratio_plan.cycle)
            from ratio_plan import cycle
            s0 = starts[0] - EPS  # (its pieces (s, s + L]: from the position before its first)
            if s0.k == 0:
                return cycle(s0.x, spacing, 0, L) & after
        core = IntervalSet([Interval._mk(a, b) for a, b in placed])
        first = min([a for a, _ in placed] + [t])
        if spacing == L:  # nothing apart: one after the other, everywhere from t
            return (Region.of(core) | Region.of(Interval(t, INF, True, False))) & after
        return Region(first, t, core, ONE, IntervalSet(), Pos(spacing),
                      IntervalSet._sorted([Interval._mk(ZERO, Pos(L))])) & after

    def targets(self, state: LineState, c) -> dict:
        return {}  # (where the chip pushes it: written in the script, generator.constraint_events)

    def spacing(self):
        """From the start of one piece to the start of the next, nothing else in the way."""
        counted = [g for g in self._gaps() if self._counts(g) and self.length >= g.at_least]
        return self.length + max((g.gap for g in counted), default=Fraction(0))

    def pushed_in(self, rules) -> list[str]:
        """The modes in which it cannot be at the chip: a mode rule that rules it out there,
        or a kind it comes with ("mode 1 → no NoScreen", a break coming with "no screen")."""
        out = []
        for r in rules.mode_rules:
            for o in r.lits():
                if o.negated and o.where.is_point and o.where.lo == ZERO and (
                        o.kind == self.kind or self._counts(MinimumGap(self.kind, 1, after=o.kind))):
                    out.append(r.mode)
        return sorted(set(out))

    def ops(self, state: LineState, c: Fraction) -> list[Op]:
        if self.length is None:
            return []  # (an order of the generator's own turns: nothing to make hold on the line)
        want = self.place(state, c)
        now = state.line.presence(self.kind) & _after(c)
        out = []
        if not (want - now).is_empty():
            out.append(Op.put(self.kind, want - now))
        if not (now - want).is_empty():
            out.append(Op.take(self.kind, now - want))
        return out

    def __str__(self) -> str:
        if self.length is None:
            return f"keep {self.kind} as early as possible"
        return f"keep {self.kind} as early as possible, {_fmt(self.length)} each"


class _Shim:
    """What problems.forbidden reads: the rules and a state."""

    def __init__(self, rules, state) -> None:
        self.gen, self.state = type("G", (), {"rules": rules})(), state


class Breaks:
    """Screen breaks of one length (OmniApp's 15-minute breaks, by their rules, but of any
    length):
    - a break lasts `length` and comes with "no screen" (the element rule
      `Break15 → NoScreen` of the line);
    - breaks are everywhere after the chip, each as early as the rules below allow;
    - after `length` or more of "no screen" (a break included), no break starts in the
      next `pause` (it may start exactly `pause` after: one break every length + pause);
    - a break that would start inside a "no screen" period starts at the start of that
      period, or right after the chip if it began before (even against the pause);
    - a break never covers a position where "no screen" or the break is ruled out
      ("no NoScreen", e.g. the chip's positions in mode 1).
    The breaks that started at or before the chip stay as they are (the frozen past).

    Exactly, on the line's intervals: a sweep from the chip places the breaks one after
    the other; past the last "no screen" or ruled-out position ahead, they repeat every
    length + pause.
    """

    def __init__(self, brk: str = "Break15", ns: str = "NoScreen", length=15, pause=120) -> None:
        self.brk, self.ns = brk, ns
        self.length, self.pause = Fraction(length), Fraction(pause)
        self.cycle = self.length + self.pause  # a break and its pause (generator.search_window)

    def __str__(self) -> str:
        return (f"keep {self.brk} on (chip, chip + {_fmt(self.length)}] with {self.ns}, as early as possible, "
                f"none within {_fmt(self.pause)} after as much {self.ns}")

    def kinds(self) -> set:
        return {self.brk, self.ns}

    def holds_by_itself(self) -> bool:
        """Its breaks repeat without end, and an edit runs the generator again."""
        return True

    def breaks(self, state: LineState, c: Fraction) -> Region:
        """The breaks there must be after the chip at c (those going on included)."""
        L, P = self.length, self.pause
        cp = c if isinstance(c, Pos) else Pos(c)
        now = state.line.presence(self.brk)
        # what is known of "no screen": the past as it is, and ahead what is not a break of ours
        others = Region.empty()
        for (v, owner), r in state.claims.pos.get(self.ns, {}).items():
            others = others | r
        known = (state.line.presence(self.ns) & _upto(c)) | (others & _after(c))
        ruled_out = state.claims.negative(self.ns) | state.claims.negative(self.brk) | state.claims.off(self.brk)
        ruled_out = ruled_out & _after(c)
        # the breaks going on at the chip (begun before it: the frozen past) stay whole; one
        # the chip has eaten up to its position (mode 1: no "no screen" there) moves on
        going = [iv for iv in now.on(cp - L - 1, cp + L + 1).intervals if iv.lo < cp < iv.hi]
        # (as long as a break is: the line may be from when breaks were shorter)
        ruled = state.claims.negative(self.ns) | state.claims.negative(self.brk) | state.claims.off(self.brk)
        placed = [(iv.lo, iv.lo + L if iv.hi < iv.lo + L and (Region.of(Interval._mk(iv.hi, iv.lo + L)) & ruled).is_empty() else iv.hi)
                  for iv in going]
        ahead = [r for r in (known & _after(c), ruled_out) if r]
        if any(not r.is_bounded() for r in ahead):  # "no screen" or a ban without end: no break past its start
            end = min(r.hi for r in ahead if not r.is_bounded())
        else:
            end = max([r.bounded().sup() for r in ahead if r.bounded()] + [cp])
        lo = cp - L - P - 1  # what is before it no longer matters to the pauses
        bans = [(iv.lo, iv.hi) for iv in ruled_out.on(cp, end + L + 1).intervals] if ruled_out else []

        def periods(extra):
            """The maximal "no screen" intervals with these breaks: (start, end)."""
            reg = known | Region.of(IntervalSet([Interval._mk(a, b) for a, b in extra])) if extra else known
            return [(iv.lo, iv.hi) for iv in reg.on(lo, end + 2 * (L + P) + 2).intervals]

        def allowed(t):
            """The first start from t that no pause, ban or break already there excludes."""
            changed = True
            while changed:
                checkpoint()
                changed = False
                for a, e in periods(placed):  # (a period with its break in it: no other one in it)
                    if (e - a).x >= L and (e < t < e + P or (a < t <= e and any(a <= pa and pe <= e for pa, pe in placed))):
                        t, changed = e + P, True
                for a, e in bans + placed:
                    if a <= t + L and e >= t:
                        t, changed = e + EPS, True
            return t

        t = allowed(cp + EPS)
        while t <= end:
            checkpoint()
            for a, e in periods([]):  # inside "no screen": the break starts at its start
                if a < t <= e:
                    t = a if a > cp else cp + EPS
                    break
            placed.append((t, t + L))
            t = allowed(t + L + EPS)
        # past `end`, nothing ahead: one break every L + P from t
        core = IntervalSet([Interval._mk(a, b) for a, b in placed])
        first = min([a for a, _ in placed] + [t])
        return Region(first, t, core, ONE, IntervalSet(), Pos(L + P), IntervalSet._sorted([Interval._mk(ZERO, Pos(L))]))

    def targets(self, state: LineState, c) -> dict:
        return {self.brk: self.breaks(state, c) & _after(c)}

    def ops(self, state: LineState, c: Fraction) -> list[Op]:
        want = self.breaks(state, c) & _after(c)
        now = state.line.presence(self.brk) & _after(c)
        out = []
        if not (want - now).is_empty():
            out.append(Op.put(self.brk, want - now))
        if not (now - want).is_empty():
            out.append(Op.take(self.brk, now - want))
        return out


FifteenMinuteBreaks = Breaks  # (its former name, from OmniApp's 15-minute break: breaks of any length)


class ScreenBreaks:
    """Several kinds of screen breaks, each (kind, length, pause), that all come with
    "no screen" (the element rules `Kind → NoScreen` of the line):
    - breaks are everywhere after the chip, one after the other, each as early as the
      rules below allow; two breaks that could start at the same position: the longer;
    - after `length` or more of "no screen" (breaks of any kind and the other "no screen"
      of the line included), no break of that kind starts in the next `pause`;
    - a break never overlaps nor touches another break, nor a position where "no screen"
      or the break is ruled out ("no NoScreen", e.g. the chip's positions in mode 1);
    - a break that would start inside a "no screen" period that has no break yet starts
      at the start of that period, or right after the chip if it began before (even
      against the pause); a period that has a break gets no other one;
    - `chain`: kinds whose continuous chains become one break. When a break of one of
      them would be followed right at its end by a longer one of them, the longer one
      starts in its place (OmniApp: a 5min break then a 15min one is one 15min break).
    The breaks that started at or before the chip stay as they are (the frozen past).

    "No screen" ahead is what the line has without the generated breaks after the chip:
    the user's, and what the rules put from other elements ("sleep" with "no screen").
    It needs the rules (`bind`, done by the generator); unbound, only the claims of "no
    screen" count. Past the last "no screen" or ruled-out position that does not repeat,
    the breaks repeat: the sweep goes on until they do (the period of what repeats ahead
    included).
    """

    MAX_BREAKS = 20000  # placed before the breaks repeat: past it, none further

    def __init__(self, breaks=(("Break20s", 20, 20 * 60), ("Break5", 5 * 60, 60 * 60), ("Break15", 15 * 60, 120 * 60)),
                 ns: str = "NoScreen", chain=("Break5", "Break15"), rules=None) -> None:
        self.kinds_ = tuple((k, Fraction(L), Fraction(P)) for k, L, P in breaks)
        if not self.kinds_ or any(L <= 0 or P < 0 for _, L, P in self.kinds_):
            raise ValueError("breaks: (kind, length > 0, pause >= 0), at least one")
        self.ns = ns
        self.chain = set(chain)
        self.rules = rules
        self.cycle = max(L + P for _, L, P in self.kinds_)  # the longest break and its pause (generator.search_window)

    def bind(self, rules) -> None:
        """The rules of the line: what puts "no screen" besides the breaks."""
        self.rules = rules

    def __str__(self) -> str:
        parts = ", ".join(f"{k} of {_fmt(L)} (none within {_fmt(P)} after as much {self.ns})" for k, L, P in self.kinds_)
        chain = f"; a chain of {', '.join(sorted(self.chain))} is its longest break" if self.chain else ""
        return f"keep {parts} with {self.ns}, as early as possible{chain}"

    def kinds(self) -> set:
        return {k for k, _, _ in self.kinds_} | {self.ns}

    def holds_by_itself(self) -> bool:
        return True

    # ------------------------------------------------------------ what is known

    CACHE = 8  # the last answers kept (`_ahead`, `breaks`): the chip asks again and again

    def __getstate__(self):
        d = dict(self.__dict__)
        d.pop("_cache", None)
        return d

    def _cached(self, name: str, same, key, compute):
        """compute(), or what it gave for a key that `same` finds equal."""
        cache = self.__dict__.setdefault("_cache", {}).setdefault(name, [])
        for k, v in cache:
            if same(k, key):
                return v
        v = compute()
        cache.insert(0, (key, v))
        del cache[self.CACHE:]
        return v

    def _ahead(self, state: LineState, c) -> Region:
        """"No screen" after the chip without the generated breaks there."""
        later = _after(c)
        if self.rules is not None:
            claims = state.claims.copy()
            for k, _, _ in self.kinds_:
                d = claims.pos.get(k, {})
                for key in list(d):
                    if key[1] == GEN:
                        d[key] = d[key] - later
                        if not d[key]:
                            del d[key]

            def line():
                try:
                    return solve(self.rules, claims.problem()).presence(self.ns)
                except Exception:  # (the rules refuse the line without them: the claims only)
                    return None
            r = self._cached("ahead", lambda a, b: a[0] is b[0] and a[1].same(b[1]), (self.rules, claims), line)
            if r is not None:
                return r & later
        return state.claims.positive(self.ns) & later

    @staticmethod
    def _around(r: Region, a, b) -> list:
        """The intervals of r that meet [a, b), with their true ends (the window grows
        while one is cut by it; at most 64 times)."""
        span = max(b - a, Pos(1))
        for _ in range(64):
            ivs = r.on(a, b).intervals
            cut_lo = ivs and ivs[0].lo <= a and r.contains(a - EPS)
            cut_hi = ivs and ivs[-1].hi >= b - EPS and r.contains(b)
            if not cut_lo and not cut_hi:
                break
            span = span * 2
            a, b = (a - span if cut_lo else a), (b + span if cut_hi else b)
        return [(iv.lo, iv.hi) for iv in ivs]

    # ------------------------------------------------------------ the sweep

    def breaks(self, state: LineState, c) -> dict:
        """{kind: the breaks of that kind there must be after the chip at c (those going
        on included)}."""
        K = self.kinds_
        cp = c if isinstance(c, Pos) else Pos(c)
        claims = state.claims
        known = (state.line.presence(self.ns) & _upto(c)) | self._ahead(state, c)
        ruled = [(claims.negative(self.ns) | claims.negative(k) | claims.off(k)) & _after(c) for k, _, _ in K]

        # the breaks going on at the chip (begun before it: the frozen past) stay whole;
        # one the chip has eaten up to its position (mode 1) moves on
        placed = []  # (index of the kind, start, end), by start
        for i, (k, L, _) in enumerate(K):
            ruled_i = claims.negative(self.ns) | claims.negative(k) | claims.off(k)
            for iv in state.line.presence(k).on(cp - L - 1, cp + L + 1).intervals:
                if iv.lo < cp < iv.hi:
                    longer = iv.hi < iv.lo + L and (Region.of(Interval._mk(iv.hi, iv.lo + L)) & ruled_i).is_empty()
                    placed.append((i, iv.lo, iv.lo + L if longer else iv.hi))
        placed.sort(key=lambda b: b[1])
        key = (cp, known, ruled, list(placed))
        return self._cached("breaks", lambda a, b: a[0] == b[0] and a[3] == b[3] and a[1] == b[1] and a[2] == b[2],
                            key, lambda: self._sweep(cp, known, ruled, placed))

    def _sweep(self, cp: Pos, known: Region, ruled: list, placed: list) -> dict:
        """`breaks` from what it reads: the breaks after cp, from those going on there."""
        K = self.kinds_
        c = cp
        maxL = max(L for _, L, _ in K)
        W = max(L + P for _, L, P in K) + maxL + 1

        # past H0, what is known and ruled out repeats every p (or is constant); a tail
        # that covers everything ends the breaks at `stop`
        H0, p, stop = cp, None, None
        for r in [known & _after(c)] + ruled:
            if not r:
                continue
            H0 = max(H0, r.hi)
            if not r.right:
                continue
            if r.right == IntervalSet._sorted([Interval._mk(ZERO, r.pr - EPS)]):
                ivs = r.on(min(r.lo, cp), r.hi + 1).intervals
                end = ivs[-1].lo if ivs and ivs[-1].hi >= r.hi - EPS else r.hi
                stop = end if stop is None else min(stop, end)
            else:
                p = r.pr if p is None else plcm(p, r.pr)
        Wc = W + (p if p is not None else 0)

        # what the sweep reads again and again, near where it is: the breaks placed (by
        # start; they never overlap, so scanning back from the last one stops early) and
        # the whole intervals of known (read once per stretch, then looked up)
        longest = [max([pe - pa for _, pa, pe in placed], default=ZERO)]

        def recent(bound):
            """The breaks placed that end at or after bound."""
            out = []
            for b in reversed(placed):
                if b[2] >= bound:
                    out.append(b)
                elif b[1] + longest[0] < bound:
                    break
            out.reverse()
            return out

        kn = {"lo": None, "hi": None, "ivs": []}

        def known_near(a, b):
            """The whole intervals of known that meet [a, b) (_around, looked up)."""
            if kn["lo"] is None or a < kn["lo"] or b > kn["hi"]:
                lo = a if kn["lo"] is None else min(a, kn["lo"])
                hi = b if kn["hi"] is None else max(b, kn["hi"])
                ahead = hi + 4 * W  # (read ahead a little: the sweep goes right; not past where
                if stop is not None:  # the breaks stop, where "no screen" may go on forever)
                    ahead = min(ahead, max(stop + W, hi))
                hi = max(hi, ahead)
                kn["lo"], kn["hi"], kn["ivs"] = lo, hi, self._around(known, lo, hi)
            return [(x, y) for x, y in kn["ivs"] if x < b and y >= a]

        def periods(a, b):
            """The maximal "no screen" periods near [a, b) with the breaks placed. Made of the
            intervals near the window only: known's whole intervals that meet the window
            and the breaks (two intervals of known never touch, so a period that meets
            [a, b) is all there); `known | breaks` on the whole region gives the same, slower."""
            mine = [Interval._mk(pa, pe) for _, pa, pe in recent(a - W)]
            if not mine:
                return known_near(a, b)
            lo = min(a, mine[0].lo) - 1
            hi = max(b, max(iv.hi for iv in mine)) + 1
            near = IntervalSet([Interval._mk(x, y) for x, y in known_near(lo, hi)]) | IntervalSet(mine)
            return [(iv.lo, iv.hi) for iv in near.intervals if iv.lo < b and iv.hi >= a]

        def allowed(i, t):
            """The first start from t that no pause, ban or break already there excludes."""
            _, L, P = K[i]
            changed = True
            while changed:
                checkpoint()
                changed = False
                for a, e in periods(t - W, t + L + 1):
                    long = (e - a).x >= L
                    if long and e < t < e + P:
                        t, changed = e + P, True
                    elif a < t <= e and any(a <= pa and pe <= e for _, pa, pe in recent(a)):
                        t, changed = (e + P if long else e + EPS), True
                bans = ruled[i].on(t, t + L + W).intervals if ruled[i] else []
                for a, e in [(iv.lo, iv.hi) for iv in bans] + [(pa, pe) for _, pa, pe in recent(t)]:
                    if a <= t + L and e >= t:
                        t, changed = e + EPS, True
            return t

        def moved(t):
            """Inside a "no screen" period: its start (or right after the chip)."""
            for a, e in known_near(t - 1, t + 1):
                if a < t <= e:
                    return a if a > cp else cp + EPS
            return t

        frm = cp + EPS
        seen = {}
        tail = None
        while len(placed) < self.MAX_BREAKS:
            starts = [allowed(i, frm) for i in range(len(K))]
            s, _, i = min((moved(t), -K[i][1], i) for i, t in enumerate(starts))
            if stop is not None and s > stop:
                break
            if K[i][0] in self.chain:
                for j in sorted((j for j in range(len(K)) if K[j][0] in self.chain and K[j][1] > K[i][1]),
                                key=lambda j: -K[j][1]):
                    if starts[j] <= s + K[i][1] + EPS:
                        i = j
                        break
            if stop is None and s >= H0 + Wc:  # what follows depends on what is near only
                ph = (s - H0) % p if p is not None else ZERO
                sig = ((ph.x, ph.k), tuple((j, (a - s).x, (a - s).k, (e - s).x, (e - s).k)
                                            for j, a, e in recent(s - Wc)))
                if sig in seen:
                    tail = seen[sig]
                    break
                seen[sig] = len(placed)
            placed.append((i, s, s + K[i][1]))
            longest[0] = max(longest[0], K[i][1])
            frm = s + EPS

        out = {}
        if tail is None:  # nothing repeats past the last break
            for i, (k, _, _) in enumerate(K):
                out[k] = Region.of(IntervalSet([Interval._mk(a, e) for j, a, e in placed if j == i]))
            return out
        a0 = placed[tail][1]
        period = s - a0
        first = min([a for _, a, _ in placed[:tail]] + [a0])
        for i, (k, _, _) in enumerate(K):
            core = IntervalSet([Interval._mk(a, e) for j, a, e in placed[:tail] if j == i])
            pattern = IntervalSet([Interval._mk(a - a0, e - a0) for j, a, e in placed[tail:] if j == i])
            out[k] = Region(first, a0, core, ONE, IntervalSet(), period, pattern)
        return out

    def targets(self, state: LineState, c) -> dict:
        return {k: r & _after(c) for k, r in self.breaks(state, c).items()}

    def ops(self, state: LineState, c: Fraction) -> list[Op]:
        out = []
        for k, want in self.targets(state, c).items():
            now = state.line.presence(k) & _after(c)
            if not (want - now).is_empty():
                out.append(Op.put(k, want - now))
            if not (now - want).is_empty():
                out.append(Op.take(k, now - want))
        return out
