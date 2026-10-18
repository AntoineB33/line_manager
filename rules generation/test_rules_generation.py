"""Tests: python -m unittest test_rules_generation   (RULESGEN_SEEDS=n for more random runs)."""
from __future__ import annotations

import itertools
import os
import random
import unittest
from fractions import Fraction

from rules_generation_logic import Region, TrackManager, at_or_behind, restrict

SEEDS = int(os.environ.get("RULESGEN_SEEDS", "40"))
F = Fraction


def union(elements) -> dict[str, Region]:
    out: dict[str, Region] = {}
    for kind, region in elements:
        out[kind] = out.get(kind, Region()) | region
    return out


def random_text(seed: int):
    """Rules that depend on the track, the position and the mode."""
    def rules(track, position, mode):
        rng = random.Random(hash((seed, position, mode, tuple(sorted(track)))))
        for _ in range(rng.randint(0, 3)):
            a = position + F(rng.randint(0, 12), 2)
            if rng.random() < 0.4:
                region = Region.point(a if a > position else a + F(1, 2))
            else:
                region = Region.interval(a, a + F(rng.randint(1, 6), 2),
                                         left_closed=a > position, right_closed=rng.random() < 0.5)
            yield rng.choice("ABC"), region
    return rules


class ChipTests(unittest.TestCase):
    def test_chip_only_advances(self):
        m = TrackManager(initial_position=3)
        with self.assertRaises(ValueError):
            m.update_chip(2)
        m.update_chip(3)
        self.assertEqual(m.chip.position, 3)

    def test_unknown_mode_refused(self):
        with self.assertRaises(ValueError):
            TrackManager(modes=(1, 2), initial_mode=3)
        m = TrackManager(modes=(1, 2))
        with self.assertRaises(ValueError):
            m.update_chip(1, 3)

    def test_gap_must_be_strictly_positive(self):
        for gap in (0, -1):
            with self.assertRaises(ValueError):
                TrackManager(min_transition_gap=gap)

    def test_transition_gap(self):
        m = TrackManager(modes=("a", "b"), initial_mode="a", min_transition_gap=F(1, 2))
        m.update_chip(0, "b")              # the initial mode is not a transition
        with self.assertRaises(ValueError):
            m.update_chip(0, "a")          # distance 0
        with self.assertRaises(ValueError):
            m.update_chip(F(49, 100), "a")  # below the minimum gap
        self.assertEqual((m.chip.position, m.chip.mode), (0, "b"))
        m.update_chip(F(1, 2), "a")
        m.update_chip(1, "b")

    def test_exact_positions(self):
        m = TrackManager(min_transition_gap=0.1)
        m.update_chip(0.1, 2)
        m.update_chip(0.2, 1)  # 0.2 - 0.1 is exactly the gap, not 0.09999...
        self.assertEqual(m.chip.position, F(1, 5))


