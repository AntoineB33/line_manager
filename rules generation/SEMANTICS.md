# Rules generation: semantics and proofs

Code: `chip.py` (chip state and mode transitions), `track.py` (elements placed
by rules texts on a reverse-logic `LineSystem`), `rules_generation_logic.py`
(public API and the demo). `test_rules_generation.py` checks every item below,
including on random runs.

## Requirements and how each one is enforced

**R1. Continuous track, the chip advances.** Positions are exact rationals
(`fractions.Fraction`, via `region.num`), so there are no rounding errors. A new
position is never smaller than the current one.

**R2. State = (position, mode).** The mode belongs to a finite set of modes that
is fixed when the track is created. An unknown mode is refused.

**R3. Strictly positive distance between consecutive mode transitions.** A gap
that is merely positive still allows a Zeno sequence of transitions (1/2, 3/4,
7/8, ...) that never gets past position 1. That is infinitely frequent
switching. So every gap must be at least `min_transition_gap`, which must itself
be > 0. A bounded stretch of length L then holds at most floor(L / gap) + 1
transitions. The initial mode is not a transition.

**R4. Elements are appended strictly ahead of the chip.** Every appended region
must lie inside (position, +inf). If any element of an evaluation breaks this,
the whole evaluation is rejected and nothing changes.

**R5. Rules depend on (track elements, chip position, chip mode) only.** A rules
text is a function of these that returns the elements to append. It receives a
read-only snapshot and gets no handle on the manager or the chip. Rules are
evaluated after every chip update and right after a text switch, because their
result depends on the position and the mode.

**R6. Switching texts discards unreached elements, using reverse logic.** Every
unreached element placed by a text is discarded, and then the new text's rules
are applied. An element is *unreached* iff its whole region lies in
(position, +inf). An element the chip is on or has passed, even partly, is
reached. Each placed element is its own edit of the `LineSystem`, and
discarding it is `LineSystem.reverse` of that edit. By theorem T4 of
`reverse logic/SEMANTICS.md`, the track is then exactly as if the element had
never been placed. The reversal is never refused: every edit is an `Add` and
the line has no rules, so no edit takes anything or can be contradicted, and
no edit requires another. Elements the track starts with were not placed by a text,
so they are never discarded.

## Invariant: the past is immutable

No operation changes the track on (-inf, position].

*Proof.* The line has no rules between kinds, so every edit is an `Add` and no
contribution is ever cancelled. Every effective claim is therefore a
present-claim, and the line is the union of the active edits' regions.
Appending adds a region inside (position, +inf). Discarding removes edits whose
regions lie inside (position, +inf). Every other active edit keeps its region,
so the union is the same at every point <= position. Moving the chip changes no
edit. ∎

The code also checks this invariant on every operation.

Rules between kinds (for example "A implies B 3 units to the left") are not
supported. With them, discarding an unreached element could remove something
it implied behind the chip, which would break this invariant.

## Atomicity

Every operation runs on a copy of the line, which replaces the real one only
once the whole operation has succeeded. If an operation raises, the chip, the
current text and the track are left exactly as they were.

## Running

```
python rules_generation_logic.py                 # the demo
python -m unittest test_rules_generation         # RULESGEN_SEEDS=300 for a stress run
```
