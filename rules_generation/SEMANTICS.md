# Rules generation: semantics and proofs

Code: `chip.py` (chip position and mode transitions), `track.py` (a
reverse-logic `LineSystem` edited by rules texts and by the user),
`machine.py` (the machine that runs an optimizer to generate the texts),
`scheduler.py` (the optimizer: an optimization problem and its solver),
`rules_generation_logic.py` (public API and the demo).
`test_rules_generation.py` checks every item of the requirements below, for any
optimizer, and `test_scheduler.py` every item of "The optimizer", both including
on random runs.

Requirements: `../requirement.md` and `requirements.md`. Everything about edits,
rules, adaptation and reversal comes from `../reverse logic/SEMANTICS.md`.

## Requirements and how each one is met

**R1. The chip moves right, continuously; it can stop.** Positions are exact
rationals. `update_chip(q)` refuses q < position and accepts q = position. A
program can only observe the chip at a sequence of positions, like a mouse
drag; every observed position is handled (no step is skipped).

**R2. One mode per position, transitions decided by the text.** Each text
gives a finite list of transitions `(position, mode)`, strictly increasing and
strictly to the right of the chip (`Chip.schedule`). The mode at q is the mode
of the last transition at or before q, or the initial mode: the new mode applies
from the transition position on. A new text keeps the transitions that already
happened (at or before the chip) and replaces the others.

**R3. Finitely many transitions in every bounded stretch.** Each text adds a
finite list, and a run installs finitely many texts, so the whole list of
transitions is finite. ∎

**R4. A text's rules edit the right side of the chip, from (position, mode).**
`Text.rules(position, mode)` yields edits (any reverse-logic op). Every region
they take from or put on must lie in (position, +∞); otherwise `ValueError` and
nothing changes. They are evaluated at every chip position and right after the
text is installed. An edit that is refused by reverse logic (it removes
something absent, contradicts the rules, …) is skipped, recorded in
`TrackManager.refused`, and the text keeps running.

**R5. Continuous edits.** `Text.follow(position, mode)` gives elements whose
region depends on the chip's position. They are shown as the pending edit of
the `LineSystem`, recomputed from scratch at every chip position, exactly like
a drag step (T6 of reverse logic). When the chip moves, the part of the shown
elements that the chip has reached stays on the line, as an edit of the text.

**R6. The left side of the chip never changes.** Let B = (−∞, position]. Every
text edit and user edit is applied with `keep=B`: it is refused if the line on
B would change (for example through a rule "C 3 units on the left"). Moving the
chip changes no edit. When something else changes B (a text switch, or a
swept continuous edit that cannot be kept), `_pin_left` adds an internal edit,
newest of all, claiming every element that was on B present and everything
else on B absent; the right side adapts to it.
*The pin is never refused.* It is the newest level, processed with Γ = ∅, and
its claims are those of a line that satisfies the rules (the line before the
operation), restricted to B; that line's own choice of operands is an
assignment without violation, so by Lemma 3.3 of reverse logic nothing of the
pin is dropped, and it holds exactly on B (T2). ∎
Every operation also runs on a copy, which replaces the manager only if the
whole operation succeeded and the left side is unchanged.

**R7. A text switch.** `switch_rules_text(text)`:
1. hides the previous text's continuous edit (it lies strictly to the right of the chip);
2. reverses every edit of the previous text, and every user edit that requires one of
   them, with `LineSystem.reverse_all`; the line becomes the line of the remaining edits,
   that is, as if the reversed edits had never been made (T4 of reverse logic);
3. pins the left side as in R6. So nothing changes on B, the right side is what it would
   be without the previous text's edits, adapted where B requires it (for example, an A
   just left of the chip with "A → B 2 units on the right" keeps its B), and an element
   that straddles the chip is cut there;
4. installs the new text's transitions and applies its rules.

Elements the track starts with, and user edits that require no text edit, are kept.

**R8. Generation time.** It is outside of this system: the new text applies
from the position of the chip when `switch_rules_text` is called.

**R9. The machine generates the texts, when it decides to.** `Machine` drives a
`TrackManager` with an `Optimizer`, which supplies its own policy: `plan(view,
resources)` returns the time to allow a new run, or `None`; `generate(view,
deadline)` returns a text, or `None`. The objective is the optimizer's own; the
requirements constrain only how its runs reach the line, which is what is proved
and tested, for any optimizer (the tests use scripted ones, a simulated clock and
a runner that finishes runs when the test chooses, plus real processes for R11
and R12). The optimization problem the machine solves is the one of
`ModeScheduler`, below ("The optimizer").
An observation is the start, a chip move, a user edit, or `poll`. At each one,
in this order:
1. the run in progress is handled: stopped past its deadline (R12), or, if it has finished, as in R13;
2. if no run is in progress, `plan` is called with a snapshot `View` of the line
   and the current `Resources`, and a run starts if it returns a time. The view
   holds the chip's position, mode and every transition, the line, the installed
   text and the refused edits; the run is given the same view without the text
   (a text installed directly need not be picklable).

