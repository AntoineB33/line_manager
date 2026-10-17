import copy
import uuid
from collections.abc import Callable
from typing import Any


class Event:
    pass

class AddEvent(Event):
    def __init__(self, element_id: str, element_type: str, position: int):
        self.element_id = element_id
        self.element_type = element_type
        self.position = position

class MoveEvent(Event):
    def __init__(self, element_id: str, new_position: int):
        self.element_id = element_id
        self.new_position = new_position

class RemoveEvent(Event):
    def __init__(self, element_id: str):
        self.element_id = element_id


class LineState:
    def __init__(self, size: int):
        self.size = size
        self.cells: list[list[dict[str, Any]]] = [[] for _ in range(size)]
        self.active_entities: dict[str, dict[str, Any]] = {}
        # Z-index strictly enforces chronological overlap hierarchy
        self.next_z_index = 0

    def get_visible_line(self) -> list[str | None]:
        """Returns the top visible element type for each cell (highest z-index), or None."""
        return [cell[-1]['type'] if cell else None for cell in self.cells]

    def add_entity(self, element_id: str, element_type: str, position: int):
        if 0 <= position < self.size:
            z_index = self.next_z_index
            self.next_z_index += 1
            
            entity = {'id': element_id, 'type': element_type, 'pos': position, 'z_index': z_index}
            self.active_entities[element_id] = entity
            self.cells[position].append(entity)
            # Ensure the stack remains ordered chronologically
            self.cells[position].sort(key=lambda e: e['z_index'])

    def move_entity(self, element_id: str, new_position: int):
        if element_id in self.active_entities and 0 <= new_position < self.size:
            entity = self.active_entities[element_id]
            old_pos = entity['pos']
            
            # Remove from old cell
            self.cells[old_pos] = [e for e in self.cells[old_pos] if e['id'] != element_id]
            
            # Update position and append to new cell
            entity['pos'] = new_position
            self.cells[new_position].append(entity)
            # Re-sort to maintain chronological hierarchy regardless of when the move happened
            self.cells[new_position].sort(key=lambda e: e['z_index'])

    def remove_entity(self, element_id: str):
        if element_id in self.active_entities:
            pos = self.active_entities[element_id]['pos']
            self.cells[pos] = [e for e in self.cells[pos] if e['id'] != element_id]
            del self.active_entities[element_id]

    def clone(self) -> 'LineState':
        """Deep copy to create a disposable view state for rules."""
        new_state = LineState(self.size)
        new_state.cells = copy.deepcopy(self.cells)
        new_state.active_entities = copy.deepcopy(self.active_entities)
        new_state.next_z_index = self.next_z_index
        return new_state


class LineEditor:
    def __init__(self, size: int = 10):
        self.size = size
        self.events: list[Event] = []
        self.rules: list[Callable[[LineState], None]] = []

    def add_rule(self, rule_func: Callable[[LineState], None]):
        self.rules.append(rule_func)

    def add(self, element_type: str, position: int) -> str:
        element_id = str(uuid.uuid4())
        self.events.append(AddEvent(element_id, element_type, position))
        return element_id

    def move(self, element_id: str, new_position: int):
        self.events.append(MoveEvent(element_id, new_position))

    def remove(self, element_id: str):
        self.events.append(RemoveEvent(element_id))

    def compute_state(self) -> LineState:
        # 1. Build the Pure Base State from user events
        base_state = LineState(self.size)

        for event in self.events:
            if isinstance(event, AddEvent):
                base_state.add_entity(event.element_id, event.element_type, event.position)
            elif isinstance(event, MoveEvent):
                base_state.move_entity(event.element_id, event.new_position)
            elif isinstance(event, RemoveEvent):
                base_state.remove_entity(event.element_id)

        # 2. Apply rules to a disposable View State
        view_state = base_state.clone()
        for rule in self.rules:
            rule(view_state)

        return view_state

    @property
    def current_line(self) -> list[str | None]:
        return self.compute_state().get_visible_line()


# ==========================================
# RULES DEFINITION
# ==========================================

def rule_a_removes_b(state: LineState):
    """If task A exists anywhere, all task B elements are removed (suppressed)."""
    line = state.get_visible_line()
    if 'A' in line:
        b_ids_to_remove = [
            entity['id'] for entity in state.active_entities.values() 
            if entity['type'] == 'B'
        ]
        for b_id in b_ids_to_remove:
            state.remove_entity(b_id)

def rule_a_adds_b_left(state: LineState):
    """If task A exists, task B must exist 3 units to its left."""
    line = state.get_visible_line()
    for i, elem_type in enumerate(line):
        if elem_type == 'A':
            target_pos = i - 3
            if target_pos >= 0 and line[target_pos] != 'B':
                state.add_entity(f"auto_B_for_{i}", 'B', target_pos)


# ==========================================
# PROOF OF MATHEMATICAL CONSTRAINTS
# ==========================================

if __name__ == "__main__":
    editor = LineEditor(size=10)
    editor.add_rule(rule_a_removes_b)

    print("--- Proof 1: Constraint 2 (Overriding and cascade removal recovery) ---")
    b1_id = editor.add('B', 2)
    b2_id = editor.add('B', 4)
    print("Initial (Added two Bs):      ", editor.current_line)
    
    a1_id = editor.add('A', 4) 
    print("Add A (Overrides B, kills B):", editor.current_line)
    
    editor.remove(a1_id)
    print("Remove A (Bs are restored):  ", editor.current_line)
    
    print("\n--- Proof 2: Constraint 3 Base (Move and Move Back) ---")
    editor.move(b1_id, 8)
    print("Move B from idx 2 to 8:      ", editor.current_line)
    
    editor.move(b1_id, 2)
    print("Move B back to idx 2:        ", editor.current_line)

    print("\n--- Proof 3: Constraint 3 Z-Index Strict Overlap Repair ---")
    overlap_editor = LineEditor(size=10)
    
    x_id = overlap_editor.add('X', 2)
    y_id = overlap_editor.add('Y', 2)
    print("Added X then Y to idx 2 (Y is on top): ", overlap_editor.current_line)
    
    overlap_editor.move(x_id, 8)
    print("Moved X to idx 8 (Unstacked):          ", overlap_editor.current_line)
    
    overlap_editor.move(x_id, 2)
    # If using list.append(), X would now be on top. With z-index, Y remains on top.
    print("Moved X back to idx 2 (Y stays top):   ", overlap_editor.current_line)