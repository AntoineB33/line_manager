## Some valid rules are refused
The requirements allow any element rule, but `RuleError` refuses some forms:
- the operands of an "or" must all be relative to p, or all relative to l;
- with the sweep, a left side reads a chosen kind only as present, and a chosen kind
  that a left side reads must be placed at an offset >= 0.
Example refused: `K6 → K0 1 unit on the left of p or no K0`, when K0 is read by a left
side and an "or" can change it.
Without these conditions the sweep has no exact method for "the leftmost operand that
can be chosen". The requirement may not be achievable as written.

## An "or" on an element with no left end
With no first position, and choices acting on each other, the sweep refuses the edit. The
lattices decide some of these.

## The probability finder's benefit is not measured
Nothing measures whether it makes the generator more performant at a low allocated
resources quantity. It only exists and is used.
