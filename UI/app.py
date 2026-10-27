"""Interactive line manager: define the rules, edit the line, move the chip.

    python UI/app.py                   an empty session (then type "help")
    python UI/examples/<example>.py    a session defined by a script (copy one to start yours)

An example script defines the rules, the modes, the chip's start and speed, the
initial state of the line and the generator's problems, then calls start(...).

Everything is exact (fractions). The user's edits are kept, in order: the line is
computed from all of them (resolve.py), so changing the rules recomputes it, and
"undo" gives the line as if the last edit had never been made. The initial state of
the line is the user's first edit. The chip only moves when asked ("run", "move"),
at its speed; as it moves, the modes are revealed, the generator runs and its script
acts, the mode rules included (simulation.py).
"""

from __future__ import annotations

import math
import os
import re
import sys
from fractions import Fraction as F

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

from generator import RulesGenerator  # noqa: E402
from intervals import INF, Interval, IntervalSet  # noqa: E402
from line import Op  # noqa: E402
from modes import ModeTimeline  # noqa: E402
from problems import MinimumLengthProblem, PresenceRatioProblem  # noqa: E402,F401 (for the example scripts)
from region import Region  # noqa: E402
from rules import RuleError, parse_rules  # noqa: E402
from simulation import Chip, Simulation  # noqa: E402
from solver import Line  # noqa: E402

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
The chip
  speed <v>                the chip's speed, v > 0 (positions per unit of time)
  run <d>                  let the time run for d: the chip moves d * speed
  move <x>                 let the time run until the chip is at x
  status                   the time, the chip, its mode and speed, the problems
  script                   the generated script now
Display
  show                     the line, exactly, and a strip (^ is the chip)
  claims                   what applies of the edits now, and who made it
  history                  the user's edits, oldest first
  view <a> <b> [step]      the stretch the strip shows; "view chip" follows the chip
