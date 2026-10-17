from __future__ import annotations

import uuid
from collections import defaultdict
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

        rem_points = [
            p for p in self.points 
            if p not in other.points and not any(s <= p <= e for s, e in other.intervals)
        ]
        return Region.create(cur_intervals, rem_points)

    def intersection(self, other: Region) -> Region:
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
# 2. Logic Rules Engine (Priority-Aware)
# ============================================================================
# Assertions map elements to a list of (Region, priority) tuples.
# A higher priority (newer edit timestamp) forces lower priorities to adapt.

class Rule:
    def evaluate(self, presence: dict[str, list[tuple[Region, int]]], absence: dict[str, list[tuple[Region, int]]]) -> bool:
        raise NotImplementedError

def apply_assertion(target_dict: dict[str, list[tuple[Region, int]]], element: str, new_reg: Region, priority: int) -> bool:
    if new_reg.is_empty():
        return False
    existing_list = target_dict.get(element, [])
    
    # Check if this new region is already fully covered at an equal or higher priority
    uncovered = new_reg
    for ex_reg, ex_pri in existing_list:
        if ex_pri >= priority:
            uncovered = uncovered.difference(ex_reg)
            if uncovered.is_empty():
                return False
                
    # Insert new assertion
    existing_list.append((uncovered, priority))
    target_dict[element] = existing_list
    return True


def cross_intersect(list_a: list[tuple[Region, int]], list_b: list[tuple[Region, int]], offset: float = 0.0) -> list[tuple[Region, int]]:
    """Yields intersections of two priority lists. The resulting priority is the MINIMUM of the two (weakest link)."""
    results = []
    for reg_a, pri_a in list_a:
        shifted_a = reg_a.shift(offset)
        for reg_b, pri_b in list_b:
            intersect = shifted_a.intersection(reg_b)
            if not intersect.is_empty():
                results.append((intersect, min(pri_a, pri_b)))
    return results


class ImpliesRule(Rule):
    def __init__(self, cond: str, cons: str, offset: float = 0.0, is_not: bool = False):
        self.cond = cond
        self.cons = cons
        self.offset = offset
        self.is_not = is_not

    def evaluate(self, presence: dict[str, list[tuple[Region, int]]], absence: dict[str, list[tuple[Region, int]]]) -> bool:
        changed = False
        target_dict = absence if self.is_not else presence

        # Forward: A -> (B or Not B)
        for reg_a, pri_a in presence.get(self.cond, []):
            shifted = reg_a.shift(self.offset)
            if apply_assertion(target_dict, self.cons, shifted, pri_a):
                changed = True

        # Contrapositive
        contra_src = presence if self.is_not else absence
        for reg_c, pri_c in contra_src.get(self.cons, []):
            shifted_back = reg_c.shift(-self.offset)
            if apply_assertion(absence, self.cond, shifted_back, pri_c):
                changed = True

        return changed


class AndRule(Rule):
    def __init__(self, cond_a: str, cond_b: str, cons: str, offset: float = 0.0):
        self.a = cond_a
        self.b = cond_b
        self.c = cons
        self.offset = offset

    def evaluate(self, presence: dict[str, list[tuple[Region, int]]], absence: dict[str, list[tuple[Region, int]]]) -> bool:
        changed = False

        # Forward: A AND B -> C
        overlaps = cross_intersect(presence.get(self.a, []), presence.get(self.b, []))
        for overlap, pri in overlaps:
            shifted = overlap.shift(self.offset)
            if apply_assertion(presence, self.c, shifted, pri):
                changed = True

        # Contrapositive 1: Not C AND A -> Not B
        not_c_and_a = cross_intersect(absence.get(self.c, []), presence.get(self.a, []), offset=-self.offset)
        for overlap, pri in not_c_and_a:
            if apply_assertion(absence, self.b, overlap, pri):
                changed = True

        # Contrapositive 2: Not C AND B -> Not A
        not_c_and_b = cross_intersect(absence.get(self.c, []), presence.get(self.b, []), offset=-self.offset)
        for overlap, pri in not_c_and_b:
            if apply_assertion(absence, self.a, overlap, pri):
                changed = True

        return changed


class DisjunctionRule(Rule):
    def __init__(self, cond: str, cons_first: str, cons_second: str):
        self.cond = cond
        self.first = cons_first
        self.second = cons_second

    def evaluate(self, presence: dict[str, list[tuple[Region, int]]], absence: dict[str, list[tuple[Region, int]]]) -> bool:
        changed = False
        # If A, then B OR C (Deterministic: B is first operand)
        for reg_cond, pri_cond in presence.get(self.cond, []):
            # Only parts where C is NOT present need B
            unmet = reg_cond
            for reg_second, pri_second in presence.get(self.second, []):
                unmet = unmet.difference(reg_second)
            
            if apply_assertion(presence, self.first, unmet, pri_cond):
                changed = True
        return changed


