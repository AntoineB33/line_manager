## The probability finder's benefit is not measured
Nothing measures whether it makes the generator more performant at a low allocated
resources quantity. It only exists and is used.

## The chain of 5min and 15min breaks, and a push among other kinds
Static, the 5min and 15min breaks of screen_breaks_5_15_rules.py are the hand-written
ones. Dragged in mode 1, neither version is right: the rules' script pushes the 5min
break with a pattern of its own from the line (`constraint_events`: take it off (0, inf),
put a cycle), which runs into the 15min breaks that stay where they are, and nothing
says the chain (a 5min break touching a 15min one becomes the 15min one, at the line).
The hand-written ScreenBreaks refuses decisions there and leaves pieces behind.
Needs: a push of a window only (the pushed piece and its gap, the other pieces kept
where they are) and a chain constraint whose edit is a decision of the mode rules at
the position where the pieces touch.

## A pull read on overlapping gaps
`MinimumGap.apart` with `pull` pulls each component of the union of its gaps by that
component's last position (`pulled`). Two gaps that overlap merge, and a gap that should
be pulled is not: a 20s break pulled to 1100 and the one before it give one window
ending at 2320, so its own 1220 is no longer read. It does not show today because the
constraints of one kind are computed on the same line (`constraint_ops`). Pulling each
gap by its own end is the fix; it needs the pieces' length exact, which they now are
((s, s + L]).

## A push's text is no longer all it does
Where a push's pattern would meet what stays (the user's "no screen", another kind's
pieces, a gap after them), the mode rules place the later pieces with the kind's
AsEarlyAsPossible instead of moving them (`ModeRules._targets`), and the kinds that read
a pushed one are placed again after it (`_placed_after_pushes`). The event's text, "take
K off (0, inf), put R", still says only the pattern: a script read as text, or the
same events fired at each instant without the mode rules, does not do that part.

## Pieces of a kind back to back inside a "no screen" period
A gap only follows the end of a "no screen" stretch, so AsEarlyAsPossible places its
pieces one after the other inside a long "no screen" the user put (a 20s break every 20
seconds through it). That is what MinimumGap says; OmniApp takes a break once per
"no screen" period (`State.taken`). The same question as a break due during a lock.

## The obligation checked as the chip goes: seeds 5 and 16
`tests/fuzz_rules.py <seed> 1 12 max` (each kind's AsEarlyAsPossible and MinimumGap, and a
MaximumGap of the same gap) gives the same lines as the default variant, and checks
MaximumGap after each step too, which the default variant never checked. Two seeds break
it for a step, both from what a push leaves behind, not from the obligation:
- seed 5 (two kinds): A pushed ahead of B at the chip in mode 1, B's piece cut, nothing of
  B behind the chip; the line has B's next piece only at (30, 60].
- seed 16 (one kind): mode 2 drags A, mode 3 follows; the dragged piece is dropped at the
  change, and the "no screen" of the modes is 58 long at the check, 2 short of counting
  as the break. AsEarlyAsPossible's own edits disagree with that line too.
With `obligation` (no AsEarlyAsPossible, the obligation alone) seed 16 holds, and seed 5
does not end: its second step decides the mode rules position by position (the run's
window covers past the change to mode 2, where nothing was compiled), with two kinds and
the points of B the push leaves behind; minutes per step. The "max" variant had the same
until MaximumGap stopped widening the search window (`MaximumGap.cycle` = 0). The slow
path itself is older than the obligation: any window that ends past a mode change on
that line would meet it.

