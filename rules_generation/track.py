"""The track: a reverse-logic line edited by rules texts and by the user.  See SEMANTICS.md."""
from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction
from types import MappingProxyType

from chip import Chip, Mode, Number
from engine import ContradictionError, MissingElementError, put, taken
from region import INF, num
from reverse_logic import Add, LineSystem, Region, Remove

Track = Mapping[str, Region]
Op = object  # an edit operation of reverse logic: Add, Remove, Move, Extend, Retract
# discrete rules: (chip position, mode) -> edits to apply, each an op or a list of ops
RulesText = Callable[[Fraction, Mode], Iterable]
# continuous rules: (chip position, mode) -> elements (kind, region) shown at this position
FollowText = Callable[[Fraction, Mode], Iterable[tuple[str, Region]]]


def _nothing(position, mode):
    return ()


def at_or_behind(position: Number) -> Region:
    """(-inf, position]: the left side of the chip, the chip's position included."""
    return Region(((-INF, False, num(position), True),))


def ahead(position: Number) -> Region:
    """(position, +inf): the right side of the chip."""
    return Region(((num(position), False, INF, False),))


def restrict(track: Track, region: Region) -> dict[str, Region]:
    out = {k: r & region for k, r in track.items()}
    return {k: r for k, r in out.items() if r}


@dataclass(frozen=True)
class Text:
    id: str
    rules: RulesText = _nothing                      # discrete edits
    follow: FollowText = _nothing                    # continuous edits
    transitions: tuple[tuple[Number, Mode], ...] = ()  # mode transitions, right of the chip


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
    _follow: list[tuple[str, Region]] = field(init=False, default_factory=list)  # shown continuous edit

    def __post_init__(self):
        self.chip = Chip(self.modes, self.initial_mode, self.initial_position)
        self.line = LineSystem(list(self.rules))
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
    def update_chip(self, new_position: Number) -> None:
        """Advance the chip, then apply the current text at the new position."""
        def run(m: TrackManager):
            before = m.line.state()
            m._hide_follow()
            m.chip.advance(new_position)
            m._commit_swept(before)
            m._pin_left(before)
            m._apply_text()
        self._transact(run)

    def switch_rules_text(self, text: Text) -> None:
        """Reverse every edit of the previous text (and the user edits that
        require them) without changing the left side of the chip, then apply
        the new text."""
        def run(m: TrackManager):
            before = m.line.state()
            m._hide_follow()
            gone = m.line.reverse_all(list(m.text_edits)) if m.text_edits else []
            for i in gone:
                m.text_edits.pop(i, None)
            m.user_edits = [i for i in m.user_edits if i not in gone]
            m._pin_left(before)
            m.chip.schedule(text.transitions)
            m.text = text
            m._apply_text()
        self._transact(run)

    def user_edit(self, ops) -> int:
        """An edit by the user.  Like the texts, it cannot change the left side of the chip."""
        out = []

        def run(m: TrackManager):
            m._hide_follow()
            out.append(m.line.apply(list(ops), keep=at_or_behind(m.chip.position)))
            m.user_edits.append(out[0])
            m._show_follow()
        self._transact(run)
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

    def _transact(self, run: Callable[[TrackManager], None]) -> None:
        """Run on a copy that replaces this manager only if everything succeeded,
        so that a failure changes nothing."""
        work = copy.deepcopy(self)
        run(work)
        behind = at_or_behind(work.chip.position)
        if restrict(self.line.state(), behind) != restrict(work.line.state(), behind):
            raise AssertionError("the left side of the chip changed")
        self.__dict__.update(work.__dict__)

    def _hide_follow(self) -> None:
        self.line.clear_pending()

    def _show_follow(self) -> None:
        """Show the continuous edit of the current text at the chip's position."""
        self._follow = []
        if self.text is None:
            return
        p, mode = self.chip.position, self.chip.mode
        elements = [self._element(e) for e in self.text.follow(p, mode)]
        for kind, region in elements:
            if region & at_or_behind(p):
                raise ValueError(f"continuous edit of text {self.text.id!r}: {kind!r} on {region} "
                                 f"is not strictly to the right of the chip at {p}")
        if not elements:
            return
        ops = [Add(k, r) for k, r in elements]
        try:
            self.line.set_pending(ops, keep=at_or_behind(p))
            self._follow = elements
        except (ContradictionError, MissingElementError) as err:
            self.refused.append(Refused(self.text.id, p, tuple(ops), str(err)))

    def _commit_swept(self, before: Track) -> None:
        """The part of the shown continuous edit that the chip has now reached
        stays on the line, as an edit of its text."""
        behind = at_or_behind(self.chip.position)
        swept = [Add(k, r & behind) for k, r in self._follow if r & behind]
        self._follow = []
        if swept and self.text is not None:
            try:
                self.text_edits[self.line.apply(swept)] = self.text.id
            except (ContradictionError, MissingElementError):
                pass  # _pin_left restores the left side

    def _pin_left(self, before: Track) -> None:
        """If the left side of the chip differs from `before`, pin it back.

        The pin is an internal edit, newest of all, claiming every element of
        `before` on the left side present and everything else there absent.
        The right side adapts to it."""
        behind = at_or_behind(self.chip.position)
        now = self.line.state()
        if restrict(now, behind) == restrict(before, behind):
            return
        kinds = sorted(self.line.ruleset.kinds() | set(before) | set(now))
        ops = []
        for k in kinds:
            present = before.get(k, Region()) & behind
            if present:
                ops.append(Add(k, present))
            if behind - present:
                ops.append(Remove(k, behind - present))
        self.line.force(ops)

    def _apply_text(self) -> None:
        """Apply the current text's discrete edits at the chip's position, then
        show its continuous edit.  Refused edits are skipped and recorded."""
        if self.text is None:
            return
        p, mode = self.chip.position, self.chip.mode
        for edit in self.text.rules(p, mode):
            ops = list(edit) if isinstance(edit, (list, tuple)) else [edit]
            for op in ops:
                if (taken(op) | put(op)) & at_or_behind(p):
                    raise ValueError(f"edit of text {self.text.id!r}: {op} is not strictly "
                                     f"to the right of the chip at {p}")
            try:
                self.text_edits[self.line.apply(ops, keep=at_or_behind(p))] = self.text.id
            except (ContradictionError, MissingElementError) as err:
                self.refused.append(Refused(self.text.id, p, tuple(ops), str(err)))
        self._show_follow()

    def print_track(self) -> None:
        p = self.chip.position
        print(f"[Chip at {p} | Mode {self.chip.mode} | Text {self.current_text_id}]")
        track = self.line.state()
        if not track:
            print("  Track is empty.")
        for kind in sorted(track):
            print(f"  {kind}: {track[kind]}")
