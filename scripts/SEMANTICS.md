# Line manager: formal semantics

This document maps each part of `../requirement.md` to its formal definition,
the code that implements it, and the test that checks it.

- Run the tests: `python -m unittest discover -s tests` (standard library only).
- Watch the chip move in a window, define rules and edit the line: `python UI/app.py`
  (type `help` in its command box), or run an example script of `UI/examples/`. A script defines the
  rules, the modes, the chip's start and speed, the initial state of the line and
  the generator's problems; copy one to make your own.

All arithmetic is exact. Times, percentages and probabilities are `Fraction`s;
positions are x + kε (below); ±∞ appears only as an open endpoint.

## 1. Positions and sets of the line (`intervals.py`, `region.py`)

- **Positions x + kε.** x is rational, k an integer and ε a positive
  infinitesimal: positions are compared by x, then by k, so
  1 < 1+ε < 1+2ε < 1.0001. A real number x is the position x + 0ε.
  - x + ε is the position right after x. This is what makes "the leftmost
    operand that can be chosen" defined when the positions where an operand fits
    start just after some y but do not include y: the operand goes at y + ε
    (§5). Among the reals alone there is no first number after 1.
  - Lengths are real: ε has none, so presence ratios do not change.
  - Rule distances are real; positions keep their ε part when shifted:
    (1+ε) + 1 = 2+ε.
- **Intervals.** An interval is closed in this order: an open end of a real
  interval is the next position inside, so (a, b] = [a+ε, b] and [a, b) =
  [a, b−ε]. Nothing lies between b and b+ε, so [0, 1] and [1+ε, 2] touch and
  merge into [0, 2]. Infinite ends are open. They are shown as reals when they
  can be: [1+ε, 2] is shown (1, 2], and {1+ε} is a single position.
- **Regions.** A `Region` is a core [lo, hi) plus a periodic tail on each side.
  - It covers every finite union of intervals, whether bounded or not.
  - It also covers the infinite repeating results the requirement allows when an
    element extends infinitely. A period may have an ε part: B at n + nε for
    every n ≥ 0 repeats every 1+ε.
  - It is closed under ∪, ∩, \\, complement and translation, except that two
    tails repeating with periods of different slopes (1+ε and 2) have no common
    period (`IncompatiblePeriods`, which refuses the edit if it ever happens). A
    constant tail goes with any period.
  - Equality is set equality.
- **Tests.** `PositionTest`, `RegionTest` and `test_intervals.py` check these
  operations point by point against brute force, on positions x − ε, x, x + ε.

## 2. The state of the line (`line.py`, `solver.py`)

- **Claims.** A *claim* is an element placed by an edit. It is "K at p%" on a
  region (p ∈ (0, 100]), a "no K" element on a region, or a removal of the user,
  "off K" on a region (K absent there). Each claim has an owner: the user, the
  generated script, or a mode rule.
- **The user's edits are kept.** The state keeps all the user's edits, in order
  (`LineState.history`). The claims are what applies of them now, recomputed at
  each edit of the user (§6), together with the claims of the script and the mode
  rules.
- **The line.** The *line* is computed from the claims. For each kind it maps a
  percentage to a region. These regions are disjoint, so a kind is present at
  most once at each position (`rules_hold` checks this in every test).
- **The requirement's example state** (A on [x₁, x₂) at 100%, A at x₂ at 50%, and
  so on) is accepted. Putting the same kind twice at one position is refused
  (`test_requirement_state_example`).
- **Consistency.** Every state is consistent: where a left side is met, its
  right side is applied. `rules_hold` verifies this independently, with region
  algebra only, on every state of every simulation.

## 3. The chip and the modes (`simulation.Chip`, `modes.py`)

- **The chip.** Its position is a continuous, non-decreasing, piecewise-linear
  function of time, so it never moves left but may stop. Events happen at the
  exact first time the chip reaches a position.
  - The chip is at real positions: at c it is at c + 0ε. It passes c+ε, c+2ε, …
    at the same moment it leaves c, in that order, and c−ε, c−2ε, … just before
    reaching c. So "the left side of the chip and its position" is
    (−∞, c] = all positions up to c + 0ε, and c+ε is on its right.
- **Modes.**
  - Modes are defined on [start, ∞), the positions the chip can be at.
  - A timeline is an initial mode plus a finite list of strictly increasing
    transitions, each of which changes the mode.
  - The mode at x is the mode of the last transition at or before x. A mode
    holds on [t_i, t_{i+1}) = [t_i, t_{i+1} − ε].
  - Consequently exactly one mode applies at each position
    (`test_one_mode_per_position`), and each bounded stretch has finitely many
    transitions.
- **Modes are external.** The generator only receives the timeline up to the
  chip.

## 4. Rules (`rules.py`)

### Element rules

An element rule has the form L → R.

- **Left side L.** A boolean expression on kinds and percentages. Its atoms are:
  - `K`: K present, that is, above 0%;
  - `K p%`: K at exactly p%. `K 0%` means K absent, since an element at 0% is
    absent;
  - `K >= p%`, `K > p%`, `K <= p%`, `K < p%`: an absent K counts as 0%.

  Atoms combine with `not`/`no`, `and`/`&`, `or`/`|` and parentheses. The set
  where L is met is computed exactly with region algebra (`lhs_region`), and
  `test_left_sides` checks it point by point.