class TrackTests(unittest.TestCase):
    def test_elements_must_be_strictly_ahead(self):
        for bad in (Region.point(0), Region.closed(0, 1), Region.closed(-2, -1)):
            m = TrackManager()
            with self.assertRaises(ValueError):
                m.switch_rules_text("t", lambda track, p, mode, bad=bad: [("A", Region.point(5)), ("A", bad)])
            self.assertEqual(dict(m.track()), {})
            self.assertIsNone(m.current_text_id)
        m = TrackManager()
        m.switch_rules_text("t", lambda track, p, mode: [("A", Region.interval(0, 1, left_closed=False))])
        self.assertEqual(dict(m.track()), {"A": Region.interval(0, 1, left_closed=False)})

    def test_rules_get_a_read_only_snapshot(self):
        seen = []

        def rules(track, position, mode):
            seen.append((dict(track), position, mode))
            with self.assertRaises(TypeError):
                track["Z"] = Region.point(9)
            return []

        m = TrackManager(initial_elements=[("A", Region.point(4))])
        m.switch_rules_text("t", rules)
        m.update_chip(1, 2)
        self.assertEqual(seen, [({"A": Region.point(4)}, 0, 1), ({"A": Region.point(4)}, 1, 2)])

    def test_switch_discards_unreached_and_keeps_reached(self):
        m = TrackManager(initial_elements=[("I", Region.point(10))])
        m.switch_rules_text("alpha", lambda t, p, mode: [] if "A" in t else
                            [("A", Region.point(2)), ("A", Region.closed(3, 6)), ("A", Region.point(8))])
        m.update_chip(4)
        m.switch_rules_text("beta", lambda t, p, mode: [])
        # {2} is passed and [3, 6] is partly reached; {8} is unreached.  The
        # initial element was not placed by a text.
        self.assertEqual(dict(m.track()), {"A": Region.point(2) | Region.closed(3, 6),
                                           "I": Region.point(10)})

    def test_discard_respects_multiplicity(self):
        # Two texts place A on overlapping regions; discarding the unreached one
        # leaves the reached one whole.
        m = TrackManager()
        m.switch_rules_text("alpha", lambda t, p, mode: [] if t else [("A", Region.closed(1, 5))])
        m.update_chip(1)
        m.switch_rules_text("beta", lambda t, p, mode: [("A", Region.closed(2, 3))])
        m.switch_rules_text("gamma", lambda t, p, mode: [])
        self.assertEqual(dict(m.track()), {"A": Region.closed(1, 5)})

    def test_failed_switch_changes_nothing(self):
        m = TrackManager()
        m.switch_rules_text("alpha", lambda t, p, mode: [("A", Region.point(3))])

        def broken(track, position, mode):
            yield "B", Region.point(5)
            raise RuntimeError("bad text")

        with self.assertRaises(RuntimeError):
            m.switch_rules_text("beta", broken)
        self.assertEqual(dict(m.track()), {"A": Region.point(3)})
        self.assertEqual(m.current_text_id, "alpha")
        self.assertEqual([e.region for e in m.unreached()], [Region.point(3)])

    def test_random_runs(self):
        for seed in range(SEEDS):
            self._random_run(random.Random(seed))

    def _random_run(self, rng: random.Random):
        m = TrackManager(modes=(1, 2, 3), min_transition_gap=1,
                         initial_elements=[("A", Region.closed(2, 3))])
        initial = [("A", Region.closed(2, 3))]
        frozen: list[tuple[Fraction, dict]] = []  # (position, track on (-inf, position])
        transitions = []
        for step in range(25):
            p0, mode0 = m.chip.position, m.chip.mode
            track0 = dict(m.track())
            if rng.random() < 0.3:
                unreached = m.unreached()
                m.switch_rules_text(f"t{step}", random_text(rng.randrange(10**6)))
                # Every unreached element was discarded before the new rules ran.
                gone = {e.edit_id for e in unreached}
                self.assertFalse(gone & {e.edit_id for e in m.placed})
                self.assertTrue(all(e.text_id == f"t{step}" for e in m.unreached()))
                self.assertEqual((m.chip.position, m.chip.mode), (p0, mode0))
            else:
                p = p0 + F(rng.randint(0, 4), 2)
                mode = rng.choice((1, 2, 3))
                try:
                    m.update_chip(p, mode)
                except ValueError:
                    self.assertNotEqual(mode, mode0)
                    self.assertTrue(transitions and p - transitions[-1] < 1)
                    self.assertEqual(dict(m.track()), track0)
                    self.assertEqual((m.chip.position, m.chip.mode), (p0, mode0))
                    continue
                if mode != mode0:
                    transitions.append(p)
            # The track is exactly the initial elements plus the active placed ones.
            self.assertEqual(dict(m.track()), union(initial + [(e.kind, e.region) for e in m.placed]))
            # The past never changes.
            frozen.append((m.chip.position, restrict(m.track(), at_or_behind(m.chip.position))))
            for q, past in frozen:
                self.assertEqual(restrict(m.track(), at_or_behind(q)), past)
        self.assertTrue(all(b - a >= 1 for a, b in itertools.pairwise(transitions)))


if __name__ == "__main__":
    unittest.main()
