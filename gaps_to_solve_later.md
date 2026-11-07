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
  so B's next piece falls in its gap (seed 33). Placing the kinds in rounds until they
  stop changing fixes that seed and breaks seed 5: not kept.
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
