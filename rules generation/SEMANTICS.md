# Rules generation: semantics and proofs

Code: `chip.py` (chip position and mode transitions), `track.py` (a
reverse-logic `LineSystem` edited by rules texts and by the user),
`rules_generation_logic.py` (public API and the demo).
`test_rules_generation.py` checks every item below, including on random runs.

Requirements: `../requirement.md` and `requirements.md`. Everything about edits,
rules, adaptation and reversal comes from `../reverse logic/SEMANTICS.md`.

## Requirements and how each one is met

**R1. The chip moves right, continuously; it can stop.** Positions are exact
rationals. `update_chip(q)` refuses q < position and accepts q = position. A
program can only observe the chip at a sequence of positions, like a mouse
drag; every observed position is handled (no step is skipped).

**R2. One mode per position, transitions decided by the text.** Each text
gives a finite list of transitions `(position, mode)`, strictly increasing and
strictly to the right of the chip (`Chip.schedule`). The mode at q is the mode
of the last transition at or before q, or the initial mode: the new mode applies
from the transition position on. A new text keeps the transitions that already
happened (at or before the chip) and replaces the others.

**R3. Finitely many transitions in every bounded stretch.** Each text adds a
finite list, and a run installs finitely many texts, so the whole list of
transitions is finite. ∎

**R4. A text's rules edit the right side of the chip, from (position, mode).**
`Text.rules(position, mode)` yields edits (any reverse-logic op). Every region
they take from or put on must lie in (position, +∞); otherwise `ValueError` and
nothing changes. They are evaluated at every chip position and right after the
text is installed. An edit that is refused by reverse logic (it removes
something absent, contradicts the rules, …) is skipped, recorded in
`TrackManager.refused`, and the text keeps running.

**R5. Continuous edits.** `Text.follow(position, mode)` gives elements whose
region depends on the chip's position. They are shown as the pending edit of
the `LineSystem`, recomputed from scratch at every chip position, exactly like
a drag step (T6 of reverse logic). When the chip moves, the part of the shown
elements that the chip has reached stays on the line, as an edit of the text.

**R6. The left side of the chip never changes.** Let B = (−∞, position]. Every
text edit and user edit is applied with `keep=B`: it is refused if the line on
B would change (for example through a rule "C 3 units on the left"). Moving the
chip changes no edit. When something else changes B (a text switch, or a
swept continuous edit that cannot be kept), `_pin_left` adds an internal edit,
newest of all, claiming every element that was on B present and everything
else on B absent; the right side adapts to it.
*The pin is never refused.* It is the newest level, processed with Γ = ∅, and
its claims are those of a line that satisfies the rules (the line before the
operation), restricted to B; that line's own choice of operands is an
assignment without violation, so by Lemma 3.3 of reverse logic nothing of the
pin is dropped, and it holds exactly on B (T2). ∎
Every operation also runs on a copy, which replaces the manager only if the
whole operation succeeded and the left side is unchanged.

**R7. A text switch.** `switch_rules_text(text)`:
1. hides the previous text's continuous edit (it lies strictly to the right of the chip);
2. reverses every edit of the previous text, and every user edit that requires one of
   them, with `LineSystem.reverse_all`; the line becomes the line of the remaining edits,
   that is, as if the reversed edits had never been made (T4 of reverse logic);
3. pins the left side as in R6. So nothing changes on B, the right side is what it would
   be without the previous text's edits, adapted where B requires it (for example, an A
   just left of the chip with "A → B 2 units on the right" keeps its B), and an element
   that straddles the chip is cut there;
4. installs the new text's transitions and applies its rules.

Elements the track starts with, and user edits that require no text edit, are kept.

**R8. Generation time.** It is outside of this system: the new text applies
from the position of the chip when `switch_rules_text` is called.

## Running

```
python rules_generation_logic.py                 # the demo
python -m unittest test_rules_generation         # RULESGEN_SEEDS=300 for a stress run
```