Other
  load <file>              run the commands of a file
  help, quit"""

KIND = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
RESERVED = {"no", "at", "on", "everywhere", "mode"}


class Session:
    def __init__(self, rules=(), modes=((0, "1"),), chip=0, speed=1, line=(), problems=(), q=0.5) -> None:
        self.texts = list(rules)
        self.rs = parse_rules(self.texts)
        self.problems = list(problems)
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
        self.chip = Chip([(0, x0)])
        self.modes = ModeTimeline(x0, initial, transitions)
        self.sim = Simulation(_empty(), self.generator(self.rs), self.chip, self.modes)
        self.edits: list[str] = []  # the user's edits, as typed, oldest first
        self.view: tuple | None = None  # None: follow the chip
        self.seen = (0, 0, 0)  # changes, refusals and generator runs already reported
        ops = [op for text in line for op in parse_op(text)]
        why = self.sim.edit_now(ops) if ops else (self.sim.start([]) or None)
        if ops and why:
            print(f"the initial state is refused: {why}")
        elif ops:
            self.edits.append("; ".join(line))
        self.seen = self.counts()

    def generator(self, rs) -> RulesGenerator:
        return RulesGenerator(rs, self.problems, self.q)

    def counts(self) -> tuple:
        log = self.sim.log
        return len(log.changes), len(log.refused), len(log.runs)

    # ------------------------------------------------------------ commands

    def run(self, cmd: str) -> bool:
        """Carry out one command; False to quit."""
        cmd = cmd.strip()
        if not cmd or cmd.startswith("#"):
            return True
        word, _, rest = cmd.partition(" ")
        rest = rest.strip()
        if word in ("quit", "exit"):
            return False
        handler = {"help": lambda: print(HELP), "rule": lambda: self.add_rule(rest), "rules": self.show_rules,
                   "unrule": lambda: self.remove_rule(rest), "undo": self.undo, "clear": self.clear,
                   "speed": lambda: self.set_speed(rest), "run": lambda: self.run_for(rest), "move": lambda: self.move(rest),
                   "status": self.status, "script": self.show_script, "show": self.show, "claims": self.show_claims,
                   "history": self.show_history, "view": lambda: self.set_view(rest), "load": lambda: self.load(rest)}.get(word)
        if handler is None:
            if word in ("add", "no", "remove"):
                self.edit(cmd)
            else:
                print(f"unknown command {word!r}: type help")
            return True
        handler()
        return True

    def add_rule(self, text: str) -> None:
        if not text:
            return print("usage: rule <rule>")
        self.set_rules(self.texts + [text])

    def remove_rule(self, n: str) -> None:
        if not n.isdigit() or not 1 <= int(n) <= len(self.texts):
            return print(f"usage: unrule <n>, n from 1 to {len(self.texts)}")
        self.set_rules(self.texts[:int(n) - 1] + self.texts[int(n):])

    def set_rules(self, texts: list[str]) -> None:
        try:
            rs = parse_rules(texts)
        except (RuleError, ValueError) as e:
            return print(f"refused: {e}")
        try:
            self.sim.set_generator(self.generator(rs))
        except Exception as e:  # the edits have no result with these rules
            self.sim.set_generator(self.generator(self.rs))
            return print(f"refused: with these rules the edits have no result ({e})")
        self.texts, self.rs = texts, rs
        self.show_rules()
        self.report()

    def edit(self, text: str) -> None:
        try:
            ops = [op for part in text.split(";") if part.strip() for op in parse_op(part)]
        except ValueError as e:
            return print(f"not understood: {e}")
        why = self.sim.edit_now(ops)
        if why:
            self.seen = self.counts()
            return print(f"refused: {why}")
        self.edits.append(text)
        self.report()

    def undo(self) -> None:
        if not self.edits:
            return print("no edit to undo")
        self.sim.undo_now()
        print(f"undone: {self.edits.pop()}")
        self.report()

    def clear(self) -> None:
        self.sim.clear_now()
        self.edits = []
        print("no edit left")
        self.report()

    def set_speed(self, rest: str) -> None:
        try:
            v = number(rest)
        except ValueError:
            v = F(0)
        if v <= 0:
            return print("usage: speed <v>, v > 0")
        self.speed = v
        print(f"speed {v}")

    def run_for(self, rest: str) -> None:
        try:
            d = number(rest)
        except ValueError:
            d = F(0)
        if d <= 0:
            return print("usage: run <d>, d > 0")
        self.advance(d)

    def move(self, rest: str) -> None:
        try:
            x = number(rest)
        except ValueError:
            return print("usage: move <x>")
        here = self.chip.position(self.sim.t)
        if x <= here:
            return print(f"the chip is at {here} and only moves to the right")
        self.advance((x - here) / self.speed)

    def advance(self, d: F) -> None:
        t0 = self.sim.t
        x0 = self.chip.position(t0)
        self.chip.extend(t0 + d, x0 + self.speed * d)
        self.sim.advance(t0 + d)
        print(f"time {self.sim.t}, chip at {self.chip.position(self.sim.t)}, mode {self.mode()}")
        self.report()

    def set_view(self, rest: str) -> None:
        if rest == "chip":
            self.view = None
            return self.show()
        try:
            parts = [number(p) for p in rest.split()]
            a, b = parts[0], parts[1]
            step = parts[2] if len(parts) > 2 else F(1, 2)
            if not a < b or step <= 0:
                raise ValueError
        except (ValueError, IndexError):
            return print('usage: view <a> <b> [step], with a < b and step > 0, or "view chip"')
        self.view = (a, b, step)
        self.show()

    def load(self, path: str) -> bool:
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError as e:
            print(f"cannot read {path}: {e}")
            return True
        for line in lines:
            if line.strip() and not line.strip().startswith("#"):
                print(f"> {line.strip()}")
            if not self.run(line):
                return False
        return True

    # ------------------------------------------------------------ display

    def mode(self) -> str:
        return self.modes.mode_at(self.chip.position(self.sim.t))

    def report(self) -> None:
        """What happened since the last report, then the line."""
        log = self.sim.log
        changes, refused, runs = self.seen
        by = {}
        for ch in log.changes[changes:]:
            if ch.source != "user":
                by[ch.source] = by.get(ch.source, 0) + 1
        if by:
            print("changes: " + ", ".join(f"{n} by the {'generated script' if s == 'script' else 'mode rules'}" for s, n in sorted(by.items())))
        for t, src, why in log.refused[refused:]:
            if src != "user":
                print(f"  refused at time {t} ({src}): {why}")
        for t, c, why, script, stats in log.runs[runs:]:
            if stats.chosen != "no-op":
                print(f"  generator run at chip {c} ({why}): chose {stats.chosen}")
        self.seen = self.counts()
        self.show()

    def show_rules(self) -> None:
        if not self.texts:
            return print("no rule")
        for i, r in enumerate(self.rs.rules, 1):
            print(f"  {i}. {r}")

    def show(self) -> None:
        line = self.sim.state.line
        print(line)
        chip = self.chip.position(self.sim.t)
        view = self.view or (F(math.floor(chip)) - 2, F(math.floor(chip)) + 14, F(1, 2))
        print(strip(line, *view, chip=chip))

    def show_claims(self) -> None:
        print(self.sim.state.claims)

    def show_history(self) -> None:
        if not self.edits:
            return print("no edit")
        for i, e in enumerate(self.edits, 1):
            print(f"  {i}. {e}")

    def status(self) -> None:
        print(f"time {self.sim.t}, chip at {self.chip.position(self.sim.t)}, mode {self.mode()}, speed {self.speed}")
        if not self.problems:
            print("no optimization problem")
        for p, w in self.problems:
            targets = getattr(p, "targets", None) or getattr(p, "lengths", {})
            print(f"  {p.name}, weight {w}, horizon {p.horizon}: " + ", ".join(f"{k} {v}" for k, v in sorted(targets.items())))
        print(f"  allocated resources quantity {self.q}, {len(self.sim.log.runs)} generator runs")

    def show_script(self) -> None:
        print(self.sim.script.text() or "(empty script)")


def _empty():
    from line import LineState
    return LineState.empty()


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


def strip(line: Line, lo: F, hi: F, step: F, chip=None) -> str:
    """One row per kind: # present at 100% on the whole cell, + partly present or at
    another percentage, . absent; a cell is [x, x + step). ^ marks the chip's cell."""
    n = int((hi - lo) / step)
    cells = [Region.of(Interval(lo + i * step, lo + (i + 1) * step, True, False)) for i in range(n)]
    kinds = line.kinds()
    width = max([len(k) for k in kinds] + [4]) + 1
    axis = ""
    for i in range(n):
        x = lo + i * step
        if len(axis) <= i:
            axis += str(x) if x % (4 * step) == 0 else " "
    rows = [" " * width + axis[:n]]
    for k in kinds:
        pres, full = line.presence(k), line.vals[k].get(F(100), Region.empty())
        rows.append(k.ljust(width) + "".join("." if not (pres & c) else "#" if c.issubset(full) else "+" for c in cells))
    if chip is not None and lo <= chip < hi:
        rows.append("chip".ljust(width) + " " * int((chip - lo) / step) + "^")
    return "\n".join(rows)


def start(rules=(), modes=((0, "1"),), chip=0, speed=1, line=(), problems=(), q=0.5) -> None:
    """Open the interactive program on these definitions.
    rules:    the rules, as text
    modes:    [(position, mode)]: the mode from each position on (the first one at or
              before the chip's start); revealed as the chip reaches them
    chip:     where the chip starts (at time 0)
    speed:    its speed, > 0
    line:     the initial state of the line, as edits ("A on [1, 3)", "A 50% at 2")
    problems: [(problem, weight)]: PresenceRatioProblem, MinimumLengthProblem
    q:        the allocated resources quantity, in (0, 1)"""
    try:
        s = Session(rules, modes, chip, speed, [f"add {t}" if not t.split()[0] in ("no", "remove") else t for t in line],
                    problems, q)
    except (RuleError, ValueError) as e:
        print(f"the definitions are refused: {e}")
        return
    print('Line manager. Type "help" for the commands.')
    s.show_rules()
    s.status()
    s.show()
    while True:
        try:
            cmd = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not sys.stdin.isatty():
            print(cmd)
        if not s.run(cmd):
            return


if __name__ == "__main__":
    start()
