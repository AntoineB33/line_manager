# Line manager: formal semantics

This document maps each part of `../requirement.md` to its formal definition,
the code that implements it, and the test that checks it.

- Run the tests: `python -m unittest discover -s tests` (standard library only).
- Run the demo: `python UI/demo.py`.

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

- **Claims.** A *claim* is an element placed by an edit. It is either "K at p%"
  on a region (p ∈ (0, 100]) or a "no K" element on a region. Each claim has an
  owner: the user, the generated script, or a mode rule.
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
- **Right side R.** A list of items, all applied. An item is one of:
  - `K p%`: K present at exactly p%;
  - `K`: K present (it gets 100% when it is added);
  - `no K` or `K 0%`: K absent;
  - `L₁ or L₂ or …`: an ordered disjunction of such literals (§5).
- **Locations.** Each item has a location relative to an anchor: "at d", "d units
  on the left/right" or "on [a, b)", followed by "of p" or "of l".
  - p is each position where L is met. The literal applies at p + d, or on
    p + [a, b) (`test_intervals_relative_to_p`).
  - l is the left edge of the largest interval where L is met, the one that
    contains p: its first position. The literal applies at l + d, or on
    l + [a, b), once per largest interval. The left edge of (6, 8) is 6+ε.
  - Without "of", a single position is relative to p and an interval to l.
  - A largest interval that is unbounded on the left has no left edge, so nothing
    is placed for it (`test_no_left_edge`).
  - `Region.left_edges` and `Region.dilate` compute these sets exactly, tails
    included. `test_left_edges_dilate_components` checks them point by point.

### Mode rules

`mode m [& L] → R` is a mode rule. It can add or remove elements only at the
chip's position, so every location in R must be "at 0 of p"; any other location
is refused.

### Restrictions (`RuleError`)

- **No cycle between kinds**, whatever the distances. The graph is "kind read by
  L → kind of R".
  - It includes the kinds read under "not" and the "no K" literals, because "no K"
    is an element of kind K.
  - A → A counts as a cycle, and so does `B → no B 1 unit on the right`.
  - The requirement's example (`A → B 1% 1 unit on the right of l`,
    `B → A 10%`) is refused.
- **Operands of "or"** are all relative to p or all relative to l. Relative to
  l, one choice is made per largest interval, at its left edge.
- **Chosen kinds.** A kind is *chosen* if it is a present operand of an "or", or
  if a rule whose left side reads a chosen kind places it.
  - A left side reads chosen kinds only as present (`K`, `K p%` with p > 0,
    `K >= p%`, `K > p%`), never under "not" and never as `K < p%` or `K 0%`. So
    choosing an operand only adds consequences, and a contradiction found with
    the choices on the left stays whatever comes next.
  - When the choices need the sweep (§5), a chosen kind that some left side reads
    is placed at or after the position where it is decided: at an offset ≥ 0 from
    p, or from l. Then what lies left of the sweep, its largest intervals and
    their left edges included, never changes again.

  These conditions are not in the requirement; they keep "the leftmost operand
  that can be chosen" defined and computable (`test_restrictions`).

## 5. Consequences: the "or" rules (`solver.py`)

### Definition

The line of a set of claims is defined as follows:

1. It contains every claim.
2. It is closed under the rules.
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
  in the order of the rules. Since there is no cycle, a left side only reads kinds
  that are already computed.
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
  - beyond the claims everything is invariant by translation. When the choices of
    the last stretch as long as the rules reach repeat, translated, they repeat
    forever, possibly with an ε part in the period.

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
    adds ±1. So the search looks for changes within ±2 of the ε parts of the
    nearby ends of the claims, fixed regions and choices. This is the one
    assumption it makes. A check between two candidate positions refuses the edit
    if a change falls elsewhere.
  - **What makes it fast.** The search remembers each situation it completed or
    failed: what the choices before P placed and forbade, as far as the rules
    reach from P. A failure also rules out every situation with no less placed
    and forbidden and the same presence just before P, because choices only add
    consequences. Positions where nothing could be chosen are remembered too, and
    an operand that leaves one of them with nothing, whatever comes next, is
    skipped. Beyond a number of steps (3000), the edit is refused rather than
    guessed.

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

## 6. The result of an edit (`resolve.py`)

An edit is a list of operations:

- put "K p%" on a region;
- put "no K" on a region;
- take K off a region (K must then be absent there);
- take the "no K" elements off a region.

The result of an edit is determined as follows:

1. **Direct effects.** The edit's own operations apply. A put replaces what was
   claimed of that kind on its region.
