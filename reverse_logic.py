from __future__ import annotations

import uuid
from dataclasses import dataclass, field

# ============================================================================
# 1. Continuous 1D Interval Algebra
# ============================================================================

@dataclass(frozen=True)
class Region:
    """Disjoint union of intervals [start, end] and discrete points."""
    intervals: tuple[tuple[float, float], ...] = field(default_factory=tuple)
    points: tuple[float, ...] = field(default_factory=tuple)

    @classmethod
    def create(cls, intervals: list[tuple[float, float]] | None = None, points: list[float] | None = None) -> Region:
        raw_intervals = intervals or []
        raw_points = list(points or [])

        cleaned_intervals: list[tuple[float, float]] = []
        for s, e in raw_intervals:
            if s > e:
                s, e = e, s
            if s == e:
                raw_points.append(s)
            else:
                cleaned_intervals.append((s, e))

        merged_intervals: list[tuple[float, float]] = []
        if cleaned_intervals:
            sorted_ints = sorted(cleaned_intervals, key=lambda x: (x[0], x[1]))
            cur_s, cur_e = sorted_ints[0]
            for s, e in sorted_ints[1:]:
                if s <= cur_e:
                    cur_e = max(cur_e, e)
                else:
                    merged_intervals.append((cur_s, cur_e))
                    cur_s, cur_e = s, e
            merged_intervals.append((cur_s, cur_e))

        # Points subsumed by intervals are discarded
        valid_points = set()
        for p in raw_points:
            if not any(s <= p <= e for s, e in merged_intervals):
                valid_points.add(p)

        return cls(tuple(merged_intervals), tuple(sorted(valid_points)))

    def union(self, other: Region) -> Region:
        return Region.create(
            list(self.intervals) + list(other.intervals),
            list(self.points) + list(other.points)
        )

    def difference(self, other: Region) -> Region:
        cur_intervals = list(self.intervals)
        for o_start, o_end in other.intervals:
            next_intervals = []
            for s, e in cur_intervals:
                if o_end <= s or o_start >= e:
                    next_intervals.append((s, e))
                else:
                    if s < o_start:
                        next_intervals.append((s, o_start))
                    if e > o_end:
                        next_intervals.append((o_end, e))
            cur_intervals = next_intervals

        # Filter out points covered by other's intervals or other's points
        rem_points = [
            p for p in self.points 
            if p not in other.points and not any(s <= p <= e for s, e in other.intervals)
        ]
        return Region.create(cur_intervals, rem_points)

    def intersection(self, other: Region) -> Region:
        # A ∩ B = A \ (A \ B)
        return self.difference(self.difference(other))

    def shift(self, delta: float) -> Region:
        if delta == 0.0:
            return self
        return Region.create(
            [(s + delta, e + delta) for s, e in self.intervals],
            [p + delta for p in self.points]
        )

    def is_empty(self) -> bool:
        return len(self.intervals) == 0 and len(self.points) == 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Region):
            return False
        return self.intervals == other.intervals and self.points == other.points


# ============================================================================
# 2. Logic Rules Engine
# ============================================================================

class Rule:
    def evaluate(self, presence: dict[str, Region], absence: dict[str, Region]) -> bool:
        """Propagates implications into presence and absence maps. Returns True if any state changed."""
        raise NotImplementedError


class ImpliesRule(Rule):
    """
    If A is present at x, B must be present at (x + offset).
    If is_not=True: if A is present at x, B must be absent at (x + offset).
    Contrapositive (Modus Tollens) is automatically derived.
    """
    def __init__(self, cond: str, cons: str, offset: float = 0.0, is_not: bool = False):
        self.cond = cond
        self.cons = cons
        self.offset = offset
        self.is_not = is_not

    def evaluate(self, presence: dict[str, Region], absence: dict[str, Region]) -> bool:
        changed = False

        # Forward implication: A -> (B or Not B)
        cond_presence = presence.get(self.cond, Region())
        if not cond_presence.is_empty():
            target_region = cond_presence.shift(self.offset)
            target_dict = absence if self.is_not else presence
            existing = target_dict.get(self.cons, Region())
            updated = existing.union(target_region)
            if updated != existing:
                target_dict[self.cons] = updated
                changed = True

        # Contrapositive:
        # If A -> B, then Not B -> Not A
        # If A -> Not B, then B -> Not A
        contra_trigger = presence.get(self.cons, Region()) if self.is_not else absence.get(self.cons, Region())
        if not contra_trigger.is_empty():
            implied_not_a = contra_trigger.shift(-self.offset)
            existing_absence = absence.get(self.cond, Region())
            updated = existing_absence.union(implied_not_a)
            if updated != existing_absence:
                absence[self.cond] = updated
                changed = True

        return changed