- **Right side R.** Any expression of literals with "and" (`&`), "or" and
  parentheses (`test_right_side_expressions`). A literal is one of:
  - `K p%`: K present at exactly p%;
  - `K`: K present (it gets 100% when it is added);
  - `no K` or `K 0%`: K absent.

  "&" separates the items, all applied, and "or" binds tighter: `B or C & D` is
  `(B or C) & D`. Parentheses group anything, and the parentheses of an interval
  (`on (0, 1] of l`) belong to its literal.
  - **"or" (§5).** An "or" takes the leftmost operand that can be chosen. An
    operand can be a conjunction, `(B & C) or D`, or contain other "or"s. The
    operands are tried with the left ones first, so `((B or C) & E) or D` tries
    `B & E`, then `C & E`, then `D`.
  - **Hidden kinds.** An operand `B & C` is a hidden kind `§(B & C)`, placed
    where the operand goes, with the rule `§(B & C) → B & C`. Choosing it places
    B and C, and it can be chosen exactly when both can. Hidden kinds are not
    shown on the line, and they are not counted as lost elements
    (`RightSideTest`).
- **Locations.** Each item has a location relative to an anchor: "at d", "d units
  on the left/right" or "on [a, b)", followed by "of p" or "of l".
  - p is each position where L is met. The literal applies at p + d, or on
    p + [a, b) (`test_intervals_relative_to_p`).
  - l is the left edge of the largest interval where L is met, the one that
    contains p: its first position. The literal applies at l + d, or on
    l + [a, b), once per largest interval. The left edge of (6, 8) is 6+ε.
  - Without "of", a single position is relative to p and an interval to l.
  - On a largest interval that is unbounded on the left, l is −∞ (unbound). Then
    l + d, and l + [a, b] with b finite, are at −∞, which is not on the line, so
    nothing is placed. l + [a, +∞) is the whole line (`test_no_left_edge`).
  - `Region.left_edges` and `Region.dilate` compute these sets exactly, tails
    included. `test_left_edges_dilate_components` checks them point by point.

### Mode rules

`mode m [& L] → R` is a mode rule. It can add or remove elements only at the
chip's position, so every location in R must be "at 0 of p"; any other location
is refused.

### Cycles

Rules may place each other in a cycle, whatever the distances: `A → A 1 unit on the
right`, or `A → B 1% 1 unit on the right of l` with `B → A 10%`.

- **Groups.** `RuleSet.blocks` groups the kinds that are strongly connected through
  "a left side reads K, its rule places K′ present". Each group comes after the
  groups it reads, and a group has a *cycle* if it has more than one kind, or one kind
  that places itself (`test_cycles`).
  - A "no K" places nothing: `B → no B 1 unit on the right` is a constraint and makes
    no cycle.
  - A mode rule acts once, at the chip, on the line as it is then; it is not kept
    true, so it makes no cycle (`mode 1 & not X → D` with `D → X 1 unit on the
    right`).
- **Moving cycles.** A cycle *moves* if one of its literals is not "at 0 of p". What
  it places can then go on without end: A at 0 with `A → A 1 unit on the right` gives
  A at every n ≥ 0.
- **Through what an "or" chooses.** A cycle among chosen kinds goes to the lattices
  only if it does not move and is read as present only. Otherwise one choice would
  act without end, and the sweep decides, with its conditions below.

### Restrictions (`RuleError`)

- **Operands of "or"** are all relative to p or all relative to l. Relative to
  l, one choice is made per largest interval, at its left edge.
- **Chosen kinds.** A kind is *chosen* if it is a present operand of an "or", or
  if a rule whose left side reads a chosen kind places it.
  - When the choices need the sweep (§5), two more conditions apply:
    - A left side reads chosen kinds only as present (`K`, `K p%` with p > 0,
      `K >= p%`, `K > p%`), never under "not" and never as `K < p%` or `K 0%`.
      Then choosing an operand only adds consequences, and a contradiction found
      with the choices on the left stays whatever comes next.
    - A chosen kind that some left side reads is placed at or after the position
      where it is decided: at an offset ≥ 0 from p, or from l. Then what lies
      left of the sweep, its largest intervals and their left edges included,
      never changes again.
  - On lattices, a left side may also read chosen kinds as absent (`not K`,
    `K 0%`), and they may be placed before their decision (`AbsentReadTest`).

  These conditions, with the one on the anchors of an "or", are not in the
  requirement. Without them, the sweep has no exact method for "the leftmost
  operand that can be chosen" (`test_restrictions`).

## 5. Consequences: the "or" rules (`solver.py`)

### Definition

The line of a set of claims is defined as follows:

1. It contains every claim.
2. It is closed under the rules, and contains nothing more: it is the least such
   state, each element placed by the rules from the claims. With a cycle through a
   "not" (or `K < p%`, an exact percentage, a left edge: the position before it must
   be without K), the least state is the well-founded one. If it does not decide every
   element, the rules do not decide the line (`Undetermined`): `X & not B → B` needs B
   exactly where B is absent, and `X & not C → B` with `X & not B → C` has two
   results.
