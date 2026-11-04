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
- FifteenMinuteBreaks: the 15-minute screen breaks of OmniApp
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


class FifteenMinuteBreaks:
    """The 15-minute screen breaks (OmniApp's rules restricted to them):
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

        def periods(a, b):
            """The maximal "no screen" periods near [a, b) with the breaks placed."""
            mine = IntervalSet([Interval._mk(pa, pe) for _, pa, pe in placed if pe >= a - W])
            return self._around(known | Region.of(mine), a, b) if mine else self._around(known, a, b)

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
                    elif a < t <= e and any(a <= pa and pe <= e for _, pa, pe in placed if pe >= a):
                        t, changed = (e + P if long else e + EPS), True
                bans = ruled[i].on(t, t + L + W).intervals if ruled[i] else []
                for a, e in [(iv.lo, iv.hi) for iv in bans] + [(pa, pe) for _, pa, pe in placed if pe >= t]:
                    if a <= t + L and e >= t:
                        t, changed = e + EPS, True
            return t

        def moved(t):
            """Inside a "no screen" period: its start (or right after the chip)."""
            for a, e in self._around(known, t - 1, t + 1):
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
                                            for j, a, e in placed if e >= s - Wc))
                if sig in seen:
                    tail = seen[sig]
                    break
                seen[sig] = len(placed)
            placed.append((i, s, s + K[i][1]))
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
