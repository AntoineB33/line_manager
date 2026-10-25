"""Probability finder: probability rules in the pattern of mode transitions.

From the revealed modes it counts, over completed segments,
- which mode follows which:        P(next = b | current = a)
- how long each mode lasts:        P(length = l | mode = a)
(the first segment is excluded from the lengths: the chip may have started in
the middle of it). All probabilities are exact fractions.

From these rules it predicts the future as weighted scenarios: sequences of
mode segments after the frontier, conditioned on what is known of the current
segment (it already lasted e, so its length l satisfies l > e). Where the rules
have no data a scenario stops and the rest of it is "uncovered".
"""

from __future__ import annotations

import heapq
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction

from intervals import Interval, IntervalSet
from modes import ModeKnowledge


@dataclass
class Scenario:
    probability: Fraction
    cover: dict[str, IntervalSet]  # positions (after the frontier) predicted to have each mode
    uncovered: IntervalSet  # positions after the frontier with no prediction

    def mode_region(self, mode: str) -> IntervalSet:
        return self.cover.get(mode, IntervalSet())


@dataclass
class ModeModel:
    next_counts: dict[str, Counter] = field(default_factory=dict)
    length_counts: dict[str, Counter] = field(default_factory=dict)

    def rules(self) -> list[str]:
        out = []
        for a in sorted(self.next_counts):
            total = sum(self.next_counts[a].values())
            for b, n in sorted(self.next_counts[a].items()):
                out.append(f"after mode {a}, mode {b} follows with probability {Fraction(n, total)}")
        for a in sorted(self.length_counts):
            total = sum(self.length_counts[a].values())
            for l, n in sorted(self.length_counts[a].items()):
                out.append(f"mode {a} lasts {l} with probability {Fraction(n, total)}")
        return out

    def scenarios(self, known: ModeKnowledge, until: Fraction, max_scenarios: int = 8, max_expansions: int = 256) -> tuple[list[Scenario], Fraction]:
        """Most probable scenarios for (F, until], best first, and the probability left out."""
        F = known.frontier
        # partial scenario: (segments [(lo, hi, mode)], next segment start, its mode, elapsed or None)
        heap = [(-Fraction(1), 0, [], known.current_start(), known.current_mode(), F - known.current_start())]
        counter = 1
        done: list[Scenario] = []
        expansions = 0
        while heap and len(done) < max_scenarios and expansions < max_expansions:
            negp, _, segs, start, mode, elapsed = heapq.heappop(heap)
            expansions += 1
            p = -negp
            if start >= until:
                done.append(self._scenario(p, segs, F, until))
                continue
            if elapsed is None:  # a new segment starts at `start` with `mode`
                lengths = self.length_counts.get(mode, Counter())
                options = dict(lengths)
            else:
                options = {l: n for l, n in self.length_counts.get(mode, Counter()).items() if l > elapsed}
            total = sum(options.values())
            if not total:
                done.append(self._scenario(p, segs, F, until))
                continue
            for l, n in sorted(options.items()):
                end = start + l
                seg = (max(start, F), end, mode)
                nxt = self.next_counts.get(mode, Counter())
                pl = p * Fraction(n, total)
                if end >= until:
                    heapq.heappush(heap, (-pl, counter, segs + [seg], until, mode, None))
                    counter += 1
                    continue
                ntotal = sum(nxt.values())
                if not ntotal:
                    done.append(self._scenario(pl, segs + [seg], F, until))
                    continue
                for b, k in sorted(nxt.items()):
                    heapq.heappush(heap, (-(pl * Fraction(k, ntotal)), counter, segs + [seg], end, b, None))
                    counter += 1
        residual = 1 - sum((s.probability for s in done), Fraction(0))
        return done, residual

    @staticmethod
    def _scenario(p: Fraction, segs, F: Fraction, until: Fraction) -> Scenario:
        horizon = IntervalSet.of(Interval(F, until, False, True))
        cover: dict[str, IntervalSet] = {}
        for lo, hi, m in segs:
            iv = Interval.make(lo, hi, True, False)
            if iv:
                cover[m] = cover.get(m, IntervalSet()) | (IntervalSet.of(iv) & horizon)
        covered = IntervalSet()
        for c in cover.values():
            covered = covered | c
        return Scenario(p, cover, horizon - covered)


class ProbabilityFinder:
    def fit(self, known: ModeKnowledge) -> ModeModel:
        model = ModeModel()
        segs = known.segments()
        for i in range(len(segs) - 1):  # completed segments
            lo, hi, m = segs[i]
            model.next_counts.setdefault(m, Counter())[segs[i + 1][2]] += 1
            if i > 0:
                model.length_counts.setdefault(m, Counter())[hi - lo] += 1
        return model
