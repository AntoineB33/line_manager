# Reverse logic: formal semantics and proofs

Code: `region.py` (exact region algebra), `rules.py` (rules), `engine.py`
(history reduction, solver, `LineSystem`, `Drag`), `choice.py` (the operands of
ordered disjunctions), `reverse_logic.py` (public
API and the three examples of `requirements.md`). `test_reverse_logic.py`
checks every theorem below on random histories, checks acceptance against a
brute-force search, and checks the three examples step by step.

Requirements: `../requirement.md` (the line, elements, rules) and
`requirements.md` (edits, reversal, drags).

## 0. Verdict

| Requirement | Where |
|---|---|
| Line, elements as sets of intervals and points, touching intervals form one element | §1 (regions) |
| No kind present twice at a position | §2 (a kind's line is a set); puts onto the same kind are refused (§4) |
| Edits: additions, removals, extensions, retractions, shifts | §2 |
| Removing, moving or retracting what is not there is refused | §4 (fit) |
| Putting a kind where it already is is refused | §4 (fit) |
| Add then remove, remove then add, move then move back, extend then retract back, retract then extend back → previous line | T3 |
| Reverse A when B doesn't require A → as if A never happened; then reverse B → before A | T4, with "requires" defined in §4 |
| Elements adapt to every kind of edit (the newest wins); contradictory edits are refused | T1, T2 |
| Drags: every step equals a direct move; no debounce; back to start → initial line | T6 |
| Examples 1, 2, 3 | `test_requirements_example_1/2/3`, and `python reverse_logic.py` |
| No cycle between kinds, whatever the distances | `RuleCycleError` (§3) |
| Unique result; first operand that can be placed; competing positions closest to 0 first | T5, §3 step 2, Theorem 3.4 |
| Older positions that can no longer be placed adapt alone (the newest wins) | §3 step 2, except below |

The requirements leave two things to choose; §5 states the choices made.

**Not yet met.** On a lattice where an older edit has infinitely many positions of an ordered
disjunction (an element on a ray) and a newer edit leaves some of them no operand, the older
positions are not kept one after the other (§3, step 4): with `A → B ∨ C`, `B → ¬B 1 unit to
the right`, `X → ¬B`, `X → ¬C`, A on [0, +∞) and then X at 10, A is dropped at every integer
instead of at 10 only.

## 1. Regions and why the line is computed from the history

A kind's presence is a region: a union of intervals with independently open or
closed endpoints: finitely many (the *core*), and on each side, optionally, a
*tail* that repeats a pattern forever with a rational period (an ordered
disjunction over a ray can give B on [0, 1) ∪ [2, 3) ∪ …, §3). The family is
closed under ∪, ∩, \ and translation: [0,2] minus [0,1] is (1,2]; two regions are
combined on the core between their tails' starts, and on each side on one common
period (the lcm of theirs). Regions are kept in a canonical form: the core sorted,
disjoint and maximal; a tail with its least period, starting at
sup{x : (x ∈ S) ≠ (x + P ∈ S)} (the left one at the corresponding inf, not after
the right one; 0 if S is periodic everywhere); a constant tail is a core part to
infinity. These are functions of the set, so `==` is set equality and two
touching intervals of one kind are one element (`test_periodic_algebra…` checks
every operation at a dense set of points, and equal sets built differently).
Edits are finite sets of intervals (requirements), so only the solver's choices
make tails. Coordinates are exact rationals (`fractions.Fraction`); floats are
read as their decimal text, because in floating point (x + d) − d ≠ x for some
x and d, which would break "move back".

The engine keeps the edit history, not only the line, because undo needs it.
With the rule `D → ¬C`: from {D on r}, adding C on r gives {C on r} (D adapts);
from ∅ it also gives {C on r}. Removing C must give back {D on r} in the first
case and ∅ in the second, though the visible line was the same. So the line is
recomputed from the active edits every time.

## 2. Edits and history reduction

Ops (`engine.py`): `Add(K,R)`, `Remove(K,R)`, `Move(K,R,δ)`, `Extend(K,R)`,
`Retract(K,R)`. An op *takes* K from a region (`Remove`, `Retract`, the source R
of `Move`) and *puts* K on a region (`Add`, `Extend`, the target R+δ of `Move`).
Its contribution to the count of K is −1 where it takes and +1 where it puts.
`inverse` swaps Add↔Remove and Extend↔Retract and moves back, so it negates
the contribution exactly and swaps taking and putting.

Within one edit and one kind: no two ops take from the same point, no two put
on the same point, nothing is added or extended where something is taken, and
nothing is removed or retracted where something is put (a move's own source
and target may overlap). This condition is unchanged by `inverse`.

Let the active edits be e₁ < … < eₙ (by creation time). Fix a kind K and a point x, and let cᵢ = cᵢ(K,x) ∈ ℤ.
**Reduction** processes i = n, n−1, …, 1 (newest first) with a stack. cᵢ is
pushed. If its sign is opposite to the top of the stack, the two cancel by min(|cᵢ|, |top|). What
remains of cᵢ is eᵢ's **effective claim** at (K,x): *present* if > 0, *absent* if
< 0, none if 0. Call Pᵢ and Nᵢ the regions of effective present and absent claims of eᵢ.
(Counts can exceed 1 internally: an older claim dropped by adaptation is
invisible, so a newer edit may put K there. The visible line is always a set.)

**Lemma 2.1.** If eₙ₋₁ = e and eₙ = inverse(e), every effective claim is the same as for
e₁…eₙ₋₂, and eₙ₋₁ and eₙ have none.
*Proof.* At every (K,x), the first two entries processed are −c then c. −c is pushed onto
the empty stack, and c cancels it exactly. The stack is then empty, so the rest of the run is
the run for e₁…eₙ₋₂. ∎

## 3. Solving: priorities, adaptation and the least model

Rules are definite clauses `Implies`, negative clauses `Forbids`, and ordered
disjunctions `ImpliesAny`, all pointwise and translation-invariant (`rules.py`).
The graph "premise kind → produced kind" must be acyclic, whatever the offsets
(`RuleCycleError`); this gives a topological order of the kinds.

Levels are processed from **newest to oldest**, carrying the accepted state Γ:
accepted present claims, accepted absent claims, Γ⁺ = their least model, and the operand recorded for every
`ImpliesAny` anchor in Γ⁺. For level i:

A *new anchor* of `P → O₁ ∨ … ∨ Oₘ` is an anchor not already in Γ⁺; old anchors keep their
recorded operand. The *violations* of a closure C are: an atom of C on an accepted or level-i
absent claim, a `Forbids` instance in C, or a new anchor with no operand. For seeds (atoms
taking part in violations), Supp = every atom outside Γ⁺ used in *some* derivation (in C) of a
seed, computed exactly on regions by backward propagation in reverse topological order.

1. **Conflicts no operand avoids.** C₀ = least model of Γ⁺ ∪ Pᵢ, new anchors getting no operand.
   Its violations remain whatever the operands, since the rules are monotone. Drop Pᵢ ∩ Supp
   and Nᵢ ∩ C₀.
2. **Choices.** If some assignment of operands to the new anchors gives no violation, the
   first such assignment is used: positions are decided one after the other, the closest to 0
   first and the one on the left first when two are equally close (the requirement), the rules
   at one position in topological order, and each takes the first operand, in its written
   order, that still lets the remaining positions be placed. On a lattice where no assignment
   exists, the present claims of level i on it are kept one after the other in the same order
   (the kinds at one position in topological order): each is kept if some assignment has no
   violation with it and the ones kept before it. The others are dropped, and the kept ones
   get the assignment above (`Solver._keep_in_order`).
3. **Closure.** C = least model of Γ⁺ ∪ Pᵢ with these choices (one pass in topological order).
4. **Remaining conflicts**, only on a lattice with infinitely many new anchors where no
   assignment exists (below): drop Pᵢ ∩ Supp and Nᵢ ∩ C, then recompute C. Add what remains to Γ.

The line is Γ⁺ after the oldest level. Everything not in Γ⁺ is absent.

Example: with `A → B ∨ C`, `B → ¬B 1 unit to the right`, `X → ¬B` and `X → ¬C`, the line
A {0, 1, 10}, B {0, 10}, C {1} becomes A {0, 1}, B {0}, C {1}, X {10} when X is added at 10:
only A at 10 has no operand left (`test_older_positions_that_cannot_be_placed_adapt_alone`;
`test_older_claims_are_kept_closest_to_0_first` checks step 2 against a brute-force search).

**Computing step 2 exactly.** Every rule offset is an integer multiple of g, the gcd of all
offsets, so a rule only relates points of one lattice r + gℤ. Split the residues r ∈ [0, g)
at 0, g/2 and every endpoint of every region involved (mod g; a tail's endpoints have
finitely many residues). On each piece every region contains the same lattice points r + kg
for every r, and the order of the points by distance to 0 is the same (it changes only at 0
and g/2), so all lattices of the piece pose the same problem, solved once (`choice.py`).
After step 1 the line of no new anchor has no violation (Theorem 3.2).
* *Finitely many new anchors*: backtracking over them in the order of step 2, within
  2·reach of them (reach = (number of kinds + 1) × the largest span of a rule bounds how far a
  decision and its derivations go). Pruning is sound because the rules are monotone: an
  undecided anchor derives nothing and deciding it only adds atoms, so a violation already
  found remains. The search is exponential in the worst case; deciding whether an assignment
  exists encodes SAT, so no exact method avoids this in general. Where none exists, each claim
  kept or not in step 2 is one more such search, on the lattice without the claims left out.
* *Infinitely many*: Theorem 3.4. Where no assignment exists, the claims are not yet kept in
  order: the approximate choice of `Solver.choices` is used, and step 4 drops the support of
  its conflicts, which can drop more than step 2 would (§0).

**Theorem 3.4 (infinitely many positions).** On a lattice with infinitely many new anchors,
`choice.solve_infinite` gives the assignment of step 2, which repeats on each side from some
position on. *Proof.* (a) Violations are local: by the dependency of every kind on the
operands (computed through the rules, in topological order), whether there is a violation
anchored at position j depends only on the operands at j + vlo … j + vhi. (b) Beyond some
index on each side every region involved is periodic in k (its tail, intersected with the
lattice, has period lcm(P, g)/g), and so is everything (a) depends on. (c) Hence "the
positions beyond m on one side can be placed, given the last w = vhi − vlo operands" is
decided exactly: within the periodic part it depends only on (m mod period, those operands),
a finite graph, and is true iff an infinite walk starts there (nodes without successor are
removed until none is left); before, it is decided by recursion towards that part. (d) In the
order of step 2 the decided positions form one block around 0, so "the remaining positions
can be placed" is: the two sides can, once the block is at least w long (no violation then
reads both sides); before, the block is completed in every way to that length. Each step
takes the first operand for which this holds, which is the definition of step 2 (every
assignment it could still reach is valid, and the first operand that keeps one reachable is
taken). (e) Once the block is long enough and each side is in its periodic part, the next
operand on a side depends only on that side's node, by (c) and (d); nodes are finitely many,
so a node comes back, and from there the side repeats. The result is the decided block plus
each side's repeating part, as regions with tails. ∎ (`test_competing_anchors_closest_to_0_first`;
`test_infinite_positions_agree_with_long_finite_ones` compares, near 0, with the same element
cut far away.)