2. **Refusal.** If the edit contradicts the rules on its own, it is refused, for
   example C and D at x with D → no C. It is also refused when the rules do not
   decide its result, or when the search for it is too long (§5, `Undetermined`):
   no older element is removed for that.
3. **Newest wins.** The edit's elements are kept. If older elements conflict
   with them, older elements are removed.
   - **What an element is.** An element is a largest interval of one claim
     ("K p%" or "no K", with one owner), and it is removed whole.
   - **Criteria, in order:**
     - Only what is necessary: no removal set may strictly contain another one
       that works.
     - The combination that loses the fewest elements. A lost element is either
       an element of the line (a largest interval of one kind and percentage)
       that does not stay whole, or a removed "no K" element.
     - On a tie, the combination that keeps the leftmost elements: at the
       leftmost position where two combinations differ, the one that keeps the
       element there. Kinds at the same position are ordered by their first
       appearance in the rules.
4. **Removed elements are gone for good.** Removing the newer element later does
   not bring them back (`test_gone_for_good`).

**Why whole elements.** With rules relative to l, removing parts of elements has
no smallest choice. With `A → B on [0, 1] of l`, A on [0, 10], then "no B" at
1/2: removing A on [0, a) works for every a > 1/2 but not for a = 1/2. So the
requirement's "loses as few elements as possible" counts whole elements, the
intervals of the state.

The requirement's example: with "A and B → C", put A and B at x, then "no C" at
x. Only B and C are removed (`test_requirement_example`). Other cases tested:

- When removing A would lose more elements than removing B, B is removed
  (`test_fewest_lost`).
- Elements are counted, not measured: one long A goes rather than two short B
  (`test_fewest_elements_not_measure`).
- A consequence relative to l removes the whole element
  (`test_consequence_relative_to_l`).

`ResolutionTest.test_matches_brute_force` compares the result with a brute-force
search over every removal set.

### What is computed, and where it stops

A change of a left side on a set E moves its left edges only within the closure
of E, because being a left edge is a local property. A rule then acts within
`reach` (the largest distance in a location) of that change. So removing an
element can only matter within depth × reach of it, where depth is the number of
kinds.

- **Candidates.** The older elements within (depth + 1) × reach + 1 of the edit.
- **Groups.** Candidates farther apart than twice that distance are decided
  separately, from left to right. Each group counts the losses near itself.
- **Limit.** A group of more than 12 elements is refused rather than guessed. A
  refused edit has a known result: nothing changes.
- **Approximation.** With "or" choices, a forced choice can in principle travel
  along a lattice farther than this distance. There, the grouping is an
  approximation.

## 7. Who modifies the line (`simulation.py`, `guards.py`)

The line is modified only by the user and the generated script. The script makes
two kinds of edits: its own edits, and the edits of the mode rules, which it carries
out at the chip (`ModeRulesAction`).

- **User edits** may change anything, anywhere, including at or left of the chip.
- **Script edits** are refused if their *result*, after the rules have been
  applied, would break either of these:
  - **N1.** It would change the line or the claims on (−∞, chip], that is, the
    left side of the chip or the chip's position (chip + ε is on its right).
  - **N2.** It would remove or change a claim of the user.
- **Mode rules** act at the chip as it moves, while the script carries them out
  (every script carries them when there are mode rules). `mode_rules.py`.
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
    rule distances) and |d| ≤ 2 · depth · reach. The decision at x can only
    change where a moving end crosses a fixed one, at e − d + jε with |j| ≤ 2.
    A run is checked there and at the first, middle and last positions between
    them. A check that fails between them refuses the rest of the chip's
    stretch, rather than guessing. A run is logged as one continuous change:
    each of its positions changes when the chip is there.
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

**Actions:**

- `EditAction`: an edit on exact intervals of the real line, resolved as in §6
  and guarded as in §7.
- `RerunAction`: runs the generator again.
- `ModeRulesAction`: while its trigger holds, the mode rules act at the chip
  (§7). A run adds it, with `ChipIn(c, ∞)`, whenever there are mode rules.

### What a run optimizes

- **Problem.** The implemented problem is presence-ratio targets r_K on the
  window W = (c, c + H].
- **Candidates.** Candidate edits are built in a fixed order:
  1. the no-op first;
  2. then each combination of a prediction of what mode rules will do in W and
     a placement strategy.
- **Validity.** A candidate is valid if its resolution is accepted and it
  respects N1 and N2.
- **Choice.** The best valid candidate minimizes the expected ratio error. The
  expectation is taken over the probability finder's scenarios.
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
