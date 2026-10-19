# Reverse logic: formal semantics and proofs

Code: `region.py` (exact region algebra), `rules.py` (rules), `engine.py`
(history reduction, solver, `LineSystem`, `Drag`), `reverse_logic.py` (public
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
| Unique result; first operand that can be placed | T5, §3 step 1 |

The requirements leave two things to choose; §5 states the choices made.

## 1. Regions and why the line is computed from the history

A kind's presence is a region: a finite union of intervals with independently
open or closed endpoints, kept in a canonical form (sorted, disjoint, maximal),
so `==` is set equality and two touching intervals of one kind are one
element. The family is closed under ∪, ∩, \ and translation: [0,2] minus [0,1]
is (1,2]. Coordinates are exact rationals (`fractions.Fraction`); floats are
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

1. **Choices.** Every new anchor x of `P → O₁ ∨ … ∨ Oₘ` (an anchor not already in Γ⁺) gets an
   operand. If some assignment of operands gives step 3 no violation, the
   first such assignment is used. Anchors are ordered by the topological position of their rule,
   then rule order, then position from left to right, and operands in their written order.
   Old anchors keep their recorded operand.
2. **Closure.** C = least model of Γ⁺ ∪ Pᵢ with these choices (one pass in topological order).
3. **Conflicts.** Violations are: an atom of C on an accepted or level-i absent claim, a `Forbids`
   instance in C, or a failed anchor. Seeds = their atoms outside Γ⁺.
   Supp = every atom outside Γ⁺ used in *some* derivation (in C) of a seed, computed exactly on regions
   by backward propagation in reverse topological order.
4. **Adaptation.** Drop Pᵢ ∩ Supp and Nᵢ ∩ C, then recompute C. Add what remains to Γ.

The line is Γ⁺ after the oldest level. Everything not in Γ⁺ is absent.

**Computing step 1 exactly.** Every rule offset is an integer multiple of g, the gcd of all
offsets, so a rule only relates points of one lattice r + gℤ. Split the residues r ∈ [0, g)
at every endpoint of every claim (mod g). On each piece every region involved contains the
same lattice points r + kg for every r, so all lattices of the piece pose the same finite
problem, solved once by backtracking in the order of step 1 (`Solver._solve_cell`). Pruning is
sound because the rules are monotone: an undecided anchor derives nothing and deciding it only
adds atoms, so a violation already found remains. On a piece where no assignment exists any
choice gives a violation; there the approximate choice of `Solver.choices` is used and step 4
makes the level adapt. The search is exponential in the worst case; deciding whether an
assignment exists encodes SAT, so no exact method avoids this in general.

**Theorem 3.2 (adaptation removes every conflict).** After step 4, C has no violation.
*Proof.* Choices are fixed functions of the anchor, so the closure is monotone and the new C' ⊆ C.
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
*Proof.* Step 1 then uses such an assignment on every piece, so steps 3 and 4 find nothing. ∎

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
hold and every element it puts is on the line; older elements adapt (step 4) to make that
possible, whatever the kind of edit: an added element pushes out what forbids it, a removed
element removes the premises of the rules that need it. (b) An edit that fits the line is
refused by (solve) iff no choice of operands lets its own claims satisfy the rules, which
depends only on the edit and the rules, since older elements adapt; and by (post) only when
it could not be undone or could not keep an element it puts.
*Proof.* The newest level is processed first, with Γ = ∅. (a) holds by (solve) and (post).
(b) is Lemma 3.3 in one direction; in the other, if every assignment has a violation, Supp
is non-empty and something of the level is dropped. ∎

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
topological order is canonical (ties broken by name), the choice order of §3 step 1 is fixed,
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

* **Which operand when several assignments work:** the first in the order of §3 step 1.
  The requirement says "the first operand that can be placed is used" for one anchor; when
  two anchors compete (for example with "no B 1 unit right of B"), the anchor further left
  keeps its first operand.
* **Which older claims adapt:** every claim of an older edit that takes part in a conflict
  with newer edits is dropped, and no claim of that edit is preferred over another. Supp
  follows every derivation, so with redundant rules (e.g. `A → C` and `A ∧ B → C`) it can
  drop more than a minimal repair. Minimal repairs are not unique in general (with
  `A ∧ B → C`, old A and B and a newer ¬C, dropping A and dropping B are both minimal).

## 6. Running

```
python reverse_logic.py                       # the three examples
python -m unittest test_reverse_logic         # REVLOGIC_SEEDS=1500 for a stress run
```