**Theorem 3.2 (adaptation removes every conflict).** After step 1, C₀ has no violation, and
after step 4, C has none.
*Proof.* Step 2 then gives, on every lattice of finitely many new anchors, an assignment with
no violation for the claims it keeps: kept ones are kept only if one exists, and without any
of them the lattice is that of C₀ after step 1, which has none. A violation reads atoms of one
lattice within reach of its anchors, so C has violations only on the lattices of step 4.
It remains to show that dropping a support removes the conflicts, for step 1 (C₀, no new
anchor expanded) and step 4 (C).
Choices are fixed functions of the anchor, so the closure is monotone and the new C' ⊆ C.
Absent claims of level i that meet C were dropped, so they don't meet C' either.
Any other violation in C' was a violation in C, so its atoms outside Γ⁺ are seeds.
Take one such atom α ∈ C' and a derivation tree of α in C' whose leaves are in Γ⁺ ∪ Pᵢ'. The tree is
also a derivation in C, so every node outside Γ⁺ is in Supp. Leaves outside Γ⁺
are in Pᵢ ∩ Supp and were dropped, so all leaves are in Γ⁺. An instance whose premises
are all in Γ⁺ is an old anchor, and Γ⁺ is closed under old choices, so by induction α ∈ Γ⁺. That contradicts α being a seed.
So every atom of the violation is in Γ⁺, which makes it a violation of Γ alone. That is impossible by induction (Γ is
initially empty). A failed anchor in C' with all premises in Γ⁺ would be old, which is also a contradiction. ∎
(The code asserts this invariant on every level.)

