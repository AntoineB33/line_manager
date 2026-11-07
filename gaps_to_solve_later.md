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

## Found by the random rule sets (tests/fuzz_rules.py), seeds 0 to 19
- A push moves every later piece with the line, at fixed offsets from it, while the
  user's edits and the other kinds stay where they are: in mode 1 the line pushing A
  forever slides the following A's into the gap after a "no screen" the user put
  (seeds 3, 4, 5, 6: a MinimumGap broken, in the reference too). The same flaw as the
  chain. A push should move only what is relative to the line; the later pieces are
  placed again where the pushed pattern meets something that stays.
- The reference (each push an edit at each instant) misses a break the line starts
  exactly at: the event fires only once the line is inside it, and the mode rules eat
  it before (seed 18). A difference there is the reference's.
- Seeds 0, 13, 15, 16: the fast run and the reference differ, not classified yet.
