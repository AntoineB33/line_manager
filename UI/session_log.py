"""What a window does, written in logs/ at the root of the project, for whoever is not
looking at it. For a session started by UI/examples/<name>.py:
- <name>.log: what happened, oldest first, over all the sessions: each message of the
  window, Play and Pause, errors that ended a thread or a click;
- <name>.state.txt: the state of the session now (Session.state_text), replaced at most
  once per second: it says when it was written, so a stale one shows.
Nothing here stops the window: what cannot be written is dropped.
"""

from __future__ import annotations

import os
import sys
import time

FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs")
STATE_EVERY = 1.0  # the seconds between two writes of the state


class SessionLog:
    def __init__(self, name: str | None = None, folder: str = FOLDER) -> None:
        name = name or os.path.splitext(os.path.basename(sys.argv[0]))[0] or "session"
        self.journal = os.path.join(folder, f"{name}.log")
        self.state_file = os.path.join(folder, f"{name}.state.txt")
        self.next_state = 0.0
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            pass
        self.event(f"=== session started, process {os.getpid()} ===")

    def event(self, text: str) -> None:
        try:
            with open(self.journal, "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
        except OSError:
            pass

    def state_due(self) -> bool:
        return time.time() >= self.next_state

    def state(self, text: str) -> None:
        self.next_state = time.time() + STATE_EVERY
        head = f"written {time.strftime('%Y-%m-%d %H:%M:%S')}, process {os.getpid()}\n"
        tmp = self.state_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(head + text)
            os.replace(tmp, self.state_file)  # (never half written; refused while it is being read)
        except OSError:
            pass
