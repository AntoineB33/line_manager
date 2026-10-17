import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

# --- 1D Interval Algebra with Points ---

@dataclass(frozen=True)
class Region:
    """Immutable representation of 1D continuous space as disjoint intervals and discrete points."""
    intervals: tuple[tuple[float, float], ...] = field(default_factory=tuple)
    points: tuple[float, ...] = field(default_factory=tuple)

    @classmethod
    def create(cls, intervals: list[tuple[float, float]], points: list[float] | None = None) -> 'Region':
        if points is None:
            points = []
        
        merged_intervals = []
        # Normalize and merge overlapping intervals
        if intervals:
            sorted_intervals = sorted(intervals, key=lambda x: x[0])
            merged = [sorted_intervals[0]]
            for current in sorted_intervals[1:]:
                prev_start, prev_end = merged[-1]
                curr_start, curr_end = current
                if curr_start <= prev_end:
                    merged[-1] = (prev_start, max(prev_end, curr_end))
                else:
                    merged.append(current)
            
            # Filter out zero-length intervals, treating them as points instead
            for s, e in merged:
                if s < e:
                    merged_intervals.append((s, e))
                else:
                    points.append(s)

        # Filter points: keep unique points that do not fall inside any interval
        valid_points = set()
        for p in points:
            absorbed = False
            for s, e in merged_intervals:
                if s <= p <= e:
                    absorbed = True
                    break
            if not absorbed:
                valid_points.add(p)

        return cls(tuple(merged_intervals), tuple(sorted(valid_points)))

    def union(self, other: 'Region') -> 'Region':
        return self.create(
            list(self.intervals) + list(other.intervals),
            list(self.points) + list(other.points)
        )

    def difference(self, other: 'Region') -> 'Region':
        # Intervals difference
        result_intervals = []
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
            result_intervals.extend(current_pieces)
            
        # Points difference (remove if it hits another point or another interval)
        result_points = []
        for p in self.points:
            if p in other.points:
                continue
            if any(s <= p <= e for s, e in other.intervals):
                continue
            result_points.append(p)

        return self.create(result_intervals, result_points)

    def intersection(self, other: 'Region') -> 'Region':
        return self.difference(self.difference(other))

    def shift(self, delta: float) -> 'Region':
        return self.create(
            [(s + delta, e + delta) for s, e in self.intervals],
            [p + delta for p in self.points]
        )

    def is_subset(self, other: 'Region') -> bool:
        diff = self.difference(other)
        return len(diff.intervals) == 0 and len(diff.points) == 0

    def is_empty(self) -> bool:
        return len(self.intervals) == 0 and len(self.points) == 0


# --- State Engine & Reversible History ---

@dataclass
class SubEdit:
    """An atomic operation within an Edit."""
    action: str  # 'ADD', 'REMOVE', 'SHIFT'
    element_type: str
    region: Region
    delta: float = 0.0
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
        Reversing an edit conceptually removes it from the timeline timeline.
        Because operations are algebraically independent, this completely neutralizes its effects.
        """
        for edit in self.history:
            if edit.id == edit_id:
                edit.is_active = not edit.is_active
                break

    def compute_state(self) -> dict[str, Region]:
        """
        Calculates the observable state by applying a commutative integer multiplicity field.
        This mathematically guarantees that Add -> Remove, Remove -> Add, and 
        Shift -> Shift-Back identically equal the original state space.
        """
        multiplicities = {}

        # 1. Project all active edits onto the mathematical multiplicity fields
        for edit in self.history:
            if not edit.is_active:
                continue

            for op in edit.sub_edits:
                mult = multiplicities.setdefault(op.element_type, {'intervals': [], 'points': {}})
                
                # A Shift algebraically equals a removal at the source and an addition at the destination
                actions = []
                if op.action == 'ADD':
                    actions.append(('ADD', op.region, 0.0))
                elif op.action == 'REMOVE':
                    actions.append(('REMOVE', op.region, 0.0))
                elif op.action == 'SHIFT':
                    actions.append(('REMOVE', op.region, 0.0))
                    actions.append(('ADD', op.region, op.delta))

                for act, region, delta in actions:
                    sign = 1 if act == 'ADD' else -1
                    
                    # Log boundary events for interval overlap calculation
                    for s, e in region.intervals:
                        mult['intervals'].append((s + delta, sign))
                        mult['intervals'].append((e + delta, -sign))
                    
                    # Accumulate direct point multiplicities
                    for p in region.points:
                        pos = p + delta
                        mult['points'][pos] = mult['points'].get(pos, 0) + sign

        # 2. Threshold the multiplicities (> 0) down into flattened Boolean Regions
        flattened_state: dict[str, Region] = {}
        for elem_type, data in multiplicities.items():
            
            # Aggregate interval boundary changes occuring at identical coordinate points
            pos_deltas = {}
            for pos, delta in data['intervals']:
                pos_deltas[pos] = pos_deltas.get(pos, 0) + delta
            
            interval_events = sorted(pos_deltas.items(), key=lambda x: x[0])
            
            active_intervals = []
            current_sum = 0
            start_pos = None
            
            # Sweep-line algorithm to locate segments where multiplicity > 0
            for pos, delta in interval_events:
                prev_sum = current_sum
                current_sum += delta
                if prev_sum <= 0 and current_sum > 0:
                    start_pos = pos
                elif prev_sum > 0 and current_sum <= 0:
                    if start_pos is not None and start_pos < pos:
                        active_intervals.append((start_pos, pos))
                    start_pos = None
            
            # Include discrete points possessing positive multiplicity
            active_points = [p for p, count in data['points'].items() if count > 0]
            
            if active_intervals or active_points:
                flattened_state[elem_type] = Region.create(active_intervals, active_points)

        return flattened_state

    def validate_state(self) -> list[str]:
        state = self.compute_state()
        return [err for rule in self.rules if (err := rule(state)) is not None]