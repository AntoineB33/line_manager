# Line manager: formal semantics

Each part of `../requirement.md` is listed below with its formal definition, where it
lives in the code, and how it is checked. Tests: `python -m unittest discover -s tests`.
Demo: `python UI/demo.py`.

All arithmetic is exact: positions, times, percentages and probabilities are
`Fraction`s, and ±∞ is used only as an open endpoint.

## 1. The line and its state (`intervals.py`, `line.py`)

- The line is ℝ. An interval has rational or infinite endpoints and is open or
  closed at each one. A point `{x}` is the interval [x, x]. An `IntervalSet` is a
  finite union of intervals in canonical form, so two sets are equal as sets of
  reals iff they are equal as objects. Its operations (∪, ∩, \, complement,
  Minkowski sum ⊕, negation) are exact. Tests compare them point by point with
  brute force, and the Minkowski sum is checked on a grid that is fine enough to
  make the comparison exact.
- **State.** For each element type T, a finite set of pieces (interval,
  percentage p ∈ (0, 100], owner ∈ {user, generated}) with pairwise disjoint
  intervals. s_T(x) is the percentage of the piece that contains x, or 0 if no
  piece does. *"The same element type can't be present several times at the same
  position"* is exactly this disjointness: `LineState.from_entries` refuses
  overlapping entries, and every write replaces what was there before.
- The example state of requirement.md (A on [x₁, x₂) at 100% and A at x₂ at 50%)
  satisfies this rule: the two A pieces touch at x₂ but do not overlap.
  `test_requirement_state_example` builds it, and checks that putting A twice at
  x₂ ([x₁, x₂] and {x₂}) is refused.

## 2. The chip (`simulation.Chip`)

The chip's position is a continuous, non-decreasing, piecewise-linear function of
time. It never moves left, but it may stop. Every event depends on the exact
first time the chip reaches a position x, min{t : chip(t) ≥ x}, so no position is
skipped. For example, a follow edit produces exactly [2, 7) ∪ [9, 14), and the
same holds with stops (`test_follow_is_exact`, `test_chip_with_stops`).

## 3. Modes (`modes.py`)

- Modes are defined on [start, ∞), the positions the chip can be at. A timeline
  has an initial mode and a finite list of transitions (tᵢ, mᵢ), where the tᵢ are
  strictly increasing and after start, and each mᵢ differs from mᵢ₋₁. The mode at x
  is the mode of the last transition at or before x.
- So **exactly one mode applies at each position** (`test_one_mode_per_position`),
  and **every bounded stretch of the line has finitely many transitions**,
  because the list is finite.
- **Given externally.** The timeline is an input of the simulation. The generator
  only receives `ModeKnowledge`, which is the timeline cut at the chip: modes on
  [start, chip]. Positions after the chip have unknown modes.

## 4. Element rules (`rules.py`)

- A rule L → R has:
  - a left side L: a conjunction of element types with at most one `mode m`;
  - a right side R: a set of items (U, I, allowed). I is a bounded set of offsets,
    and allowed is one of: `U` (s_U > 0), `U p%` (s_U = p), or `no U` (s_U = 0).
- L is met at x iff s_T(x) > 0 for every T in L and, if there is a mode literal,
  mode(x) = m.
- The rule holds at x iff, whenever L is met at x, s_U(y) is allowed for every
  item and every y ∈ x + I.
- The violation set is computed exactly:

  V = met(L) ∩ ⋃ (bad_U ⊕ (−I)),  where bad_U = {y : s_U(y) not allowed}

  A brute-force oracle checks it on random states (`test_violations_match_brute_force`).
- Syntax covers every example in the requirement: `A → B`, `A → no B`,
  `A & B → C 3 units on the left`, `mode 1 → D & E`, `A → A at -1`, plus
  `B 50% on [0, 2)`.

## 5. Who modifies the line; what a generated script may never do (`guards.py`, `simulation.py`)

Only user edits (`UserEdit`, which may change anything, anywhere) and script
actions change the state. The executor checks every script action, so the
following hold for **any** script, not only generated ones
(`HandWrittenScriptsTest`):

- **N1. It never changes (−∞, chip], the left side of the chip including the
  chip's position.** An atomic edit (`SetAction`) is refused as a whole if it
  touches (−∞, chip]. A continuous edit (`FollowAction`) has offsets J > 0. At
  every chip position c it writes on c + J, which lies strictly right of c.
- **N2. It never removes or changes an element added by the user.** Generated
  removals only remove generated pieces. An atomic edit that would overwrite a
  user piece is refused. A continuous edit skips user pieces of its type.
- **N3. Added for the element rules: it never adds a violation, whatever the
  unknown modes turn out to be.** Let W_r(S) be the violation set of rule r in
  state S when every position after the chip may have any mode. Because a
  violation at x depends only on the mode at x, W_r(S′) ⊆ W_r(S) for all r iff,
  in every future of the modes, every violation of S′ is already a violation of S.
  - Atomic edits are refused unless this inclusion holds.
  - Continuous edits may only write types that appear in no left side; the
    executor refuses any other. For such a type the positions that would add a
    violation form exactly the zone ⋃ met(L) ⊕ I, taken over the items that do not
    allow the written value. The edit skips that zone, which is constant between
    two instants.
  - This is what Example 1 needs: with `A → A at -1` and the chip at 0, any A
    placed after the chip requires another A one unit to its left, which
    eventually falls in the frozen part (N1), or leaves a new violation (N3).
