import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

# --- 1D Interval Algebra ---

@dataclass(frozen=True)
class Region:
    """Immutable representation of 1D continuous space as disjoint intervals."""
    intervals: tuple[tuple[float, float], ...] = field(default_factory=tuple)

    @classmethod
    def create(cls, intervals: list[tuple[float, float]]) -> 'Region':
        if not intervals:
            return cls(tuple())
        sorted_intervals = sorted(intervals, key=lambda x: x[0])
        merged = [sorted_intervals[0]]
        for current in sorted_intervals[1:]:
            prev_start, prev_end = merged[-1]
            curr_start, curr_end = current
            if curr_start <= prev_end:
                merged[-1] = (prev_start, max(prev_end, curr_end))
            else:
                merged.append(current)
        return cls(tuple(merged))

    def union(self, other: 'Region') -> 'Region':
        return self.create(list(self.intervals) + list(other.intervals))

    def difference(self, other: 'Region') -> 'Region':
        result = []
        for start, end in self.intervals:
            current_pieces = [(start, end)]
            for o_start, o_end in other.intervals:
                next_pieces = []
                for p_start, p_end in current_pieces:
                    if o_end <= p_start or o_start >= p_end:
                        next_pieces.append((p_start, p_end))
                    else:
                        if p_start < o_start: next_pieces.append((p_start, o_start))
                        if p_end > o_end: next_pieces.append((o_end, p_end))
                current_pieces = next_pieces
            result.extend(current_pieces)
        return self.create(result)

    def intersection(self, other: 'Region') -> 'Region':
        return self.difference(self.difference(other))

    def shift(self, delta: float) -> 'Region':
        return self.create([(s + delta, e + delta) for s, e in self.intervals])

    def is_subset(self, other: 'Region') -> bool:
        return not bool(self.difference(other).intervals)

    def is_empty(self) -> bool:
        return len(self.intervals) == 0

# --- State Engine & Reversible History ---

@dataclass
class SubEdit:
    """An atomic operation within an Edit."""
    action: str  # 'ADD', 'REMOVE', 'SHIFT'
    element_type: str
    region: Region
    delta: float = 0.0
    # Every atomic addition creates a mathematically distinct instance
    instance_id: str = field(default_factory=lambda: str(uuid.uuid4()))

@dataclass
class Edit:
    """A finite set of additions, removals, and shifts. Represents a single user action."""
    id: str
    sub_edits: list[SubEdit]
    is_active: bool = True

class LineSystem:
    def __init__(self):
        self.history: list[Edit] = []
        self.rules: list[Callable[[dict[str, Region]], str | None]] = []

    def add_rule(self, rule_func: Callable[[dict[str, Region]], str | None]):
        self.rules.append(rule_func)

    def commit_edit(self, sub_edits: list[SubEdit]) -> str:
        edit_id = str(uuid.uuid4())
        self.history.append(Edit(edit_id, sub_edits))
        return edit_id

    def reverse_edit(self, edit_id: str):
        """
        Satisfies the requirement: Reversing an edit toggles its active state.
        Because state is projected functionally, independent edits commute perfectly.
        """
        for edit in self.history:
            if edit.id == edit_id:
                edit.is_active = not edit.is_active
                break

    def compute_state(self) -> dict[str, Region]:
        """
        Calculates the observable state by replaying the active history.
        Operations apply to their specific mathematical instances, avoiding geometric information loss.
        """
        # 1. Track the lifecycle and spatial footprint of every distinct added instance
        instances_by_type: dict[str, dict[str, Region]] = {}

        for edit in self.history:
            if not edit.is_active:
                continue

            for op in edit.sub_edits:
                type_dict = instances_by_type.setdefault(op.element_type, {})

                if op.action == 'ADD':
                    # Additions create new independent matter
                    type_dict[op.instance_id] = op.region

                elif op.action == 'REMOVE':
                    # Removals target the spatial footprint of *all* active instances of that type at that moment
                    for inst_id, inst_region in type_dict.items():
                        type_dict[inst_id] = inst_region.difference(op.region)

                elif op.action == 'SHIFT':
                    # Shifts target and move the matter in the region, isolating it from merged surroundings
                    for inst_id, inst_region in type_dict.items():
                        affected_matter = inst_region.intersection(op.region)
                        unaffected_matter = inst_region.difference(op.region)
                        shifted_matter = affected_matter.shift(op.delta)
                        type_dict[inst_id] = unaffected_matter.union(shifted_matter)

        # 2. Project instances down to the flattened boolean spatial state required for rules
        flattened_state: dict[str, Region] = {}
        for elem_type, instances in instances_by_type.items():
            combined = Region.create([])
            for inst_region in instances.values():
                combined = combined.union(inst_region)
            flattened_state[elem_type] = combined

        return flattened_state

    def validate_state(self) -> list[str]:
        state = self.compute_state()
        return [err for rule in self.rules if (err := rule(state)) is not None]

# --- Verification & Rule Implementation Examples ---

def rule_a_requires_b(state: dict[str, Region]) -> str | None:
    region_a = state.get('A', Region.create([]))
    region_b = state.get('B', Region.create([]))
    if not region_a.is_subset(region_b):
        return "Violation: A exists without B."
    return None

def rule_ab_requires_c_shifted(state: dict[str, Region]) -> str | None:
    region_a = state.get('A', Region.create([]))
    region_b = state.get('B', Region.create([]))
    region_c = state.get('C', Region.create([]))
    
    ab_overlap = region_a.intersection(region_b)
    required_c = ab_overlap.shift(-3)
    
    if not required_c.is_subset(region_c):
        return "Violation: A+B overlap lacks C shifted -3 units."
    return None