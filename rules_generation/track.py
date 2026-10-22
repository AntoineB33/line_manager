"""The track: a reverse-logic line edited by rules texts and by the user.  See SEMANTICS.md."""
from __future__ import annotations

import copy
import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from types import MappingProxyType

from chip import Chip, Mode, Number
from engine import ContradictionError, MissingElementError, _gcd, _rule_atoms, put, taken
from region import EMPTY, FULL, INF, lcm, num
from reverse_logic import Add, LineSystem, Region, Remove

Track = Mapping[str, Region]
# A text's rules are parameterized by (chip position, mode, state of the line); the
# state is the line without the text's continuous edit.
# discrete rules: -> edits to apply, each an op or a list of ops
RulesText = Callable[[Fraction, Mode, Track], Iterable]
# whether the text asks the machine for a new one
RerunText = Callable[[Fraction, Mode, Track], bool]


def _nothing(position, mode, state):
    return ()


def _never(position, mode, state):
    return False


def at_or_behind(position: Number) -> Region:
    """(-inf, position]: the left side of the chip, the chip's position included."""
    return Region(((-INF, False, num(position), True),))


def ahead(position: Number) -> Region:
    """(position, +inf): the right side of the chip."""
    return Region(((num(position), False, INF, False),))


def restrict(track: Track, region: Region) -> dict[str, Region]:
    out = {k: r & region for k, r in track.items()}
    return {k: r for k, r in out.items() if r}


def _just_right_in(region: Region, x: Fraction) -> bool:
    """Whether (x, x + e) lies in `region` for some e > 0."""
    return any(lo <= x < hi for lo, _, hi, _ in region.within(x, x + 1).parts)


def _modes(modes) -> frozenset | None:
    return None if modes is None else frozenset(modes)


@dataclass(frozen=True)
class Rel:
    """The position `offset` from the chip: an end that moves with the chip."""
    offset: Fraction = Fraction(0)

    def __post_init__(self):
        object.__setattr__(self, "offset", num(self.offset))


@dataclass(frozen=True)
class Follow:
    """A continuous edit: `kind` from `lo` to `hi`, each a fixed position or a
    `Rel` (moving with the chip), wherever the chip is in `where` and in one of
    `modes` (any mode if None).  Only its part strictly right of the chip is
    shown: a text never changes the chip's position."""
    kind: str
    lo: Rel | Fraction
    hi: Rel | Fraction
    lo_closed: bool = True
    hi_closed: bool = True
    where: Region = FULL
    modes: frozenset | None = None

    def __post_init__(self):
        if not isinstance(self.kind, str):
            raise TypeError(f"element kind must be a str, got {self.kind!r}")
        if not isinstance(self.where, Region):
            raise TypeError(f"`where` must be a Region, got {self.where!r}")
        for name in ("lo", "hi"):
            end = getattr(self, name)
            object.__setattr__(self, name, end if isinstance(end, Rel) else num(end))
        object.__setattr__(self, "modes", _modes(self.modes))

    def active(self, position: Fraction, mode: Mode) -> bool:
        return position in self.where and (self.modes is None or mode in self.modes)

    def at(self, position: Fraction) -> Region:
        lo, hi = (position + e.offset if isinstance(e, Rel) else e for e in (self.lo, self.hi))
        if lo > hi or (lo == hi and not (self.lo_closed and self.hi_closed)):
            return EMPTY
        return Region.interval(lo, hi, self.lo_closed, self.hi_closed) & ahead(position)

    def moving(self) -> list[Fraction]:
        return [e.offset for e in (self.lo, self.hi) if isinstance(e, Rel)]

    def fixed(self) -> list[Fraction]:
        return [e for e in (self.lo, self.hi) if not isinstance(e, Rel)]


def _end_at(end: Rel | Fraction, position: Fraction) -> Fraction:
    return position + end.offset if isinstance(end, Rel) else end