3. Each anchor of an "or" takes an operand ("the leftmost operand that can be
   chosen"). The anchors are decided one after the other, from left to right
   (the line goes from −∞ on the left to ∞ on the right). At one position, an
   "or" whose left side reads what another "or" places comes after it; otherwise
   the rules are in written order.

   Each anchor takes the first operand that still lets all the other anchors be
   placed, given the choices on its left. This is exactly the lexicographically
   smallest feasible choice, from left to right.
4. If no choice works, the claims are in conflict.

Choices are made for every anchor that some choice could meet. The order is
well defined even when an anchor's existence depends on a later choice.

**Why ε is needed.** With `A → B or C`, `B → E` and `B → no E on (0, 1] of l`,
and A on [0, 2]: B goes at 0 (its "no E" covers (0, 1], where nothing needs E).
No B can then be on (0, 1]: it would need E there. After 1, a B fits at any
position, but among the reals there is no first one. Among the positions x + kε
there is: 1+ε. So the result is B and E at 0 and 1+ε, C on (0, 1] and on
[1+2ε, 2] (`test_requirement_question`). On [0, ∞), B goes at n + nε for every n,
repeating every 1+ε (`test_drift`).

**No first position.** On an element unbounded on the left, there is no first
position. If the choices there act on each other (as in `ALTERNATE` below), any
shift of a valid pattern is valid, and nothing decides between them: the edit is
refused (`Undetermined`). Where they do not act on each other, each position
simply takes its first valid operand (`test_unbounded_on_the_left`).

### How it is computed exactly

- **Region algebra.** Kinds that are not chosen are computed with region algebra,
  group after group (`solver.close`). A group without a cycle only reads kinds
  already computed: one step.
- **A group with a cycle** is a fixpoint, reached step after step from nothing.
  - **Only present.** If its left sides read its own kinds only as present, the steps
    only grow, up to the least fixpoint.
  - **Otherwise** a step reads the absences from a fixed state. The least fixpoint with
    the absences read from an overestimate gives an underestimate, and the other way
    round, until both stop changing (the alternating fixpoint). They are equal, or
    `Undetermined`. A left edge is read in the same way: met there, and not met at the
    position before (x − ε).
  - **Without end.** When the steps grow by layers that move by a translation t (over
    up to 4 steps, kinds taking turns), they are followed to the end: the limit adds
    each next layer forever, a repeating tail. This is exact (`_Group._jump`): what is
    read on a half-line near the layer, and what turns a layer into what is read (the
    claims, the overestimate), are the same one step later translated by t, and a step
    only reads positions within the reach of the rules. By induction each next layer
    is placed by the steps. The limit is also checked to be closed.
    `A → A 1/1000 unit on the right` from A on [0, 1] gives [0, +∞) at once.
  - **Long but bounded.** When what is read looks the same translated by t only up
    to some position (`NoScreen & Long → Long on [0, 1] of p` through NoScreen on
    [0, 3000]), the same induction holds for the layers whose reads stay before it:
    the steps jump to the state that many layers later, and go on from there
    (`test_long_but_bounded`).
  - **Rounds without end.** A "not" read one position before what it places (B at
    x + 1 where A is and B is not at x) decides one more position per round. A round
    is a whole fixpoint, not local, so the induction above does not apply to rounds:
    nothing proves that they go on as they started, and they are not followed to an end.
  - Beyond 300 steps or 40 rounds the edit is refused (`Undetermined`).
- **Two solvers for the choices.** When the rules that read chosen kinds read
  them in a conjunction and place single positions relative to p, and the "or"
  operands are present elements at single positions, the choices are solved on
  lattices (below). Otherwise intervals and left edges make every position act on
  the positions near it, and a sweep decides them (`sweep.py`). Both look ahead.
- **Lattices.** In a rule that reads chosen kinds, the rest L' of its left side
  reads no chosen kind. L' is computed first, as a fixed "context" region; for an
  "or" relative to l, the context is the left edges of L'. The left side is then
  "the chosen kinds present, and the context". Every offset of these rules is a
  single position, and a multiple of g, the gcd of the offsets. A rule therefore
  only links points of one lattice r + gℤ (ε parts are kept: r = x + kε).
- **Pieces of residues.** The residues r ∈ [0, g) are cut at 0, at g/2, and at
  every endpoint involved. On each resulting piece, every lattice poses the same
  problem. Each piece is solved once.
  - This is how A on [1, 2] is decided "one position after the other" on a
    continuum.
- **The core.** On a lattice, the points with |x| ≤ T are decided by an exact
  depth-first search from left to right. Pruning is sound because violations are
  monotone in the elements present.
  - When a left side reads a chosen kind as absent, a choice can also remove a
    consequence. Atoms are then true, false or unknown. A missing choice, or an
    anchor out of the window, gives unknown, and only definite violations prune.
    So pruning stays sound without monotonicity. The left ray is then not shown
    independent: an "or" acting on an element unbounded on the left is
    `Undetermined` there.
- **The rays.** Beyond T the problem is translation-invariant.
  - A violation involves at most ρ + 1 consecutive points, so the choices form a
    finite automaton whose states are the last ρ + 1 choices.
  - A state is *good* if an infinite valid path leaves it.
  - The core search accepts a choice only if both of its boundary states are
    good.
  - On the right ray, the choices are then a greedy walk through good states.
    That walk repeats with a finite period, so an infinite element gives a result
    that repeats infinitely.
  - A left ray with choices is decided only if no violation can involve the
    choices of two positions; otherwise `Undetermined`.
- **The sweep.** From left to right over the positions x + kε:
  - at a position m, the "or"s met there take, in order, the leftmost operand
    that does not contradict the rules with everything on their left;
  - the same operands then go on (a run) up to the first position where the
    decision would differ: the operand cannot go on, an earlier operand can be
    chosen again, or an "or" starts or stops being met;
  - given what is decided before a run, the consequences of the run and of a
    choice at m are regions whose ends are fixed or move with m (m + d, d a sum of
    distances along the rules). Whether the rules hold changes only where a moving
    end crosses a fixed one, so the sweep looks only at those positions, e − d + jε
    (and checks a position between two of them, refusing the edit if the check
    fails);
  - beyond the claims everything is invariant by translation, or repeats with a
    period q (an element a cycle repeats: `Sleep → Sleep 1440 units on the right`).
    When the choices of the last stretch as long as the rules reach repeat,
    translated (by a multiple of q, with no ε part, if the data repeats), they
    repeat forever, possibly with an ε part in the period
    (`test_choices_over_a_repeating_element`).

  This greedy sweep is the definition above whenever it never meets a position
  where no operand can be chosen. Each operand it took can be placed, and the
  sweep's own result shows it. An operand it skipped contradicts the rules with
  what is on its left: a choice only adds consequences, and what is on the left of
  the sweep never changes.
- **Looking ahead in the sweep.** If the greedy sweep meets a position where
  nothing can be chosen, an earlier choice must change. The positions are then
  decided one block at a time, from left to right. Each operand is kept only if the
  rest of the line can still be completed, which is asked recursively, the greedy
  sweep first. This is a depth-first search in the order of the definition. A block
  is a position, or all the positions strictly between two positions where a
  choice can change, which then take the same operand.
  - **Real parts.** The rules on chosen kinds move positions by multiples of g,
    the gcd of their distances. Take every change of the result whose real part
    is r mod g, with r not the real part of an end of the claims or fixed regions
    mod g. Shifting all of them by a small real amount keeps every rule satisfied,
    and one of the two directions gives an earlier position an earlier operand.
    That contradicts the definition, so the changes are at real parts e + n·g.
  - **ε parts.** A real distance keeps the ε part of a position, and an open end
    adds ±1. A chain of rules goes through each kind once, so what it places is within
    `RuleSet.eps_reach` of the ε parts of the nearby ends of the claims, fixed regions
    and choices: 1 + the open ends of the rules (at most the kinds + 1), and 2 at
    least. A cycle that moves positions with an open end has no such bound
    (`RuleSet.drifts`): the choices, and the mode rules there, are refused.
  - **What makes it fast.** The search remembers each situation it completed or
    failed: what the choices before P placed and forbade, as far as the rules
    reach from P. A failure also rules out every situation with no less placed
    and forbidden and the same presence just before P, because choices only add
    consequences. Positions where nothing could be chosen are remembered too, and
    an operand that leaves one of them with nothing, whatever comes next, is
    skipped. Beyond a number of steps (3000), the edit is refused rather than
    guessed.
  - **Cycles.** With chosen kinds that place each other in a cycle, the line of
    given choices is a fixpoint (above). If the cycle moves, what a choice places
    goes on without end, to the right only (offsets ≥ 0). The sweep then looks for
    moving ends up to every end of the data, and does not use its probe (which takes
    the past to place nothing far after P). What the past placed after P follows,
    through the rules, from what it placed within their reach of P, so the
    situations it remembers stay exact.

  With A on [0, 2], X at 1/2 and `X → no C`, B must be at 1/2. A B on [0, 1/2)
  would forbid the E it needs there, so the result is C on [0, 1/2), B at 1/2, C
  until the next B at 3/2+ε (`test_looking_ahead`). If nothing can be chosen
  whatever the choices, the claims are in conflict.

### Tests

- The requirement's example: "A → B 100% or C 100%" gives B
  (`test_first_operand`), and "Edits from the user always win": A and B on
  [1, 2], the user removes B at 1.5, which gives B on [1, 1.5) ∪ (1.5, 2] and C at
  1.5 (`test_user_edits_win`).
- The rules "A → B or C", "B → D", "B → no D 1 unit on the right" (`ALTERNATE`)
  allow no B right after a B. With A added on [1, 2], the result is B on [1, 2)
  and C at 2 (`test_alternate`). With A on [0, ∞), B and C alternate on unit
  intervals forever.
- Left to right, and looking ahead on lattices: `test_left_to_right`.
- An "or" relative to l: one choice per largest interval (`test_or_relative_to_l`).
- Comparison with an independent brute-force search, which enumerates every
  assignment in order: `OrTest.test_matches_brute_force` (lattices) and
  `EpsilonTest.test_sweep_matches_brute_force` (the sweep, on single points, half
  the cases needing to look ahead).
- The sweep gives the lattices' result wherever both apply, looking ahead
  included (`test_sweep_matches_the_lattices`).
- On intervals, at positions sampled around every change of the sweep's result
  (x − 2ε … x + 2ε, and between them), the operand taken is the first one that the
  choices on its left allow and that leaves a completion
  (`test_each_position_takes_the_first_operand`).
- Cycles (`CycleTest`):
  - A at 0 and `A → A 1 unit on the right` gives A at every n ≥ 0, forever; with
    `A & X → A 1 unit on the right` it stops one unit after X.
  - The requirement's former example, `A → B 1% 1 unit on the right of l` with
    `B → A 10%`: A at 0 gives A 10% and B 1% at every n ≥ 1, and A on [0, 1] is in
    conflict (B 1% at 1 makes A 10% where A is 100%).
  - Through "not": `not A & C → B` with `B → A 1 unit on the right` is decided; the
    two undecided examples above are refused.
  - Through "or": a cycle at one position on lattices, moving cycles by the sweep.
  - An older edit gives way through a cycle: A on [0, 1/2] repeats every unit, and
    "no A" at 3, newer, removes A at 0 only.
  - `test_matches_brute_force` compares with the well-founded fixpoint computed on
    sets of integers over [−40, 40], on random rules that place each other both
    ways, under "not" too: lines, conflicts and undecided cases.

## 6. The result of an edit (`resolve.py`)

An edit is a list of operations:

- put "K p%" on a region;
- put "no K" on a region;
- take K off a region (K must then be absent there);
- take the "no K" elements off a region.

A later operation of an edit wins over an earlier one where both act.

**The user's edits, newest first.** The line is computed from all the user's edits,
kept in the order they were made. At each edit of the user, the claims are computed
again from all of them:

- The newest edit applies whole. If it has no result alone, it is refused (step 2
  below), and it is not kept.
- Each older edit, from the newest to the oldest, then applies except where it
  contradicts the newer ones as they apply:
  - **directly:** at a position, the newer of "K p%" and "off K" decides K; the
    newer of "no K" and a take of the "no K" elements decides the "no K" elements;
    "K p%" and "no K" contradict each other;
  - **through the rules:** its parts that conflict with the newer ones give way by
    step 3 below, the newer ones being "the edit's own", and the lost elements
    counted on the line of that older edit alone (so the result depends only on
    the edits, not on the line before).
- So an older edit gives way only where, and as long as, it contradicts newer
  ones: when what it contradicted goes, it applies again. With "A and B → C", A and
  B at x, then "no C" at x, B gives way; when "no C" is taken off, B is back
  (`test_comes_back`).
- **A removal is an edit** like an addition (requirement option (a)). With `Y → A`
  and Y at 0, taking A off at 0 makes Y give way; putting A back at 0 brings Y back
  (`test_a_removal_is_an_edit`). A removal also gives way to a newer edit that needs
  the kind (`test_a_removal_gives_way`).
- **"Contradicts newer ones" is taken literally:** only the newer edits count. With
  `X & not A → no B` and the edits A (oldest), X, then B (newest), X gives way: with
  B, it contradicts. A would make X harmless, but A is older
  (`test_contradicts_newer_ones_literally`).
- **The newer edit wins even when that loses more.** "Fewest lost" (step 3) chooses
  among the parts of one edit. Between two edits, the older one gives way: A in an
  older edit than B goes, even where removing B would lose fewer elements
  (`test_many_elements_in_conflict`).
- **The script's and the mode rules' elements** come after every edit of the user:
  they give way to them by step 3, against the line before the edit. They are not
  kept as edits: what gives way of them is gone, and the generator places again
  what it needs at its next run, which every edit of the user triggers
  (`test_the_scripts_elements_are_not_kept`). Their own edits are resolved against
  the claims as they are, by the steps below, and refused if a claim of the user
  would change (N2, §7).

The requirement's examples (`test_requirement_examples`) all come out as written.
`HistoryTest.test_matches_brute_force` compares the result of random histories of
two to four edits with a brute-force fold: each older edit loses a minimal set of
its elements, the fewest lost, the leftmost kept on a tie. A fold from the oldest
first, or one that forgets the older edits, fails it.

The result of one edit against the claims (for the user's, one step of the fold
above) is determined as follows:

1. **Direct effects.** The edit's own operations apply. A put replaces what was
   claimed of that kind on its region.
2. **Refusal.** An edit is refused only when it has no result on its own: it
   contradicts the rules by itself, for example C and D at x with D → no C, or
   the rules do not decide its own result (§5, `Undetermined`). It is never
   refused because of what is on the line: edits from the user win over it.
3. **The edit wins.** The edit's elements are kept. If older elements conflict
   with them, parts of older elements are removed, nothing more than necessary,
   one conflict after the other, from left to right.
   - **What an element is.** An element is a largest interval of one claim
     ("K p%", "no K" or "off K", with one owner). Only the part of it that has to go is
     removed.
   - **A conflict** is two requirements that contradict on a region X: K present
     and "no K", or two percentages of K. Each requirement comes from a claim or
     from a rule r applied on X. The conflict region X is all of the region
     where those two contradict, not one point.
   - **Where a rule stops applying.** A requirement from a rule r gives way when
     r's left side stops being met where r reaches X, and only there:
     - relative to p: on the anchors whose literal reaches X;
     - relative to l: from each left edge whose literal reaches X, up to the
       first position of its interval whose literal does not. That position
       becomes the left edge. With `A → B on [0, 1] of l`, A on [0, 10] and
       "no B" at 1/2, A goes on [0, 1/2]: the left edge becomes 1/2+ε and B is
       on (1/2, 3/2+ε] (`test_consequence_relative_to_l`).
   - **The combinations.** The left side stops being met there when one term or
     more of it stop holding there. That means removing the parts of the
     elements those terms rest on: these are the combinations. A kind that a
     rule places rests on that rule's left side, and so on. Through a cycle this
     comes back to what is already being removed, which then needs nothing more:
     the line is the least one, so what only the cycle places goes with the rest
     (`test_an_older_edit_gives_way_through_a_cycle`). Beyond 400 rules followed
     back, the whole elements are removed instead (below). A term that holds where
     a kind is absent (`not`, `K 0%`, `K < p%`)
     cannot be made false by removing. A requirement from an older claim gives
     way by removing that claim on X ("no K" and "off K" alike). The edit's own
     requirements never give way.
   - **Criteria, in order,** among the combinations that remove the conflict
     from X:
     - Only what is necessary: no combination may remove everything that
       another working combination removes, and more.
     - The combination that loses the fewest elements. A lost element is either
       an element of the line (a largest interval of one kind and percentage)
       that does not stay whole, or a "no K" or "off K" element that loses a part.
     - On a tie, the combination that keeps the leftmost elements: at the
       leftmost position where two combinations differ, the one that keeps the
       element there. Kinds at the same position are ordered by their first
       appearance in the rules.
   - **Conflict by conflict.** The choice is made for each conflict in turn,
     not over all conflicts at once. It tries a few combinations per conflict
     (2^k for a left side of k terms), however many elements there are, so an
     edit with thousands of elements in conflict is resolved in seconds. The
     result is not always the smallest loss over all conflicts together.
   - **Otherwise: whole elements, to keep the computation fast.** The fallback
     removes more than necessary, which the requirement allows for a fast
     computation. It handles four cases:
     - conflicts through the "or" choices, which region algebra does not
       compute;
     - conflicts that no combination removes;
     - lines whose "or" choices the edit leaves undecided or not computed in
       time (`Undetermined`, `test_undecided_with_the_line_gives_way`);
     - more than 4 × (number of older elements) + 16 conflicts one after the
       other.

     The rest of the older elements are removed whole, one after the other,
     those within (depth + 1) × reach + 1 of the edit first, then from left to
     right, until the claims are consistent and decided. The edit alone is
     consistent and decided (step 2), so this ends. Each element removed that way
     is then put back as it was if the claims stay consistent and decided with
     it.
4. **Removed, for this computation only.** For the user's edits, the parts removed
   give way only in this computation: the next edit of the user computes the claims
   again from all the edits.

**Removing parts.** "Nothing more than necessary" removes parts of elements, not
whole elements. In positions x + kε a smallest part exists where the reals have
none. With `A → B on [0, 1] of l`, A on [0, 10] and "no B" at 1/2, removing A on
[0, a) works for every real a > 1/2 but not for a = 1/2, so the reals have no
smallest removal. Among positions there is one: [0, 1/2], since 1/2+ε is the
first position after 1/2.

The requirement's example: with "A and B → C", put A and B at x, then "no C" at
x. Only B and C are removed (`test_requirement_example`). Other cases tested:

- `A → C`, A on [0, 10], "no C" at 5: A only loses the point 5
  (`test_a_point_is_enough`).
- When removing A would lose more elements than removing B, B is removed
  (`test_fewest_lost`). Of B on [2, 12], only the part [5, 10] where C has to go
  is removed (`test_part_of_an_element`).
- Elements are counted, not measured: one long A loses two parts rather than two
  short B going (`test_fewest_elements_not_measure`).
- 200 older elements in conflict at once, and 100 separate conflicts, are
  resolved (`test_many_elements_in_conflict`).

`ResolutionTest.test_matches_brute_force` compares the result with a brute-force
search over every removal set, minimizing the loss over all conflicts together.
On those small random cases, the conflict-by-conflict choice gives the same
result.

## 7. Who modifies the line (`simulation.py`, `guards.py`)

The line is modified only by the user (edits, and the mode) and the generated
script. The script makes two kinds of edits: its own edits, and the edits of the
mode rules, which each run compiles for the modes it predicts (`Script.stretches`).

- **User edits** may change anything, anywhere, including at or left of the chip.
- **Script edits** are refused if their *result*, after the rules have been
  applied, would break either of these:
  - **N1.** It would change the line or the claims on (−∞, chip], that is, the
    left side of the chip or the chip's position (chip + ε is on its right).
  - **N2.** It would remove or change a claim of the user, removals included.
    `resolve.py` already refuses such an edit, and the user's edits always come
    before the script's elements, so a later computation never gives the script
    the upper hand either.
- **Mode rules** act at the chip as it moves, from the start of the program,
  through the script's stretches only. `mode_rules.py`.
  - The chip's position is a single point x. When the chip is at x, the mode
    rules of the mode at x are decided at x, in their order, on the line as it is
    then. Nothing ahead of the chip is decided or written.
  - For each item of a rule whose left side is met at x: the leftmost operand
    that can be chosen. That is the first that already holds at x, or else the
    first whose edit on {x} is accepted: resolved like any edit, and refused if
    its result would break either of these:
    - **M1.** It would change the line or the claims on (−∞, x), or the claims
      outside {x}: a mode rule adds or removes elements only at the chip.
    - **N2.** It would remove or change a claim of the user: edits from the user
      always win.
  - If no operand is accepted, the item does nothing at x.
  - **How it is computed exactly.** From a position m, the same decision goes on
    over [m, x) as x moves right (a run). The ends of the claims and the line are
    then fixed, or move with x as x + d, with d a multiple of g (the gcd of the
    rule distances) and |d| ≤ 2 · depth · reach. With a cycle that moves, what is
    placed at x goes on without end, and d goes as far as the ends of the state
    and a period of its repeating parts (`test_through_a_cycle`: D at the chip
    repeats every 3 units, and the user's "no D" at 13 keeps it from 1, 4, 7 and
    10). The decision at x can only
    change where a moving end crosses a fixed one, at e − d + jε with |j| ≤ 2.
    A run is checked there and at the first, middle and last positions between
    them. A check that fails between them refuses the rest of the chip's
    stretch, rather than guessing. A run is logged as one continuous change:
    each of its positions changes when the chip is there.
  - **Compiled by the generator.** Each run of the generator carries the mode
    rules out ahead, from the line after its own edit, as the chip goes from its
    position to the next run's position through the predicted modes
    (`mode_rules.compile_runs`, `RulesGenerator.predicted`). A run of the mode
    rules (`mode_rules._Plan`) has the positions of the state where it was decided,
    and each stretch between two of them is checked once, to its end. On a stretch
    the line's ends are fixed or move with the chip (above): the first and middle
    checks give which ones move, and the last check must be exactly that line,
    else the positions there are solved one by one. Each run becomes stretches
    (`mode_rules.Stretch`), a shortcut kept with the script: the script's text says
    "if the chip is in [a, b] and mode is m, then apply the mode rules". As the chip
    goes through a stretch, the simulation only reads the line from it: a break
    that the chip drags in mode 1 costs nothing per step
    (`test_compiled_by_the_generator`). Where the line differs from the one compiled,
    the same is computed on the line as it is. Where the modes differ from what
    the text says, the mode rules do nothing, and the script's `Uncovered` event runs
    the generator again (`test_unpredicted_modes`).
  - **Moved scripts.** The rules are the same at every position. A script put in
    place with the chip d after the position its run compiled from (the run took
    that long), on the line the run compiled from moved by d, applies its stretches
    moved by d (`Simulation._align`, `test_late_script_moved`). Any other line,
    for example with a user's element at a fixed position, takes the stretches as
    they are.
  - **Constraints.** A constraint holds whatever a run had time for (a run can be
    cancelled by the next, again and again). So the script of a generator with
    constraints writes them (`keep ...`) and says "elsewhere, apply the mode rules": no
    run is needed. The chip back in mode 1 inside a break cuts it at that very position
    and drags a new one (`test_back_to_mode_1_inside_a_break`).
  - **Progressive runs.** At the start of a run the simulation itself publishes a first
    script (`Simulation.first_script`): the script in place, with the generator's
    constraints and the mode rules in any mode on the 10 after the chip. It needs nothing
    from the run, so it is there at once. The run then publishes its scripts as it finds
    them (`RulesGenerator.stages`, `Simulation.publish`, `Simulation.complete` for the
    last), from the same snapshot: frontiers from c + 10 up to c + H, 1 + ⌈q (H − 10) / 10⌉
    of them, so a lower q has fewer stages and a shorter run. A stage only adds an edit
    between the last frontier and its own, so the lines before a frontier never change,
    whatever the modes: that is "definitive". A run in a process of its own edits nothing
    in the first 10 after c. The pace does not depend on the run: at each 10 s without
    a new frontier, the simulation moves it 10 further with the script in place
    (`Simulation.keep_pace`), and what the run sends later only counts after it
    (`Simulation._fit`). A published script does not undo what happened; if the line went
    away from it, its triggers ask for a new run. The run ends with its last script,
    or when the script in place has reached c + H, and the compiled shortcut comes with
    the last script.
  - **Calculations are instantaneous.** A step lets the time that passed run, however
    long, and a click counts from the position of the chip when it was made, so the
    result of a slow edit is what it would have been at once (`gui._up_to`).
  - **The script is its text.** What the text says is what the script does (script.py):
    the mode rules apply where a line says so, on the line as it is; what a run
    compiled ahead is only a shortcut. So the user can change any line (the file
    "Generated script" opens, saved): `script_text.parse_script` reads it and
    `Simulation.replace_script` puts it in place, or `ScriptError` says which lines are
    not correctly written and the script in place stays. A `keep` line changed is the
    generator's constraint from then on, for the next runs too. The run going on is
    cancelled, or its script would replace the user's; the events that were there stay
    as they were, and the next run replaces the rest.
  - **Edits against the constraints.** A user's edit after which the constraints cannot
    hold (what they ask for would be refused) is refused (`Simulation._against_constraints`).
  - **Stopped.** A step of the simulation, a generator run included, can be
    stopped from another thread (`solver.STOP`, Pause in the window): the long
    loops raise `Interrupted`, and the step is undone (`Simulation.snapshot`,
    `restore`).
  - `ModeRulesTest` checks:
    - nothing changes beyond the chip;
    - the user's elements are kept;
    - no element away from the chip is removed, and the positions that can take
      D still get it;
    - the operand changes inside one stretch when it has to;
    - without the script, the mode rules do nothing.

    `check_log` checks M1 and N2 on every change of the mode rules.

`ScriptTest` checks that scripts are refused for:

- touching the left side, directly or through a consequence;
- removing the user's element;
- changing the user's element;
- pushing the user's element out, which "newest wins" would otherwise do.

`check_log` replays every change of random simulations. For every state it
checks that the rules hold and that the line equals the solve of its claims. For
script changes it also checks N1 and N2.

## 8. The rules generator and its scripts (`generator.py`, `script.py`)

### The script

A run receives the state, the chip position c and the revealed modes, and
returns a script made of events.

**Triggers:**

- `ChipIn(lo, hi, mode)`: fires at each instant the chip enters [lo, hi) with
  that mode.
- `TransitionCount(k)`: fires after k revealed transitions.
- `Start`: the initial script's only trigger: the program starts.
- `Edited`: fires at each edit of the user. A user edit runs the generator only
  through it; a change of rules or problems runs the new generator.
- `Uncovered`: the chip is where the script does not apply the mode rules (§7).
- `PresenceAbove(K, v, y, H, lo, hi, m)`: the ratio of K at v% on (chip, chip + H] is
  above y, with the chip in [lo, hi) and in mode m.
- `ScoreBelow(z)`: the optimization score 1 / (1 + the weighted error on the line as it
  is) is below z. Both are looked at at each instant (a step of the chip, an edit, a
  transition), so one crossed between two instants fires at the next one.

**Actions:**

- `EditAction`: an edit on exact intervals of the real line, resolved as in §6
  and guarded as in §7.
- `ChipEditAction`: an edit written from the chip, moved to the chip's position
  when it fires. A run's constraints (`RulesGenerator(constraints=...)`, e.g.
  `Future on (0, inf)`) are kept this way: the run makes the edit first, scores
  its candidates on top of it, and its script makes it again where it is put in
  place, unless the line already has it (`test_constraints`).