class MutuallyExclusiveRule(Rule):
    def __init__(self, elem_a: str, elem_b: str):
        self.a = elem_a
        self.b = elem_b

    def evaluate(self, presence: dict[str, list[tuple[Region, int]]], absence: dict[str, list[tuple[Region, int]]]) -> bool:
        changed = False
        for reg_a, pri_a in presence.get(self.a, []):
            if apply_assertion(absence, self.b, reg_a, pri_a):
                changed = True
        for reg_b, pri_b in presence.get(self.b, []):
            if apply_assertion(absence, self.a, reg_b, pri_b):
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
        self._clock += 1
        temp_edit = Edit(id="temp", sub_edits=sub_edits, timestamp=self._clock)
        self._compute_state_internal(test_edit=temp_edit)  # Validate

        edit_id = str(uuid.uuid4())
        self.history.append(Edit(id=edit_id, sub_edits=sub_edits, timestamp=self._clock))
        return edit_id

    def reverse_edit(self, edit_id: str):
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

        presence: dict[str, list[tuple[Region, int]]] = defaultdict(list)
        absence: dict[str, list[tuple[Region, int]]] = defaultdict(list)

        # Apply raw user operations with their timestamps as priorities
        for edit in active_edits:
            ts = edit.timestamp
            for op in edit.sub_edits:
                elem = op.element_type
                if op.action == 'ADD':
                    apply_assertion(presence, elem, op.region, ts)
                elif op.action == 'REMOVE':
                    apply_assertion(absence, elem, op.region, ts)
                elif op.action == 'SHIFT':
                    apply_assertion(absence, elem, op.region, ts)
                    apply_assertion(presence, elem, op.region.shift(op.delta), ts)

        # Propagate logical constraints
        iteration = 0
        while iteration < 100:
            iteration += 1
            changed = False
            for rule in self.rules:
                if rule.evaluate(presence, absence):
                    changed = True
            if not changed:
                break
        else:
            raise ValueError("Rule cycle detected: infinite deduction loop.")

        # Resolve state and test for hard contradictions
        final_state: dict[str, Region] = {}
        all_elements = set(presence.keys()).union(absence.keys())
        
        for elem in all_elements:
            final_reg = Region()
            # Sort all assertions for this element by priority (lowest to highest)
            elem_assertions = [(r, p, 'P') for r, p in presence.get(elem, [])] + \
                              [(r, p, 'A') for r, p in absence.get(elem, [])]
            
            # Cross-check for contradictions at the EXACT SAME priority
            pres_at_pri = defaultdict(Region)
            abs_at_pri = defaultdict(Region)
            for reg, pri, kind in elem_assertions:
                if kind == 'P': pres_at_pri[pri] = pres_at_pri[pri].union(reg)
                else: abs_at_pri[pri] = abs_at_pri[pri].union(reg)
                
            for pri in set(pres_at_pri.keys()).intersection(abs_at_pri.keys()):
                conflict = pres_at_pri[pri].intersection(abs_at_pri[pri])
                if not conflict.is_empty():
                    raise ValueError(f"Contradiction: '{elem}' both present and absent at {conflict} with equal priority {pri}")

            # Apply layer by layer, newer (higher priority) masking older
            elem_assertions.sort(key=lambda x: x[1])
            for reg, pri, kind in elem_assertions:
                if kind == 'P':
                    final_reg = final_reg.union(reg)
                else:
                    final_reg = final_reg.difference(reg)

            if not final_reg.is_empty():
                final_state[elem] = final_reg

        return final_state


# ============================================================================
# 4. Strict Requirements Verification Test
# ============================================================================

if __name__ == "__main__":
    system = LineSystem()

    # Rule: D implies NOT C (Mutually Exclusive)
    system.add_rule(MutuallyExclusiveRule('D', 'C'))

    r_67 = Region.create([(6, 7)])
    edit_init = system.define_edit([SubEdit('ADD', 'D', r_67)])
    print("Initial state:", list(system.compute_state().keys()))
    # Output: ['D']

    # Edit A: User explicitly adds C at [6, 7]
    edit_A = system.define_edit([SubEdit('ADD', 'C', r_67)])
    print("After Edit A (C adapts and replaces D):", list(system.compute_state().keys()))
    # Output: ['C']

    # Edit B: User explicitly adds D at [6, 7]
    edit_B = system.define_edit([SubEdit('ADD', 'D', r_67)])
    print("After Edit B (D replaces C):", list(system.compute_state().keys()))
    # Output: ['D']

    # Reverse Edit B -> C returns
    system.reverse_edit(edit_B)
    print("After reversing Edit B (C returns):", list(system.compute_state().keys()))
    # Output: ['C']

    # Reverse Edit A -> Original D returns
    system.reverse_edit(edit_A)
    print("After reversing Edit A (Initial D returns):", list(system.compute_state().keys()))
    # Output: ['D']

    # Edit C: Try to add both C and D at point 8 in the EXACT SAME edit
    r_8 = Region.create([], [8.0])
    try:
        system.define_edit([SubEdit('ADD', 'C', r_8), SubEdit('ADD', 'D', r_8)])
        print("ERROR: Edit C was permitted!")
    except ValueError as e:
        print("Success: Edit C was properly rejected:")
        print(f" -> {e}")