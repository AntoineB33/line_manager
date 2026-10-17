import uuid
from collections.abc import Callable
from typing import Any


class Action:
    def __init__(self, action_type: str, payload: dict):
        self.id = str(uuid.uuid4())
        self.action_type = action_type  # 'ADD', 'REMOVE', 'MOVE'
        self.payload = payload
        self.is_active = True  # Toggling this perfectly satisfies "as if it never happened"

class LineState:
    def __init__(self, size: int):
        self.size = size
        # Element storage: eid -> {'id': str, 'type': str, 'pos': int, 'z_index': int}
        self.elements: dict[str, dict[str, Any]] = {}

    def get_visible_line(self) -> list[str | None]:
        cells: list[list[dict[str, Any]]] = [[] for _ in range(self.size)]
        
        for e in self.elements.values():
            if 0 <= e['pos'] < self.size:
                cells[e['pos']].append(e)
                
        # Sort each cell by z_index to maintain layer stability
        for cell in cells:
            cell.sort(key=lambda x: x['z_index'])
            
        return [cell[-1]['type'] if cell else None for cell in cells]

class LineEditor:
    def __init__(self, size: int = 10):
        self.size = size
        self.history: list[Action] = []
        # Rules map a LineState to a LineState declaratively
        self.rules: list[Callable[[LineState], None]] = []
        
        # Track the original z-index of an entity globally. 
        # This mathematically ensures: "Remove -> Add = State as if before removal"
        # because the re-added element perfectly drops back into its original layer.
        self.entity_z_indices: dict[str, int] = {}
        self.next_z_index = 0

    def add_rule(self, rule_func: Callable[[LineState], None]):
        self.rules.append(rule_func)

    def do_edit(self, action_type: str, payload: dict) -> str:
        """Applies an edit and returns the Action ID so it can be reversed."""
        action = Action(action_type, payload)
        self.history.append(action)
        return action.id

    def reverse_edit(self, action_id: str):
        """Mathematically erases an edit as if it never happened."""
        for action in self.history:
            if action.id == action_id:
                action.is_active = not action.is_active
                break

    def add(self, element_type: str, position: int, element_id: str | None = None) -> tuple[str, str]:
        if not element_id:
            element_id = str(uuid.uuid4())
        action_id = self.do_edit('ADD', {'id': element_id, 'type': element_type, 'pos': position})
        return element_id, action_id

    def move(self, element_id: str, new_position: int) -> str:
        return self.do_edit('MOVE', {'id': element_id, 'pos': new_position})

    def remove(self, element_id: str) -> str:
        return self.do_edit('REMOVE', {'id': element_id})

    def compute_intended_state(self) -> LineState:
        """
        Step 1: Compute the pure intended state of the user without any rules.
        Because this folds commutative actions, it strictly satisfies the edit reversibility.
        """
        state = LineState(self.size)
        
        for action in self.history:
            if not action.is_active:
                continue
                
            payload = action.payload
            eid = payload['id']
            
            if action.action_type == 'ADD':
                if eid not in self.entity_z_indices:
                    self.entity_z_indices[eid] = self.next_z_index
                    self.next_z_index += 1
                
                state.elements[eid] = {
                    'id': eid,
                    'type': payload['type'],
                    'pos': payload['pos'],
                    'z_index': self.entity_z_indices[eid]
                }
            elif action.action_type == 'MOVE':
                if eid in state.elements:
                    state.elements[eid]['pos'] = payload['pos']
            elif action.action_type == 'REMOVE':
                if eid in state.elements:
                    del state.elements[eid]
                    
        return state

    def compute_state(self) -> LineState:
        """
        Step 2: Apply rules as declarative constraints over the intended state.
        This prevents rules from permanently polluting the user's action history.
        """
        state = self.compute_intended_state()
        
        for rule in self.rules:
            rule(state)

        return state

    @property
    def current_line(self) -> list[str | None]:
        return self.compute_state().get_visible_line()