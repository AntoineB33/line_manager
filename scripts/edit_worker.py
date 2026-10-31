"""The calculations of the user's edits in a process of its own (UI/app.py, Calculator).

A process can be stopped at any moment, where a thread or a checkpoint cannot: past the
time limit it is ended, and the edit is refused whatever it was computing. It stays
ready between two edits, its imports done, so that an edit costs only its calculation.

Writes a first pickled value when it is ready, then reads pickled jobs from stdin, each
a name of JOBS with its arguments, and writes the pickled answer of each to stdout.
"""

import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from intervals import Pos  # noqa: E402
from mode_rules import ModeRules  # noqa: E402
from simulation import user_edit  # noqa: E402


def mode_change(rules, state, mode, x, constraints):
    """What the mode rules of `mode` do from x, to know that it can be computed: why not."""
    for ev in ModeRules(rules, mode, constraints).act(state, Pos(x), Pos(x) + 1):
        pass


JOBS = {"edit": user_edit, "mode": mode_change}

if __name__ == "__main__":
    out = sys.stdout.buffer
    pickle.dump("ready", out)
    out.flush()
    while True:
        try:
            name, args = pickle.load(sys.stdin.buffer)
        except EOFError:
            break
        pickle.dump(JOBS[name](*args), out)
        out.flush()