**Lemma 3.3 (nothing adapts without need).** If some assignment of operands gives level i no
violation, nothing of level i is dropped.
*Proof.* C₀ is contained in the closure with that assignment, and violations of C₀ (atoms on
absent claims, `Forbids` instances) stay violations in a larger closure, so step 1 finds
none. Step 2 then finds an assignment on every piece, keeps every claim, and step 4 finds
nothing. ∎

**Theorem T1 (rules always hold).** The line satisfies every rule, and every accepted claim holds.
*Proof.* By induction on Theorem 3.2: the final least model satisfies the `Implies` rules and, with its
recorded choices, the `ImpliesAny` rules; no `Forbids` rule or absent claim is violated. ∎

## 4. Valid edits, reversal and dependency

`apply(e)` accepts e iff e is **valid** on top of the active history H, whose line is L:

* (fit) e takes only what is in L; it puts K nowhere K is in L, except where e itself takes K;
  every part of an extension touches an element of its kind, and every part of a retraction
  touches what remains of its element (else `MissingElementError`);
* (solve) with e as the newest level, nothing of e's level is dropped;
* (post) every region e puts on is on the resulting line L', and inverse(e) fits L'.

Otherwise it raises `ContradictionError` or `MissingElementError` and leaves the history unchanged.
(post) is required by the requirements themselves: an edit that could not be undone right
after would make "add then remove", "move then move back", etc. impossible.