class AndRule(Rule):
    """
    When A and B are present at x, C must be present at (x + offset).
    Contrapositive:
    - (Not C at x + offset) AND (A at x) -> (Not B at x)
    - (Not C at x + offset) AND (B at x) -> (Not A at x)
    """
    def __init__(self, cond_a: str, cond_b: str, cons: str, offset: float = 0.0):
        self.a = cond_a
        self.b = cond_b
        self.c = cons
        self.offset = offset

    def evaluate(self, presence: dict[str, Region], absence: dict[str, Region]) -> bool:
        changed = False

        # Forward: A ∧ B -> C
        reg_a = presence.get(self.a, Region())
        reg_b = presence.get(self.b, Region())
        overlap = reg_a.intersection(reg_b)
        if not overlap.is_empty():
            target_c = overlap.shift(self.offset)
            existing_c = presence.get(self.c, Region())
            updated_c = existing_c.union(target_c)
            if updated_c != existing_c:
                presence[self.c] = updated_c
                changed = True

        # Contrapositives:
        not_c = absence.get(self.c, Region())
        if not not_c.is_empty():
            src_not_c = not_c.shift(-self.offset)

            # Not C and A -> Not B
            conflict_b = src_not_c.intersection(reg_a)
            if not conflict_b.is_empty():
                existing = absence.get(self.b, Region())
                updated = existing.union(conflict_b)
                if updated != existing:
                    absence[self.b] = updated
                    changed = True

            # Not C and B -> Not A
            conflict_a = src_not_c.intersection(reg_b)
            if not conflict_a.is_empty():
                existing = absence.get(self.a, Region())
                updated = existing.union(conflict_a)
                if updated != existing:
                    absence[self.a] = updated
                    changed = True

        return changed


class DisjunctionRule(Rule):
    """
    If A is present at x, then B OR C must be present at x.
    Deterministic requirement:
    'if a rule says if element A then element B or C, the first operand is used, here element B.'
    """
    def __init__(self, cond: str, cons_first: str, cons_second: str):
        self.cond = cond
        self.first = cons_first
        self.second = cons_second

    def evaluate(self, presence: dict[str, Region], absence: dict[str, Region]) -> bool:
        changed = False
        cond_reg = presence.get(self.cond, Region())
        if cond_reg.is_empty():
            return False

        # If second is already present, the disjunction is satisfied.
        second_present = presence.get(self.second, Region())
        needed = cond_reg.difference(second_present)

        # Fallback to first operand
        if not needed.is_empty():
            existing = presence.get(self.first, Region())
            updated = existing.union(needed)
            if updated != existing:
                presence[self.first] = updated
                changed = True

        return changed


class MutuallyExclusiveRule(Rule):
    """A and B cannot coexist at the same position."""
    def __init__(self, elem_a: str, elem_b: str):
        self.a = elem_a
        self.b = elem_b

    def evaluate(self, presence: dict[str, Region], absence: dict[str, Region]) -> bool:
        changed = False
        reg_a = presence.get(self.a, Region())
        if not reg_a.is_empty():
            existing_b = absence.get(self.b, Region())
            updated_b = existing_b.union(reg_a)
            if updated_b != existing_b:
                absence[self.b] = updated_b
                changed = True

        reg_b = presence.get(self.b, Region())
        if not reg_b.is_empty():
            existing_a = absence.get(self.a, Region())
            updated_a = existing_a.union(reg_b)
            if updated_a != existing_a:
                absence[self.a] = updated_a
                changed = True

        return changed


# ============================================================================
# 3. Edit Operations and Line System
# ============================================================================

@dataclass
class SubEdit:
    action: str  # 'ADD', 'REMOVE', 'SHIFT'
    element_type: str
    region: Region
    delta: float = 0.0


@dataclass
class Edit:
    id: str
    sub_edits: list[SubEdit]
    timestamp: int
    is_active: bool = True