- `RerunAction`: runs the generator again.
- Stretches (`Script.stretches`): the mode rules a run compiled, each acting while
  the chip goes through it in its mode on the line compiled for it (§7).

**Runs that take time.** In the background mode (`Simulation.background`, the
interactive program), a call to run the generator is only recorded
(`Simulation.request`). The script in place goes on, and the run's script replaces
it when the run completes (`Simulation.complete`), unless a newer call was recorded
meanwhile. The window runs each call in a process of its own
(`scripts/generator_worker.py`): it computes in parallel with the chip, and a newer
call ends it (`test_background_runs`).

### What a run optimizes (`problems.py`)

- **Problems and weights.** A generator is given optimization problems, each with
  a weight ≥ 0, and minimizes the weighted sum of their errors. Each problem has a
  horizon H and looks at the window (c, c + H]; the generator plans the window W of
  the longest one. Two problems are implemented:
  - **Presence ratio.** A target r_K ∈ [0, 1] for each kind: the error is the sum of
    |(1/H) ∫ s_K/100 − r_K| over the kinds, with what the mode rules are expected to
    add or remove in the window.
  - **Minimum length.** A minimum length L_K for each kind: ideally each interval of
    K is at least L long. An interval i of length ℓ_i is supported by
    S_i = Σ_j ℓ_j · max(0, 1 − d_ij / L), d_ij its gap to the interval j (0 for i
    itself), and falls short by max(0, L − S_i). The error is the sum of the
    shortfalls of the intervals that meet the window, divided by L. One interval of
    length L: none. Cut by a small hole h: each piece falls short by a bit more
    than h, so making up for it takes more than the hole. A piece further than L
    from every other one needs the full L on its own (`ProblemsTest`). The mode
    rules are not predicted for this one: it reads the line as it is.
