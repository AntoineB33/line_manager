"""Public entry point.  Requirements, semantics and proofs: SEMANTICS.md."""
from __future__ import annotations

import io
import sys
from fractions import Fraction

from chip import Chip, ChipState, Mode
from track import (
    PlacedElement,
    Region,
    RulesText,
    Text,
    Track,
    TrackManager,
    at_or_behind,
    restrict,
)

__all__ = [
    "Chip",
    "ChipState",
    "Mode",
    "PlacedElement",
    "Region",
    "RulesText",
    "Text",
    "Track",
    "TrackManager",
    "at_or_behind",
    "restrict",
]


# ==========================================
# MOCK GENERATIONS & PROOF
# ==========================================

def generated_text_alpha(track: Track, position: Fraction, mode: Mode):
    # Keep three A points ahead of the chip, 5 apart, starting 2 ahead.
    ahead = track.get("A", Region()) - at_or_behind(position)
    if not ahead:
        for i in range(3):
            yield "A", Region.point(position + 2 + 5 * i)


def generated_text_beta(track: Track, position: Fraction, mode: Mode):
    # In mode 2, keep an X interval of length 1 starting 1 ahead.
    if mode == 2 and not (track.get("X", Region()) - at_or_behind(position)):
        yield "X", Region.closed(position + 1, position + 2)


if __name__ == "__main__":
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")  # Region reprs use "∪"

    manager = TrackManager(modes=(1, 2), initial_mode=1, min_transition_gap=1)

    print("--- 1. First Rule Text (Alpha) ---")
    manager.switch_rules_text("Text_Alpha", generated_text_alpha)
    manager.print_track()

    print("\n--- 2. Chip advances to 4 ---")
    manager.update_chip(4)
    manager.print_track()

    print("\n--- 3. Machine Switches Text (Beta), then mode 2 at 4.5 ---")
    manager.switch_rules_text("Text_Beta", generated_text_beta)
    manager.update_chip(4.5, 2)
    manager.print_track()

    print("\n--- 4. Mode transition too soon is refused ---")
    try:
        manager.update_chip(5, 1)
    except ValueError as err:
        print("refused:", err)
