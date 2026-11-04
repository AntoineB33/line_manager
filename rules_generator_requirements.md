Rules generator: presence ratio targets and minimum lengths.

Read from left to right, the line keeps every stretch of it as close as possible to the targets: on every stretch that ends at a position, each element's share is as close as possible to its ratio target, the earlier positions first. Every interval of an element is at least its minimum length long, where it can be. If several lines do it, one is chosen at random; if none does (another line always does better), the definitions are refused. It must be found very quickly.

Consequences, with the chip at x and n every integer ≥ 0:
- A: ratio 1. A 100% at (x, ∞).
- A, B: ratio 0.5. A 50% and B 50% at (x, ∞).
- A, B: ratio 0.5, B → no A. Refused: a faster alternation of A and B always does better.
- A, B: ratio 0.5, minimum length 1, B → no A. At random, A 100% at (x+2n, x+2n+1] and B 100% at (x+2n+1, x+2n+2], or the reverse.
- The same, with A at (−∞, x]. B 100% at (x+2n, x+2n+1], A 100% at (x+2n+1, x+2n+2]: the stretches ending just after x already have too much A.
- The same, with B at (x−0.5, x]. B 100% at (x, x+0.5], then A and B alternating by 1: B's interval is at least 1 long.
