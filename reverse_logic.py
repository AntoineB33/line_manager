import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

# --- 1D Interval Algebra (Constructive Solid Geometry) ---

@dataclass
class Region:
    """A mathematically strict representation of 1D continuous space as disjoint intervals."""
    intervals: list[tuple[float, float]] = field(default_factory=list)

    def normalize(self) -> 'Region':
        """Merges overlapping/adjacent intervals to maintain a canonical disjoint state."""
        if not self.intervals:
            return Region([])
        sorted_intervals = sorted(self.intervals, key=lambda x: x[0])
        merged = [sorted_intervals[0]]
        for current in sorted_intervals[1:]:
            prev_start, prev_end = merged[-1]
            curr_start, curr_end = current
            if curr_start <= prev_end:  # Overlapping or adjacent
                merged[-1] = (prev_start, max(prev_end, curr_end))
            else:
                merged.append(current)
        return Region(merged)

    def union(self, other: 'Region') -> 'Region':
        return Region(self.intervals + other.intervals).normalize()

    def difference(self, other: 'Region') -> 'Region':
        result = []
        for start, end in self.intervals:
            current_pieces = [(start, end)]
            for o_start, o_end in other.intervals:
                next_pieces = []
                for p_start, p_end in current_pieces:
                    # No overlap
                    if o_end <= p_start or o_start >= p_end:
                        next_pieces.append((p_start, p_end))
                    else:
                        # Cut overlapping parts
                        if p_start < o_start:
                            next_pieces.append((p_start, o_start))
                        if p_end > o_end:
                            next_pieces.append((o_end, p_end))
                current_pieces = next_pieces
            result.extend(current_pieces)
        return Region(result).normalize()

    def intersection(self, other: 'Region') -> 'Region':
        # A intersect B = A - (A - B)
        return self.difference(self.difference(other))

    def shift(self, delta: float) -> 'Region':
        return Region([(s + delta, e + delta) for s, e in self.intervals]).normalize()

    def is_subset(self, other: 'Region') -> bool:
        """True if self is completely contained within other (self - other is empty)."""
        return not bool(self.difference(other).intervals)

    def is_empty(self) -> bool:
        return len(self.intervals) == 0

# --- State Engine & Validation ---

@dataclass
class Edit:
    action_type: str  # 'ADD', 'REMOVE', 'SHIFT'
    element_type: str
    region: Region
    delta: float = 0.0
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    is_active: bool = True

class LineSystem:
    def __init__(self):
        self.history: list[Edit] = []
        # Rules are constraint functions: (state_dict) -> list of violation messages
        self.rules: list[Callable[[dict[str, Region]], str | None]] = []

    def add_rule(self, rule_func: Callable[[dict[str, Region]], str | None]):
        self.rules.append(rule_func)

    def do_edit(self, action_type: str, element_type: str, region: Region, delta: float = 0.0) -> str:
        edit = Edit(action_type, element_type, region, delta)
        self.history.append(edit)
        return edit.id

    def reverse_edit(self, edit_id: str):
        for edit in self.history:
            if edit.id == edit_id:
                edit.is_active = not edit.is_active
                break

    def compute_state(self) -> dict[str, Region]:
        """
        Calculates the mathematical state of the line by sequentially applying active edits.
        This completely eliminates the 'Ghost Shift' flaw by only shifting what exists.
        """
        state: dict[str, Region] = {}
        
        for edit in self.history:
            if not edit.is_active:
                continue
                
            if edit.element_type not in state:
                state[edit.element_type] = Region([])

            if edit.action_type == 'ADD':
                state[edit.element_type] = state[edit.element_type].union(edit.region)
                
            elif edit.action_type == 'REMOVE':
                state[edit.element_type] = state[edit.element_type].difference(edit.region)
                
            elif edit.action_type == 'SHIFT':
                # Only shift the actual matter that exists within the target region
                existing_matter = state[edit.element_type].intersection(edit.region)
                state[edit.element_type] = state[edit.element_type].difference(edit.region)
                state[edit.element_type] = state[edit.element_type].union(existing_matter.shift(edit.delta))

        return state

    def validate_state(self) -> list[str]:
        """Runs the validation rules over the computed finite spatial sets."""
        state = self.compute_state()
        violations = []
        for rule in self.rules:
            error = rule(state)
            if error:
                violations.append(error)
        return violations

# --- Rule Implementation Examples ---

def rule_a_requires_b(state: dict[str, Region]) -> str | None:
    # "When there is element A, there must be element B"
    region_a = state.get('A', Region([]))
    region_b = state.get('B', Region([]))
    if not region_a.is_subset(region_b):
        return "Constraint Violation: Element A exists in locations without Element B."
    return None

def rule_a_excludes_b(state: dict[str, Region]) -> str | None:
    # "When there is element A, there is not element B"
    region_a = state.get('A', Region([]))
    region_b = state.get('B', Region([]))
    if not region_a.intersection(region_b).is_empty():
        return "Constraint Violation: Element A and Element B overlap."
    return None

def rule_ab_requires_c_shifted(state: dict[str, Region]) -> str | None:
    # "When there is element A and B, there is element C 3 units before"
    region_a = state.get('A', Region([]))
    region_b = state.get('B', Region([]))
    region_c = state.get('C', Region([]))
    
    ab_intersection = region_a.intersection(region_b)
    required_c = ab_intersection.shift(-3)
    
    if not required_c.is_subset(region_c):
        return "Constraint Violation: A and B overlap, but C is missing 3 units prior."
    return None

# --- Usage Example ---
system = LineSystem()
system.add_rule(rule_a_requires_b)

# 1. User drafts an edit
system.do_edit('ADD', 'A', Region([(0, 5)]))

# 2. System validates
errors = system.validate_state()
if errors:
    print("Commit rejected:", errors)
    # The application layer would reverse the invalid draft edit here.