"""The window of the line manager (tkinter, in the standard library).

The line around the chip, one row per kind, with the revealed modes above it. The chip
moves smoothly at its speed (over the line, the mouse wheel scrolls and Ctrl + wheel zooms), and each frame shows the line at the chip's position of
that frame, from the formula of the script (Simulation.ahead): the pixels are the only
rounding; where the formula is not known yet, the chip waits for the step. Pause stops it, Slower and Faster halve and double its
speed, and the mode buttons (or the session's selectors, on a row of their own) change the mode from the chip on. Dashed blue lines show where the generator runs again (its script's rerun
triggers), blue ticks where it ran. "Generated script" opens the current script in a
text file (saved with changes, it replaces the script in place, or the window says why
it is not correctly written): at first only "run the generator once, as soon as possible", which the
first step asks for, paused or not. A generator run goes on in a process of its own
(Session.runs), paused or not, while the chip moves with the script in place; the
status line says when one is going on. Rules and edits are typed in the command box
(type help); while the chip is paused, nothing that changes the line, the mode or the
rules is accepted (the mode buttons and the selectors are disabled).

The script's S_f goes as script.py says: each frame executes s_f (`draw`), which s_v makes
(`_rebuild`) when the script, the view or what it shows changes; the simulation's thread is
the exact loop (`_step`), and any other loop of the script has a thread of its own
(`_looper`). `lock` lets only one of them change the simulation at a time.

The simulation runs in a thread of its own, so the window never waits for it. Each
step lets as much time run as has passed since the previous one, so whatever a
calculation takes, the line ends up as if it had taken no time. A frame takes the chip
from the end of the last step by the time passed since, and never stops for a
calculation: where the formula stops, or while an edit computes, the line goes on as it
was shown, and takes its place when the step is done. A click counts from where the chip
was when it was made (`_up_to`). The time is the clock's, so a program put to sleep finds
the chip where it would have gone. Pause stops
a step being computed (solver.STOP): it is undone, and computed again after Play.
The GIL's switch interval is shortened (`run`) so that the drawing is not starved by
the simulation's thread. `run` also writes what happens and the state in logs/
(session_log.py). What is drawn stays on the canvas from one frame to the next (view.py). Opening a window closes the one open (single_window.py).
"""

from __future__ import annotations

import os
import shutil
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from fractions import Fraction as F
from tkinter import font as tkfont, messagebox

from app import EDITS, PAUSED, ScriptError, parse_op
from intervals import Pos
from script import STEP
from script_file import PROJECT, ScriptFile
from session_log import SessionLog
from single_window import listen, take_over
from solver import STOP, Interrupted
from view import CAPTION, RIGHT, TOPMOST, Retained, fixed_items, items, num, tick  # noqa: F401 (RIGHT, tick: also for the tests)

CONTROL = 0x4  # in the state of an event
MIN_SPAN, MAX_SPAN = F(3), F(3 * 2 ** 17)  # the positions the window can show
MIN_LEFT = 70  # (the captions on the left may need more: `App._edge`)
_tick = tick


def open_in_editor(path: str) -> None:
    """The file in VS Code if it is there (its `code` command), with the project's folder,
    whose settings let it find what the file imports; else the system's editor (on Windows
    "edit", not "open": that would run a .py file; else Notepad)."""
    code = shutil.which("code")
    if code is not None:
        flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
        subprocess.Popen([code, PROJECT, path], creationflags=flags)
    elif sys.platform.startswith("win"):
        try:
            os.startfile(path, "edit")  # noqa: S606 (the user's own editor)
        except OSError:
            subprocess.Popen(["notepad.exe", path])
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])


def keeps_frame(script, shown: int) -> bool:
    """Whether s_f stays as the user wrote it: until s_v has made it again (ShowLine)."""
    return script.frame_given is not None and shown <= script.frame_given