So the optimizer is asked at every observation where it could start a run, and
never while one is in progress: *at most one run is in progress at a time.* ∎

**R10. CPU resources: every CPU, up to a limit the user sets.**
`Resources(limit)`: a run may use every CPU this process may run on
(`usable_cpus()`), or only `limit` of them, an integer from 1 to that number
(`None` means all; anything else is `ValueError`). The user may replace
`machine.resources` at any time; `plan` and `generate` receive the current value.
The time of a run is the optimizer's choice: `plan` returns any positive, finite
number of seconds t, and the run gets `Deadline(start, t)`, with `start` the clock
when it starts. Any other answer from `plan`, or an exception in it, is recorded
in `refused_plans` and starts no run. *Nothing the optimizer does makes a chip
move or a user edit raise:* that move or edit has already happened when `plan`
is asked, so an exception there would wrongly suggest that it failed.

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
* `finished > start + t`: outcome `late`, the text is discarded (possible when it
  returns just before the stop takes effect). With R12, *no text produced after
  the time the run allowed itself is ever installed*, however late the
  observation comes;
* it returned `None`: outcome `no text`;
* otherwise `switch_rules_text(text)` at the chip's position at that moment (R7,
  R8): outcome `installed`, or `refused` if the switch raised (for example
  transitions now behind the chip). The switch is a transaction, so a refused
  text changes nothing.

The run's `View` is a copy taken when it started, and the chip may have moved
since: the text applies from the chip's position at install, not from
`view.position`. Every installed text goes through `switch_rules_text`, so R1–R7
hold for the machine as they do for direct calls. The run's process start-up
time counts in its time t.

## The optimizer: mode scheduling with switching costs

The requirements leave the optimization problem open; the one chosen here
decides the chip's modes, and so uses what the texts control: mode transitions
(R2) and continuous edits (R5). `ModeScheduler(rates, switch_cost, base=...)`.

**The problem.** Modes M. Per unit of length, mode m earns `base[m]`, plus
`rates[m][K]` wherever kind K is present (rates may be negative: a cost). A
change from mode a to mode b costs c(a, b) ≥ 0, with c(a, a) = 0 and the triangle
inequality c(a, d) ≤ c(a, b) + c(b, d); anything else is `ValueError`. For a line
L, the rate of m at x is r_m(x) = base[m] + Σ{rates[m][K] : x ∈ L[K]}. A
*schedule* on (s, E] from mode y is a finite list of transitions
s ≤ t_1 < … < t_r ≤ E to modes m_1 … m_r (m_j applies from t_j on, as in R2;
m_0 = y). Its value is

  J = ∫_(s, E] r_μ(x)(x) dx − Σ_j c(m_(j−1), m_j),  μ(x) = the mode at x.

P(L, y, s, E) is: maximize J. The *cells* are the open intervals between
s = e_0 < e_1 < … < e_n = E, where e_1 … e_(n−1) are the endpoints, strictly
between s and E, of the regions of the kinds that some rate mentions. On a cell
every rate is constant; g_i(m) is what m earns on cell i.

**Lemma 1 (it suffices to switch at cell ends).** For every schedule there is
one, at least as good, with one mode per cell, its transitions at cell ends.
*Proof.* In each cell i, let the new schedule use the mode of best rate among the
modes the old one uses in that cell. The gain does not decrease: on cell i the
old one earns a combination of those rates with total length that of the cell
(single points have no length). The new list of modes is a subsequence of the old
one (cells are in order, and each pick is used inside its cell), and by the triangle
inequality removing a mode b from …, a, b, d, … does not increase the cost:
c(a, d) ≤ c(a, b) + c(b, d); equal neighbours cost 0. ∎
So P has a maximum (over the finitely many such schedules) and it is the best
one-mode-per-cell schedule.

**Theorem 2 (`solve` is exact).** V_0 = (0 at y, −∞ elsewhere),
V_(i+1)(b) = max_a (V_i(a) − c(a, b)) + g_i(b): V_n(b) is the best value of a
schedule ending in b (induction on i, by Lemma 1), and back pointers give one
schedule of value max_b V_n(b). All numbers are exact fractions. With several
workers, each computes, for a chunk of cells, the best value from every entry
mode to every exit mode (a max-plus matrix); these products are associative, so
composing them in order gives the same V at every chunk end, and the back pointers
of the entry mode chosen on the way back give the schedule. *Anytime:* cells are
taken in rounds, and the result is always V and a schedule for the cells done,
that is, an optimum of P(L, y, s, e_d), d the cells done. ∎

