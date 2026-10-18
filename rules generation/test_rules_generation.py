"""Tests: python -m unittest test_rules_generation   (RULESGEN_SEEDS=n for more random runs)."""
from __future__ import annotations

import os
import random
import unittest
from fractions import Fraction

from rules_generation_logic import (
    Add,
    Move,
    Region,
    Remove,
    Text,
    TrackManager,
    ahead,
    at_or_behind,
    forbids,
    implies,
    restrict,
)

# isort: split
# reverse_logic is only importable once chip (via rules_generation_logic) has extended sys.path.
from reverse_logic import LineSystem

SEEDS = int(os.environ.get("RULESGEN_SEEDS", "40"))
F = Fraction


def put_ahead(seed: int):
    """Discrete rules: random edits to the right of the chip."""
    def rules(position, mode):
        rng = random.Random(hash((seed, position, mode)))
        for _ in range(rng.randint(0, 2)):
            a = position + F(rng.randint(1, 12), 2)
            region = Region.point(a) if rng.random() < 0.4 else Region.closed(a, a + F(rng.randint(1, 6), 2))
            kind = rng.choice("ABC")
            yield Add(kind, region) if rng.random() < 0.7 else Remove(kind, region)
    return rules


def follow_ahead(seed: int):
    """Continuous rules: an element that follows the chip."""
    def follow(position, mode):
        if mode != 1:
            yield "F", Region.closed(position + F(seed % 3 + 1, 2), position + F(seed % 3 + 3, 2))
    return follow


class ChipTests(unittest.TestCase):
    def test_chip_never_moves_left(self):
        m = TrackManager(initial_position=3)
        with self.assertRaises(ValueError):
            m.update_chip(2)
        m.update_chip(3)  # it can stop
        self.assertEqual(m.chip.position, 3)

    def test_unknown_mode_refused(self):
        with self.assertRaises(ValueError):
            TrackManager(modes=(1, 2), initial_mode=3)
        m = TrackManager(modes=(1, 2))
        with self.assertRaises(ValueError):
            m.switch_rules_text(Text("t", transitions=((1, 3),)))

    def test_transitions_are_decided_by_the_text(self):
        m = TrackManager(modes=("a", "b"), initial_mode="a")
        m.switch_rules_text(Text("t", transitions=((1, "b"), (F(3, 2), "a"))))
        self.assertEqual([m.chip.mode_at(q) for q in (0, F(99, 100), 1, F(149, 100), F(3, 2))],
                         ["a", "a", "b", "b", "a"])  # the new mode applies from the transition on
        m.update_chip(F(5, 4))
        # A new text keeps the transitions that happened and replaces the others.
        m.switch_rules_text(Text("u", transitions=((2, "a"),)))
        self.assertEqual(m.chip.transitions, [(1, "b"), (2, "a")])
        for bad in (((F(5, 4), "a"),), ((1, "a"),), ((3, "a"), (2, "b"))):
            with self.assertRaises(ValueError):  # at or behind the chip, or not increasing
                m.switch_rules_text(Text("v", transitions=bad))
        self.assertEqual(m.current_text_id, "u")

    def test_rules_get_position_and_mode(self):
        seen = []
        m = TrackManager(modes=(1, 2))
        m.switch_rules_text(Text("t", rules=lambda p, mode: seen.append((p, mode)) or (), transitions=((1, 2),)))
        m.update_chip(F(1, 2))
        m.update_chip(1)
        self.assertEqual(seen, [(0, 1), (F(1, 2), 1), (1, 2)])


