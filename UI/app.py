"""Line manager: define the rules, edit the line, watch the chip move.

    python UI/app.py                   an empty session
    python UI/examples/<example>.py    a session defined by a script (copy one to start yours)

An example script defines the rules, the modes, the chip's start and speed, the
initial state of the line and the generator's problems, then calls start(...), which
opens the window (gui.py).

Everything is exact (fractions). The user's edits are kept, in order: the line is
computed from all of them (resolve.py), so changing the rules recomputes it, and
"undo" gives the line as if the last edit had never been made. The initial state of
the line is the user's first edit. The generated script is at first only an
instruction to run the generator once, as soon as possible: the window's first step
asks for it (`Session.begin`), paused or not. Each run of the generator goes on in a
process of its own (scripts/generator_worker.py) while the chip moves with the script
in place (`Session.runs`): its script replaces that one when it completes, and a newer
call cancels it (the process is ended). As the chip moves, the modes are revealed,
the generator runs and its script acts, the mode rules included (simulation.py).

This file is the session the window drives: the definitions, the commands typed in
the window (`Session.command`), and the chip's motion (`Session.tick`).
"""

from __future__ import annotations

import atexit
import os
import pickle
import queue
import re
import subprocess
import sys
import threading
from fractions import Fraction as F

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from constraints import FifteenMinuteBreaks, KeepOn  # noqa: E402,F401 (FifteenMinuteBreaks: for the example scripts)
from generator import RulesGenerator  # noqa: E402
from intervals import INF, Interval, IntervalSet  # noqa: E402
from line import LineState, Op  # noqa: E402
from modes import ModeTimeline  # noqa: E402
from problems import MinimumLengthProblem, PresenceRatioProblem  # noqa: E402,F401 (for the example scripts)
from region import Region  # noqa: E402
from rules import RuleError, is_hidden, parse_rules  # noqa: E402
from script import ChipIn, RerunAction, Script, TransitionCount  # noqa: E402
from script_text import ScriptError, parse_script  # noqa: E402,F401 (ScriptError: for the window)
from simulation import Chip, Simulation  # noqa: E402
from solver import Interrupted  # noqa: E402

WORKER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "generator_worker.py")

HELP = """\
Rules
  rule <rule>              add a rule, e.g.  rule A & B → C 45% 3 units on the left of p
                           ("->" works for "→"; a mode rule starts with "mode 1 →")
  rules                    list the rules
  unrule <n>               remove rule n
Edits of the user (several in one edit: separate them with ";")
  add <K> [p%] <where>     put K, e.g.  add A on [1, 3)   add A 50% at 2
  no <K> <where>           put a "no K" element
  remove <K> <where>       remove K (K is then absent there)
  remove no <K> <where>    remove the "no K" elements
  <where> is "at x", "on <interval>", "on <interval> U <interval> ...", or
  "everywhere"; an interval is [a, b], (a, b), [a, b) or (a, b], with fractions,
  decimals, -inf and inf, e.g.  on [0, 1/2) U (3, inf)
  undo                     cancel the last edit, as if it had never been made
  clear                    cancel every edit (the rules stay)
The modes (given externally: by MODES in the script, or by you)
  mode <name>              the mode is <name> from the chip on (the mode buttons do it too),
                           until the next transition MODES plans
Information
  line                     the line, exactly
  claims                   what applies of the edits now, and who made it
  history                  the user's edits, oldest first
  status                   the time, the chip, its mode and speed, the problems
  help"""

KIND = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
# the commands that change the line, the mode or the rules: refused while the chip is paused
EDITS = {"add", "no", "remove", "undo", "clear", "mode", "rule", "unrule"}
PAUSED = "the chip is paused: press Play to edit"
RESERVED = {"no", "at", "on", "everywhere", "mode"}


