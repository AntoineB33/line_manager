"""The window of the line manager (tkinter, in the standard library).

The line around the chip, one row per kind, with the revealed modes above it. The chip
moves smoothly at its speed; Pause stops it, Slower and Faster halve and double its
speed, and the mode buttons change the mode from the chip on. Dashed blue lines show where the generator runs again (its script's rerun
triggers), blue ticks where it ran. "Generated script" opens the current script in a
text file: at first only "run the generator once, as soon as possible", which the
first step does, paused or not. Rules and edits are typed in the command box (type help).

The simulation runs in a thread of its own, so the window never waits for it. Each
step lets as much time run as has passed since the previous one (1/20 s to 1/2 s, in
twentieths of a second), so the chip keeps its speed while the generator or the mode
rules compute; the window slides the chip toward where the step being computed takes
it, and waits there if the step is not done yet. While paused, the mode rules at the
chip are compiled ahead, so Play does not wait for them. Pause stops
a step being computed (solver.STOP): it is undone, and computed again after Play.
The GIL's switch interval is shortened (`run`) so that the drawing is not starved by
the simulation's thread.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from fractions import Fraction as F
from tkinter import font as tkfont

from solver import STOP

STEP, MAX_STEP = F(1, 20), F(1, 2)  # the time of a step, in seconds
COLORS = ["#2e86de", "#10ac84", "#ee5253", "#ff9f43", "#5f27cd", "#01a3a4", "#c44569", "#8395a7", "#576574", "#b33939"]
MODE_COLORS = ["#dfe6e9", "#ffeaa7", "#c7ecee", "#fab1a0", "#d6a2e8", "#badc58"]
OWNERS = {"user": ("#2d3436", "you"), "generated": ("#e17055", "the generated script"), "mode": ("#8e44ad", "the mode rules")}
LEFT, RIGHT, TOP = 70, 20, 34
MODE_H, ROW_H, BAR_H = 20, 30, 16


class App:
    def __init__(self, root: tk.Tk, session) -> None:
        self.root, self.s = root, session
        self.paused = True
        self.span = F(24)  # the positions the window shows
        self.jobs: queue.Queue = queue.Queue()
        self.out: queue.Queue = queue.Queue()
        self.snap = None
        self.heading = None  # (wall time, from, to, duration): the step being computed
        self.stopping = False
        self._build()
        self.post(lambda: None)
        if session.initial_refused:
            self.log(f"the initial state is refused: {session.initial_refused}")
        self.log('Press Play to move the chip. Type "help" below for the commands.')
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.worker.start()
        self.root.after(40, self._poll)

    # ------------------------------------------------------------ the window

    def _build(self) -> None:
        r = self.root
        r.title("Line manager")
        r.geometry("1100x640")
        bar = tk.Frame(r)
        bar.pack(fill="x", padx=6, pady=4)
        self.play = tk.Button(bar, text="▶ Play", width=9, command=self.toggle)
        self.play.pack(side="left")
        tk.Button(bar, text="Slower", command=lambda: self.speed(F(1, 2))).pack(side="left", padx=(8, 0))
        tk.Button(bar, text="Faster", command=lambda: self.speed(F(2))).pack(side="left")
        self.speed_label = tk.Label(bar, width=20, anchor="w")
        self.speed_label.pack(side="left", padx=6)
        tk.Button(bar, text="Zoom out", command=lambda: self.zoom(F(2))).pack(side="left", padx=(8, 0))
        tk.Button(bar, text="Zoom in", command=lambda: self.zoom(F(1, 2))).pack(side="left")
        tk.Button(bar, text="Generated script", command=self.open_script).pack(side="left", padx=(16, 0))
        tk.Label(bar, text="Mode:").pack(side="left", padx=(16, 0))
        self.mode_bar = tk.Frame(bar)
        self.mode_bar.pack(side="left")
        self.mode_buttons: dict[str, tk.Button] = {}
        self.button_color = self.play.cget("background")
        self.status = tk.Label(r, anchor="w", justify="left")
        self.status.pack(fill="x", padx=8)
        self.canvas = tk.Canvas(r, background="white", height=320, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=6)
        bottom = tk.Frame(r)
        bottom.pack(fill="both", padx=6, pady=4)
        self.text = tk.Text(bottom, height=9, wrap="word", font=tkfont.nametofont("TkFixedFont"))
        self.text.pack(fill="both", expand=True)
        self.text.configure(state="disabled")
        entry_row = tk.Frame(bottom)
        entry_row.pack(fill="x", pady=(4, 0))
        tk.Label(entry_row, text="Command:").pack(side="left")
        self.entry = tk.Entry(entry_row)
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", self.on_command)
        self.entry.focus_set()
        r.protocol("WM_DELETE_WINDOW", self.close)

    def toggle(self) -> None:
        self.paused = not self.paused
        if self.paused:
            STOP.set()  # the step being computed stops, and is undone
            self.heading = None
        self.play.configure(text="▶ Play" if self.paused else "❚❚ Pause")

    def speed(self, factor: F) -> None:
        def change():
            self.s.speed *= factor
        self.post(change)

    def set_mode(self, m: str) -> None:
        self.post(lambda: self.s.set_mode(m))

    def _mode_buttons(self, names: list[str], current: str) -> None:
        if list(self.mode_buttons) != names:
            for b in self.mode_buttons.values():
                b.destroy()
            self.mode_buttons = {}
            for m in names:
                b = tk.Button(self.mode_bar, text=m, width=3, command=lambda m=m: self.set_mode(m))
                b.pack(side="left")
                self.mode_buttons[m] = b
        for m, b in self.mode_buttons.items():
            b.configure(relief="sunken" if m == current else "raised", background="#ffeaa7" if m == current else self.button_color)

    def zoom(self, factor: F) -> None:
        self.span = min(max(self.span * factor, F(3)), F(768))

    def on_command(self, _event=None) -> None:
        cmd = self.entry.get().strip()
        if not cmd:
            return
        self.entry.delete(0, "end")
        self.log(f"> {cmd}")
        self.post(lambda: self.s.command(cmd))

    def open_script(self) -> None:
        if self.snap is None:
            return
        path = os.path.join(tempfile.gettempdir(), "line_manager_generated_script.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.snap["script"])
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # noqa: S606 (the user's own text editor)
            else:
                subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])
        except OSError as e:
            self.log(f"cannot open {path}: {e}")

    def log(self, msg: str) -> None:
        if not msg:
            return
        self.text.configure(state="normal")
        self.text.insert("end", msg + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def close(self) -> None:
        self.stopping = True
        self.root.destroy()

    # ------------------------------------------------------------ the simulation (its own thread)

    def post(self, job) -> None:
        """Run job() in the simulation's thread; what it returns is shown."""
        self.jobs.put(job)

    def _work(self) -> None:
        last = time.monotonic()
        while not self.stopping:
            began = time.monotonic()
            busy = False
            while True:
                try:
                    job = self.jobs.get_nowait()
                except queue.Empty:
                    break
                busy = True
                STOP.clear()  # a command is not stopped by Pause
                try:
                    self.out.put(job())
                except Exception as e:  # shown, and the simulation goes on
                    self.out.put(f"error: {e}")
            if not self.s.sim.started:  # the initial script: the generator runs as soon as possible
                self.heading = None
                self._publish()
                STOP.clear()
                self.s.begin()
                busy = True
                last = time.monotonic()
            elif self.paused:
                self.heading = None
                last = began
                if not self.s.compiled():  # the mode rules at the chip, compiled ahead of Play
                    self._publish()
                    STOP.clear()
                    self.s.compile()
                    busy = True
            else:
                dt = min(max(F(round((began - last) * 20), 20), STEP), MAX_STEP)
                last = began
                x0 = self.s.position
                self.heading = (began, x0, x0 + self.s.speed * dt, float(dt))
                STOP.clear()
                try:
                    if self.paused:  # paused meanwhile
                        self.heading = None
                    elif not self.s.tick(dt):
                        self.heading = None
                        self.out.put("paused: the step being computed is undone")
                    busy = True
                except Exception as e:
                    self.paused, self.heading = True, None
                    self.out.put(f"the chip stops: {e}")
                    self.out.put(("paused",))
            if busy or self.snap is None:
                self._publish()
            time.sleep(max(0.0, float(STEP) - (time.monotonic() - began)))

    def _publish(self) -> None:
        s = self.s
        reruns, other = s.reruns()
        for msg in s.news():
            self.out.put(msg)
        self.snap = {"t": s.sim.t, "x": s.position, "mode": s.mode(), "speed": s.speed, "line": s.sim.state.line,
                     "claims": s.sim.state.claims, "kinds": s.kinds(), "reruns": reruns, "other": other,
                     "past": s.past_runs(), "modes": s.known_modes(), "script": s.script_text(),
                     "mode_names": list(s.mode_names)}

    # ------------------------------------------------------------ drawing (the window's thread)

    def _poll(self) -> None:
        if self.stopping:
            return
        while True:
            try:
                msg = self.out.get_nowait()
            except queue.Empty:
                break
            if msg == ("paused",):
                self.paused = False
                self.toggle()
            elif isinstance(msg, str):
                self.log(msg)
        if self.snap is not None:
            self.draw(self.snap)
        self.root.after(40, self._poll)

    def draw(self, sn: dict) -> None:
        c = self.canvas
        c.delete("all")
        w = max(c.winfo_width(), 300)
        x, span = sn["x"], self.span
        head = self.heading
        if head is not None and head[1] == x:  # on its way to where the step takes it
            x = x + (head[2] - x) * F(min(1.0, (time.monotonic() - head[0]) / head[3])).limit_denominator(1000)
        left = x - span / 4
        right = left + span
        X = lambda p: LEFT + float((p - left) / span) * (w - LEFT - RIGHT)
        kinds = sn["kinds"]
        bottom = TOP + MODE_H + 8 + ROW_H * len(kinds)
        self._mode_buttons(sn["mode_names"], sn["mode"])
        self.speed_label.configure(text=f"speed: {_num(sn['speed'])} per second")
        when = [f"chip at {_num(p)}" for p in sn["reruns"]] + sn["other"]
        self.status.configure(text=f"time {float(sn['t']):.2f} s    chip at {float(x):.3f}    mode {sn['mode']}    "
                                   f"generator runs again: {' or '.join(when) if when else 'not planned'}")
        # the modes: revealed up to the chip, unknown after
        c.create_text(LEFT - 6, TOP + MODE_H / 2, text="modes", anchor="e")
        names = sorted({m for _, _, m in sn["modes"]})
        for lo, hi, m in sn["modes"]:
            a, b = max(lo, left), min(hi, right)
            if a < b:
                c.create_rectangle(X(a), TOP, X(b), TOP + MODE_H, fill=MODE_COLORS[names.index(m) % len(MODE_COLORS)], outline="#b2bec3")
                if X(b) - X(a) > 14:
                    c.create_text((X(a) + X(b)) / 2, TOP + MODE_H / 2, text=m)
        if x < right:
            c.create_rectangle(X(x), TOP, X(right), TOP + MODE_H, fill="#f5f6fa", outline="#dcdde1", stipple="gray25")
            c.create_text((X(x) + X(right)) / 2, TOP + MODE_H / 2, text="not revealed yet", fill="#7f8fa6")
        # one row per kind: its percentages, and under them who placed it
        line, claims = sn["line"], sn["claims"]
        for i, k in enumerate(kinds):
            y = TOP + MODE_H + 8 + i * ROW_H
            color = COLORS[i % len(COLORS)]
            c.create_text(LEFT - 6, y + BAR_H / 2, text=k, anchor="e", font=("TkDefaultFont", 10, "bold"))
            c.create_line(LEFT, y + BAR_H / 2, w - RIGHT, y + BAR_H / 2, fill="#ecf0f1")
            for v, reg in line.vals.get(k, {}).items():
                fill = _blend(color, float(v) / 100)
                for a, b in _ivs(reg, left, right):
                    _bar(c, X(a), y, X(b), y + BAR_H, fill)
            for (v, owner), reg in claims.pos.get(k, {}).items():
                for a, b in _ivs(reg, left, right):
                    _bar(c, X(a), y + BAR_H + 2, X(b), y + BAR_H + 6, OWNERS.get(owner, ("#636e72", owner))[0])
            for d in (claims.neg.get(k, {}), claims.taken.get(k, {})):
                for owner, reg in d.items():
                    for a, b in _ivs(reg, left, right):
                        c.create_line(X(a), y + BAR_H + 4, max(X(b), X(a) + 2), y + BAR_H + 4, fill="#d63031", width=2, dash=(3, 2))
        # the axis
        step = _tick(span)
        p = (left // step + 1) * step
        while p < right:
            c.create_line(X(p), bottom, X(p), bottom + 5, fill="#636e72")
            c.create_text(X(p), bottom + 14, text=_num(p), fill="#636e72")
            p += step
        c.create_line(LEFT, bottom, w - RIGHT, bottom, fill="#636e72")
        # where the generator ran, and where it runs again
        for p in sn["past"]:
            if left <= p <= right:
                c.create_polygon(X(p) - 5, bottom + 24, X(p) + 5, bottom + 24, X(p), bottom + 17, fill="#0984e3")
        for p in sn["reruns"]:
            if left <= p <= right:
                c.create_line(X(p), TOP - 4, X(p), bottom, fill="#0984e3", dash=(6, 4), width=2)
                c.create_text(X(p) + 4, TOP - 12, text="generator runs again", anchor="w", fill="#0984e3")
            elif p > right:
                c.create_text(w - RIGHT, TOP - 12, text=f"generator runs again at {_num(p)} →", anchor="e", fill="#0984e3")
        # the chip
        c.create_line(X(x), TOP - 2, X(x), bottom, fill="#d63031", width=2)
        c.create_polygon(X(x) - 7, TOP - 16, X(x) + 7, TOP - 16, X(x), TOP - 4, fill="#d63031")
        # the legend
        y = bottom + 40
        c.create_text(LEFT, y, anchor="w", fill="#2d3436",
                      text="bar: lighter = lower percentage   under the bar, who placed it:")
        xl = LEFT + 390
        for color, who in OWNERS.values():
            c.create_rectangle(xl, y - 3, xl + 18, y + 3, fill=color, outline="")
            c.create_text(xl + 22, y, anchor="w", text=who)
            xl += 30 + 7 * len(who)
        c.create_line(xl, y, xl + 18, y, fill="#d63031", width=2, dash=(3, 2))
        c.create_text(xl + 22, y, anchor="w", text='"no K" or removed')
        c.create_polygon(LEFT, y + 22, LEFT + 10, y + 22, LEFT + 5, y + 15, fill="#0984e3")
        c.create_text(LEFT + 14, y + 18, anchor="w", text="the generator ran here      dashed blue: where it runs again      red: the chip")


def _ivs(reg, a, b):
    """The (lo, hi) real parts of the intervals of reg within [a - 1, b + 1)."""
    return [(iv.lo.x, iv.hi.x) for iv in reg.on(a - 1, b + 1).intervals]


def _bar(c, x1, y1, x2, y2, fill) -> None:
    if x2 - x1 < 2:  # a point, or almost
        x1, x2 = x1 - 1, x1 + 1
    c.create_rectangle(x1, y1, x2, y2, fill=fill, outline="")


def _blend(color: str, amount: float) -> str:
    """The color at this strength (1: itself, toward white below)."""
    k = 0.25 + 0.75 * amount
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(255 - (255 - v) * k) for v in (r, g, b))


def _tick(span: F) -> F:
    """A round distance between the axis labels: about 12 of them."""
    for t in (F(1, 4), F(1, 2), F(1), F(2), F(5), F(10), F(20), F(50), F(100)):
        if span / t <= 14:
            return t
    return F(200)


def _num(x) -> str:
    x = F(x)
    return str(x) if x.denominator <= 8 else f"{float(x):.2f}"


def run(session) -> None:
    # Each Tk call releases the GIL and waits up to the switch interval to get it back
    # from the simulation's thread: a few hundred per drawing took ~1 s at the 5 ms default.
    sys.setswitchinterval(0.0005)
    root = tk.Tk()
    App(root, session)
    root.mainloop()
