import copy
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
        self.cells: list[list[dict[str, Any]]] = [[] for _ in range(size)]
        self.active_entities: dict[str, dict[str, Any]] = {}
        self.next_z_index = 0

    def get_visible_line(self) -> list[str | None]:
        return [cell[-1]['type'] if cell else None for cell in self.cells]

    def _resort_cell(self, pos: int):
        self.cells[pos].sort(key=lambda e: e['z_index'])

    def execute(self, action: Action):
        if not action.is_active:
            return

        payload = action.payload
        if action.action_type == 'ADD':
            eid, etype, pos = payload['id'], payload['type'], payload['pos']
            if 0 <= pos < self.size:
                # Z-index is tied to the ADD action's sequence, not the entity itself.
                # If an ADD action is replayed, its relative chronological z-index is perfectly preserved.
                entity = {'id': eid, 'type': etype, 'pos': pos, 'z_index': self.next_z_index}
                self.next_z_index += 1
                self.active_entities[eid] = entity
                self.cells[pos].append(entity)
                self._resort_cell(pos)

        elif action.action_type == 'MOVE':
            eid, new_pos = payload['id'], payload['pos']
            if eid in self.active_entities and 0 <= new_pos < self.size:
                entity = self.active_entities[eid]
                old_pos = entity['pos']
                self.cells[old_pos] = [e for e in self.cells[old_pos] if e['id'] != eid]
                entity['pos'] = new_pos
                self.cells[new_pos].append(entity)
                self._resort_cell(new_pos)

        elif action.action_type == 'REMOVE':
            eid = payload['id']
            if eid in self.active_entities:
                pos = self.active_entities[eid]['pos']
                self.cells[pos] = [e for e in self.cells[pos] if e['id'] != eid]
                del self.active_entities[eid]

    def clone(self) -> 'LineState':
        new_state = LineState(self.size)
        new_state.cells = copy.deepcopy(self.cells)
        new_state.active_entities = copy.deepcopy(self.active_entities)
        new_state.next_z_index = self.next_z_index
        return new_state

class LineEditor:
    def __init__(self, size: int = 10):
        self.size = size
        self.history: list[Action] = []
        self.rules: list[Callable[[LineState], None]] = []
        # We track entity IDs created so "Remove then Add" targets the EXACT same historical entity
        self._entity_registry: dict[str, str] = {} 

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

    # Helper UI Methods mapping to the generic do_edit
    def add(self, element_type: str, position: int, element_id: str | None = None) -> tuple[str, str]:
        if not element_id:
            element_id = str(uuid.uuid4())
        action_id = self.do_edit('ADD', {'id': element_id, 'type': element_type, 'pos': position})
        return element_id, action_id

    def move(self, element_id: str, new_position: int) -> str:
        return self.do_edit('MOVE', {'id': element_id, 'pos': new_position})

    def remove(self, element_id: str) -> str:
        return self.do_edit('REMOVE', {'id': element_id})

    def compute_state(self) -> LineState:
        # 1. Pure projection from the active timeline
        base_state = LineState(self.size)
        for action in self.history:
            base_state.execute(action)

        # 2. Rule application on disposable view
        view_state = base_state.clone()
        for rule in self.rules:
            rule(view_state)

        return view_state

    @property
    def current_line(self) -> list[str | None]:
        return self.compute_state().get_visible_line()