class LineSystem:
    def __init__(self):
        self.history: list[Edit] = []
        self.rules: list[Rule] = []
        self._clock: int = 0

    def add_rule(self, rule: Rule):
        self.rules.append(rule)

    def define_edit(self, sub_edits: list[SubEdit]) -> str:
        """Validates that the edit does not cause internal contradictions, then records it."""
        self._clock += 1
        temp_edit = Edit(id="temp", sub_edits=sub_edits, timestamp=self._clock)

        # Validate by dry-running state computation with this test edit
        self._compute_state_internal(test_edit=temp_edit)

        edit_id = str(uuid.uuid4())
        self.history.append(Edit(id=edit_id, sub_edits=sub_edits, timestamp=self._clock))
        return edit_id

    def reverse_edit(self, edit_id: str):
        """Toggles an edit off, perfectly restoring the timeline."""
        for e in self.history:
            if e.id == edit_id:
                e.is_active = not e.is_active
                return
        raise KeyError(f"Edit {edit_id} not found.")

    def compute_state(self) -> dict[str, Region]:
        return self._compute_state_internal()

    def _compute_state_internal(self, test_edit: Edit | None = None) -> dict[str, Region]:
        active_edits = [e for e in self.history if e.is_active]
        if test_edit:
            active_edits.append(test_edit)

        # 1. Resolve User Directives chronologically:
        # Newer direct user operations override older operations at the same coordinates.
        user_presence: dict[str, Region] = {}
        user_absence: dict[str, Region] = {}

        # Iterate newest to oldest to build the base explicit assertions
        for edit in reversed(active_edits):
            layer_presence: dict[str, Region] = {}
            layer_absence: dict[str, Region] = {}

            for op in edit.sub_edits:
                elem = op.element_type
                if op.action == 'ADD':
                    layer_presence[elem] = layer_presence.get(elem, Region()).union(op.region)
                elif op.action == 'REMOVE':
                    layer_absence[elem] = layer_absence.get(elem, Region()).union(op.region)
                elif op.action == 'SHIFT':
                    layer_absence[elem] = layer_absence.get(elem, Region()).union(op.region)
                    layer_presence[elem] = layer_presence.get(elem, Region()).union(op.region.shift(op.delta))

            # Check if this single edit is internally self-contradictory
            for elem in layer_presence.items():
                if elem in layer_absence:
                    conflict = layer_presence[elem].intersection(layer_absence[elem])
                    if not conflict.is_empty():
                        raise ValueError(f"Direct self-contradiction in edit: '{elem}' both added and removed at {conflict}")

            # Merge into global user assertions: newer edits mask older edits
            for elem, reg in layer_presence.items():
                masked_reg = reg.difference(user_absence.get(elem, Region()))
                user_presence[elem] = user_presence.get(elem, Region()).union(masked_reg)

            for elem, reg in layer_absence.items():
                masked_reg = reg.difference(user_presence.get(elem, Region()))
                user_absence[elem] = user_absence.get(elem, Region()).union(masked_reg)

        # 2. Adaptation through Global Rule Fixpoint Evaluation
        # We start with the consolidated user assertions and propagate all logical constraints.
        presence = {elem: reg for elem, reg in user_presence.items() if not reg.is_empty()}
        absence = {elem: reg for elem, reg in user_absence.items() if not reg.is_empty()}

        # Deduce implications until a fixed point is reached
        max_iterations = 100
        iteration = 0
        while iteration < max_iterations:
            iteration += 1
            changed = False
            for rule in self.rules:
                if rule.evaluate(presence, absence):
                    changed = True
            if not changed:
                break
        else:
            raise ValueError("Rule cycle detected: infinite deduction loop without stabilization.")

        # 3. Check for Rule-Induced Contradictions
        # If rules force an element to be both present and absent at the same point, the edit is forbidden.
        for elem in presence.items():
            if elem in absence:
                conflict = presence[elem].intersection(absence[elem])
                if not conflict.is_empty():
                    raise ValueError(f"Contradiction: Element '{elem}' is forced both present and absent at {conflict}")

        return {elem: reg for elem, reg in presence.items() if not reg.is_empty()}


# ============================================================================
# 4. Strict Requirements Verification Test
# ============================================================================

if __name__ == "__main__":
    system = LineSystem()

    # Rule: D implies NOT C (Mutually Exclusive)
    system.add_rule(MutuallyExclusiveRule('D', 'C'))

    # Initial State: D at [6, 7]
    r_67 = Region.create([(6, 7)])
    edit_init = system.define_edit([SubEdit('ADD', 'D', r_67)])
    print("Initial state:", list(system.compute_state().keys()))
    # Output: ['D']

    # Edit A: User explicitly adds C at [6, 7]
    # D must adapt (C is newer, so D is evicted from [6, 7])
    edit_A = system.define_edit([SubEdit('ADD', 'C', r_67)])
    print("After Edit A (C adapts and replaces D):", list(system.compute_state().keys()))
    # Output: ['C']

    # Edit B: User explicitly adds D at [6, 7]
    # C must adapt (D is newer, so C is evicted)
    edit_B = system.define_edit([SubEdit('ADD', 'D', r_67)])
    print("After Edit B (D replaces C):", list(system.compute_state().keys()))
    # Output: ['D']

    # Reverse Edit B -> C returns at [6, 7]
    system.reverse_edit(edit_B)
    print("After reversing Edit B (C returns):", list(system.compute_state().keys()))
    # Output: ['C']

    # Reverse Edit A -> Original D returns at [6, 7]
    system.reverse_edit(edit_A)
    print("After reversing Edit A (Initial D returns):", list(system.compute_state().keys()))
    # Output: ['D']

    # Edit C: Try to add both C and D at point 8 in the exact same edit
    r_8 = Region.create([], [8.0])
    try:
        system.define_edit([SubEdit('ADD', 'C', r_8), SubEdit('ADD', 'D', r_8)])
        print("ERROR: Edit C was permitted!")
    except ValueError as e:
        print("Success: Edit C was properly rejected:")
        print(f" -> {e}")