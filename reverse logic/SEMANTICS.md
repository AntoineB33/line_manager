# Reverse logic: formal semantics and proofs

Code: `region.py` (exact region algebra), `rules.py` (rules), `engine.py`
(history reduction, solver, `LineSystem`), `reverse_logic.py` (public API and
the example). `test_reverse_logic.py` checks every theorem below on random
histories.

## 0. Verdict

| Requirement | Status |
|---|---|
| R1 add then remove → previous line | **Holds for every line**, but only with history semantics (§1, I1–I2) |
| R2 remove then add → previous line | **Holds for every line**, same caveat |
| R3 move then move back → previous line | **Holds for every line**, including when same-kind elements overlap (I3) |
| R4 reverse A after B → as if A never happened; then reverse B → before A | **Holds** whenever B does not require A, with "require" defined precisely (I6, T4) |
| R5 older elements adapt to a new edit; contradictory edits are refused | **Holds** (T1, T2) |
| R6 no ambiguity | **Holds**: the line is a function of the active history (T5). Where the requirements leave a choice, a policy is fixed (§5) because unique minimal adaptation does not exist in general (I4) |
| No rule cycles | Enforced on production rules (`RuleCycleError`) |

Some literal readings are **impossible**. Section 1 proves this and explains which reading is implemented.

## 1. Impossibility results

**I1. The line can't be updated as a function of the visible line alone.**
Take the rule `D → ¬C`. Let S₀ = {D on r} and S₀' = ∅. By R5, adding C on r gives {C on r} in
both cases (D has to adapt). If "remove C" is a function f of the visible line,
then f({C on r}) must equal S₀ by R1 starting from S₀, and S₀' by R1 starting from S₀'. Since S₀ ≠ S₀', no such f exists. ∎
So the line has to be computed from the edit history. The engine never stores
the line. It recomputes it from the active edits.

**I2. R1 and R2 force multiplicity.** Suppose C is already present on r and the user
adds C on r. If "add" is a set union, the line doesn't change, and then R1 requires
that removing C on r leaves C present. So "remove" can't always mean "make absent".
Symmetrically, R2 means "add" can't always mean "make present". The engine therefore counts
claims: adding twice and removing once leaves the element present. ∎

**I3. R3 has the same issue for moves.** {A on [0,1], A on [5,6]} and {A on [5,6]} both become
{A on [5,6]} after moving [0,1] by +5. Moving [5,6] back by −5 must
restore two different lines. Again, counts (or identity) are needed. ∎

**I4. "Minimal adaptation" is not unique.** With `A ∧ B → C`, the old claims A and B at
x, and a newer claim ¬C at x, both "drop A" and "drop B" are minimal repairs. No
rule-independent principle prefers one of them, so the requirements alone are ambiguous. A
policy must be fixed (§5). Similarly, with ordered disjunctions plus negative rules,
deciding whether *some* choice of operands is consistent encodes SAT. The
requirement's own wording ("the first operand is used") already chooses ordered
semantics, and that is what is implemented.

**I5. Floats break R3.** In floating point, (x + d) − d ≠ x for some x and d. All coordinates are
exact rationals (`fractions.Fraction`). Floats are read as their decimal text, so 0.1 means 1/10.

**I6. R4 needs "B requires A" to be defined.** Example (rule `F → ¬E`): A removes E,
then B adds E and F. Without A, B is self-contradictory, so the line "as if A
never happened" cannot contain B. Definition: **B requires A iff B would be
rejected in the history without A** (`LineSystem.requires`). R4 is proved for
every B that does not require A. Reversing A is always allowed; if B requires A,
B's claims simply adapt.

Endpoints matter as well: [0,2] minus [0,1] is (1,2], not [1,2]. Regions are finite unions of
intervals with independently open/closed endpoints. This family is closed under ∪, ∩, \, and
translation, and is kept in a canonical form, so `==` is set equality.

## 2. Edits and history reduction

An edit is a finite set of ops. Each op adds a signed indicator to a count
function per kind:
`Add(K,R)`: +1_R. `Remove(K,R)`: −1_R. `Move(K,R,δ)`: −1_R + 1_{R+δ}.
`inverse(ops)` negates the contribution exactly.
An edit is rejected outright if, for some kind, an `Add` region meets a `Remove` region in the same edit.

Let the active edits be e₁ < … < eₙ (by creation time). Fix a kind K and a point x, and let cᵢ = cᵢ(K,x) ∈ ℤ.
**Reduction** processes i = n, n−1, …, 1 (newest first) with a stack. cᵢ is
pushed. If its sign is opposite to the top of the stack, the two cancel by min(|cᵢ|, |top|). What
remains of cᵢ is eᵢ's **effective claim** at (K,x): *present* if > 0, *absent* if
< 0, none if 0. Call Pᵢ and Nᵢ the regions of effective present and absent claims of eᵢ.

**Lemma 2.1.** If eₙ₋₁ = e and eₙ = inverse(e), every effective claim is the same as for
e₁…eₙ₋₂, and eₙ₋₁ and eₙ have none.
*Proof.* At every (K,x), the first two entries processed are −c then c. −c is pushed onto
the empty stack, and c cancels it exactly. The stack is then empty, so the rest of the run is
the run for e₁…eₙ₋₂. ∎ (Reducing oldest-first does *not* have this property.
For +₁ −₂ +₃, it keeps +₃ instead of +₁, which breaks R2.)