- **Candidates.** Candidate edits are built in a fixed order:
  1. the no-op first;
  2. then, for each combination of a prediction of what mode rules will do in W and
     a placement strategy, the edits all the problems propose together, then (with
     several problems) each problem's alone;
  3. then the other edits of each problem (for the minimum length: removing the
     short generated intervals).

  An edit already built is skipped, and does not count in the budget B(q).
  Presence ratio places what is missing (or removes generated elements when there
  is too much). Minimum length grows each interval that falls short by its
  shortfall, on the free positions after the chip: not where the kind is, nor where
  a claim removes or forbids it.
- **Validity.** A candidate is valid if its resolution is accepted and it
  respects N1 and N2.
- **Choice.** The best valid candidate minimizes the weighted sum of the errors,
  expected over the probability finder's scenarios where a problem predicts the
  mode rules. The earliest wins ties, so the no-op wins when nothing is better
  (`test_weights`: the heavier problem decides).
- **Futility.** A run concludes that later runs would do nothing until the user
  edits in two cases:
  - there is nothing to optimize; or
  - the line is empty, there is no mode rule, and the run does nothing.

  In both cases the script has no events at all.

## 9. The allocated resources quantity q ∈ (0, 1)

| quantity | definition | monotonicity |
|---|---|---|
| work of a run | min(B(q), number of candidates), with B(q) = 2 + ⌊36q⌋ | non-decreasing in q |
| rerun distance | D(q) = H(1 − q/2) | strictly decreasing in q |
| rerun transitions | K(q) = 1 + ⌊4(1 − q)⌋ | non-increasing in q |
| compiled ahead | q D(q) / 6 | strictly increasing in q |
| margin of the watches | (1 − q) / 2 | strictly decreasing in q |

