## Some valid rules are refused
The requirements allow any element rule, but `RuleError` refuses some forms:
- the operands of an "or" must all be relative to p, or all relative to l;
- with the sweep, a left side reads a chosen kind only as present, and a chosen kind
  that a left side reads must be placed at an offset >= 0.
Example refused: `K6 → K0 1 unit on the left of p or no K0`, when K0 is read by a left
side and an "or" can change it.
Without these conditions the sweep has no exact method for "the leftmost operand that
can be chosen". The requirement may not be achievable as written.

## "Reasonable time" is a step count, not a time
The limits are MAX_STEPS = 300 and MAX_ROUNDS = 40 (plus MAX_OPTIONS, MAX_DEPTH,
MAX_EVENTS, MAX_CALLS), so no duration is guaranteed. Past some of them the code changes
whole elements instead of refusing, which the requirements allow.

## "Solve optimization problems" is not proven optimal
A run tries at most 2 + floor(36 q) candidates from a fixed list and keeps the best. The
result is the best of those tried, not a proven optimum of the problem.

## The probability finder's benefit is not measured
Nothing measures whether it makes the generator more performant at a low allocated
resources quantity. It only exists and is used.

## A wrong line: a rule cycle through an "or" over a repeating element
With `A → D or E` and `E → A`, the sweep (second solver) lets A and E hold each other up
with nothing placing them: A claimed on [135, 150) comes out on [135, 202].
See `test_a_cycle_over_what_repeats`, marked as an expected failure.

## The sweep cannot choose from 0 outwards
requirement.md: the position closest to 0 chooses first. The lattices do it. The sweep
(second solver) only goes from left to right, so with choices that act on each other left
of 0, or on an element with no left end, it refuses the edit instead of giving a wrong line.
Example refused: `A → B on [0, 1] of l or C on [0, 1] of l` with `B → no C on (0, 1] of l`,
`A` on [-5, -1].
