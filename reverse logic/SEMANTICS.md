# Reverse logic: formal semantics and proofs

Code: `region.py` (exact region algebra), `rules.py` (rules), `engine.py`
(history reduction, solver, `LineSystem`), `reverse_logic.py` (public API and
the example). `test_reverse_logic.py` checks every theorem below on random
histories, and checks the acceptance of edits against a brute-force search.

## 0. Verdict

"Holds" means proved below with no extra assumption. Where the requirements
leave something undefined, the definition used is named, and it is listed in
§5 so that it can be changed.

| Requirement | Status |
|---|---|
| R1 add then remove → previous line | **Holds** (T3) |
| R2 remove then add → previous line | **Holds** (T3) |
| R3 move then move back → previous line | **Holds** (T3) |
| R4 reverse A after B → as if A never happened; then reverse B → before A | **Holds** (T4), for "B requires A" as defined in §4. The requirements do not define it |
| R5 older elements adapt to a new edit; contradictory edits are refused | **Holds** (T1, T2): an edit is refused iff no choice of operands satisfies the rules |
| R6 no ambiguity | **Holds** (T5). Where the requirements allow several results, §5 fixes one |
| No rule cycles | **Holds**: production rules are checked (`RuleCycleError`) |
| Removing or moving something that is not on the line | **Refused** (`MissingElementError`) |

Not decided by the requirements and therefore open (§5): adding an element
where the same kind is already present. It is currently allowed and counted
twice, so moving it afterwards leaves one copy behind.

## 1. Why the line is computed from the history

The requirements do not say how the line is stored. These facts explain why
the engine keeps the edit history instead of only the visible line. They are
reasons for the design, not limits of the requirements.

**H1. Undo needs memory.** Take the rule `D → ¬C`. From {D on r}, adding C on r
gives {C on r} (D adapts, R5). From ∅, adding C on r also gives {C on r}.
Removing C must then give back {D on r} in the first case and ∅ in the second
(R1). The visible line {C on r} is the same in both cases, so the engine has to
remember more than the visible line. It keeps the active edits and recomputes
the line from them.

**H2. Adding twice.** Adding C on r while C is already there, then removing it,
must leave C present (R1). So the engine counts claims: two adds and one remove
leave the element present.

**H3. Floats break R3.** In floating point, (x + d) − d ≠ x for some x and d.
All coordinates are exact rationals (`fractions.Fraction`). Floats are read as
their decimal text, so 0.1 means 1/10.

Regions are finite unions of intervals with independently open or closed
endpoints: [0,2] minus [0,1] is (1,2], not [1,2]. This family is closed under
∪, ∩, \ and translation, and is kept in a canonical form, so `==` is set
equality.

## 2. Edits and history reduction

An edit is a finite set of ops. Each op adds a signed indicator to a count
function per kind:
`Add(K,R)`: +1_R. `Remove(K,R)`: −1_R. `Move(K,R,δ)`: −1_R + 1_{R+δ}.
`inverse(ops)` negates the contribution exactly.

An op *takes* K from R (`Remove`, and the source of `Move`) and *puts* K on a
region (`Add`, and the target R+δ of `Move`). Within one edit and one kind, no
two ops take from the same point, no two put on the same point, nothing is
added where something is taken, and nothing is removed where something is put.
A move's own source and target may overlap. This condition is unchanged by
`inverse`, which swaps taking and putting.

Let the active edits be e₁ < … < eₙ (by creation time). Fix a kind K and a point x, and let cᵢ = cᵢ(K,x) ∈ ℤ.
**Reduction** processes i = n, n−1, …, 1 (newest first) with a stack. cᵢ is
pushed. If its sign is opposite to the top of the stack, the two cancel by min(|cᵢ|, |top|). What
remains of cᵢ is eᵢ's **effective claim** at (K,x): *present* if > 0, *absent* if
< 0, none if 0. Call Pᵢ and Nᵢ the regions of effective present and absent claims of eᵢ.

**Lemma 2.1.** If eₙ₋₁ = e and eₙ = inverse(e), every effective claim is the same as for
e₁…eₙ₋₂, and eₙ₋₁ and eₙ have none.
*Proof.* At every (K,x), the first two entries processed are −c then c. −c is pushed onto
the empty stack, and c cancels it exactly. The stack is then empty, so the rest of the run is
the run for e₁…eₙ₋₂. ∎

## 3. Solving: priorities, adaptation and the least model

Rules are definite clauses `Implies`, negative clauses `Forbids`, and ordered
disjunctions `ImpliesAny`, all pointwise and translation-invariant (`rules.py`). Production rules form an acyclic graph, which
gives a topological order of the kinds.

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
   Supp = every atom outside Γ⁺ used in *some* derivation (in C) of a seed. This is computed exactly on regions
   by backward propagation in reverse topological order.
4. **Adaptation.** Drop Pᵢ ∩ Supp and Nᵢ ∩ C, then recompute C. Add what remains to Γ.

The line is Γ⁺ after the oldest level. Everything not in Γ⁺ is absent.

