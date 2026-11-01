"""A new window closes the one open: between processes, with no window on the screen."""

import os
import subprocess
import sys
import unittest

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "UI")
sys.path.insert(0, UI)
import single_window as W  # noqa: E402

PORT = W.PORT + 1  # (not the port of a window the user has open)
OPEN = """
import sys, time
sys.path.insert(0, sys.argv[1])
import single_window as W
server = W.take_over({port})
W.listen(server, {asked})
print("open", flush=True)
time.sleep(30)
"""


class TakeOverTest(unittest.TestCase):
    def window(self, asked):
        p = subprocess.Popen([sys.executable, "-c", OPEN.format(port=PORT, asked=asked), UI], stdout=subprocess.PIPE, text=True)
        self.addCleanup(p.stdout.close)
        self.addCleanup(p.wait, 10)
        self.addCleanup(p.kill)
        self.assertEqual(p.stdout.readline().strip(), "open")
        return p

    def test_the_port_is_free(self):
        server = W.take_over(PORT)
        self.assertIsNotNone(server)
        server.close()

    def test_a_window_closes_when_asked(self):
        p = self.window("lambda: __import__('os')._exit(0)")
        server = W.take_over(PORT)
        self.addCleanup(server.close)
        self.assertEqual(p.wait(10), 0)

    def test_a_window_that_does_not_close_is_ended(self):
        p = self.window("lambda: None")
        server = W.take_over(PORT, wait=0.3)
        self.addCleanup(server.close)
        self.assertIsNotNone(server)
        self.assertIsNotNone(p.wait(10))

    def test_each_new_window_closes_the_one_before(self):
        p = self.window("lambda: __import__('os')._exit(0)")
        q = None
        try:
            q = subprocess.Popen([sys.executable, "-c", OPEN.format(port=PORT, asked="lambda: __import__('os')._exit(0)"), UI],
                                 stdout=subprocess.PIPE, text=True)
            self.assertEqual(q.stdout.readline().strip(), "open")
            self.assertEqual(p.wait(10), 0)
            server = W.take_over(PORT)
            self.addCleanup(server.close)
            self.assertEqual(q.wait(10), 0)
        finally:
            if q is not None:
                q.kill()
                q.wait(10)
                q.stdout.close()

    def test_a_port_held_by_another_program_is_not_a_window(self):
        other = W._bind(PORT)  # listens, and never answers
        self.addCleanup(other.close)
        self.assertIsNone(W.take_over(PORT, wait=0.1))


if __name__ == "__main__":
    unittest.main()
