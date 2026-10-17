import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Region:
    """Represents a finite set of intervals or points mathematically."""
    points: set[float] = field(default_factory=set)
    intervals: list[tuple[float, float]] = field(default_factory=list)
    
    def shift(self, delta: float) -> 'Region':
        new_points = {p + delta for p in self.points}
        new_intervals = [(start + delta, end + delta) for start, end in self.intervals]
        return Region(new_points, new_intervals)

class Action:
    def __init__(self, action_type: str, payload: dict):
        self.id = str(uuid.uuid4())
        self.action_type = action_type  # 'ADD', 'REMOVE', 'SHIFT'
        self.payload = payload
        self.is_active = True 

class LineState:
    def __init__(self):
        # eid -> {'id': str, 'type': str, 'region': Region, 'z_index': int, 'is_deleted': bool}
        self.elements: dict[str, dict[str, Any]] = {}

class LineEditor:
    def __init__(self):
        self.history: list[Action] = []
        self.rules: list[Callable[[LineState], None]] = []
        self.next_z_index = 0

    def add_rule(self, rule_func: Callable[[LineState], None]):
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

    def add(self, element_id: str, element_type: str, region: Region) -> str:
        return self.do_edit('ADD', {'id': element_id, 'type': element_type, 'region': region})

    def shift(self, element_id: str, delta: float) -> str:
        return self.do_edit('SHIFT', {'id': element_id, 'delta': delta})

    def remove(self, element_id: str) -> str:
        return self.do_edit('REMOVE', {'id': element_id})

    def compute_state(self) -> LineState:
        user_presence = {}
        base_regions = {}
        element_types = {}
        net_shifts = defaultdict(float)
        z_indices = {}
        
        # 1. Aggregate net user actions purely functionally (Abelian constraints)
        for action in self.history:
            if not action.is_active:
                continue
                
            payload = action.payload
            eid = payload['id']
            
            if action.action_type == 'ADD':
                user_presence[eid] = True
                # The mathematical identity of the element is anchored on its first creation
                if eid not in base_regions:
                    base_regions[eid] = payload['region']
                    element_types[eid] = payload['type']
                    z_indices[eid] = self.next_z_index
                    self.next_z_index += 1
                    
            elif action.action_type == 'REMOVE':
                user_presence[eid] = False
                
            elif action.action_type == 'SHIFT':
                # Shifts sum independently of the transient existence of the element
                net_shifts[eid] += payload['delta']
                
        # 2. Fixed point evaluation on a clean state
        state = LineState()
        
        def get_fingerprint():
            fp = []
            for k, v in sorted(state.elements.items()):
                pts = frozenset(v['region'].points) if isinstance(v['region'], Region) else frozenset()
                ivs = tuple(v['region'].intervals) if isinstance(v['region'], Region) else ()
                fp.append((k, v.get('is_deleted', False), pts, ivs))
            return tuple(fp)

        # Repeatedly apply rules and user shifts until full mathematical convergence is reached.
        # This solves cascading generation where Rule A generates Element B, and Element B must 
        # seamlessly inherit user shifts that were historically applied to it.
        while True:
            before_fp = get_fingerprint()
            
            # A. Seed state with user-intended elements
            for eid, is_present in user_presence.items():
                if is_present and eid not in state.elements:
                    if eid not in z_indices:
                        z_indices[eid] = self.next_z_index
                        self.next_z_index += 1
                    state.elements[eid] = {
                        'id': eid,
                        'type': element_types[eid],
                        'region': base_regions[eid],
                        'z_index': z_indices[eid],
                        'is_deleted': False,
                        '_shift_applied': 0.0
                    }
                    
            # B. Apply mathematical sum of user shifts to ALL active elements
            for eid, el in state.elements.items():
                if not el.get('is_deleted', False) and eid in net_shifts:
                    pending_shift = net_shifts[eid] - el.get('_shift_applied', 0.0)
                    if pending_shift != 0.0:
                        el['region'] = el['region'].shift(pending_shift)
                        el['_shift_applied'] = net_shifts[eid]
                        
            # C. Apply rules block
            for rule in self.rules:
                rule(state)
                
            # D. Ensure any elements newly created by a rule in this iteration have tracking fields
            for eid, el in state.elements.items():
                if '_shift_applied' not in el:
                    el['_shift_applied'] = 0.0
                    if eid not in z_indices:
                        z_indices[eid] = self.next_z_index
                        self.next_z_index += 1
                    el['z_index'] = z_indices[eid]
                    
            after_fp = get_fingerprint()
            if before_fp == after_fp:
                break
                
        # 3. Compile pure output state, stripping internal tracking
        final_state = LineState()
        final_state.elements = {
            k: {key: val for key, val in v.items() if key != '_shift_applied'}
            for k, v in state.elements.items() 
            if not v.get('is_deleted', False)
        }
        
        return final_state