## 3. Solving: priorities, adaptation and the least model

Rules are definite clauses `Implies`, negative clauses `Forbids`, and ordered
disjunctions `ImpliesAny`, all pointwise and translation-invariant (`rules.py`). Production rules form an acyclic graph, which
gives a topological order of the kinds.

Levels are processed from **newest to oldest**, carrying the accepted state Γ:
accepted present claims, accepted absent claims, Γ⁺ = their least model, and the operand recorded for every
`ImpliesAny` anchor in Γ⁺. For level i:

1. **Choices.** b₀ = least model of Γ ∪ Pᵢ without expanding new disjunction
   anchors. Forb(K) = points y where adding K(y) to b₀ creates a violation that wasn't there before
   (computed exactly by a relative closure, recursing on kinds in reverse
   topological order). A new anchor x of `P → O₁ ∨ … ∨ Oₘ` takes the first Oⱼ
   with x+oⱼ ∉ Forb(Oⱼ). If there is none, the anchor *fails*. Old anchors keep their recorded choice.
2. **Closure.** C = least model of Γ⁺ ∪ Pᵢ with these choices (one pass in topological order).
3. **Conflicts.** Violations are: an atom of C on an accepted or level-i absent claim, a `Forbids`
   instance in C, or a failed anchor. Seeds = their atoms outside Γ⁺.
   Supp = every atom outside Γ⁺ used in *some* derivation (in C) of a seed. This is computed exactly on regions
   by backward propagation in reverse topological order.
4. **Adaptation.** Drop Pᵢ ∩ Supp and Nᵢ ∩ C, then recompute C. Add what remains to Γ.

The line is Γ⁺ after the oldest level. Everything not in Γ⁺ is absent.

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

**Theorem T1 (rules always hold).** The line satisfies every rule, and every accepted claim holds.
*Proof.* By induction on Theorem 3.2: the final least model satisfies the `Implies` rules and, with its
recorded choices, the `ImpliesAny` rules. Theorem 3.2 shows no `Forbids` rule or absent claim is
violated. ∎

## 4. Acceptance

`apply(e)` computes the solution with e as the newest level and **accepts iff
nothing of e's level is dropped**. Otherwise it raises `ContradictionError` and
leaves the history unchanged.

**Theorem T2 (R5).** (a) After an accepted edit, all of its effective claims hold, and
older levels adapt (step 4) to make that possible. (b) Whether an edit is accepted depends only on its own effective
claims and the rules, never on the elements already on the line.
*Proof.* The newest level is processed first, with Γ = ∅. So its conflicts involve only its own
claims, and if there are none it is fully accepted and lies in the final Γ. ∎
For the example, adding C and D at x₈ in one edit violates `D → ¬C` with Γ = ∅, so the edit is refused.

**Theorem T3 (R1, R2, R3).** Let H be any history and e any accepted edit. Then
`inverse(e)` applied right after e is always accepted, and the line afterwards is the line of H.
*Proof.* By Lemma 2.1, the two newest levels have no claims and all other claims are those of H.
A level with no claims, processed while Γ = ∅, leaves Γ = ∅. So the solver then runs exactly as on H.
inverse(e) is accepted because it has no claims to drop, and the same-edit
Add/Remove check is symmetric under inverse. "Remove it" is `Remove(K,R)` after
`Add(K,R)`, "add it" is the converse, and "move them back" is `Move(K,R+δ,−δ)` after
`Move(K,R,δ)`. ∎

**Theorem T4 (R4).** Let A precede B, with B not requiring A (I6). After reversing A, the line
equals the line of the same history with A never made. After then reversing B, it equals the line
before A was made (plus any other still-active edits).
*Proof.* The solver's input is the ordered list of active edits, and only their relative order matters.
Reversing A yields exactly the list of the history without A. That history is
realizable because B, and by the same definition every later edit, would have been accepted. Reversing B
then removes B from the list. ∎

**Theorem T5 (R6, determinism).** The line is a function of the ordered active
history. The topological order is canonical (ties broken by name). Every step is a set-theoretic definition,
independent of iteration order. Ordered disjunctions use the first
non-forbidden operand. ∎

## 5. Policies where the requirements leave a choice

* **Same-level conflicts (I4).** If claims of one edit can no longer coexist
  because of newer edits, *all* of them that take part in the conflict are dropped. No claim of
  that edit is preferred over another. Supp follows every derivation, so with
  redundant rules (e.g. `A → C` and `A ∧ B → C`) it can drop more than a
  minimal repair would.
* **Ordered disjunction.** `implies_any("A", ["B","C"])` uses B unless B is forbidden
  by newer claims (or by the same edit's own claims). Then it uses C. If every operand
  is forbidden, A adapts, or, in the newest edit, the edit is refused. B is used
  even if C already happens to be present. An edit can be refused even though a
  different operand assignment would work (I4); "contradictory" means contradictory under ordered semantics.
* **Counts (I2).** Removing an element that is present only because a rule
  derives it records an absent claim, so the rule's premise adapts. Removing
  where nothing is present leaves a debt that a later add cancels.
  Moves translate counts, not "whatever is there".

## 6. Running

```
python reverse_logic.py                       # the requirements example
python -m unittest test_reverse_logic         # REVLOGIC_SEEDS=1500 for a stress run
```
