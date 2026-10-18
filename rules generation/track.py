"""The track: elements placed by rules texts on a reverse-logic line.  See SEMANTICS.md."""
from __future__ import annotations

import copy
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from types import MappingProxyType

from chip import Chip, ChipState, Mode, Number
from region import INF, num
from reverse_logic import Add, LineSystem, Region

Track = Mapping[str, Region]
# rules(track, chip position, chip mode) -> elements (kind, region) to append
RulesText = Callable[[Track, Fraction, Mode], Iterable[tuple[str, Region]]]


def at_or_behind(position: Number) -> Region:
    """(-inf, position]: the part of the track the chip has reached."""
    return Region(((-INF, False, num(position), True),))


def restrict(track: Track, region: Region) -> dict[str, Region]:
    out = {k: r & region for k, r in track.items()}
    return {k: r for k, r in out.items() if r}


@dataclass(frozen=True)
class Text:
    id: str
    rules: RulesText


@dataclass(frozen=True)
class PlacedElement:
    edit_id: int
    text_id: str
    kind: str
    region: Region


class TrackManager:
    def __init__(self, modes: Iterable[Mode] = (1, 2), initial_mode: Mode = 1,
                 initial_position: Number = 0, min_transition_gap: Number = 1,
                 initial_elements: Iterable[tuple[str, Region]] = ()):
        self.chip = Chip(modes, initial_mode, initial_position, min_transition_gap)
        self.line = LineSystem()  # no rules between kinds: see SEMANTICS.md
        # Elements the track starts with were not placed by a text, so they
        # are never discarded.
        initial = [Add(*self._check_element(e)) for e in initial_elements]
        if initial:
            self.line.apply(initial)
        self.text: Text | None = None
        self.placed: list[PlacedElement] = []  # active text-placed elements, oldest first

    # ---- queries ----------------------------------------------------------
    @property
    def current_text_id(self) -> str | None:
        return self.text.id if self.text else None

    def track(self) -> Track:
        return MappingProxyType(self.line.state())

    def unreached(self) -> list[PlacedElement]:
        behind = at_or_behind(self.chip.position)
        return [e for e in self.placed if not (e.region & behind)]

    # ---- operations -------------------------------------------------------
    def update_chip(self, new_position: Number, new_mode: Mode | None = None):
        """Advance the chip (and possibly switch modes), then apply the active rules."""
        self._transact(self.chip.next_state(new_position, new_mode), self.text, discard=[])

    def switch_rules_text(self, new_text_id: str, rules: RulesText):
        """Discard every unreached text-placed element, then apply the new rules."""
        self._transact(self.chip.state, Text(new_text_id, rules), discard=self.unreached())

    # ---- internals --------------------------------------------------------
    @staticmethod
    def _check_element(element) -> tuple[str, Region]:
        kind, region = element
        if not isinstance(kind, str):
            raise TypeError(f"element kind must be a str, got {kind!r}")
        if not isinstance(region, Region) or not region:
            raise ValueError(f"element {kind!r} needs a non-empty Region, got {region!r}")
        return kind, region

    def _transact(self, state: ChipState, text: Text | None, discard: list[PlacedElement]):
        """Discard, then evaluate the text's rules at `state` and append their elements.

        All of it happens on a copy of the line that replaces the real one only
        once everything succeeded, so a failure changes nothing."""
        line = copy.deepcopy(self.line)
        for e in discard:
            line.reverse(e.edit_id)
        behind = at_or_behind(state.position)
        added = []
        if text is not None:
            new = []
            for element in text.rules(MappingProxyType(line.state()), state.position, state.mode):
                kind, region = self._check_element(element)
                if region & behind:
                    raise ValueError(f"element {kind!r} on {region} is not strictly ahead "
                                     f"of the chip at {state.position}")
                new.append((kind, region))
            for kind, region in new:
                added.append(PlacedElement(line.apply([Add(kind, region)]), text.id, kind, region))
        if restrict(self.line.state(), behind) != restrict(line.state(), behind):
            raise AssertionError(f"the track changed behind the chip at {state.position}")

        gone = {e.edit_id for e in discard}
        self.line = line
        self.placed = [e for e in self.placed if e.edit_id not in gone] + added
        self.chip.state = state
        self.text = text

    def print_track(self):
        p = self.chip.position
        print(f"[Chip at {p} | Mode {self.chip.mode} | Text {self.current_text_id}]")
        track = self.line.state()
        if not track:
            print("  Track is empty.")
        behind = at_or_behind(p)
        for kind in sorted(track):
            print(f"  {kind}: {track[kind]}")
        for e in self.placed:
            status = "reached  " if e.region & behind else "unreached"
            print(f"    {status} | {e.kind} on {e.region} | placed by {e.text_id}")
