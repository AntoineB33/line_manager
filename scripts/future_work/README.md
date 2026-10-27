# Future work: the "or" choices without the restrictions R1, R2, R3

## Status

Rule sets that break R1, R2 or R3 (below) are refused **when the rules are loaded**
(`RuleError` in `rules.py`), so no edit with them can be made. The requirement does
not allow this: it only allows refusing an edit whose result "cannot be computed in
reasonable time". It is the one gap of `requirement.md` left in the code. `SEMANTICS.md`
§4 lists the restrictions and §5 the solvers.

The definition itself looks sound in these cases: on finer and finer grids the
results settle (see "Evidence"). So what is missing is an exact algorithm, not a
decision in the requirement.

## The setting

**Positions.** A position is x + kε, with x rational and k an integer; ε is a positive
infinitesimal. Positions are ordered by x, then by k: 1 < 1+ε < 1+2ε < 1.0001. A
rational distance keeps the ε part: (x + kε) + d = (x + d) + kε. −∞ and +∞ are not
positions.

**Intervals and regions.** Intervals are closed in this order: (a, b] = [a+ε, b] and
[a, b) = [a, b−ε]. A region is a finite union of intervals plus an eventually periodic
tail on each side, whose period may have an ε part (1+ε). Results must be regions.

**Kinds and claims.** On the line, each kind K has a percentage in (0, 100] where it
is present. The input is claims: "K at p% on R" and "no K on R".

**Element rules L → R.**
- L is a boolean formula, evaluated at each position. Its atoms are `K` (present) and
  `K = / >= / > / <= / < p%`, where an absent K counts as 0%. They combine with not,
  and, or.
- R is a conjunction of items. An item is a literal ("K", "K p%", "no K") or an
  ordered "or" of operands. Compound operands like `(B & C) or D` are already reduced
  to hidden kinds (`SEMANTICS.md` §4).
- Each literal has a location: an offset d, or an interval [a, b] with rational ends,
  each end open or closed. The location is relative to an anchor:
  - **p:** each position where L holds;
  - **l:** the left edge of the largest interval of {L holds} that contains p.
- If that largest interval has no left end, l = −∞. Then l + d, or l + [a, b] with b
  finite, is not on the line: nothing is placed. l + [a, +∞) is the whole line.
- **No cycles.** The graph "kind read by L → kind in R" of the element rules is
  acyclic. That includes kinds read under not, and "no K" literals.

**The line of the claims.** It contains exactly the claims and what the rules
require, and every rule holds everywhere. An "or" takes "the leftmost operand that can
be chosen":
- **Anchors** are (position, "or") pairs, for every position where some assignment
  could meet that "or"'s left side. For an "or" relative to l, the anchors are the
  left edges.
- Anchors are ordered by position, then by a fixed order of the "or"s at one
  position.
- Each anchor takes the first operand for which a valid completion of all the other
  anchors exists, given the choices made at the anchors before it.
- If no assignment is valid, the claims are in conflict.

## What works, and the restrictions

**Single positions (solved exactly).** When the rules that read a chosen kind (one an
"or" can change, or one derived from it) only place single positions relative to p,
and every operand is a single position, the solver works on lattices (`solver.py`).
- All offsets are multiples of their gcd g, so each lattice r + gZ is independent.
- A depth-first search decides a bounded core.
- Beyond the core, a finite automaton finds the rest, and the result is eventually
  periodic.
- Atoms are evaluated with three values, so a kind read under "not" is handled
  (`AbsentReadTest`).

**Intervals and left edges.** The sweep (`sweep.py`) decides positions from left to
right:
- It runs a greedy pass first, then looks ahead with a depth-first search over blocks.
- Candidate positions for a change are e − d + jε.
- It detects when the choices start repeating.

The sweep is only correct under three restrictions:

- **R1 (monotone).** Left sides read chosen kinds only as present: never under not,
  never as `K < p%` or `K 0%`. A choice then only adds consequences, which is what
  makes the pruning and the runs sound.
- **R2 (one anchor per "or").** All operands of an "or" are relative to p, or all
  relative to l. A mixed example: `A → B or C on [0, 1] of l` takes, at each p,
  either B at p or C on l(p) + [0, 1].
- **R3 (causal).** A chosen kind that a left side reads is placed at an offset ≥ 0
  from where its choice is decided. Then what lies on the left of the sweep never
  changes again.

## Evidence that the definition is sound (`grid.py`, `convergence.py`)

On a grid of step h the definition is finite, and `grid.py` computes it exactly.
That's an exhaustive left-to-right search with three-valued atoms, so it doesn't rely
on R1, R2 or R3. As h shrinks (1/4, 1/8, 1/16), the results should settle if the
continuum definition is sound.

- **Hand-picked cases.** All nine converge to what the definition predicts.
  - **R1:** `A & not C → D on (0, 1] of p`, `D → no B`, with X at 1, gives B at 0,
    1+, 2+. That's the n + nε pattern of `test_drift`.
  - **R2:** a forced choice at 2 places C on [0, 1], relative to the left edge. That
    makes B impossible at 0, so the result starts with C there.
  - **R3:** a forced C at 2 forbids B behind it, so C spreads back to 0.
- **Random cases.** About 2,000 random rule sets for R1, R2 and R3 (seeds 11–14,
  420 s each). None had a valid assignment on one grid and not on another, and none
  disagreed between h = 1/8 and 1/16 away from boundaries.
- **What this does not show.** Grids can't see ε-level effects; points within 1/4 of
  a boundary are ignored; distances are multiples of 1/2; the line is bounded; no
  claims are unbounded. It's evidence, not proof.

Reproduce from the project's root:

    python scripts/future_work/convergence.py cases
    python scripts/future_work/convergence.py random 11 420

## An external answer, for the record

Asked this problem, Gemini 3 claimed that the definition breaks down without R1–R3,
and that the problem becomes undecidable. It gave no counterexample and no encoding.
- It ignored the absence of cycles. For example, it claimed that a choice at p
  changes l(p). It can't: l comes from the rule's left side, which can't read its own
  "or"'s operands.
- It wrongly claimed that monadic second-order logic over the real line gives
  eventually periodic results. That logic is undecidable (Shelah, 1975).

The grid evidence above contradicts its claim that the definition breaks down.

## Open questions

1. **Well-definedness.** Prove that the definition has exactly one result on positions
   x + kε, with R1, R2, R3 lifted (separately or together). Or give a counterexample.
2. **Regularity.** Is the result always a region (finitely many intervals plus
   eventually periodic tails)? And are its changes finitely many in each bounded
   stretch?
3. **An exact algorithm**, for each lifted restriction or for all of them, with a
   proof of where choices can change, ε parts included. For example:
   - R1: the sweep with three-valued atoms, as on lattices;
   - R3: a look-ahead that may revise choices on the left;
   - R2: placements relative to l(p).
4. **Otherwise,** the largest class with an exact algorithm, and one that is exact
   whenever it terminates. It should signal failure clearly: an edit may then be
   refused ("cannot be computed in reasonable time").

## How to accept a solution

- **The tests.** It must pass `python -m unittest discover -s tests`. Each restriction
  lifted needs a test comparing with an independent brute force, like
  `AbsentReadTest.test_matches_brute_force`.
- **Mutation check.** The brute-force test must fail on a deliberately broken
  variant. For example, read unknown atoms as absent: the test must catch it.
- **Agreement with the grids.** On the cases above, the results must agree with
  `grid.py` as h shrinks.
