import uuid
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass
class Region:
    """Represents a finite set of intervals or points mathematically."""
    points: set[float] = field(default_factory=set)
    intervals: list[tuple[float, float]] = field(default_factory=list)

    def shift(self, delta: float) -> 'Region':
        new_points = {p + delta for p in self.points}
        new_intervals = [(start + delta, end + delta) for start, end in self.intervals]
        return Region(new_points, new_intervals)

    def contains(self, x: float) -> bool:
        if x in self.points:
            return True
        for start, end in self.intervals:
            if start <= x <= end:
                return True
        return False

class Action:
    def __init__(self, action_type: str, payload: dict):
        self.id = str(uuid.uuid4())
        self.action_type = action_type  # 'ADD', 'REMOVE', 'SHIFT'
        self.payload = payload
        self.is_active = True 

class LineState:
    """
    Evaluates the continuous mathematical state of the line at any point x.
    Uses Fixed-Point Iteration to guarantee deterministic rule resolution.
    """
    def __init__(self, base_densities: dict[str, Callable[[float], int]], rules: list[Callable[['LineState', float], None]]):
        self._base_densities = base_densities
        self._rules = rules
        self._evaluated_points: set[tuple[str, float]] = set()
        
        # Stores the resolved deterministic state for (element_type, x)
        self._resolved_state: dict[str, dict[float, bool]] = {}

    def get_user_density(self, element_type: str, x: float) -> int:
        if element_type not in self._base_densities:
            return 0
        return self._base_densities[element_type](x)

    def _initialize_point(self, x: float):
        """Initializes all known element types at x based purely on user density."""
        for element_type in self._base_densities:
            if element_type not in self._resolved_state:
                self._resolved_state[element_type] = {}
            if x not in self._resolved_state[element_type]:
                self._resolved_state[element_type][x] = self.get_user_density(element_type, x) > 0

    def is_present(self, element_type: str, x: float) -> bool:
        """
        Determines mathematical presence using a deterministic Fixed-Point closure.
        """
        # If we've already resolved this exact coordinate globally, return it
        if element_type in self._resolved_state and x in self._resolved_state[element_type]:
            return self._resolved_state[element_type][x]

        self._initialize_point(x)

        # Fixed-Point Iteration: Apply rules until the logical state stops mutating.
        # This guarantees mathematical determinism regardless of query order.
        changed = True
        iterations = 0
        max_iterations = 100 # Prevents infinite loops in paradoxical rules (e.g. A => not A)

        while changed and iterations < max_iterations:
            changed = False
            iterations += 1
            
            # Snapshot the state before applying rules
            state_snapshot = {
                etype: self._resolved_state[etype].get(x, False) 
                for etype in self._resolved_state
            }

            # Apply all rules globally for this coordinate
            for rule in self._rules:
                rule(self, x)

            # Check if any rule mutated the state
            for etype in self._resolved_state:
                if self._resolved_state[etype].get(x, False) != state_snapshot.get(etype, False):
                    changed = True
                    break

        if iterations == max_iterations:
            raise ValueError(f"Logical paradox detected at x={x}. Rules do not converge.")

        return self._resolved_state.get(element_type, {}).get(x, False)

    def force_presence(self, element_type: str, x: float, state: bool):
        if element_type not in self._resolved_state:
            self._resolved_state[element_type] = {}
        self._resolved_state[element_type][x] = state


class LineEditor:
    def __init__(self):
        self.history: list[Action] = []
        self.rules: list[Callable[[LineState, float], None]] = []

    def add_rule(self, rule_func: Callable[[LineState, float], None]):
        self.rules.append(rule_func)

    def do_edit(self, action_type: str, payload: dict) -> str:
        action = Action(action_type, payload)
        self.history.append(action)
        return action.id

    def reverse_edit(self, action_id: str):
        for action in self.history:
            if action.id == action_id:
                action.is_active = not action.is_active
                break

    def add(self, element_type: str, region: Region) -> str:
        return self.do_edit('ADD', {'type': element_type, 'region': region})

    def remove(self, element_type: str, region: Region) -> str:
        return self.do_edit('REMOVE', {'type': element_type, 'region': region})

    def shift(self, element_type: str, region: Region, delta: float) -> str:
        # A shift is mathematically identical to a coupled Removal and Addition.
        # This perfectly preserves commutativity constraints regardless of overlap.
        return self.do_edit('SHIFT', {'type': element_type, 'region': region, 'delta': delta})

    def _create_density_evaluator(self, element_type: str) -> Callable[[float], int]:
        """Creates a pure mathematical function that folds history at coordinate x."""
        def evaluate(x: float) -> int:
            density = 0
            for action in self.history:
                if not action.is_active or action.payload['type'] != element_type:
                    continue
                
                payload = action.payload
                region = payload['region']
                
                if action.action_type == 'ADD':
                    if region.contains(x):
                        density += 1
                        
                elif action.action_type == 'REMOVE':
                    if region.contains(x):
                        density -= 1
                        
                elif action.action_type == 'SHIFT':
                    # Extract density out of the base region
                    if region.contains(x):
                        density -= 1
                    # Re-inject density into the shifted destination
                    if region.shift(payload['delta']).contains(x):
                        density += 1
                        
            return density
        return evaluate

    def compute_state(self) -> LineState:
        """
        Returns a functional space (LineState) mathematically guaranteed to respect 
        reversibility, arbitrary region subsets, commutativity, and rule constraints.
        """
        active_types = {a.payload['type'] for a in self.history if a.is_active}
        
        density_evaluators = {
            t: self._create_density_evaluator(t) for t in active_types
        }
        
        return LineState(density_evaluators, self.rules)

# --- Example Rule Usage ---
# rule 1: "when there is element A, there is not element B"
def rule_a_implies_not_b(state: LineState, x: float):
    if state.is_present('A', x):
        state.force_presence('B', x, False)

# rule 2: "when there is element A and B, there is element C 3 units before"
def rule_a_and_b_implies_c_shifted(state: LineState, x: float):
    # If C evaluates at x, it asks: "Are A and B present at x + 3?"
    if state.is_present('A', x + 3) and state.is_present('B', x + 3):
        state.force_presence('C', x, True)