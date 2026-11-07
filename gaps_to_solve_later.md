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

## A pull read on overlapping gaps, and the ε of a placed piece
`MinimumGap.apart` with `pull` pulls each component of the union of its gaps by that
component's last position (`pulled`). Two gaps that overlap merge, and a gap that should
be pulled is not: a 20s break pulled to 1100 and the one before it give one window
ending at 2320, so its own 1220 is no longer read. It does not show today because the
constraints of one kind are computed on the same line (`constraint_ops`), so the gap
never reads the pieces its AsEarlyAsPossible is placing. Pulling each gap by its own end
is right, but then `AsEarlyAsPossible`'s pieces show their ε: each is [t, t + L], ε longer
than (s, s + L], so the sweep drifts by ε a piece while its repeating pattern does not,
and the gap cuts the first position of every later piece. Making them (s, s + L] exactly
broke the chip push, which holds [p, p + L] from a piece's first position p. The three
must be made to agree on one length.
