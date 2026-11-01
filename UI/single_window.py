"""One window at a time, whatever example it shows: a window listens on a local port, and
a new one asks whoever listens there to close (`take_over`), then takes the port. The
answer gives the process, so a window that does not close (stuck) is ended: this cannot
be another program's process, as it could with a number left in a file.
"""

from __future__ import annotations

import os
import socket
import threading
import time

PORT = 48213
WAIT = 5.0  # the seconds a window has to close


def _bind(port: int) -> socket.socket | None:
    server = socket.socket()
    if os.name == "posix":  # (elsewhere this lets two programs share the port)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind(("127.0.0.1", port))
        server.listen()
        return server
    except OSError:
        server.close()
        return None


def _ask_to_close(port: int) -> int | None:
    """The process of the window asked to close; None if no window answers there."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1) as c:
            return int(c.recv(32))
    except (OSError, ValueError):
        return None


def take_over(port: int = PORT, wait: float = WAIT) -> socket.socket | None:
    """Close the window open, if any, and hold the port. None if it stays held by something
    that is not a window: then this window just does not close the next ones."""
    server = _bind(port)
    if server is not None:
        return server
    pid = _ask_to_close(port)
    end = time.monotonic() + wait
    while time.monotonic() < end:
        server = _bind(port)
        if server is not None:
            return server
        time.sleep(0.05)
    if pid is None:
        return None
    try:
        os.kill(pid, 15)  # (on Windows, it ends at once)
    except OSError:
        pass
    end = time.monotonic() + wait
    while server is None and time.monotonic() < end:
        time.sleep(0.05)
        server = _bind(port)
    return server


def listen(server: socket.socket | None, asked) -> None:
    """Call asked() (in a thread of its own) when a new window asks this one to close."""
    def serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            with conn:
                conn.sendall(str(os.getpid()).encode())
            asked()

    if server is not None:
        threading.Thread(target=serve, daemon=True).start()