An edit is refused when the obligation does not hold on the line the constraints' edits
make (`simulation.against_constraints`), not when a MinimumGap does not: a MinimumGap with a
pull, read again on the line its own edits made, can take the piece it just let be (the
20s break pulled to the start of the user's "no screen" at 1100, `TwentySecondBreakTest`).
The obligation places nothing inside a gap that is a rule (`MaximumGap.place`), and an
"at least" longer than the "at most" of the same kind is refused when the generator is made
(`constraints.expand`); a gap of another `after` or `at_least` that leaves the obligation
no place is not checked that way.

## Two kinds of breaks: seeds 20-59 of tests/fuzz_rules.py
33 of 40 hold; the 7 that do not (20, 26, 27, 33, 35, 42, 56) all have two kinds.
- A kind placed later can change what an earlier one reads: B placed at (0, 30], then A
  right after it at (30, 50] ("B → no A" allows it), and B's "no screen" now ends at 50,
  so B's next piece falls in its gap (seeds 33, 42, 56). The same on account 3's shape: a
  20s break lands right after a 15min one every 5.7 days (494100s), and OmniApp's rule
  ("after a ≥15-minute of no screen, no 15min break in the next 2 hours", counted from the
  end of the stretch) wants that 15min break's next piece 20s later; the 20s breaks stay
  (only a 20s break bars a 20s break).
  Tried 2026-11-08, not kept: placing the kinds again in rounds while a gap does not hold,
  each kept from where a gap took it (so it only goes later). The fuzz seeds above then
  pass, but the rounds do not end: each round moves one occurrence far ahead and meets the
  next (6 rounds on seed 33 at the chip, and on account 3's shape the edits of 6 rounds
  lost the night). Placing them again until nothing changes (the first try) sent B back and
  forth with A and broke seed 5.
  What would hold: one sweep through time placing every kind's pieces in time order, each
  reading only what is placed before it (the gaps read the past, the exclusions the same
  time), the longer kind first at a tie; its pattern found as `_repeating` finds one kind's,
  the sweep's state now all kinds' bars ahead. Each kind over all time, then the next, cannot.
- What a pushed piece leaves behind the chip in mode 1. The push says K stays behind
  (`ChipPush.behind`), mode 1 says no "no screen" at the chip; a point of K is left where
  a run begins to push (seed 20: {100/3}), and a gap after K reads it as a piece that just
  ended. Taking K off that point in `decide` makes holes in the trail at every run
  boundary instead: not kept. What a pushed break leaves behind needs deciding first.

## OmniApp's score (score.py): what is not done yet
- **Rare tasks are scored, not placed.** The window is 4 times the longest minimum, so a
  task due every few months gets a panel only when its due falls inside a run's window.
  scheduler_score.md places it on the cycle in closed form; the generator has no cycle of
  this problem yet (ratio_plan's cycles are for PresenceRatioProblem).
- **J is the window's, closed by a tail.** The search scores (c, c + H] and adds the tail
  below; scheduler_score.md integrates to infinity.
- **The search (score_search.py), measured 2026-11-07 on account 3's shape.** 22 tasks that
  exclude each other at its shares, a 45min minimum, sleep and the hour before it every night,
  a past at the shares put by the generator (a past schedule, not tasks the user put: those are
  pre-placed, every other task deprived there).
  - A day's window after 60 days of past: J 158943, nothing to nobody; going on at the shares
    (stride in the past's phase) scores 167545. Before the search: 177767 and 38% to nobody.
  - After 1 day of past: 120779, 5% to nobody; stride 159104. Before: 237602 with short panels.
  - From an empty line, A30 B15 C15 (9h): 4684, against 4910 and 5421 for the example's
    patterns (`test_a30_b15_c15_from_an_empty_line`).
  - How: several seeds (the base policy due at (L + offset M) / f, five offsets), each improved
    over the whole continuation while the 4s last, the best kept. The improvement ends in a
    local optimum that depends on the seed (159k to 186k for one line), so more seeds would
    find better ones; the rollout (one or two runs deep) did worse within the time than
    improving more seeds, and is not used.
  - Not done: branch-and-bound; the continuation a re-plan replaces as a seed (scheduler_score.md
    seeds it first); a search that goes on past the 4s while the run is given time (q = 1).
  - A bug it found: the model read the compensation inside the window only, the exact score 16h
    either side; on a pre-placed past the two differed by 2 (`test_the_search_s_model_scores_as_the_score`).
- **Cost (measured, same shape).** One evaluation of the score: 0.15s with 60 days of past
  (305s before the compensation was made linear and the whole line read only 4 lambda back).
  A generator run with a 3h window: 0.6s (1 day of past), 0.8s (7 days), 2.8s (60 days); with a
  24h window: 4.9s and 6.9s, most of it the search's 4s.
- **The tail is optimistic.** Past `end`, each task is taken as served at its target, its lag
  decaying for free: a lower bound, as scheduler_score.md's rollout closes its trials, not the
  infinite continuation the score is defined on.
- **The modes are not predicted** for this problem: it reads the line as it is.

## The breaks with a daily sleep (measured 2026-11-07/08)
The default breaks in their rules form (AsEarlyAsPossible, MinimumGap, MaximumGap, the three
modes, "NoScreen → (NoComputerUnlocked or NotOnComputer) & (NoPhoneUnlocked or NotOnPhone)")
with "Sleep → NoScreen" every day, no task, no score; one generator run:

| | before | now |
|---|---|---|
| 15min breaks | 47.8s | 1.3s |
| 20s breaks | over 400s | 24.6s |
| all three | over 25 min (with the tasks) | 246.6s |

What it was, and what changed:
- The device choice ("no screen" → each device locked or not used) was solved by the sweep
  over every piece of "no screen" at each decision. The rules are local (each position acts
  on itself only), so `solver._solve_by_cells` solves one point of each cell where the claims
  are the same, for the kinds the choices decide (the others stay as the region stage made
  them, a region built by a call keeping its call).
- The 20s break kept being pulled to the start of a "no screen" period: against the user's
  rule (2026-11-07) that only a 20s break affects a 20s break. Removed (screen_break_20s_rules).
  It goes on through the night, so "no screen" (the night and the 20s breaks) repeats every 61
  days: exact, and what the line holds.
- `MaximumGap` walked every stretch of that pattern: it first checks by region algebra that no
  stretch is too long (`_holds`), the walk only where one is.
- Regions keep the shortest period over a day they repeat with (`region._shortest`); `on` cuts
  by bisection (`_clip`), the tails cut before they are shifted; the placement sweep reads its
  obstacles near each step and keeps only the gaps ahead (`constraints._first_block`, `_ahead`).
- `mode_rules._affine_region` reads a period past where each region's core is written to end:
  a region written differently (cells) is the same set, and its run's formula still holds.

Lazy regions (`region.Lazy`, 2026-11-08): two regions whose tails repeat together only after
more than a week are kept as the operation, read in windows, and computed whole only when a
whole-region question is asked (emptiness first looks for a piece near the operands). Exact
(`test_lazy_region`: random chains against the regions computed at once). The three kinds with
a night: 246.6s -> 213.5s. Not more, because the solver (its cells, the region stage), the gap
rules (`MinimumGap.long`) and the resolver ask whole-region questions at every decision of the
now line: each makes the 61-day "no screen" whole again (about 30 such computations of 1-4s per
run). What would end it: those layers working in a window near the chip where the rules allow
it (local rules, bounded gaps): a decision at the chip then costs what that window holds.
The run with account 3's tasks and the score has not been measured since.

## The instant schedule (instant.py), measured 2026-11-08
Account 3's shape (22 tasks at its shares, 45min minimum, resilience 0 to "no screen"), the
three breaks and the night placed by one generator run (280s): a day's instant schedule takes
133ms near the chip and 165ms 1000 years ahead, no task in "no screen", no idle time, the
largest shares within a few points of their targets (T458 0.30-0.32 for 0.26).
- Not read: rules other than "X → no T" (a task that brings another, percentages); the
  quantities by a date; the formulas past the window's start (read at its start).
- 133ms is a frame and a half at most rates: a drag recomputing a whole day at each event
  would lag; only the part of the view that changed needs recomputing.
- Regions far apart (an element 1000 years ahead with a night every day) are combined lazily
  (`region._far_apart`): written out, the night would have been 365000 pieces.

## The 10-minute pace with the breaks and a night (measured 2026-11-08)
The three break kinds with a night, no task: from the start of a run, its first definitive
script (definitive past the chip). The stages after the first come at once, definitive 80
minutes and more within the same second; the last stage, the mode rules compiled ahead for the
frames (`compile_runs`), then takes minutes.

| | start (the script in place) | first stage after it | in all |
|---|---|---|---|
| before | 5.3s | 9.8s | 15.1s |
| now | 4.7s | 2.8s | 7.5s |

What changed: the constraints' edits for the same state and chip are computed once per run
(`constraints.constraint_ops`, they were computed by the start, the edits written in the
script and the first stage), the same generated edit on the same state applied once
(`resolve.apply_edit`), a point of a lazy cell found in a window (`solver._a_point`), a region
the same in both lines not compared (`Line.same_on`).
Left: the start applies the placement of every break forever and checks that nothing changed
left of the chip on the whole past (the 61-day "no screen" computed whole); the requirement asks
the first script definitive on the first units almost at once. And the last stage's minutes:
until it ends, the chip's frames over the breaks decide the mode rules at each step.

## The scheduler's steps (pace.py), measured 2026-11-08
Account 3's shape, the three breaks and the night placed by one generator run, two days of
past: six steps of 10 minutes, each given 10 seconds, took 1.4s to 3.1s (the improvement found
nothing better before the budget), no task in "no screen", no idle time; the hour they made
scores J 336321 against 485894 for the instant schedule of the same hour.
- Wired in (2026-11-08, `pace.Stepped`): a session whose problem is OmniApp's score runs the
  steps as its generator: the run's first script is the constraints' edits (the breaks) with
  the instant schedule of the first 10 minutes (and the start's script, `first_edits`, the
  same); each step's slice is one more edit, the script definitive to its end; 18 steps (3
  hours), the last final. In the window, the run is in its own process as any run.
  Shown (2026-11-08): each step puts its whole look ahead on the line, its slice definitive
  and the 4 hours after it a plan the next steps start from and replace from their own slice
  on (a better schedule found from the big picture changes what is past the slice too). The
  window draws the line up to where that plan ends and the instant schedule past it
  (`Session.display`, `instant.displayed`), with the generator's claim, as the steps' own
  elements: nothing tells them apart (user rule 2026-11-08). It is recomputed when the window
  rebuilds what it shows (a new script, an edit, a scroll), not at each frame.
  Found on the way: the search's model let the generator's own tasks in its window forbid the
  others ("A → no B" read on the plan it replaces); they forbid nothing now.
  Not done: a run of 18 steps of 10s is 3 minutes, and nothing runs the steps again as the
  chip nears the end of what they reached but the generator's usual reruns.
- A step that finishes early does not use the rest of its budget: more seeds, or the
  branch-and-bound, could.
- The breaks themselves are still placed by the generator's run (287s here): the steps only
  place the tasks. With the breaks' own edits computed near the chip, a step could place both.

## The instant schedule, the same at t (2026-11-08)
User rule: what was shown at t stays when the user comes back to t, unless a schedule the
scheduler published since changes it: a step focused on its 10 minutes may change any part of
the future, not only its slice nor a fixed span after it (a rare task it moves into its slice
no longer being at t was the user's example). Done by making the instant schedule a function
of the line, the rules and the score only, so what is shown anywhere follows the latest
published schedule, and nothing else changes it: the future is cut into days on a
fixed grid, each filled from its own start (or from where the plan ends, in its day); a rare task
(period over 168h) is placed at its due dates, its last start on the line and one period after
another; days computed are kept (`Instant.CACHE`). `SameAtTTest`.
- Rough: a rare task's period is read on the wall clock, not the schedulable one; a due date that
  falls where the task may not be goes to the next place it may, within its day only.
- The days start balanced (no lag carried from the day before): fine for the look of it, not
  the score's continuity.

## The breaks in the instant layer (2026-11-08)
The window shows the breaks from the instant layer past the plan, and from the chip on while
the script in place is not of the rules in place (just changed, or no run yet): each break kind
placed straight from its constraints (`Instant.placed`: AsEarlyAsPossible.place, in their order,
written into a copy of the line with what the pieces come with, no solving: 0.1s for the three
kinds with a night); then the window solved by the rules (`instant._solved`: every kind as the
rules make it, the devices of "no screen" included, and a check that what is shown breaks no
rule). A window within a week of the chip places them only up to its end; further, forever, once.

Measured (the 15min and 20s breaks, a night, 4 tasks): a day shown before any run 0.17s; after
the rules change, the change 3.6s (from 8.7s) and the next day shown 0.56s; a day 1000 years
ahead the first time 2.2s, then 0.1s.

Found on the way and fixed: a bounded region and a repeating one combined wrote the repeating one
out up to the bounded one (a window 1000 years ahead: every night in between); intersections and
differences with a bounded region are now read on its span (`region._combine`, `Lazy.__and__`).
The script at the start of a run computes and checks the constraints' edits only as far as it
is definitive (`constraint_ops(until=...)`), its written edits whole (the script's text).

Left: a rule change takes 3.8s (the 15min and 20s breaks with a night). Done since: the check
that a script's edit changes nothing left of the chip is not made on the whole past when the
rules are local and the edit is all after the chip (`guards.check_script_edit`, with the edit's
operations and a lower bound of a lazy region, `Lazy.lower_bound`); the memory counted without
computing lazy regions (`line._pieces`). Each gives little: the layers a rule change goes through
(the replay of the user's edits, the script at the run's start, its edits applied, the cells of
the solver) each ask a whole-line question, and the same lazy regions get computed by the next.
What would end it: the replay after a rule change in the background, the window showing the
instant layer of the new rules at once meanwhile (the window and the simulation then sharing the
state across threads: not done without the user's go-ahead). `test_window.test_zooming_keeps_the_position_under_the_mouse_under_it`
failed once under the load of two test processes, and passes alone.

## The two paces (user, 2026-11-08): instant adaptation, deep scheduler
"An instantaneous adaptation of the line to prevent obviously wrong schedules without waiting for
thorough calculations, and a scheduler that has more time to make very deep calculations for the
absolute best schedule, limited by the 10 minutes every 10 seconds pace."
- Instant: a rule change is shown before it is put in place (`Session.set_rules` publishes the
  window's snapshot with the new rules' instant layer, `preview_hook`, then replays the edits
  with them: the window waits for nothing; `test_new_rules_shown_before_they_are_put_in_place`,
  under 1s). After an edit of the user the window shows the instant layer from the chip until a
  run catches up (`Session._edited_since_script`).
- Deep: each step uses all of its 10 seconds (`Model.deepen`: an iterated local search, the best
  plan shaken and improved again, kept if better; seeded by the step's number, so a step decides
  the same). Measured on account 3's shape: the six steps' hour the same as without it (562272;
  the instant schedule 761042): the improvement had already found what the shaking finds on these
  slices (one or two runs of 45min minimums in 10 minutes).
- Proved (2026-11-08, `Model.certify`, the step's last 30% of its budget): every plan of the
  slice of up to 3 runs ending on whole minutes (each run a task allowed all along it, or nobody)
  is scored, what follows the slice as the step planned it, and the best taken. Exact: the score
  is a sum of one term per task, each depending only on when that task is served, so each task's
  term for each of its patterns is computed once (191313 plans in about 0.4s). Measured on account
  3's shape: all six steps proved, none better than the search's (the hour still 562272);
  `test_the_slice_is_proved_best` checks it against plans scored whole one by one. Not proved:
  plans of 4 runs or more, or ends off the minute, in a 10-minute slice; the part after the slice
  (it is the look-ahead, replaced by the next steps); a slice with a user's element in it
  (`proved` False).
- The line itself (the simulation's state) still takes the seconds of the replay to have the new
  rules; the window does not wait for it.

## The two paces compared (tests/compare_paces.py, 2026-11-08)
Random small cases (2 to 4 tasks, priorities, minimums, a maximum sometimes, a night or not, a
past, blocks of the user's), the instant schedule against a step of 2s, both scored by the score
with the user's blocks read as it reads them (preplaced). Found and fixed:
- the instant fill ignored a task's maximum (A 195 min in a row, its maximum 30: J 24809 against
  the step's 1062);
- the step's model read a task's allowed places at the middle of segments a quarter of the
  compensation long: a meeting starting inside one ("Meeting → no A") left A allowed in it;
- the instant fill put a panel of a user block's own task against its left side.
What is left: the instant schedule's J is 1.87 times the step's (geometric mean of 40 cases),
3.9 at worst (12 of 40 over 1.5). Most of it is the fill's own lag rule: it counts the user's
block as service of its task where the score counts it as the task's alone and raises the other
tasks around it (the compensation, both sides). Picking per day between the fill and the score's
own stride (`Instant._best`, then 20ms of `Model.improve`) brings it to 1.22 on the 4 hours the
comparison reads, but it costs 60ms a day (the fill: 5), and further into the day it picks what
looks wrong (a 1min sliver of B before the user's block of A, 6 hours in). Not a term the score
misses: the score charges that sliver (758 there, J2), but read from the day's start it is
discounted by e^(-u/Theta), Theta 20min with two 10min tasks at 50%: 6 hours in it weighs e^-18.
One score read from a day's start judges the day's first hour only. Following the score
everywhere needs it read from each part's own start, as the steps read their slices (a rolling
pass of cheap steps: about 10 to 20 times the fill's cost in Python). Off (`Instant.BEST`).
Also open: a block of the user's inside the night (its task's resilience 0 there): the model
scores 1% to 4% of what the score does (seeds 20 and 31).
The rolling pass (`Instant._rolled`, on since 2026-11-08; the fill alone while the user drags,
`Session.dragging`): each part of 2h read by the score from its own start with 2h after it; on
it the score's stride with the instant schedule's rules (`Model.ruled`: a task's maximum, no
block's own task against it, no sliver before a block, a panel cut at the part's end finished
first, nobody never) at three phases, or the fill cut to the part, the lowest kept. No time in
it: the same line gives the same days. Measured on the 40 cases: 1.40 times the step's J (the
fill alone 1.87), 13 of 40 with a finding, worst 4.2; about 75ms a window (the fill: 5).
First tried as the fill and the score's plain stride mixed by part (1.37) and left: slivers of
B before the user's block, and an improvement bounded by time, not the same from run to run.
Open: on the pass's plans the step's model is 0.8% and 1.8% off the score (seeds 16 and 32 of
compare_paces.py; on the fill's within 0.15%), cause not found; short panels away from blocks
remain (4 of the 40 cases: a 1min C, a 13min B with a 30min minimum, ...).
