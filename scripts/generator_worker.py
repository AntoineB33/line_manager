"""One run of the rules generator in a process of its own (UI/app.py, Session.runs).

Reads a pickled (generator, state, chip, known modes) from stdin and writes the pickled
(script, stats) of `RulesGenerator.run` to stdout. A process of its own computes in
parallel with the window and the moving chip (threads of one process share the
interpreter's lock), and it is cancelled by ending it.
"""

import os
import pickle
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    gen, state, chip, known = pickle.load(sys.stdin.buffer)
    pickle.dump(gen.run(state, chip, known), sys.stdout.buffer)