With mode rules, a run's text covers them up to c + D(q)/6, it compiles them ahead up
to c + q D(q)/6 (a shortcut: q D(q) grows with q, so a lower q compiles less, and the
chip computes the rest), and it asks for the next run at c + D(q)/18 instead of
c + D(q).

**Per run** (`test_per_run`). The candidate list does not depend on q, and a
run evaluates a prefix of it. So with a lower q:

- the run does less work;
- the solution it finds is no better;
- its first rerun comes no sooner, because the triggers depend only on the
  external chip and modes.

Futility cannot flip the wrong way between two values of q.

**Over a whole run** (`test_whole_runs`). Runs happen at
r_{k+1} = min(e(r_k), the next user edit), where:

- e(t) is the earlier of the first time the chip reaches chip(t) + D and the
  time of the K-th transition after t;
- e is non-decreasing in t and non-increasing in q.

By induction, a lower q never has more script-triggered runs.

**Monotonicity is non-strict for counts.** Counts are integers, so they can only
be non-strictly monotone in q. D is strictly monotone.

## 10. The probability finder (`probability.py`)

- **Counting.** It counts transitions a → b and the segment lengths of each mode
  over completed segments. The first segment is excluded because the chip may
  have started in the middle of it.
- **Probability rules.** It outputs exact rules, for example "after mode 1,
  mode 2 follows with probability 1".
- **Scenarios.** It enumerates scenarios best-first, conditioned on how long the
  current mode has already lasted. Their probabilities plus the residual sum to
  exactly 1.
- **How the generator uses it.** The generator's first candidate uses the most
  probable scenario to predict what the mode rules will add, so even B(q) = 2
  benefits. `test_helps_with_low_resources` uses q = 0.01, `mode 1 → D`, a
  target of 3/4 for D, and mode 1 three units out of four:
  - with the finder the realized ratio is exactly 3/4;
  - without it, D is added where mode 2 will be.
