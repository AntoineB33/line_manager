# Rules generation: semantics and proofs

Code: `chip.py` (chip position and the mode transitions it goes through),
`track.py` (texts, and a reverse-logic `LineSystem` edited by them and by the
user), `machine.py` (the machine that runs an optimizer to generate the texts),
`scheduler.py` (an optimizer: a problem and its solver),
`rules_generation_logic.py` (public API and the demo). `test_rules_generation.py`
checks every item of the requirements below, for any optimizer, and
`test_scheduler.py` every item of "The optimizer", both including random runs.

Requirements: `../requirement.md` and `requirements.md`. Everything about edits,
rules, adaptation and reversal comes from `../reverse_logic/SEMANTICS.md`.
Section "Choices the requirements leave open" lists where several behaviours
satisfy the requirements, and which one is implemented.

## Requirements and how each one is met

**R1. The chip moves right, continuously; it can stop.** Positions are exact
rationals. `update_chip(q, transitions)` refuses q < position and accepts
q = position. A program tells the chip's moves one at a time, but the chip goes
through every position on the way, and the track computes what happens at
every one of them, exactly (R5, Theorem M): the result does not depend on how a
move is split into steps.

**R2. One mode per position; transitions are given externally, not known to the
generator.** The modes are not decided by the system: the user tells each transition `(position, mode)`
with the move that goes through it, in (chip, q], strictly increasing
(`Chip.check`). The mode at x is the mode of the last transition at or before x
(the new mode applies from the transition position on). Nothing ever sees a
transition before the chip reaches it: texts have no transitions, and the
optimizer only knows a tendency (see "The optimizer").

**R3. Finitely many transitions in every bounded stretch.** This is a property
of the user's moves, which the system cannot check in advance; each move tells
finitely many, so every stretch the chip has gone through has finitely many.