@dataclass(frozen=True)
class Ratio:
    """A condition on the state: the presence ratio of `kind` on the window from
    `lo` to `hi` (each a fixed position or a `Rel`) is above `threshold` (or
    equal to it, if `at_least`).  It never holds on an empty window."""
    kind: str
    lo: Rel | Fraction
    hi: Rel | Fraction
    threshold: Fraction
    at_least: bool = False

    def __post_init__(self):
        if not isinstance(self.kind, str):
            raise TypeError(f"element kind must be a str, got {self.kind!r}")
        for name in ("lo", "hi"):
            end = getattr(self, name)
            object.__setattr__(self, name, end if isinstance(end, Rel) else num(end))
        object.__setattr__(self, "threshold", num(self.threshold))

    def excess(self, position: Fraction, state: Track) -> Fraction | None:
        """measure(kind on the window) - threshold * its length; None if it is empty."""
        lo, hi = _end_at(self.lo, position), _end_at(self.hi, position)
        if hi <= lo:
            return None
        inside = state.get(self.kind, EMPTY) & Region.closed(lo, hi)
        return sum((b - a for a, _, b, _ in inside.parts), Fraction(0)) - self.threshold * (hi - lo)

    def holds(self, position: Fraction, state: Track) -> bool:
        e = self.excess(position, state)
        return e is not None and (e > 0 or (self.at_least and e == 0))

    def moving(self) -> list[Fraction]:
        return [e.offset for e in (self.lo, self.hi) if isinstance(e, Rel)]

    def fixed(self) -> list[Fraction]:
        return [e for e in (self.lo, self.hi) if not isinstance(e, Rel)]


@dataclass(frozen=True)
class Rule:
    """A discrete rule.  `action(position, mode, state)` gives edits; it runs
    where the chip enters each stretch of positions where it is in `where`, in
    one of `modes` (any mode if None) and, with `when`, where that condition on
    the state holds: at the first position of the stretch, or at its start if
    it is open there; and when the text is installed inside such a stretch.
    If `rerun`, running it asks the machine for a new text."""
    action: RulesText = _nothing
    where: Region = FULL
    modes: frozenset | None = None
    rerun: bool = False
    when: Ratio | None = None

    def __post_init__(self):
        if not isinstance(self.where, Region):
            raise TypeError(f"`where` must be a Region, got {self.where!r}")
        if self.when is not None and not isinstance(self.when, Ratio):
            raise TypeError(f"`when` must be a Ratio, got {self.when!r}")
        object.__setattr__(self, "modes", _modes(self.modes))

    def place(self, position: Fraction, mode: Mode) -> bool:
        """Whether the position and mode parts hold (all but `when`)."""
        return position in self.where and (self.modes is None or mode in self.modes)

    def holds(self, position: Fraction, mode: Mode, state: Track) -> bool:
        return self.place(position, mode) and (self.when is None or self.when.holds(position, state))


@dataclass(frozen=True)
class Text:
    id: str
    rules: tuple[Rule, ...] = ()      # discrete edits
    follow: tuple[Follow, ...] = ()   # continuous edits
    rerun: RerunText = _never         # when to generate a new text

    def __post_init__(self):
        object.__setattr__(self, "rules", tuple(self.rules))
        object.__setattr__(self, "follow", tuple(self.follow))
        for r in self.rules:
            if not isinstance(r, Rule):
                raise TypeError(f"not a Rule: {r!r}")
        for f in self.follow:
            if not isinstance(f, Follow):
                raise TypeError(f"not a Follow: {f!r}")


@dataclass(frozen=True)
class Refused:
    text_id: str
    position: Fraction
    ops: tuple
    reason: str