- **Check of a whole run.** `tests/helpers.check_log` is independent of the
  executor. It replays every logged change and checks N1, N2 and N3. It runs
  over 40 random worlds that include rules, modes, user edits and a chip that
  stops (`test_random_simulations`).

The user may still create violations. The generator repairs the ones it can
(`repair` candidates), because "where the left side is met, the right side must
be applied", but never at the cost of N1–N3.

## 6. The rules generator and its script (`generator.py`, `script.py`)

- **Input.** A run takes the state, the chip position c and the revealed modes.
  It returns a `Script` of events.
- **Triggers:**
  - `ChipIn(lo, hi, mode)`: true while chip ∈ [lo, hi) and, if a mode is given,
    that mode is the one at the chip. The event fires at every instant the trigger
    becomes true.
  - `TransitionCount(k)`: fires once, when the k-th transition after
    installation is revealed.
- **Actions:**
  - `SetAction`: an atomic edit of intervals, the "continuous edits" of the line,
    exact on ℝ.
  - `FollowAction`: an edit that follows the chip (c + J).
  - `RerunAction`: runs the generator again.
- **Optimization problem implemented** (from the example): presence-ratio
  targets r_T. The ratio on W = (c, c+H] is (1/H)∫_W s_T/100.
- **Selection.** Candidates are built in a fixed order. A candidate is valid iff
  it satisfies N1–N3. The best one minimizes, in this order:
  1. the expected measure of violations;
  2. the expected number of point violations;
  3. Σ|ratio − target|.

  Expectations use the probability finder's scenarios when they are available.
  Otherwise they assume a uniform distribution over modes.
- **Mode rules.** A mode rule whose items lie strictly right of the chip and
  write a free type becomes a follow edit, which reacts to the mode at the chip.
  Any other mode rule has to be prefilled before the mode is known.
- **Example 1.** It produces an empty script (`Example1Test`). A run sees that
  later runs would also do nothing until the user edits when:
  - there is nothing to optimize, or
  - the line is empty, no rule depends on a mode, and the run does nothing, so
    every later run would see the same problem, translated.

  In those cases the script has no events at all. A user edit always starts a
  new run, so "the user can add A anywhere" still works.

## 7. The allocated resources quantity q ∈ (0, 1)

| quantity | definition | monotonicity |
|---|---|---|
| work of a run | min(B(q), number of candidates), with B(q) = 2 + ⌊36q⌋ | non-decreasing in q |
| rerun distance | D(q) = H(1 − q/2) | strictly decreasing in q |
| rerun transitions | K(q) = 1 + ⌊4(1 − q)⌋ | non-increasing in q |

q outside (0, 1) is refused.

**Lower q: a faster run that uses less CPU.** The candidate list does not depend
on q, and a run evaluates a prefix of it. Work is counted deterministically
(candidates built and checked) instead of by wall-clock time, so it can be
tested. The quality of a run is non-decreasing in q, because a longer prefix
contains a shorter one and ties go to the earliest candidate.

**Lower q: less frequent reruns.**
- *Per run* (`test_per_run`): the script asks for a new run when the chip has
  moved D(q) or after K(q) transitions. Both depend only on the external chip and
  modes, so the first rerun time is non-increasing in q.
- The futility case cannot flip the wrong way. If the higher-q run does nothing,
  so does the lower-q run: its candidates are a prefix of the higher-q run's, and
  the no-op comes first.
- *Over a whole simulation* (`test_whole_runs`): runs happen at
  r_{k+1} = e(r_k), where e(t) = min(first time the chip reaches chip(t) + D,
  time of the K-th transition after t, next user edit after t). e is
  non-decreasing in t and pointwise non-increasing in q. By induction the k-th
  run with a higher q is never later, so a lower q never has more script-triggered
  runs. This argument does not cover worlds where a run is futile.
- Integer quantities (B, K, run counts) are only non-strictly monotone. A strict
  inequality is impossible for integers on a continuum of q. D is strictly
  monotone.

## 8. The probability finder (`probability.py`)

- **Counting.** From completed mode segments it counts transitions a → b and
  segment lengths per mode. The first segment is excluded from the lengths,
  because it may have started before the chip did.
- **Probability rules.** It outputs exact rules, for example "after mode 1, mode
  2 follows with probability 1" and "mode 1 lasts 2 with probability 1".
- **Scenarios.** It enumerates scenarios best-first, conditioned on how long the
  current mode has already lasted. Where the rules have no data, a scenario is
  "uncovered" and treated as uniform. The probabilities of the returned
  scenarios plus the residual sum to exactly 1.
- **Why it helps at low q.** The first candidate of a run prefills mode-dependent
  rules where the most probable scenario puts the mode, so even B(q) = 2 finds
  it. `test_helps_with_low_resources` uses q = 0.01 and periodic modes:
  - with the finder: zero violations, and D is placed only where mode 1 will be;
  - without it: violations occur.