class App:
    def __init__(self, root: tk.Tk, session, file: SessionLog | None = None) -> None:
        self.root, self.s, self.file = root, session, file
        session.calculate_apart()
        self.paused = True
        self.span = F(24)  # the positions the window shows
        self.scrolled = F(0)  # how far right of its place around the chip
        self.jobs: queue.Queue = queue.Queue()
        self.out: queue.Queue = queue.Queue()
        self.snap = None
        self.shown = None  # (chip, line, claims): what the last frame showed
        self.commands = 0  # the commands carried out: the line they change is shown at once
        self.edge = (None, MIN_LEFT)  # (kinds, where the line starts for them)
        self.wall = None  # when the chip was where the last step left it (None: it is not moving)
        self.frame = None  # (kinds, edge, width): what the fixed items were drawn for
        self.sign = None  # `_signature` of the list on the canvas
        self.valid = F(0)  # that list holds with the chip before this position
        self.formula = None  # (ahead, where it stops): what gives the line on it
        self.when = []  # when the generator runs again, as text
        self.status_text = None
        self.stopping = False
        self.replaced = False  # a new window asked this one to close (single_window.py)
        self._build()
        self.post(lambda: None)
        if session.initial_refused:
            self.log(f"the initial state is refused: {session.initial_refused}")
        self.log('Press Play to move the chip. Type "help" below for the commands.')
        self.script_file = ScriptFile()  # the generated script, one file from start to end (script_file.py)
        self.next_watch = 0.0
        self.lock = threading.Lock()
        self.loopers: set = set()  # the periods of the loops that have a thread
        self.worker = threading.Thread(target=self._work, daemon=True)
        self.worker.start()
        self.root.after(16, self._poll)

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
        tk.Button(bar, text="Generated script", command=self.open_script).pack(side="left", padx=(16, 0))
        self.generator_button = tk.Button(bar, text="Generator: on", width=13, command=self.switch_generator)
        self.generator_button.pack(side="left", padx=(8, 0))
        self.mode_buttons: dict[str, tk.Button] = {}
        self.selector_buttons: dict[tuple[int, str], tk.Button] = {}  # (selector, option)
        self.button_color = self.play.cget("background")
        if self.s.selectors:
            row = tk.Frame(r)
            row.pack(fill="x", padx=6, pady=(0, 4))
            for i, (name, options) in enumerate(self.s.selectors):
                tk.Label(row, text=f"{name.capitalize()}:").pack(side="left", padx=(16 if i else 0, 2))
                for option in options:
                    b = tk.Button(row, text=option, command=lambda i=i, option=option: self.select(i, option))
                    b.pack(side="left")
                    self.selector_buttons[i, option] = b
        else:
            tk.Label(bar, text="Mode:").pack(side="left", padx=(16, 0))
            self.mode_bar = tk.Frame(bar)
            self.mode_bar.pack(side="left")
        self.status = tk.Label(r, anchor="w", justify="left")
        self.status.pack(fill="x", padx=8)
        self.canvas = tk.Canvas(r, background="white", height=320, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=6)
        self.view = Retained(self.canvas)
        for wheel in ("<MouseWheel>", "<Button-4>", "<Button-5>"):  # (Button: the wheel on Linux)
            r.bind(wheel, self.on_wheel)  # on the window: Tk may send it to the command box, which has the focus
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
            self.wall = None
        self.play.configure(text="▶ Play" if self.paused else "❚❚ Pause")
        self.journal("pause" if self.paused else "play")

    def switch_generator(self) -> None:
        """The Generator button: no run of the rules generator while it says off."""
        off = self.generator_button.cget("text").endswith("on")
        self.generator_button.configure(text="Generator: off" if off else "Generator: on")
        self.post(lambda: self.s.switch_generator("off" if off else "on"))
        self.journal("generator off" if off else "generator on")

    def speed(self, factor: F) -> None:
        def change():
            self.s.speed *= factor
        self.post(change)

    def set_mode(self, m: str) -> None:
        if self.paused:
            self.log(PAUSED)
            return
        at = time.time()
        self.post(lambda: PAUSED if self.paused else self._up_to(at) or self.s.set_mode(m))

    def select(self, i: int, option: str) -> None:
        if self.paused:
            self.log(PAUSED)
            return
        at = time.time()
        self.post(lambda: PAUSED if self.paused else self._up_to(at) or self.s.select(i, option))

    def _show_choice(self, b: tk.Button, chosen: bool) -> None:
        """A button of the mode, disabled while the chip is paused (no edit then)."""
        b.configure(relief="sunken" if chosen else "raised", background="#ffeaa7" if chosen else self.button_color,
                    state="disabled" if self.paused else "normal")

    def _mode_buttons(self, names: list[str], current: str, selection) -> None:
        if self.selector_buttons:
            for (i, option), b in self.selector_buttons.items():
                self._show_choice(b, selection[i] == option)
            return
        if list(self.mode_buttons) != names:
            for b in self.mode_buttons.values():
                b.destroy()
            self.mode_buttons = {}
            for m in names:
                b = tk.Button(self.mode_bar, text=m, width=3, command=lambda m=m: self.set_mode(m))
                b.pack(side="left")
                self.mode_buttons[m] = b
        for m, b in self.mode_buttons.items():
            self._show_choice(b, m == current)

    def _share(self, x: int) -> F:
        """Where the canvas's x is on the line's width: 0 at its left end, 1 at its right end
        (the line is drawn from `left` = chip + scrolled - span / 4, over span: _rebuild)."""
        w = max(self.canvas.winfo_width(), 300)
        edge = self.edge[1]  # (as drawn last)
        return F(x - edge, w - edge - RIGHT)

    def at_x(self, x: int) -> F:
        """The position of the line at the canvas's x, the chip where it was drawn last."""
        return self.view.p0 + F(self.view.moved).limit_denominator(1000) + self.scrolled - self.span / 4 + self._share(x) * self.span

    def zoom(self, factor: F, x: int | None = None) -> None:
        """The span times factor; the position of the line at the canvas's x (the mouse's)
        stays at x: chip + scrolled - span / 4 + share * span is the same before and after."""
        span = min(max(self.span * factor, MIN_SPAN), MAX_SPAN)
        if x is not None:
            self.scrolled += (self.span - span) * (self._share(x) - F(1, 4))
        self.span = span

    def _pointer_x(self) -> int:
        """The mouse's x on the canvas."""
        return self.root.winfo_pointerx() - self.canvas.winfo_rootx()

    def on_wheel(self, event) -> None:
        """Over the line, the wheel scrolls (up: left, down: right), and with Ctrl it zooms
        (up: in, down: out) around the mouse: the position under it stays under it. The view
        goes on following the chip."""
        if self.root.winfo_containing(*self.root.winfo_pointerxy()) == self.canvas:
            up = event.delta > 0 or event.num == 4
            if event.state & CONTROL:
                self.zoom(F(1, 2) if up else F(2), self._pointer_x())
            else:
                self.scrolled += -self.span / 8 if up else self.span / 8

    def on_command(self, _event=None) -> None:
        cmd = self.entry.get().strip()
        if not cmd:
            return
        self.entry.delete(0, "end")
        self.log(f"> {cmd}")
        if cmd.split()[0] in EDITS:  # no edit while the chip is paused (checked again when it is made)
            if self.paused:
                self.log(PAUSED)
                return
            at = time.time()
            self.post(lambda: PAUSED if self.paused else self._up_to(at) or self.s.command(cmd))
            out = self.out_of_view(cmd)
            if out:
                self.log(out)
        else:
            self.post(lambda: self.s.command(cmd))

    def out_of_view(self, cmd: str) -> str | None:
        """For an edit (add, no, remove) none of whose positions the window shows: where
        they are, and how to see them. None otherwise (in view, or not an edit of the line)."""
        try:
            ops = [op for part in cmd.split(";") if part.strip() for op in parse_op(part)]
        except ValueError:
            return None  # (the session says what is wrong)
        if not ops:
            return None
        w = max(self.canvas.winfo_width(), 300)
        lo, hi = self.at_x(self.edge[1]), self.at_x(w - RIGHT)
        if any(op.region.on(lo, hi).intervals for op in ops):
            return None
        right = all(op.region.inf() >= hi for op in ops)
        zoom = "zoom out with Ctrl + wheel over the line"
        where, how = ("on its right", f"scroll down with the wheel or {zoom}") if right else \
            ("on its left", f"scroll up with the wheel or {zoom}") if all(op.region.sup() <= lo for op in ops) else ("on both sides", zoom)
        return f"(not in view: the window shows [{num(lo)}, {num(hi)}], this edit is {where}: {how})"

    def open_script(self) -> None:
        """The generated script's file (script_file.py), in the editor: the one file of the
        session, which the program writes again each time the script in place is another."""
        self._write_script()
        self.log(f"generated script: {self.script_file.path}")
        try:
            open_in_editor(self.script_file.path)
        except OSError as e:
            self.log(f"cannot open {self.script_file.path}: {e}")

    def _write_script(self) -> None:
        """The file holds the script in place (written again if it is another one)."""
        if self.snap is not None and self.script_file.write(self.snap["script_object"], self.snap["script"]):
            self.journal(f"generated script written to {self.script_file.path}")

    def _watch(self) -> None:
        """The script's file: written again if the script in place is another one; saved by the
        user with other text, that text goes to the simulation's thread (`_edited`), which
        replaces the script or says why it does not."""
        self._write_script()
        text = self.script_file.saved()
        if text is not None:
            self.post(lambda text=text, script=self.script_file.script: self._edited(text, script))

    def _edited(self, text: str, script):
        try:
            return self.s.edit_script(text, script)
        except ScriptError as e:
            return ("script error", f"The generated script is not correctly written, it stays as it is:\n{e}")

    def show_script_error(self, msg: str) -> None:
        self.log(msg)
        messagebox.showerror("Generated script", msg)

    def journal(self, msg: str) -> None:
        """Written in logs/ with where the chip is."""
        if self.file is not None:
            sn = self.snap
            self.file.event(f"[chip {float(sn['x']):.3f}, mode {sn['mode']}] {msg}" if sn else msg)

    def log(self, msg: str) -> None:
        if not msg:
            return
        self.journal(msg)
        self.text.configure(state="normal")
        self.text.insert("end", msg + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def close(self) -> None:
        self.journal("window closed")
        self.stopping = True
        self.s.close()
        self.s.calculator.close()
        self.root.destroy()

    # ------------------------------------------------------------ the simulation (its own thread)

    def post(self, job) -> None:
        """Run job() in the simulation's thread; what it returns is shown."""
        self.jobs.put(job)

    def _up_to(self, at: float) -> None:
        """The simulation goes up to this time, for what happened then to happen there."""
        dt = F(round((at - self.last) * 1000), 1000)
        if dt > 0 and self.wall is not None:
            self.last = self.last + float(dt)
            if self.s.tick(dt):
                self.wall = self.last

    def _work(self) -> None:
        self.last = time.time()
        while not self.stopping:
            began = time.time()
            try:
                with self.lock:
                    self._step(began)
            except Interrupted:
                pass  # Pause, in something that is read again at the next step
            except Exception as e:  # this thread goes on, or Play would do nothing from now on
                self.paused, self.wall = True, None
                self.out.put(f"the chip stops: {e}")
                self.out.put(("paused",))
            time.sleep(max(0.0, float(STEP) - (time.time() - began)))

    def _looper(self, period: F) -> None:
        """The loops of this period of the script in place, in a thread of its own."""
        while not self.stopping:
            time.sleep(float(period))
            with self.lock:
                sim = self.s.sim
                snap = sim.snapshot()
                try:
                    sim.iterate(period)
                except Interrupted:
                    sim.restore(snap)
                except Exception as e:  # shown, and the loop goes on
                    self.out.put(f"error: {e}")

    def _loopers(self) -> None:
        for loop in self.s.sim.script.loops:
            if loop.period != STEP and loop.period not in self.loopers:
                self.loopers.add(loop.period)
                threading.Thread(target=self._looper, args=(loop.period,), daemon=True).start()

    def _step(self, began: float) -> None:
        busy = False
        while True:
            try:
                job = self.jobs.get_nowait()
            except queue.Empty:
                break
            busy = True
            self.commands += 1
            STOP.clear()  # a command is not stopped by Pause
            try:
                self.out.put(job())
            except Exception as e:  # shown, and the simulation goes on
                self.out.put(f"error: {e}")
        if busy:  # what the commands did, shown at once: the step below can take seconds
            self._publish()
        n = self.s.sim.log.run_count
        STOP.clear()  # nor is a run: it goes on paused or not
        for msg in self.s.runs():  # start the run asked for, install the completed ones
            self.out.put(msg)
        busy |= self.s.sim.log.run_count != n
        if not self.s.sim.started:  # the initial script: the generator runs as soon as possible
            self.wall = None
            self._publish()
            STOP.clear()
            self.s.begin()
            busy = True
            self.last = time.time()
        elif self.paused:
            self.wall = None
            busy = True
            self.last = began
        else:
            dt = max(F(round((began - self.last) * 1000), 1000), F(1, 1000))
            self.last = began
            STOP.clear()
            if self.paused:  # paused meanwhile
                self.wall = None
            elif not self.s.tick(dt):
                self.wall = None
                self.out.put("paused: the step being computed is undone")
            else:
                self.wall = began
            busy = True
        if busy or self.snap is None:
            self._publish()

    def _publish(self) -> None:
        s = self.s
        self._loopers()
        reruns, other = s.reruns()
        for msg in s.news():
            self.out.put(msg)
        self.snap = {"t": s.sim.t, "x": s.position, "mode": s.mode(), "speed": s.speed, "line": s.sim.state.line,
                     "claims": s.sim.state.claims, "kinds": s.kinds(), "reruns": reruns, "other": other,
                     "past": s.past_runs(), "modes": s.known_modes(), "script": s.script_text(), "script_object": s.sim.script,
                     "mode_names": list(s.mode_names), "running": s.running() is not None, "generator_off": s.sim.generator_off, "ahead": s.sim.ahead(), "wall": self.wall, "commands": self.commands, "shown": s.sim.shown,
                     "selection": s.selection() if s.selectors else None}
        if self.file is not None and self.file.state_due():
            self.file.state(s.state_text())

    # ------------------------------------------------------------ drawing (the window's thread)

    def _poll(self) -> None:
        if self.stopping:
            return
        if self.replaced:
            self.close()
            return
        if time.time() >= self.next_watch:
            self.next_watch = time.time() + 0.5
            self._watch()
        while True:
            try:
                msg = self.out.get_nowait()
            except queue.Empty:
                break
            if msg == ("paused",):
                self.paused = False
                self.toggle()
            elif isinstance(msg, tuple):
                self.show_script_error(msg[1])
            elif isinstance(msg, str):
                self.log(msg)
        if self.snap is not None:
            self.draw(self.snap)
        self.root.after(16, self._poll)

    def _edge(self, kinds: list[str]) -> int:
        """Where the line starts: after the longest caption (measured once per list of kinds)."""
        if self.edge[0] != kinds:
            font = tkfont.Font(root=self.root, font=CAPTION)
            self.edge = (list(kinds), max([MIN_LEFT] + [font.measure(k) + 14 for k in kinds]))
        return self.edge[1]

    def _line(self, sn: dict, p):
        """The line and the claims at p: from the formula, else as they were shown, up to
        where the formula that showed them stops, for the step."""
        line, claims = sn["line"], sn["claims"]
        if sn["wall"] is None or p <= sn["x"] or sn["ahead"] is None:
            return line, claims
        try:
            state = sn["ahead"](Pos(p))  # (the chip has gone through its position too)
        except Exception:  # (a step going on can stop what it reads)
            state = None
        if state is not None:
            return state.line, state.claims
        shown = self.shown
        if shown is not None and shown[0] >= sn["x"]:
            if self.formula is not None:
                ahead, until = self.formula
                try:
                    state = ahead(until)
                except Exception:
                    state = None
                if state is not None:
                    return state.line, state.claims
            return shown[1:]
        return line, claims

    def _signature(self, sn: dict, w: int) -> tuple:
        """What the list of what is shown is made from, but for the chip: the line, unless a
        formula gives it."""
        ahead = sn["ahead"]
        key = getattr(ahead, "key", None)
        line = (key,) if key is not None and sn["wall"] is not None else (id(sn["line"]), id(sn["claims"]))
        return (sn["commands"], id(sn["script_object"]), sn["shown"], line, self.span, self.scrolled, w, self.paused, sn["mode"], sn["speed"], tuple(sn["reruns"]), tuple(sn["other"]),
                len(sn["past"]), sn["past"][-1:], len(sn["modes"]), tuple(sn["kinds"]), tuple(sn["mode_names"]), sn["selection"], sn["running"])

    def _rebuild(self, sn: dict, p, w: int) -> None:
        line, claims = self._line(sn, p)
        self.shown = None if sn["wall"] is None else (p, line, claims)
        kinds, span = sn["kinds"], self.span
        edge = self._edge(kinds)
        if self.frame != (tuple(kinds), edge, w):
            self.frame = (tuple(kinds), edge, w)
            fixed_items(self.canvas, kinds, edge, w, self.canvas.winfo_height())
            self.canvas.tag_raise(TOPMOST)
        left = p + self.scrolled - span / 4
        scale = (w - edge - RIGHT) / float(span)
        a, b = left - span / 2, left + span + span / 2
        X = lambda at: edge + float(at - left) * scale
        script = sn["script_object"]
        shows = script.shows_line()  # s_v
        now = items(sn, line, claims, p, left, span, a, b, scale, w) if shows else {}
        ahead = sn["ahead"]
        until = ahead.until(Pos(p)) if sn["wall"] is not None and ahead is not None and hasattr(ahead, "until") else None
        self.formula, later, delta = None, None, None
        self.valid = p + span / 2  # where what is listed stops
        if until is not None and until.x > p:
            delta = min(span / 8, until.x - p)
            state = None
            try:
                state = ahead(Pos(p + delta))
            except Exception:
                pass
            if state is not None:
                self.formula = (ahead, until)
                self.valid = min(self.valid, until.x)
                left2 = p + delta + self.scrolled - span / 4
                later = items(sn, state.line, state.claims, p + delta, left2, span, a, b, scale, w) if shows else None
        self.view.sync(now, later, delta, p, X, scale)
        if not keeps_frame(script, sn["shown"]):
            script.frame = self.view.frame()
        if self.formula is None and sn["wall"] is not None:
            self.valid = p  # no formula: each frame reads the line
        self._mode_buttons(sn["mode_names"], sn["mode"], sn["selection"])
        self.speed_label.configure(text=f"speed: {num(sn['speed'])} per second")
        self.when = [f"chip at {num(r)}" for r in sn["reruns"]] + sn["other"]

    def draw(self, sn: dict) -> None:
        """A frame: the list of what is shown only if it can have changed (view.py)."""
        w = max(self.canvas.winfo_width(), 300)
        x = sn["x"]
        p = x
        if sn["wall"] is None:
            self.shown = None
        else:  # one position for the chip and the line, the time passed since the step
            p = x + sn["speed"] * F(time.time() - sn["wall"]).limit_denominator(1000)
            if self.shown is not None and p < self.shown[0]:
                p = self.shown[0]
        if self.shown is not None and self.sign is not None and self.sign[0] != sn["commands"]:
            self.shown = None  # (the line the last frame showed is from before the commands)
        sign = self._signature(sn, w)
        if sign != self.sign or p >= self.valid or self.frame is None:
            self.sign = sign
            self._rebuild(sn, p, w)
        else:
            self.view.move(p, sn["script_object"].frame)  # s_f
            if self.shown is not None:
                self.shown = (p, *self.shown[1:])
        label = "Generator: off" if sn.get("generator_off") else "Generator: on"  # (the command too)
        if self.generator_button.cget("text") != label:
            self.generator_button.configure(text=label)
        t = sn["t"] + (p - x) / sn["speed"]
        text = (f"time {float(t):.2f} s    chip at {float(p):.3f}    mode {sn['mode']}    "
                f"generator runs again: {' or '.join(self.when) if self.when else 'not planned'}"
                + ("    (a generator run is going on)" if sn["running"] else ""))
        if text != self.status_text:
            self.status_text = text
            self.status.configure(text=text)


def run(session) -> None:
    # Each Tk call releases the GIL and waits up to the switch interval to get it back
    # from the simulation's thread: a few hundred per drawing took ~1 s at the 5 ms default.
    sys.setswitchinterval(0.0005)
    server = take_over()  # one window at a time, whatever example it shows
    root = tk.Tk()
    file = SessionLog()
    threading.excepthook = lambda a: file.event("".join(traceback.format_exception(a.exc_type, a.exc_value, a.exc_traceback)))
    root.report_callback_exception = lambda *e: file.event("".join(traceback.format_exception(*e)))
    app = App(root, session, file)
    listen(server, lambda: setattr(app, "replaced", True))
    root.mainloop()