@dataclass
class TrackManager:
    modes: Iterable[Mode] = (1, 2)
    initial_mode: Mode = 1
    initial_position: Number = 0
    rules: Iterable = ()
    initial_elements: Iterable[tuple[str, Region]] = ()
    chip: Chip = field(init=False)
    line: LineSystem = field(init=False)
    text: Text | None = field(init=False, default=None)
    text_edits: dict[int, str] = field(init=False, default_factory=dict)  # active edit id -> text id
    user_edits: list[int] = field(init=False, default_factory=list)       # active user edit ids
    refused: list[Refused] = field(init=False, default_factory=list)      # skipped text edits
    rerun: bool = field(init=False, default=False)  # the text asks for a new one, as of the last operation
    _asked: bool = field(init=False, default=False)  # a rule with `rerun` ran since the text was installed
    _fired: dict[int, Fraction] = field(init=False, default_factory=dict)  # rule -> where it last ran
    _showing: bool | None = field(init=False, default=None)  # the continuous edit is on the line (None: not yet tried)
    _g: Fraction = field(init=False, default=Fraction(0))  # every rule offset is a multiple of it

    def __post_init__(self):
        self.chip = Chip(self.modes, self.initial_mode, self.initial_position)
        self.line = LineSystem(list(self.rules))
        self._g = _gcd([o for r in self.line.ruleset.rules for _, o in _rule_atoms(r)])
        # Elements the track starts with were not placed by a text, so they
        # are never reversed by a text switch.
        initial = [Add(*self._element(e)) for e in self.initial_elements]
        if initial:
            self.user_edits.append(self.line.apply(initial))

    # ---- queries ----------------------------------------------------------
    @property
    def current_text_id(self) -> str | None:
        return self.text.id if self.text else None

    def track(self) -> Track:
        return MappingProxyType(self.line.state())

    # ---- operations -------------------------------------------------------
    def update_chip(self, new_position: Number, transitions: Iterable[tuple[Number, Mode]] = ()) -> None:
        """The chip moves continuously to `new_position`, through `transitions`,
        the mode transitions (position, mode) it met on the way: after the chip
        and at most at `new_position`.  The text acts at every position on the
        way, exactly (sec. "Moving the chip" of SEMANTICS.md)."""
        self._transact(lambda m: m._advance(new_position, transitions))

    def switch_rules_text(self, text: Text) -> None:
        """Reverse every edit of the previous text (and the user edits that
        require them) without changing the left side of the chip, then apply
        the new text."""
        if not isinstance(text, Text):
            raise TypeError(f"not a Text: {text!r}")

        def run(m: TrackManager):
            before = m.line.state()
            m._hide()
            gone = m.line.reverse_all(list(m.text_edits)) if m.text_edits else []
            for i in gone:
                m.text_edits.pop(i, None)
            m.user_edits = [i for i in m.user_edits if i not in gone]
            m._pin_left(before)
            m.text, m._asked, m._showing, m._fired = text, False, None, {}
            m._fire(m.chip.position)
            m._ask_rerun()
            m._show()
        self._transact(run)

    def user_edit(self, ops) -> int:
        """An edit by the user.  It may change the left side of the chip: only the
        user's edits do."""
        out = []

        def run(m: TrackManager):
            m._hide()
            out.append(m.line.apply(list(ops)))
            m.user_edits.append(out[0])
            m._ask_rerun()
            m._show()
        self._transact(run, keep_left=False)
        return out[0]

    # ---- internals --------------------------------------------------------
    @staticmethod
    def _element(element) -> tuple[str, Region]:
        kind, region = element
        if not isinstance(kind, str):
            raise TypeError(f"element kind must be a str, got {kind!r}")
        if not isinstance(region, Region) or not region:
            raise ValueError(f"element {kind!r} needs a non-empty Region, got {region!r}")
        return kind, region

    def _transact(self, run: Callable[[TrackManager], None], keep_left: bool = True) -> None:
        """Run on a copy that replaces this manager only if everything succeeded,
        so that a failure changes nothing."""
        work = copy.deepcopy(self)
        run(work)
        if keep_left:
            behind = at_or_behind(self.chip.position)
            if restrict(self.line.state(), behind) != restrict(work.line.state(), behind):
                raise AssertionError("the left side of the chip changed")
        self.__dict__.update(work.__dict__)

    def _state(self) -> Track:
        """The state of the line given to a text: without its continuous edit, which is hidden then."""
        return MappingProxyType(self.line.state())

    def _hide(self) -> None:
        self.line.clear_pending()

    def _shown(self, position: Fraction, mode: Mode) -> list:
        """The continuous edit of the text at a chip position: one Add per kind."""
        if self.text is None:
            return []
        regions: dict[str, Region] = {}
        for f in self.text.follow:
            if f.active(position, mode):
                regions[f.kind] = regions.get(f.kind, EMPTY) | f.at(position)
        return [Add(k, r) for k, r in sorted(regions.items()) if r]

    def _show(self) -> None:
        """Show the continuous edit at the chip's position, as the pending edit."""
        p = self.chip.position
        ops = self._shown(p, self.chip.mode)
        if not ops:
            return
        try:
            self.line.set_pending(ops, keep=at_or_behind(p))
            self._showing = True
        except (ContradictionError, MissingElementError) as err:
            self._refuse(p, ops, err)

    def _refuse(self, position: Fraction, ops, err) -> None:
        """Record a refused continuous edit where it stops being shown."""
        if self._showing is not False:
            assert self.text is not None
            self.refused.append(Refused(self.text.id, position, tuple(ops), str(err)))
        self._showing = False

    def _ask_rerun(self) -> None:
        self.rerun = self.text is not None and (self._asked or bool(
            self.text.rerun(self.chip.position, self.chip.mode, self._state())))

    def _run(self, i: int, p: Fraction) -> None:
        """Run rule i at chip position p.  Refused edits are skipped and recorded."""
        assert self.text is not None
        rule, mode = self.text.rules[i], self.chip.mode_at(p)
        self._fired[i] = p
        if rule.rerun:
            self._asked = True
        for edit in list(rule.action(p, mode, self._state())):
            ops = list(edit) if isinstance(edit, (list, tuple)) else [edit]
            for op in ops:
                if (taken(op) | put(op)) & at_or_behind(p):
                    raise ValueError(f"edit of text {self.text.id!r}: {op} is not strictly "
                                     f"to the right of the chip at {p}")
            try:
                self.text_edits[self.line.apply(ops, keep=at_or_behind(p))] = self.text.id
            except (ContradictionError, MissingElementError) as err:
                self.refused.append(Refused(self.text.id, p, tuple(ops), str(err)))

    def _fire(self, p: Fraction, before: dict[int, bool] | None = None) -> None:
        """Run, in order, the rules that hold at p but not just before it (with
        `before` None, at an install: every rule that holds at p)."""
        if self.text is None:
            return
        mode = self.chip.mode_at(p)
        for i, rule in enumerate(self.text.rules):
            if self._fired.get(i) != p and rule.holds(p, mode, self._state()) \
                    and (before is None or not before[i]):
                self._run(i, p)

    # ---- moving the chip --------------------------------------------------
    def _advance(self, new_position: Number, transitions) -> None:
        q, new = self.chip.check(new_position, transitions)
        self._hide()
        self.chip.transitions += new
        pos = self.chip.position
        while pos < q:
            nxt = self._next(pos, q)
            mode = self.chip.mode_at((pos + nxt) / 2)
            gap, end = self._sweep(pos, nxt, mode)
            truths = self._truths(pos, nxt, mode, gap)
            # Rules whose stretch starts just after pos, open there: they run as
            # the chip enters it, still at pos; then the gap is computed again.
            here, state = self.chip.mode_at(pos), self._state()
            rules = self.text.rules if self.text is not None else ()
            entering = [i for i, (right, _, _) in enumerate(truths)
                        if right and self._fired.get(i) != pos and not rules[i].holds(pos, here, state)]
            if entering:
                for i in entering:
                    self._run(i, pos)
                continue
            cut = min((r for _, _, roots in truths for r in roots if pos < r < nxt), default=None)
            if cut is not None:  # a condition changes inside the gap: stop there
                nxt, end = cut, set(gap)
            self._freeze(pos, nxt, gap, end)
            self.chip.position = nxt
            self._fire(nxt, {i: left(nxt) for i, (_, left, _) in enumerate(truths)})
            pos = nxt
        self._ask_rerun()
        self._show()

    def _truths(self, pos: Fraction, nxt: Fraction, mode: Mode, gap: set[str]):
        """For every rule, on the gap (pos, nxt): whether it holds just after pos,
        whether it holds just before a position x of the gap or at its end (a
        function of x), and where inside the gap its condition changes.  The
        excess of a condition is affine on a gap (sec. "Conditions on the state"
        of SEMANTICS.md), so two positions give it exactly."""
        rules = self.text.rules if self.text is not None else ()
        mid = (pos + nxt) / 2
        samples = None
        out = []
        for rule in rules:
            place = rule.place(mid, mode)
            if not place or rule.when is None:
                out.append((place, lambda x, v=place: v, ()))
                continue
            if samples is None:
                ps = (pos + (nxt - pos) / 3, pos + 2 * (nxt - pos) / 3)
                samples = [(p, self.line.preview([self._pin_ops(self._left(pos, p, gap, gap), p)])) for p in ps]
            (p1, s1), (p2, s2) = samples
            e1, e2 = rule.when.excess(p1, s1), rule.when.excess(p2, s2)
            if e1 is None or e2 is None:  # an empty window on the whole gap
                out.append((False, lambda x: False, ()))
                continue
            slope = (e2 - e1) / (p2 - p1)
            ge = rule.when.at_least

            def at(x, e1=e1, p1=p1, slope=slope):
                return e1 + slope * (x - p1)

            def left(x, at=at, slope=slope, ge=ge):
                return at(x) > 0 or (at(x) == 0 and (slope < 0 or (ge and slope == 0)))

            right = at(pos) > 0 or (at(pos) == 0 and (slope > 0 or (ge and slope == 0)))
            out.append((right, left, (p1 - e1 / slope,) if slope else ()))
        return out

    def _offsets(self) -> set[Fraction]:
        """Offsets from the chip of the moving ends of the continuous edits and of
        the windows of conditions."""
        if self.text is None:
            return {Fraction(0)}
        return ({Fraction(0)} | {a for f in self.text.follow for a in f.moving()}
                | {a for r in self.text.rules if r.when is not None for a in r.when.moving()})

    def _next(self, pos: Fraction, q: Fraction) -> Fraction:
        """The next position after `pos` (at most q) where the line may change
        otherwise than continuously: c - a + k*g, for every fixed position c
        (an end of an element or of an edit, of a rule's or a continuous edit's
        positions, of a continuous edit or of a condition's window, a
        transition, 0) and every offset a of a moving end.  Without continuous
        edit or condition, only the fixed positions themselves."""
        consts = {Fraction(0), q} | {p for p, _ in self.chip.transitions}
        continuous = bool(self.text and (self.text.follow or any(r.when for r in self.text.rules)))
        # Elements may repeat forever: their ends near (pos, q] are enough, and
        # with g, the ends in a window as long as every period (all their residues).
        offsets = self._offsets() if continuous else {Fraction(0)}
        margin = max(abs(a) for a in offsets) + 1
        state = self.line.state()
        if continuous and self._g:
            span = self._g
            for r in state.values():
                for t in r.periods():
                    span = lcm(span, t)
            margin += span
        for r in state.values():
            consts.update(r.ends(pos - margin, q + margin))
        for e in self.line._active():
            for op in e.ops:
                for r in (op.region, *((op.target,) if hasattr(op, "target") else ())):
                    consts.update(x for part in r.parts for x in (part[0], part[2]))
        if self.text is not None:
            for rule in self.text.rules:
                consts.update(x for part in rule.where.parts for x in (part[0], part[2]))
                if rule.when is not None:
                    consts.update(rule.when.fixed())
            for f in self.text.follow:
                consts.update(x for part in f.where.parts for x in (part[0], part[2]))
                consts.update(f.fixed())
        g = self._g if continuous else Fraction(0)
        best = q
        for c in consts:
            if math.isinf(c):
                continue
            for a in offsets:
                b = c - a
                cand = b + g * (math.floor((pos - b) / g) + 1) if g else b
                if pos < cand < best:
                    best = cand
        return best

    def _eta(self, pos: Fraction, nxt: Fraction) -> Fraction:
        """A distance smaller than the gap and than every |a + k*g| != 0."""
        cands = [nxt - pos]
        g = self._g
        for a in self._offsets():
            if g:
                r = a % g
                cands += [r, g - r] if r else [g]
            elif a:
                cands.append(abs(a))
        return min(c for c in cands if c > 0) / 2

    def _sweep(self, pos: Fraction, nxt: Fraction, mode: Mode) -> tuple[set[str], set[str]]:
        """The kinds the chip leaves on (pos, nxt) and at nxt: those the line had
        there just before the chip reached them."""
        state = self.line.state()
        p = nxt - self._eta(pos, nxt)
        ops = self._shown(p, mode)
        if ops:
            try:
                st = self.line.trial([], ops)  # the continuous edit shown, if the left side allows it
                gap = {k for k, r in st.items() if _just_right_in(r, p)}
                end = {k for k, r in st.items() if nxt in r}
                # It is shown at every position of the gap iff it is at p, with
                # the left side up to p being what this showing leaves there.
                self.line.trial([self._pin_ops(self._left(pos, p, gap, gap), p)], ops, keep=at_or_behind(p))
                self._showing = True
                return gap, end
            except (ContradictionError, MissingElementError) as err:
                self._refuse(p, ops, err)
        return ({k for k, r in state.items() if _just_right_in(r, p)},
                {k for k, r in state.items() if nxt in r})

    def _left(self, pos: Fraction, upto: Fraction, gap: set[str], end: set[str]) -> dict[str, Region]:
        """The left side of the line up to `upto`: as now up to pos, then `gap`
        on (pos, upto), and `end` at upto."""
        out = restrict(self.line.state(), at_or_behind(pos))
        for k in gap:
            out[k] = out.get(k, EMPTY) | Region.open(pos, upto)
        for k in end:
            out[k] = out.get(k, EMPTY) | Region.point(upto)
        return {k: r for k, r in out.items() if r}

    def _pin_ops(self, left: dict[str, Region], upto: Fraction) -> list:
        """An edit claiming `left` present, and everything else absent, on (-inf, upto]."""
        behind = at_or_behind(upto)
        kinds = sorted(self.line.ruleset.kinds() | set(left) | set(self.line.state()))
        ops = []
        for k in kinds:
            present = left.get(k, EMPTY) & behind
            if present:
                ops.append(Add(k, present))
            if behind - present:
                ops.append(Remove(k, behind - present))
        return ops

    def _freeze(self, pos: Fraction, nxt: Fraction, gap: set[str], end: set[str]) -> None:
        left = self._left(pos, nxt, gap, end)
        if restrict(self.line.state(), at_or_behind(nxt)) != left:
            self.line.force(self._pin_ops(left, nxt), replace=True)

    def _pin_left(self, before: Track) -> None:
        """If the left side of the chip differs from `before`, pin it back.

        The pin is an internal edit, newest of all, claiming every element of
        `before` on the left side present and everything else there absent.
        The right side adapts to it."""
        behind = at_or_behind(self.chip.position)
        if restrict(self.line.state(), behind) != restrict(before, behind):
            self.line.force(self._pin_ops(restrict(before, behind), self.chip.position), replace=True)

    def print_track(self) -> None:
        p = self.chip.position
        print(f"[Chip at {p} | Mode {self.chip.mode} | Text {self.current_text_id}]")
        track = self.line.state()
        if not track:
            print("  Track is empty.")
        for kind in sorted(track):
            print(f"  {kind}: {track[kind]}")