class TrackTests(unittest.TestCase):
    def test_edits_must_be_strictly_right_of_the_chip(self):
        for bad in (Region.point(0), Region.closed(0, 1), Region.closed(-2, -1)):
            m = TrackManager()
            with self.assertRaises(ValueError):
                m.switch_rules_text(Text("t", rules=lambda p, mode, bad=bad: [Add("A", Region.point(5)),
                                                                              Add("A", bad)]))
            self.assertEqual(dict(m.track()), {})
            self.assertIsNone(m.current_text_id)
        m = TrackManager()
        m.switch_rules_text(Text("t", rules=lambda p, mode: [Add("A", Region.interval(0, 1, left_closed=False))]))
        self.assertEqual(dict(m.track()), {"A": Region.interval(0, 1, left_closed=False)})

    def test_refused_edit_is_skipped(self):
        m = TrackManager()
        m.switch_rules_text(Text("t", rules=lambda p, mode: [Remove("A", Region.point(5)),
                                                            Add("B", Region.point(6))]))
        self.assertEqual(dict(m.track()), {"B": Region.point(6)})
        self.assertEqual([r.text_id for r in m.refused], ["t"])

    def test_switch_reverses_right_side_only_and_cuts_at_the_chip(self):
        m = TrackManager(initial_elements=[("I", Region.point(10))])
        m.switch_rules_text(Text("alpha", rules=lambda p, mode: [Add("A", Region.point(2)),
                                                                Add("A", Region.closed(3, 6)),
                                                                Add("A", Region.point(8))] if p == 0 else []))
        m.update_chip(4)
        m.switch_rules_text(Text("beta"))
        # {2} is passed, [3, 6] is cut at the chip, {8} is gone; the initial element stays.
        self.assertEqual(dict(m.track()), {"A": Region.point(2) | Region.closed(3, 4), "I": Region.point(10)})

    def test_left_side_keeps_what_it_requires_on_the_right(self):
        m = TrackManager(rules=[implies("A", ("B", 2))])
        m.switch_rules_text(Text("alpha", rules=lambda p, mode: [Add("A", Region.point(1))] if p == 0 else []))
        self.assertEqual(dict(m.track()), {"A": Region.point(1), "B": Region.point(3)})
        m.update_chip(2)
        m.switch_rules_text(Text("beta"))
        self.assertEqual(dict(m.track()), {"A": Region.point(1), "B": Region.point(3)})

    def test_switch_brings_back_what_the_text_had_removed(self):
        m = TrackManager(initial_elements=[("U", Region.closed(5, 6))])
        m.switch_rules_text(Text("alpha", rules=lambda p, mode: [Remove("U", Region.closed(5, 6))] if p == 0 else []))
        self.assertEqual(dict(m.track()), {})
        m.switch_rules_text(Text("beta"))
        self.assertEqual(dict(m.track()), {"U": Region.closed(5, 6)})

    def test_user_edits_that_require_the_text_are_reversed_too(self):
        m = TrackManager()
        m.switch_rules_text(Text("alpha", rules=lambda p, mode: [Add("A", Region.point(5))] if p == 0 else []))
        moved = m.user_edit([Move("A", Region.point(5), 2)])  # moves what alpha placed
        kept = m.user_edit([Add("U", Region.point(9))])
        m.switch_rules_text(Text("beta"))
        self.assertEqual(dict(m.track()), {"U": Region.point(9)})
        self.assertEqual(m.user_edits, [kept])
        self.assertNotIn(moved, m.user_edits)

    def test_user_cannot_change_the_left_side(self):
        m = TrackManager(rules=[implies("A", ("B", -3))])
        m.update_chip(2)
        with self.assertRaises(ValueError):
            m.user_edit([Add("A", Region.point(1))])
        with self.assertRaises(ValueError):
            m.user_edit([Add("A", Region.point(4))])  # its B would be at 1
        m.user_edit([Add("A", Region.point(6))])

    def test_continuous_edit_follows_the_chip(self):
        m = TrackManager()
        m.switch_rules_text(Text("t", follow=lambda p, mode: [("X", Region.closed(p + 1, p + 2))]))
        self.assertEqual(dict(m.track()), {"X": Region.closed(1, 2)})
        m.update_chip(F(1, 2))
        self.assertEqual(dict(m.track()), {"X": Region.closed(F(3, 2), F(5, 2))})
        m.update_chip(2)  # the chip reached [3/2, 2] of the X shown at 1/2: it stays
        self.assertEqual(dict(m.track()), {"X": Region.closed(F(3, 2), 2) | Region.closed(3, 4)})
        m.switch_rules_text(Text("u"))
        self.assertEqual(dict(m.track()), {"X": Region.closed(F(3, 2), 2)})

    def test_failed_switch_changes_nothing(self):
        m = TrackManager()
        m.switch_rules_text(Text("alpha", rules=lambda p, mode: [Add("A", Region.point(3))] if p == 0 else []))

        def broken(position, mode):
            yield Add("B", Region.point(5))
            raise RuntimeError("bad text")

        with self.assertRaises(RuntimeError):
            m.switch_rules_text(Text("beta", rules=broken))
        self.assertEqual(dict(m.track()), {"A": Region.point(3)})
        self.assertEqual(m.current_text_id, "alpha")

    def test_random_runs_without_rules(self):
        for seed in range(SEEDS):
            with self.subTest(seed=seed):
                self._random_run(random.Random(seed), [])

    def test_random_runs_with_rules(self):
        for seed in range(SEEDS // 2):
            with self.subTest(seed=seed):
                self._random_run(random.Random(seed), [implies("A", ("B", 2)), forbids("C", ("A", -1)),
                                                       implies("F", ("C", -2))])

    def _random_run(self, rng: random.Random, rules):
        m = TrackManager(modes=(1, 2), rules=rules, initial_elements=[("A", Region.closed(2, 3))])
        frozen: list[tuple[Fraction, dict]] = []  # (position, track on (-inf, position])
        for step in range(15):
            p0 = m.chip.position
            t = rng.random()
            if t < 0.25:
                seed = rng.randrange(10**6)
                text = Text(f"t{step}", rules=put_ahead(seed), follow=follow_ahead(seed),
                            transitions=((p0 + F(rng.randint(1, 6), 2), rng.choice((1, 2))),))
                if not rules:
                    # Once the previous text is reversed, the right side is what
                    # the remaining user edits make it.
                    m.switch_rules_text(Text("empty"))
                    replay = LineSystem()
                    for i in m.user_edits:
                        replay.apply(m.line._edits[i].ops)
                    self.assertEqual(restrict(m.track(), ahead(p0)), restrict(replay.state(), ahead(p0)))
                m.switch_rules_text(text)
            elif t < 0.4:
                a = p0 + F(rng.randint(1, 10), 2)
                mine = m.track().get("A", Region()) & ahead(p0)
                # Moving what a text placed makes the user edit require that text's edit.
                ops = ([Move("A", mine, F(rng.randint(1, 4), 2))] if mine and rng.random() < 0.5
                       else [Add(rng.choice("ABU"), Region.closed(a, a + 1))])
                try:
                    m.user_edit(ops)
                except ValueError:
                    pass
            else:
                m.update_chip(p0 + F(rng.randint(0, 4), 2))
            self.assertEqual(m.line.ruleset.violations(m.line.state()), [])
            frozen.append((m.chip.position, restrict(m.track(), at_or_behind(m.chip.position))))
            for q, past in frozen:  # the left side never changes
                self.assertEqual(restrict(m.track(), at_or_behind(q)), past)


if __name__ == "__main__":
    unittest.main()