**The text.** `generate` computes the schedule for its view (position p_0, mode
y, line L) on (s, E], s and E chosen by `plan` (below), and returns a text with
* transitions: (s, m_1), even if m_1 = y, then every change of mode;
* a continuous edit (`Served`): the kind `served` (default "S") wherever the
  schedule's mode earns from a kind present there (rate > 0), on (p_0, E] (on
  (p_0, s) with mode y), minus where `served` already is, placed by others than
  the installed text (whose edits the switch reverses).
  It is one Add, shown ahead of the chip at every step (R5), so what the chip
  goes through stays on the line, as an edit of the text.

The served kind cannot be one that earns (`ValueError`), and the line's rules should
not mention it: the optimizer does not see them. If they refuse the edit, it is
skipped and tried again at every step (R4, R5).

**Theorem 3 (an installed text holds its schedule from s on).** Let q be the
first transition of the chip after p_0, in the view (+∞ if none); `plan` takes
p_0 < s ≤ q. Only the machine installs texts, one run at a time, so the
transitions do not change between the view and the install, at the chip's position
p_1 ≥ p_0. *If p_1 ≥ s, the install is refused* (its first transition is not
right of the chip, R2), nothing changes, and `plan` tries again (below).
*Otherwise* the chip's mode is y on [p_1, s), and the schedule's from s on.
*Proof.* After the switch the transitions are the old ones at or before p_1, then
the text's (R2). An old one in (p_0, p_1] would be in (p_0, q): there is none. So
the mode on [p_1, s) is the one at p_0, y; from s, the text's transitions decide. ∎

**Theorem 4 (no new text is needed while nothing changes).** Say the installed
text has schedule σ on (s, E] for line L, the chip is at p ≥ s, and ahead of the
chip the kinds of the problem are as in L. Then σ on (p, E] is optimal for
P(line now, mode at p, p, E). *Proof.* The data of that problem are those of L on
(p, E]. A better schedule on (p, E], put after σ on (s, p], would give a schedule
on (s, E] better than σ (a switch at p is allowed, since Lemma 1 compares with
every schedule; two at p merge into one, no dearer by the triangle inequality),
which contradicts Theorem 2. ∎
And σ is *final* when no cell end lies after E and σ's last mode has the best
rate on (E, +∞): then σ, its last mode kept forever, is optimal on (s, X] for
every X ≥ E (any schedule earns at most that rate after E, and costs are ≥ 0).

**When it generates (`plan`).** At every observation without a run (R9), a run
starts when, and only when, the installed text is *stale*:
1. it is not one of this optimizer's (at the start, or after a foreign text);
2. a kind of the problem changed ahead of the chip (a user edit, an adaptation);
3. the served kind not placed by the text changed ahead of the chip, or got
   inside the text's region (which refuses the text's edit);
4. the chip is within the lead (below) of E, and the text is not final.

Otherwise, by Theorem 4 the installed text is still the optimum over the rest
of its horizon, so `plan` returns `None`: moving the chip a little, however
complex the line, starts no run. After a run that installed nothing (no text,
late, stopped, refused, error), it waits 0.5 s, doubled at each further failure
(at most 30 s), and doubles its time estimates (`startup`, `pool_startup`).

**How much time, with how many CPUs.** With n cells and k modes, one process
does n·k² max-plus operations; w > 1 worker processes do n·k³/w each (every entry
mode) and n·k to compose, after starting (`pool_startup`). `speed` is measured
once if not given. With c = `resources.cpus` (R10), `plan` takes w = 1 or w = c,
whichever is faster, and the time 1.25·(`startup` + work) + 0.1 s. Above
`max_budget`, it shortens the horizon (n), down to one cell. So a run is given less
time when it may use more CPUs, and uses them only when that is faster.
The *lead*: s = min(p_0 + lead, q), lead = 2·v·t + `min_lead`, v the chip's
speed seen by `plan` (a maximum decaying by half per second), t the run's time;
E = s + `lookahead`, or less, as above. `generate` keeps 0.05 s + 5 % of its time
to send the text back, and starts a round only if the time left exceeds the last
round's; whatever it returns is exact for its cells (Theorem 2), and a text it
cannot finish in time is never installed (R13).

## Running

```
python rules_generation_logic.py                 # the demo
python -m unittest test_rules_generation         # RULESGEN_SEEDS=300 for a stress run
python -m unittest test_scheduler                # the optimizer; RULESGEN_SEEDS as well
```