**Invariant (realizable history).** Every active edit is valid on top of the active edits
before it. `apply` keeps it by definition. `reverse(a)` replays the active edits after a
without a and refuses (`DependencyError`, nothing changes) if one of them is not valid;
the newest active edit can always be reversed. `reverse_all(ids)` instead reverses, along
with ids, every later edit that is not valid without them, so the invariant holds again.

**Definition.** B **requires** A iff B is not valid on top of the active edits before B,
without A and without the edits that themselves require A (`LineSystem.requires`).
Example: A adds E, B moves that E. Without A there is no E to move, so B requires A.

**Theorem T2 (adaptation and refusal).** (a) After an accepted edit, its effective claims
hold and every element it puts is on the line; older elements adapt (§3 steps 1, 2 and 4) to make that
possible, whatever the kind of edit: an added element pushes out what forbids it, a removed
element removes the premises of the rules that need it. (b) An edit that fits the line is
refused by (solve) iff no choice of operands lets its own claims satisfy the rules, which
depends only on the edit and the rules, since older elements adapt; and by (post) only when
it could not be undone or could not keep an element it puts.
*Proof.* The newest level is processed first, with Γ = ∅. (a) holds by (solve) and (post).
(b) is Lemma 3.3 in one direction; in the other, if every assignment has a violation, step 1
finds a violation and drops its support, or step 2 cannot keep every claim. ∎

**Theorem T3 (add/remove, remove/add, move/move back, extend/retract, retract/extend).** Let
H be a realizable history with line L and e an edit just accepted on top of it. Then
`inverse(e)` is valid right after e, and the line afterwards is L.
*Proof.* (fit): by e's (post). (solve): by Lemma 2.1 the two newest levels have no claims and
all other claims are those of H. A level with no claims, processed while Γ = ∅, leaves Γ = ∅,
so the solver runs exactly as on H, drops nothing of inverse(e), and gives L. (post):
inverse(e) puts exactly where e took, which is in L by e's (fit), and inverse(inverse(e)) = e
fits L by e's (fit). The same-edit condition of §2 is symmetric under inverse. ∎

**Theorem T4 (reversal).** Let A precede B, with B not requiring A. Reversing A is allowed
when no later active edit requires A, and the line then equals the line of the same history
with A never made. After then reversing B, it equals the line before A was made (plus any
other still-active edits).
*Proof.* The line is a function of the ordered list of active edits. Reversing A yields exactly
the list without A, and that history is realizable because `reverse` checked every later edit.
Reversing B then removes B from the list. ∎

**Theorem T5 (unique result).** The line is a function of the ordered active history. The
topological order is canonical (ties broken by name), the orders of §3 step 2 are fixed,
and every other step is a set-theoretic definition, independent of iteration order. ∎

**Theorem T6 (drags).** During a drag of a block (`LineSystem.drag`), after every step the
line equals the line of the active history with the block moved directly by the current
total distance; it does not depend on the previous steps, and no step is skipped. A step that
is not valid is refused and the line stays as after the previous step. Dropping the block at
its start leaves the line as before the drag.
*Proof.* `Drag.step(d)` calls `set_pending([Move(K,R,d) …])`, which validates and solves
the active edits plus that one move, ignoring any previous pending edit, and shows the result
at once. For d = 0 it shows the line of the active edits, and `drop` then commits nothing. ∎
In Example 3, D is absent exactly at the steps where C covers x₃ (Forbids holds at once,
T1), and present at the first step past it, since that step is computed from scratch.

## 5. Choices where the requirements leave freedom

* **Positions of different ordered disjunctions at the same position** are decided in the
  topological order of their rules (the requirement orders positions, not rules).
* **Which older claims adapt** in a conflict that no choice of operands avoids (§3 step 1):
  every claim of the older edit that takes part in it is dropped, and no claim of that edit is
  preferred over another. (Conflicts that come from the operands keep the older claims closest
  to 0 first, step 2.) Supp
  follows every derivation, so with redundant rules (e.g. `A → C` and `A ∧ B → C`) it can
  drop more than a minimal repair. Minimal repairs are not unique in general (with
  `A ∧ B → C`, old A and B and a newer ¬C, dropping A and dropping B are both minimal).

## 6. Running

```
python reverse_logic.py                       # the three examples
python -m unittest test_reverse_logic         # REVLOGIC_SEEDS=1500 for a stress run
```