**R4. A text's rules edit the right side of the chip, from (position, mode,
state).** A `Text` has discrete rules (`Rule`), continuous edits (`Follow`) and
`rerun`. A `Rule(action, where, modes, when=…)` runs `action(position, mode,
state)` where the chip enters each stretch of positions where it is in `where`,
its mode in `modes` and, with `when`, a condition on the state holds ("if
element D exceeds a presence ratio of y …", `Ratio`, see "Conditions on the
state"): at the first position of the stretch, or at its start if the stretch is
open there (the chip is then at the start, about to enter it); and at the install
if the chip is inside such a stretch. `state` is a read-only mapping kind →
region: the line without the text's continuous edit. Every region an edit takes from or
puts on must lie in (position, +∞): otherwise `ValueError` and nothing changes.
An edit that reverse logic refuses (it removes something absent, contradicts
the rules, changes the left side, …) is skipped, recorded in
`TrackManager.refused`, and the text keeps running.

**R5. Continuous edits.** `Follow(kind, lo, hi, …, where, modes)` puts `kind`
between `lo` and `hi`, each a fixed position or `Rel(a)`, the chip's position
plus a, wherever the chip is in `where` and in one of `modes`. At every chip
position p it is shown, strictly right of p, as the pending edit of the
`LineSystem`, computed from the active edits at p exactly like a drag step (T6
of reverse logic), if that is valid there; otherwise it is refused at p. What
the chip leaves behind is defined in R6 and computed exactly (Theorem M).

**R5'. A text says when the generator runs again.** `Text.rerun(position, mode,
state)` is evaluated after every operation of the track (a chip move, a user
edit, an install); a `Rule` with `rerun=True` that runs asks too. The answer is
`TrackManager.rerun`, which the machine passes to the optimizer in the `View`
(R9). The time of a run is outside of the line (R8), so this needs no more
precision than the operations.

**R6. Only user edits change the left side of the chip.** Let B = (−∞, chip].
Every edit of a text is applied with `keep=B`: it is refused if the line on B
would change (for example through a rule "C 3 units on the left"); since the
chip's position is in B, a text never changes the point the chip is at. So a
point keeps what it had when the chip reached it: the state it had just before,
lim_{p→x⁻} L_p(x), where L_p is the line with the chip at p. The track records
it with `_pin_left`/`_freeze`: an internal edit, newest of all, claiming what
is on B present and everything else on B absent (consecutive ones merge into
one). User edits are newer than the pins and are not limited: they may change
the left side, and nothing else does.
*A pin is never refused.* Its claims are the left side of a line that satisfies
the rules: the line before the operation, or, for what the chip leaves, the
limit of lines L_p that do (a rule instance among points of B holds in every
L_p near the limit, and the points' states are constant there, Theorem M). It is
processed first, with Γ = ∅, so by Lemma 3.3 of reverse logic nothing of it is
dropped, and it holds exactly on B (T2). The code asserts it.
Every operation runs on a copy, which replaces the manager only if the whole
operation succeeded (and, except for a user edit, the left side is unchanged).

**R7. A text switch.** `switch_rules_text(text)`:
1. hides the previous text's continuous edit (it lies strictly right of the chip);
2. reverses every edit of the previous text, and every user edit that requires one of
   them, with `LineSystem.reverse_all`; the line becomes the line of the remaining edits,
   as if the reversed ones had never been made (T4 of reverse logic);
3. pins the left side as in R6. So nothing changes on B; the right side is what it would
   be without the previous text's edits, adapted where B requires it (an A just left of
   the chip with "A → B 2 units on the right" keeps its B: rules triggering from the left
   still project their consequences to the right), and an element that straddles the
   chip is cut there;
4. runs the new text's rules that hold at the chip, then shows its continuous edit.

Elements the track starts with, and user edits that require no text edit, are kept.

**R8. Generation time.** It is outside of this system: the new text applies
from the position of the chip when `switch_rules_text` is called.

**R9. The machine generates the texts, when it decides to.** `Machine` drives a
`TrackManager` with an `Optimizer`, which supplies its own policy: `plan(view,
resources)` returns the time to allow a new run, or `None`; `generate(view,
deadline, resources)` returns a text, or `None`. The requirements constrain how
runs reach the line, which is what is proved and tested for any optimizer (the
tests use scripted ones, a simulated clock and a runner that finishes runs when
the test chooses, plus real processes for R11 and R12).
An observation is the start, a chip move, a user edit, or `poll`. At each one,
in this order:
1. the run in progress is handled: stopped past its deadline (R12), or, if it has finished, as in R13;
2. if no run is in progress, `plan` is called with a snapshot `View` of the line
   and the current `Resources`, and a run starts if it returns a time. The view
   holds the chip's position, mode and the transitions it went through, the line, the
   installed text, whether it asks for a new one (R5'), the refused edits and the
   performance (R14); the run is given the same view without the text (a text installed
   directly need not be picklable).

So the optimizer is asked at every observation where it could start a run, and
never while one is in progress: *at most one run is in progress at a time.* ∎

**R10. CPU resources: every CPU, up to a limit the user sets.**
`Resources(limit)`: a run may use every CPU this process may run on
(`usable_cpus()`), or only `limit` of them, an integer from 1 to that number
(`None` means all; anything else is `ValueError`). The user may replace
`machine.resources` at any time; `plan` and `generate` receive the current value.
The time of a run is the optimizer's choice: `plan` returns a positive number of
seconds t, finite, and the run gets `Deadline(start,
t)`, with `start` the clock when it starts. Any other answer from `plan`, or an
exception in it, is recorded in `refused_plans` and starts no run. *Nothing the
optimizer does makes a chip move or a user edit raise:* that move or edit has
already happened when `plan` is asked.

**R11. Each run is a process of its own, held by the operating system**
(`ProcessRunner`). The process is started fresh ("spawn") and waits until the
machine has put it under its limits; only then does it call `generate`. On
Windows the machine holds a job object, with the CPUs of the limit as its
affinity and "kill on close": Windows keeps every process of the job, including
the processes the run starts, on those CPUs, and ends all of them together. On
Linux the CPU affinity of the run is set before it starts working, the processes
it starts inherit it, and the run is a process group of its own. Platforms that
cannot limit the CPUs of a process refuse a limit below all CPUs (the run's
outcome is `error`). Since the optimizer, the view and the text are sent between
processes, they must be picklable: functions in a text must be defined at module
level (or be instances of such classes), not lambdas or nested functions. An
unpicklable optimizer is found before any process starts, and an unpicklable text
inside the run; both make the run's outcome `error`.

**R12. Hard stop.** At `start + t`, a timer ends the run's job (Windows) or
process group (elsewhere), even if nothing observes the machine at that moment;
an observation past the deadline also stops it. *The run, and every process it
started, stop running at the deadline* (up to the time the operating system takes
to end them; the test allows 0.5 s and checks that nothing they write grows
afterwards). Its outcome is `stopped`, and it gives no text. When a run returns,
whatever it left running is ended too. On Linux, a process that leaves its group
on purpose (`setsid`) escapes the stop; on Windows nothing of the job can.

**R13. A finished run.** The clock is read in the run's process when `generate`
returns (`finished`; `time.monotonic` is the same clock in every process). Then,
at the next observation:
* it raised: outcome `error`, nothing changes;
* `finished > start + t`: outcome `late`, the text is discarded. With R12, *no text
  produced after the time the run allowed itself is ever installed*;
* it returned `None`: outcome `no text`;
* otherwise `switch_rules_text(text)` at the chip's position at that moment (R7,
  R8): outcome `installed`, or `refused` if the switch raised. The switch is a
  transaction, so a refused text changes nothing.

The run's `View` is a copy taken when it started, and the chip may have moved
since: the text applies from the chip's position at install.

**R14. The performance hyperparameter**, `Machine.performance` ∈ (0, 1)
(`ValueError` otherwise, 0 and 1 included; the user may change it at any time),
is in every view. What it means depends on the problem, so the optimizer applies
it; the machine only requires a positive, finite time for every run. For the
optimizer of this module, with f = (1 − h)/h (f → +∞ as h → 0, f → 0 as h → 1,
f = 1 at h = 1/2): a run is allowed `time_scale`·f seconds, its horizon is
`lookahead`·f, its grid `resolution`/f, and a text asks for a new one when its
score may be more than `scale`·f below the optimum (Theorem 4), or near the end
of its horizon. So the ends of the range are limits, never reached:
* as h → 0, time, horizon and tolerance grow without bound and the grid gets
  infinitely fine: the expected value of a text tends to the best expected value
  of all policies, the modes not being known in advance (Theorems 3 and 5), and no
  change starts a new run;
* as h → 1, the time tends to 0: once it is too short for a single unit, the text
  is empty, and it asks for a new one at every change of the score (tolerance → 0).

## Moving the chip

The track computes, for a move from a to b, the line at every position on the
way, without sampling: `_advance` goes from one *candidate* position to the
next, and handles each gap between candidates at once.

**Candidates.** Let g be the gcd of the rule offsets (g = 0 if there is none),
A the offsets of the moving ends of the text's continuous edits and of the
windows of its conditions (with 0), and C the fixed positions: the ends of the
elements and of the active edits, of the positions of the text's rules and
continuous edits, the fixed ends of its continuous edits and windows, the
transitions, 0 and b. The candidates after the chip are the numbers c − a + k·g
(c ∈ C, a ∈ A, k ∈ ℤ); without continuous edit or condition, C itself. At a
candidate the chip stops: the transitions there apply, the left side is pinned,
and the rules whose stretch starts there run; it also stops where a condition
changes (next section).

**Theorem M (moves are exact).** With the chip at p, the line L_p is: the active
edits, the pins of what the chip left on (−∞, p], and the continuous edit at p
if valid there (R5). Then (a) between two candidates, L_p changes only by its
ends moving with p; (b) what the chip leaves on a gap, and at its end, is
computed exactly; (c) the line after a move, and every edit made during it, are
the same however the move is split into steps.
*Proof.* (a) Every region in the computation at p has ends of the form
c + k·g or p + a + k·g: the active edits have fixed ends, the continuous edit
ends p + a or fixed, the pin, `keep` and the clipping at the chip end at p, and
the solver only shifts by rule offsets (multiples of g) and takes ∪, ∩, \, which
select ends. Every decision of the solver (an empty region, a comparison, the
order of ends modulo g in its exact choices) compares two such numbers; two can
only change order where p = c − a + k·g, a candidate, since the other
differences do not depend on p. (Candidates stay discrete: new fixed positions
come from pins and rules at candidates, so all candidates lie in C₀ + dℤ, d the
generator of the group spanned by g and A — a finitely generated subgroup of ℚ is
cyclic — and there are finitely many in a bounded stretch.)
(b) With continuous edits the candidates include every multiple of g, so a gap
is shorter than g, and no rule relates two of its points; the state the chip
leaves at x in the gap, lim_{p→x⁻} L_p(x), depends on the pins in the gap only
through whether the continuous edit is shown. By (a) it is the same for every x
of the gap: the kinds K with (p, p + ε) ⊂ L_p[K], for any p of the gap. At the
gap's end c′ it is whether c′ ∈ L_p, for p = c′ − η with η smaller than every
|a + k·g| ≠ 0, so that c′ lies where it lies for p → c′⁻. Whether the
continuous edit is shown is the same on the whole gap, by (a); it is shown iff
it is valid at p with the left side that showing it leaves (`_sweep`). This last
choice is a convention (see "Choices the requirements leave open", 3).
(c) A stop at s inside a gap splits it into two gaps of the same kind, with the
same results (by (a)); no rule starts at s unless s is a candidate, and the pins
of the two halves merge. ∎ (`test_random_moves_do_not_depend_on_the_steps`.)

**Conditions on the state.** `Ratio(kind, lo, hi, y)` holds at p when kind
covers more than a fraction y of the window [lo(p), hi(p)] (each end fixed or
moving with the chip) in the state the rules see; its *excess* is
e(p) = measure(kind ∩ window) − y·length. *Lemma C.* On a gap, e is affine in p.
*Proof.* By Theorem M (a) (the windows' ends are among the candidates' offsets
and fixed positions), the regions of the state have ends fixed or p + const, in
an order that does not change on the gap, and so do the window's ends; the
measure of an intersection is then a sum of differences of such ends. ∎ So two
positions of a gap give e exactly, with the position where it changes sign: the
chip stops there, and a rule whose condition starts to hold there runs at it. A
rule runs at x when it holds at x but not just before (the stretch is closed at
x), or when it does not hold at x but holds just after (the stretch is open at x:
it runs as the chip leaves x). Just before and just after are signs of e near x,
computed from its affine form; so the rules run at the same positions however a
move is split (`test_conditions_on_the_state_are_watched_at_every_position`, and
the random runs, which use conditions and open stretches). A condition on an
arbitrary function of the state could not be watched this way: the first
position where it holds need not exist, and it may change at positions no finite
evaluation finds; conditions are therefore of this form.

Examples (tests in `ContinuousTests`): X on (chip, chip + 1] leaves X on the
whole path, whatever the steps; X on [chip + 1, chip + 2] leaves nothing; "A from
the chip to 3" leaves A on (start, 3]; an edit active in one mode stops exactly
at the transitions.

## The optimizer: the highest expected score for the modes the user may choose

"Optimal across all scenarios" is the best expected score: the modes are not known
in advance, and the tendency gives the probability of each scenario (the actual
scenario probably more or less follows it: "element A incites the user to switch
to mode 2"). `TendencyPlanner(rates, base=…, controlled=…, cost=…, tendency=…)`
maximizes it, with the time and CPUs a run allows itself.

**The problem.** Modes M. Per unit of length, mode m earns `base[m]`, plus
`rates[m][K]` wherever kind K is present (negative rates are costs). The texts
place the controlled kinds U; each element of K ∈ U costs `cost[K]` ≥ 0. The
tendency is a list of (kind, mode, q), for any kinds, placed by the texts or not
(q = 1 if omitted). At a position, the entry that applies is the first of the list
whose kind is present. Where it changes to (K, b, q), the user switches to b with
probability q; everywhere else, they stay in their mode (`Problem.switch`). So
switches happen where a stretch of K starts, independently of how the line is
cut into pieces. A *policy* places a set of controlled kinds on each piece of a
finite partition of the horizon (p, E] into intervals and points, each piece
decided from the modes seen before it; its value J is the integral of the actual
mode's rate minus the cost of its elements, and its score the expectation of J
(for E = +∞, J is an affine function a·X + b of the horizon X, compared
lexicographically: the best for every horizon long enough). Watched kinds are the
uncontrolled kinds the rates or the tendency read; *steering* kinds are those the
tendency reads.

**The units.** A text decides on *units*: the open intervals between the chip,
the multiples of the grid g, the ends of the steering kinds and E, and their end
points. Its policies decide at the start of every unit, from the mode there, the
sets on the open unit and on its end point; S_g is the best expected value among
them. Inside a unit the entry that applies is constant (the steering kinds and the
placement are), so the user may switch only where the open unit starts and at its
end point, and the rates change only at the ends of the other watched kinds.

**Lemma 1 (without steering, cells suffice).** If the tendency reads no
controlled kind, for every placement decided in advance there is one at least as
good, constant between consecutive ends of the watched kinds. *Proof.* The
probabilities of the modes then do not depend on the placement, and are constant
there. Replace every piece's set by the set of the piece with the best expected
rate: the expected integral does not decrease, and for every kind the number of
runs of presence does not increase (a block replaced by one of its values), so
neither do element costs. ∎ With steering this is false: a placed A, however
short, can switch the mode (Theorem 5).

**Theorem 2 (`solve` is exact).** For every state (mode, controlled kinds present)
at the start of every unit, `solve` gives the best expected value of the rest of
the horizon among policies deciding at unit starts: W_n = 0 (or nothing after an
infinite last unit), W_i(s) = max over the sets on the open unit and on its end
point of the expectation, over the switches where the open unit starts and at its
end point, of the unit's value plus W_{i+1} of the state it leads to (induction).
All numbers are exact (the probabilities are rationals). With several workers,
each computes the max-plus matrix of a chunk of units; products are associative,
so composing them gives the same W at every chunk end, and each chunk is then
swept from its end: the same values (`test_parallel_is_the_same`). This needs
certain switches (q ∈ {0, 1}), where the expectation is a single value; with
other probabilities, one process computes W. ∎
(`test_exact_against_every_placement` compares with a search over every policy.)

**Theorem 3 (texts are at least that good, whatever the actual modes).**
The text has, for every unit and mode, a rule on [unit start, unit end) for that
mode; it runs where the chip enters the unit, at every actual transition inside
it, and at the install. It reads the actual mode m and the kinds present at the
chip, and places on the rest of the unit the sets maximizing the expected value
of the rest of the unit plus W_{i+1}. At unit starts this is the policy of
Theorem 2. At a transition it decides again from the actual mode, and keeping the
current sets is one of its choices, so by policy improvement (finitely many
decisions in every scenario) its expected value is at least W from every state;
deviations of the user from the tendency need no new run.
∎ (`test_texts_react_to_the_actual_modes`, `test_the_expected_score_decides`:
with D making the user busy with probability q, S decided before the switch is
placed iff 2q − 1 exceeds its cost, not as soon as busy is the likelier mode.)

**Theorem 4 (the score as the line changes).** Let L be the line the text was
computed for and L′ the line now. If no steering kind changed after the chip, let
D = ∫ max_m |r′_m(x) − r_m(x)| dx over the horizon after the chip. Then the text
is at most 2·D below the best policy on units for L′. *Proof.* The units are the
same for L and L′ (they depend only on the grid and the steering kinds), and so
are the probabilities of the scenarios and the costs of every policy on them; in
every scenario its value changes by at most D, so its expected value too. The
text is optimal for L (Theorem 3), so for an optimum μ′ of L′:
E J′(μ′) ≤ E J(μ′) + D ≤ E J(text) + D ≤ E J′(text) + 2·D. If a steering kind
changed, D is +∞. ∎ (`test_the_score_is_within_twice_the_divergence`.)

**Theorem 5 (finer grids tend to the best of all policies).** Let J* be the
supremum of the expected value over all policies. Then S_g → J* as g → 0.
*Proof.* Take a policy P with E J(P) > J* − ε and at most N pieces in every
scenario (a finite partition has finitely many switches, each with two outcomes,
so finitely many scenarios). When g is smaller than every piece of P and every
distance between a boundary of P and an end of a steering kind, in every
scenario: move every boundary of P that is not such an end to a multiple of g
within 2g, on the same side of every end and every other boundary, and after the
unit start that follows every switch before it; move every point piece to a unit
end the same way. Every decision is then made at a unit start, after the
switches it depends on, so the result is a policy on units. The order of the
pieces, the steering kinds and the placed sets they meet are unchanged, so the
switches, their probabilities and the runs of every kind, hence the costs, are
unchanged; each move changes the integral by at most 2g·2R, R the largest |rate|.
So S_g ≥ E J(P) − 4N·g·R > J* − 2ε for g small enough; and S_g ≤ J*. ∎ The
supremum need not be reached: with "A incites busy", A costing 3 per unit and busy
earning 1, J* = 4 on (0, 4] but every placement is below, and S_g = 4 − g
(`test_steering_tends_to_the_best_value_as_the_grid_gets_finer`).

**When it generates (`plan`).** At every observation without a run, a run starts
when, and only when, the installed text is not one of this optimizer's, or it
asks for one (`view.rerun`): the chip is within the lead of E (2·v·t +
`min_lead`, v the chip's speed as seen by `plan`, t the run's time), or 2·D >
tolerance (R14). After a run that installed nothing it waits 0.5 s, doubled at
each further failure (at most 30 s), and doubles its time estimates.

**How much time, with how many CPUs.** With n units, P parts, S states and u
sets, one process does P·S·u²·k operations, k = 1 with certain switches and 4
otherwise (two outcomes at each of two switches); w > 1 workers do
(2·P·S·u² + n·S³)/w each, after starting (`pool_startup`), only with certain
switches (Theorem 2). `speed` is measured once if not given. With
c = `resources.cpus`, `plan` takes w = 1 or w = c, whichever is faster, and the
time 1.25·(`startup` + work) + 0.1 s, for the most units of the horizon whose time
fits in `time_scale`·f (R14); if none fits, the text is empty. A run that cannot
finish in time gives no text.

## Choices the requirements leave open

The requirements fix what must hold; where they allow several behaviours, these
are the choices made. Each satisfies the requirements.

1. **The form of texts.** A text is given as rules on stretches of positions and
   modes (`Rule`) and continuous edits whose ends are fixed or move with the chip
   (`Follow`), so that the system can compute their effect at every position the
   chip goes through (Theorem M); a black-box function of the position could only
   be evaluated at finitely many. A rule's action may be any function of
   (position, mode, state), since it runs at one known position, and its
   condition is a presence ratio on a window (Lemma C). Continuous edits
   do not read the state: some state-dependent ones have no consistent behaviour
   ("X on (chip, chip + 1] when X is not at the chip" would have to switch at every
   position), and a generator must not write such texts.
2. **Where a rule runs.** "If the chip is between x₁ and x₂ …" runs once, where the
   chip enters the stretch: at x₁, whether the stretch is [x₁, x₂] or (x₁, x₂].
3. **A continuous edit that its own trace sustains is shown.** With
   "X → Y 1 unit to the left", X on (chip, chip + 2] and Y already on [−1, 0] at the
   install at 0, showing the edit and hiding it from some position on are both
   consistent; the requirement of a unique result is met by showing it whenever
   that is consistent (`test_an_edit_sustained_by_its_trace`).
4. **The optimization problem** of the demo optimizer, its units and its grid
   (above), and how the tendency gives probabilities: a switch where a stretch of
   the kind starts, with a probability, so that the scenarios do not depend on how
   the line is cut. The line's rules should not involve the problem's kinds, or its edits
   may adapt or be refused (R4) and the optimum is that of the line as the problem
   describes it.
5. **Elements that repeat forever.** An ordered disjunction over a ray or the whole
   line can give infinitely many intervals (B on [0, 1) ∪ [2, 3) ∪ … with "no B 1 unit
   right of B", reverse logic, Theorem 3.4). The track only reads the ends of
   elements within bounded windows (around the move, and one common period for the
   residues modulo g), so moves stay exact (`test_elements_that_repeat_forever`);
   `TendencyPlanner` needs a finite horizon over them.

## Running

```
python rules_generation_logic.py                 # the demo
python -m unittest test_rules_generation         # RULESGEN_SEEDS=150 for a stress run
python -m unittest test_scheduler                # the optimizer; RULESGEN_SEEDS as well
```