class Session:
    def __init__(self, rules=(), modes=((0, "1"),), chip=0, speed=1, line=(), problems=(), q=0.5, constraints=(),
                 ) -> None:
        self.texts = list(rules)
        # constraints.py's, or text: an edit written from the chip, kept ("Future on (0, inf)")
        self.constraints = [KeepOn(op) for k in constraints if isinstance(k, str) for op in parse_op(f"add {k}")] + \
                           [k for k in constraints if not isinstance(k, str)]
        self.rs = parse_rules(self.texts)
        self.problems = [(p, F(w)) for p, w in problems]
        self.q = q
        self.speed = F(speed)
        if self.speed <= 0:
            raise ValueError("the chip's speed is > 0")
        x0 = F(chip)
        modes = sorted((F(x), m) for x, m in modes)
        if not modes or modes[0][0] > x0:
            raise ValueError(f"MODES must give the mode at the chip's start {x0}: the first position is at or before it")
        initial = [m for x, m in modes if x <= x0][-1]
        transitions, prev = [], initial
        for x, m in modes:
            if x > x0 and m != prev:
                transitions.append((x, m))
                prev = m
        self.mode_names = sorted({m for _, m in modes} | {r.mode for r in self.rs.mode_rules})
        self.chip = Chip([(0, x0)])
        self.modes = ModeTimeline(x0, initial, transitions)
        self.planned = (initial, list(self.modes.transitions))  # by MODES
        self.changes: dict[F, str] = {}  # by the user: the mode from each position on
        self.sim = Simulation(LineState.empty(), self.generator(self.rs), self.chip, self.modes)
        self.sim.background = True
        self._running = None  # (request id, process): the generator run going on
        self.script_edit = None  # (time, script): the last script the user changed
        atexit.register(self.close)
        self._done: queue.Queue = queue.Queue()  # (request id, script, stats or the error) of completed runs
        self.edits: list[str] = []  # the user's edits, as typed, oldest first
        self.seen = (0, 0)  # refusals and generator runs already reported
        ops = [op for text in line for op in parse_op(text)]
        self.initial_refused = self.sim.edit_now(ops) if ops else None  # the generator runs at `begin`
        if ops and not self.initial_refused:
            self.edits.append("; ".join(line))

    def generator(self, rs) -> RulesGenerator:
        return RulesGenerator(rs, self.problems, self.q, constraints=self.constraints)

    # ------------------------------------------------------------ the chip

    @property
    def position(self) -> F:
        return self.chip.position(self.sim.t)

    def mode(self) -> str:
        return self.modes.mode_at(self.position)

    def begin(self) -> bool:
        """The program starts: the initial script runs the generator. If solver.STOP is
        set meanwhile (Pause), it is undone and False is returned."""
        if self.sim.started:
            return True
        snap = self.sim.snapshot()
        try:
            self.sim.start()
        except Interrupted:
            self.sim.restore(snap)
            return False
        return True

    def runs(self) -> list[str]:
        """Start the generator run the simulation asks for, cancelling the one going on,
        and install the runs that completed (in the simulation's thread). What to show."""
        out = []
        while True:
            try:
                rid, script, stats = self._done.get_nowait()
            except queue.Empty:
                break
            if self._running is not None and self._running[0] == rid:
                self._running = None
            if isinstance(script, Exception):
                out.append(f"the generator run failed: {script}")
            else:
                self.sim.complete(rid, script, stats)
        req = self.sim.request
        if req is not None and (self._running is None or self._running[0] != req.id):
            self.close()  # a newer run was asked for: the one going on is cancelled
            flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
            proc = subprocess.Popen([sys.executable, WORKER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, creationflags=flags)
            self._running = (req.id, proc)
            threading.Thread(target=self._run, args=(req, proc), daemon=True).start()
        return out

    def _run(self, req, proc: subprocess.Popen) -> None:
        """Wait for the run's process (in a thread of its own) and hand its result over."""
        try:
            out, err = proc.communicate(pickle.dumps((req.gen, req.state, req.chip, req.known)))
        except (OSError, ValueError):
            return  # ended meanwhile
        if proc.returncode == 0:
            self._done.put((req.id, *pickle.loads(out)))
        elif self._running is not None and self._running[1] is proc:  # not cancelled: it failed
            lines = err.decode(errors="replace").strip().splitlines()
            self._done.put((req.id, RuntimeError(lines[-1] if lines else f"exit code {proc.returncode}"), None))

    def close(self) -> None:
        """End the generator run going on, if any."""
        if self._running is not None:
            proc = self._running[1]
            self._running = None
            if proc.poll() is None:
                proc.kill()

    def running(self) -> str | None:
        """The generator run going on, as text, or None."""
        req = self.sim.request
        return None if req is None else f"a generator run is going on, from chip {req.chip} ({req.why}, asked at time {req.time})"

    def tick(self, dt: F) -> bool:
        """Let the time run for dt: the chip moves dt * speed. If solver.STOP is set
        meanwhile (Pause), the step is undone and False is returned."""
        snap = self.sim.snapshot()
        t0 = self.sim.t
        self.chip.extend(t0 + dt, self.position + self.speed * dt)
        try:
            self.sim.advance(t0 + dt)
        except Interrupted:
            self.sim.restore(snap)
            return False
        return True

    def reruns(self) -> tuple[list[F], list[str]]:
        """When the generator runs again: the positions the chip will reach, and the
        other conditions, as text."""
        at, other = [], []
        for trig in self.sim.script.rerun_triggers():
            if isinstance(trig, ChipIn):
                at.append(F(trig.lo))
            elif isinstance(trig, TransitionCount):
                left = trig.k - self.sim._since_install
                other.append(f"after {left} more mode transition{'s' if left != 1 else ''}")
        return at, other

    def past_runs(self) -> list[F]:
        return [c for _, c, _, _, _ in self.sim.log.runs]

    def known_modes(self) -> list[tuple[F, F, str]]:
        return self.modes.known(self.position).segments()

    def kinds(self) -> list[str]:
        """The kinds to show: those of the rules, then the others on the line."""
        out = [k for k in self.rs.order if not is_hidden(k)]
        return out + [k for k in self.sim.state.line.kinds() if k not in out]

    def script_text(self) -> str:
        runs = self.sim.log.runs
        head = [f"Generated script at time {self.sim.t}, chip at {self.position} (mode {self.mode()})."]
        if runs:
            t, c, why, _, stats = runs[-1]
            head.append(f"Last generator run: from chip {c} ({why}), completed at time {t}; it chose {stats.chosen}.")
        else:
            head.append("The generator has not run yet.")
        if self.script_edit is not None and self.script_edit[1] is self.sim.script:
            head.append(f"Changed by the user at time {self.script_edit[0]}.")
        if self.running():
            head.append(self.running().capitalize() + ": this script stays until it completes.")
        return "\n".join(head) + "\n\n" + (self.sim.script.text() or "(empty script)") + "\n"

    def edit_script(self, text: str, previous: Script) -> str:
        """The text of `previous`, changed by the user, replaces the script in place, and
        the run going on is cancelled. ScriptError if it is not correctly written."""
        script = parse_script(text, previous, self.rs)
        newer = self.sim.script is not previous
        self.close()
        self.sim.replace_script(script)
        self.constraints = self.sim.gen.constraints = script.constraints  # the next runs keep them too
        self.script_edit = (self.sim.t, script)
        return "the generated script is replaced by your changes" + (" (it replaces a newer script than the one you opened)" if newer else "")

    def news(self) -> list[str]:
        """What happened since the last call: generator runs that edit, refusals."""
        log = self.sim.log
        refused, runs = self.seen
        out = []
        for t, c, why, _, stats in log.runs[runs:]:
            if stats.chosen != "no-op":
                out.append(f"generator at chip {c} ({why}): {stats.chosen}")
        for t, src, why in log.refused[refused:]:
            if src != "user":
                out.append(f"refused at time {t} ({src}): {why}")
        self.seen = (len(log.refused), len(log.runs))
        return out

    # ------------------------------------------------------------ commands

    def command(self, cmd: str) -> str:
        """Carry out one typed command; what to show."""
        cmd = cmd.strip()
        if not cmd:
            return ""
        word, _, rest = cmd.partition(" ")
        rest = rest.strip()
        if word in ("add", "no", "remove"):
            return self.edit(cmd)
        handler = {"help": lambda: HELP, "rule": lambda: self.add_rule(rest), "rules": self.show_rules,
                   "unrule": lambda: self.remove_rule(rest), "undo": self.undo, "clear": self.clear,
                   "mode": lambda: self.set_mode(rest),
                   "line": lambda: str(self.sim.state.line), "claims": lambda: str(self.sim.state.claims),
                   "history": self.show_history, "status": self.status}.get(word)
        return handler() if handler else f"unknown command {word!r}: type help"

    def add_rule(self, text: str) -> str:
        return self.set_rules(self.texts + [text]) if text else "usage: rule <rule>"

    def remove_rule(self, n: str) -> str:
        if not n.isdigit() or not 1 <= int(n) <= len(self.texts):
            return f"usage: unrule <n>, n from 1 to {len(self.texts)}"
        return self.set_rules(self.texts[:int(n) - 1] + self.texts[int(n):])

    def set_rules(self, texts: list[str]) -> str:
        try:
            rs = parse_rules(texts)
        except (RuleError, ValueError) as e:
            return f"refused: {e}"
        try:
            self.sim.set_generator(self.generator(rs))
        except Exception as e:  # the edits have no result with these rules
            self.sim.set_generator(self.generator(self.rs))
            return f"refused: with these rules the edits have no result ({e})"
        self.texts, self.rs = texts, rs
        self.mode_names = sorted(set(self.mode_names) | {r.mode for r in rs.mode_rules})
        return self.show_rules()

    def set_mode(self, m: str) -> str:
        """The mode is m from the chip's position on (given externally, by the user),
        until the next transition MODES plans: those still apply when the chip gets
        there."""
        if not m or not re.fullmatch(r"\w+", m):
            return "usage: mode <name>"
        x = self.position
        if self.modes.mode_at(x) == m:
            return f"the mode is already {m}"
        self.changes[x] = m
        initial, planned = self.planned
        initial = self.changes.get(self.modes.start, initial)
        events = dict(planned)
        events.update((p, mm) for p, mm in self.changes.items() if p > self.modes.start)  # the user's win at one position
        self.modes.replace(initial, sorted(events.items()))
        if m not in self.mode_names:
            self.mode_names = sorted(self.mode_names + [m])
        self.sim.modes_changed()
        return f"mode {m} from {x} on"

    def edit(self, text: str) -> str:
        try:
            ops = [op for part in text.split(";") if part.strip() for op in parse_op(part)]
        except ValueError as e:
            return f"not understood: {e}"
        why = self.sim.edit_now(ops)
        if why:
            return f"refused: {why}"
        self.edits.append(text)
        return f"done: {text}"

    def undo(self) -> str:
        if not self.edits:
            return "no edit to undo"
        self.sim.undo_now()
        return f"undone: {self.edits.pop()}"

    def clear(self) -> str:
        self.sim.clear_now()
        self.edits = []
        return "no edit left"

    def show_rules(self) -> str:
        return "\n".join(f"{i}. {r}" for i, r in enumerate(self.rs.rules, 1)) or "no rule"

    def show_history(self) -> str:
        return "\n".join(f"{i}. {e}" for i, e in enumerate(self.edits, 1)) or "no edit"

    def status(self) -> str:
        out = [f"time {self.sim.t}, chip at {self.position}, mode {self.mode()}, speed {self.speed}"]
        out += [f"{p.name}, weight {w}, horizon {p.horizon}: "
                + ", ".join(f"{k} {v}" for k, v in sorted((getattr(p, 'targets', None) or getattr(p, 'lengths', {})).items()))
                for p, w in self.problems] or ["no optimization problem"]
        out.append("constraints (from the chip): " + (", ".join(map(str, self.constraints)) or "none"))
        out.append(f"allocated resources quantity {self.q}, {len(self.sim.log.runs)} generator runs")
        return "\n".join(out)


def parse_op(text: str) -> list[Op]:
    """One operation: add K [p%] <where> | no K <where> | remove [no] K <where>."""
    words = text.split()
    if not words:
        raise ValueError("an empty edit")
    action, words = words[0], words[1:]
    if action == "remove" and words[:1] == ["no"]:
        action, words = "remove no", words[1:]
    if action not in ("add", "no", "remove", "remove no"):
        raise ValueError(f"an edit starts with add, no or remove, not {action!r}")
    if not words or not KIND.match(words[0]) or words[0] in RESERVED:
        raise ValueError(f"a kind is expected after {action!r}")
    kind, words = words[0], words[1:]
    value = F(100)
    if action == "add" and words and words[0].endswith("%"):
        value = number(words[0][:-1])
        if not 0 < value <= 100:
            raise ValueError("a percentage in (0, 100]")
        words = words[1:]
    where = parse_where(" ".join(words))
    if action == "add":
        return [Op.put(kind, where, value)]
    if action == "no":
        return [Op.put_no(kind, where)]
    if action == "remove":
        return [Op.take(kind, where)]
    return [Op.take_no(kind, where)]


def parse_where(text: str) -> Region:
    """at x | on <interval> [U <interval>]... | everywhere."""
    t = text.strip()
    if t == "everywhere":
        return Region.universe()
    if t.startswith("at "):
        return Region.point(number(t[3:]))
    if t.startswith("on "):
        ivs = [interval(p) for p in re.split(r"\s+[Uu]\s+", t[3:].strip())]
        return Region.of(IntervalSet(ivs))
    raise ValueError(f"where? {t!r} (at x, on [a, b], everywhere)")


def interval(text: str) -> Interval:
    t = text.strip()
    if len(t) < 2 or t[0] not in "[(" or t[-1] not in "])" or t.count(",") != 1:
        raise ValueError(f"an interval like [a, b) is expected, not {t!r}")
    a, b = (number(s, infinite=True) for s in t[1:-1].split(","))
    lo_closed, hi_closed = t[0] == "[" and a != -INF, t[-1] == "]" and b != INF
    if a == INF or b == -INF or a > b or (a == b and not (lo_closed and hi_closed)):
        raise ValueError(f"an empty interval {t!r}")
    return Interval(a, b, lo_closed, hi_closed)


def number(text: str, infinite: bool = False):
    s = text.strip().replace("−", "-")
    if infinite and s in ("inf", "+inf", "∞", "+∞"):
        return INF
    if infinite and s in ("-inf", "-∞"):
        return -INF
    try:
        return F(s)
    except (ValueError, ZeroDivisionError):
        raise ValueError(f"a number is expected, not {s!r}")


def start(rules=(), modes=((0, "1"),), chip=0, speed=1, line=(), problems=(), q=0.5, constraints=()) -> None:
    """Open the window on these definitions.
    rules:    the rules, as text
    modes:    [(position, mode)]: the mode from each position on (the first one at or
              before the chip's start); revealed as the chip reaches them
    chip:     where the chip starts
    speed:    its speed, > 0, in positions per second
    line:     the initial state of the line, as edits ("A on [1, 3)", "A 50% at 2")
    problems: [(problem, weight)]: PresenceRatioProblem, MinimumLengthProblem
    q:        the allocated resources quantity, in (0, 1)
    constraints: what the generated script keeps true after the chip: constraints.py's
              (FifteenMinuteBreaks()), or edits written from the chip, as text
              ("Future on (0, inf)": Future on (chip, inf))"""
    import gui
    try:
        s = Session(rules, modes, chip, speed, [t if t.split()[0] in ("no", "remove") else f"add {t}" for t in line],
                    problems, q, constraints)
    except (RuleError, ValueError) as e:
        print(f"the definitions are refused: {e}")
        return
    gui.run(s)


if __name__ == "__main__":
    start()