**Computing step 1 exactly.** Every rule offset is an integer multiple of g, the gcd of all
offsets. A rule therefore only relates points of the same lattice r + gℤ. Split the
residues r ∈ [0, g) at every endpoint of every claim (mod g). On each piece, every region
involved contains the same lattice points r + kg for every r, so all lattices of the piece
pose the same finite problem. It is solved once per piece by a backtracking search in the
order of step 1 (`Solver._solve_cell`). Pruning is sound because the rules are monotone: an
undecided anchor derives nothing, and deciding it only adds atoms, so a violation already
found remains. On a piece where no assignment exists, any choice gives a violation. There
the approximate choice of `Solver.choices` is used (first operand that does not create a
violation by itself, or a failed anchor), and step 4 makes the level adapt. In the worst
case the search is exponential in the number of anchors on a lattice. Deciding whether an
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
recorded choices, the `ImpliesAny` rules. Theorem 3.2 shows no `Forbids` rule or absent claim is
violated. ∎

## 4. Valid edits, reversal and dependency

`apply(e)` accepts e iff e is **valid** on top of the active history H:

* (pre) every region e takes from is on the line of H (else `MissingElementError`);
* (solve) with e as the newest level, nothing of e's level is dropped;
* (post) every region e puts on is on the resulting line.

Otherwise it raises and leaves the history unchanged. Removing a derived
element is allowed: it is on the line, and the rule's premise adapts.

**Invariant (realizable history).** Every active edit is valid on top of the
active edits before it. `apply` keeps this by definition. `reverse(a)` replays
the active edits after a without a and refuses (`DependencyError`, nothing
changes) if one of them is not valid. The newest active edit can always be
reversed.

**Definition.** B **requires** A iff B is not valid on top of the active edits before B,
without A and without the edits that themselves require A (`LineSystem.requires`).
Example: A adds E, B moves that E. Without A there is no E to move, so B requires A.
`reverse(a)` is refused iff some later active edit requires A.

**Theorem T2 (R5).** (a) After an accepted edit, its effective claims hold and every element it puts is on
the line; older levels adapt (step 4) to make that possible. (b) Leaving aside the (pre) check, an edit is
refused by (solve) iff no choice of operands lets its own claims satisfy the rules. This depends only on the
edit and the rules, never on the older elements, which adapt.
*Proof.* The newest level is processed first, with Γ = ∅. (a) holds by (solve) and (post). For (b), apply
Lemma 3.3 in one direction. In the other, if every assignment has a violation, Supp is non-empty and something
of the level is dropped. ∎
For the example, adding C and D at x₈ in one edit violates `D → ¬C` with Γ = ∅, so the edit is refused.

**Theorem T3 (R1, R2, R3).** Let H be a realizable history and e an edit just accepted on top of it.
Then `inverse(e)` is valid right after e, and the line afterwards is the line of H.
*Proof.* (pre): inverse(e) takes exactly where e put, which is on the line by e's (post). By Lemma 2.1
the two newest levels have no claims and all other claims are those of H. A level with no claims, processed
while Γ = ∅, leaves Γ = ∅, so the solver runs exactly as on H: (solve) holds and the line is that of H.
(post): inverse(e) puts exactly where e took, which was on the line of H by e's (pre). The same-edit
condition of §2 is symmetric under inverse. "Remove it" is `Remove(K,R)` after `Add(K,R)`, "add it" is the
converse, and "move them back" is `Move(K,R+δ,−δ)` after `Move(K,R,δ)`. ∎

**Theorem T4 (R4).** Let A precede B, with B not requiring A. Reversing A is allowed when no later
active edit requires A, and the line then equals the line of the same history with A never made.
After then reversing B, it equals the line before A was made (plus any other still-active edits).
*Proof.* The line is a function of the ordered list of active edits. Reversing A yields exactly the list
without A, and that history is realizable because the replay in `reverse` checked every later edit.
Reversing B then removes B from the list. ∎

**Theorem T5 (R6, determinism).** The line is a function of the ordered active
history. The topological order is canonical (ties broken by name), the choice order of step 1 is fixed, and
every other step is a set-theoretic definition, independent of iteration order. ∎

## 5. Definitions chosen where the requirements are silent

Each item below is a choice, not a consequence of the requirements. Each one can be changed.

* **Which operand when several assignments work.** The first in the order of §3 step 1. The requirement only
  says "the first operand is used" for a single anchor. When two anchors compete (for example with
  "no B 1 unit right of B"), the anchor further left keeps B.
* **Which older claims adapt.** When older claims must give way, every claim of that edit that takes
  part in the conflict is dropped, and no claim of the edit is preferred over another. Supp
  follows every derivation, so with redundant rules (e.g. `A → C` and `A ∧ B → C`) it can drop more
  than a minimal repair. Minimal repairs are not unique in general (with `A ∧ B → C`, old A and B
  and a newer ¬C, dropping A and dropping B are both minimal), so some rule is needed.
* **"B requires A"** is defined in §4. Reversing an edit that a later edit requires is refused
  rather than reversing both.
* **Adding where the same kind is already present** is allowed and counted twice (H2). Moving
  then moves one copy and leaves the other. *Open:* refusing such adds would make every
  element single, so a move would always move everything there.

## 6. Running

```
python reverse_logic.py                       # the requirements example
python -m unittest test_reverse_logic         # REVLOGIC_SEEDS=1500 for a stress run
```
