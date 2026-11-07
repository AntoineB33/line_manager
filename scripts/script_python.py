"""A generated script as the Python that builds it (`to_python`), and back (`from_python`).

The text is a Python module whose SCRIPT is the whole script in place (script.py): its
loops and their events (S_f), what happens at a user edit (s_u) and when the view moves
(s_v), s_f (`frame`), and the rest of its state: where the mode rules apply (`covered`,
`live`), the constraints it keeps, the chip's position and the line the run compiled from
(`origin`, `base`), and which of its events were true and fired when last read (`true`,
`fired`, `since`). Everything is a value: running the text computes nothing, it only
builds the script. A region a generator run built from a pattern is written as that call
(ratio_plan.cycle, turns), each once at the top, which builds it at once.

Not in the text: the mode rules the run compiled ahead (Script.stretches), a cache of
what the chip computes itself where there is none; `from_python` keeps the previous
script's where the text still covers them.

`from_python(text, previous, rules)` runs the text and gives its SCRIPT.
"""

from __future__ import annotations

import dataclasses
import os
import re
from dataclasses import replace
from fractions import Fraction

from constraints import AsEarlyAsPossible, Breaks, KeepOn, MaximumGap, MinimumGap, ScreenBreaks
from intervals import INF, Interval, IntervalSet, Pos
from line import Claims, LineState, Op
from region import Region
from script import Script
from script_text import ScriptError

HEADER = f"""import sys

sys.path.insert(0, {os.path.dirname(os.path.abspath(__file__))!r})  # (scripts/: this file runs on its own too)

from fractions import Fraction as F

from constraints import AsEarlyAsPossible, Breaks, KeepOn, MaximumGap, MinimumGap, ScreenBreaks
from intervals import INF, Interval, IntervalSet, Pos
from line import Op
from ratio_plan import cycle, turns
from region import Region
from resolve import Record
from solver import Line
from script import (ChangeAction, ChipIn, ChipOn, ConstraintEditAction, Edited, EditAction, Event, Loop, Place, PresenceAbove,
                    RerunAction, ScoreBelow, Script, ShowLine, Start, TransitionCount, Uncovered, Viewed)
from script_python import claims, line_state
"""


def claims(pos=None, neg=None, taken=None) -> Claims:
    """The claims of a line state: pos {kind: {(value, owner): region}}, neg and taken
    {kind: {owner: region}}."""
    c = Claims()
    c.pos = {k: dict(d) for k, d in (pos or {}).items()}
    c.neg = {k: dict(d) for k, d in (neg or {}).items()}
    c.taken = {k: dict(d) for k, d in (taken or {}).items()}
    return c


def line_state(c: Claims, line, history=()) -> LineState:
    """A line state: its claims, its line (solver.Line: the values of each kind) and the
    user's edits, as they are (nothing is computed)."""
    return LineState(c, line, tuple(history))


def py(v) -> str:
    """The Python expression of a value of a script."""
    if v is None or isinstance(v, (bool, int, str)):
        return repr(v)
    if isinstance(v, float):
        if v in (float("inf"), float("-inf")):
            return "INF" if v > 0 else "-INF"
        return repr(v)
    if isinstance(v, Fraction):
        return f"F({v.numerator})" if v.denominator == 1 else f"F({v.numerator}, {v.denominator})"
    if isinstance(v, Pos):
        x = str(v.x.numerator) if v.x.denominator == 1 else py(v.x)
        return f"Pos({x})" if not v.k else f"Pos({x}, {v.k})"
    if isinstance(v, Interval):
        if v.is_point:
            return f"Interval.point({py(v.lo)})"
        return f"Interval({py(v.lo)}, {py(v.hi)}, {type(v.lo) is not float}, {type(v.hi) is not float})"
    if isinstance(v, IntervalSet):
        return f"IntervalSet([{', '.join(py(iv) for iv in v.intervals)}])" if v.intervals else "IntervalSet()"
    if isinstance(v, Region):
        src = getattr(v, "src", None)
        if isinstance(src, tuple):  # an operation on regions: each by its name
            op, left, right = src
            return f"({py(left)} {op} {py(right)})"
        if src:
            if _names is None:
                return src
            m = _NAMED.match(src)  # (each call once, at the top: the line, the edits use its name)
            if m:
                name = _names.setdefault(m[1], f"TURNS{sum(n.startswith('TURNS') for n in _names.values()) + 1}")
                return f"{name}[{m[2]}]"
            return _names.setdefault(src, f"R{sum(n.startswith('R') for n in _names.values()) + 1}")
        if v.is_bounded() or (not v.left and not v.right):
            return f"Region.of({py(v.core)})"
        if not v.left and v.right == IntervalSet.of(Interval(0, v.pr.x, True, False)):
            # its intervals, and everything from v.hi on: as intervals (the last to INF)
            ivs = (v.core | IntervalSet.of(Interval(v.hi, INF, True, False))).intervals
            return f"Region.of({py(ivs[0])})" if len(ivs) == 1 else f"Region.of(IntervalSet([{', '.join(py(iv) for iv in ivs)}]))"
        return f"Region({py(v.lo)}, {py(v.hi)}, {py(v.core)}, {py(v.pl)}, {py(v.left)}, {py(v.pr)}, {py(v.right)})"
    if isinstance(v, Op):
        name = {"put": "put", "put_no": "put_no", "take": "take", "take_no": "take_no"}[v.action]
        value = f", {py(v.value)}" if v.action == "put" and v.value != 100 else ""
        return f"Op.{name}({v.kind!r}, {py(v.region)}{value})"
    if isinstance(v, Claims):
        parts = [f"{name}={py(getattr(v, name))}" for name in ("pos", "neg", "taken") if getattr(v, name)]
        return f"claims({', '.join(parts)})"
    if isinstance(v, LineState):
        history = f", {py(tuple(v.history))}" if v.history else ""
        return f"line_state({py(v.claims)}, Line({py(v.line.vals)}){history})"
    if isinstance(v, Breaks):
        return f"Breaks({v.brk!r}, {v.ns!r}, length={py(v.length)}, pause={py(v.pause)})"
    if isinstance(v, ScreenBreaks):
        breaks = ", ".join(f"({k!r}, {py(L)}, {py(P)})" for k, L, P in v.kinds_)
        return f"ScreenBreaks([{breaks}], {v.ns!r}, chain={tuple(sorted(v.chain))!r})"
    if isinstance(v, AsEarlyAsPossible):
        return f"AsEarlyAsPossible({v.kind!r}" + (f", length={py(v.length)})" if v.length is not None else ")")
    if isinstance(v, MaximumGap):
        more = f", after={v.after!r}, at_least={py(v.at_least)}" if v.after != v.kind or v.at_least else ""
        more += f", length={py(v.length)}" if v.length != v.at_least else ""
        return f"MaximumGap({v.kind!r}, {py(v.gap)}{more})"
    if isinstance(v, MinimumGap):
        more = f", after={v.after!r}, at_least={py(v.at_least)}" if v.after != v.kind or v.at_least else ""
        more += f", pull={v.pull!r}" if v.pull else ""
        return f"MinimumGap({v.kind!r}, {py(v.gap)}{more})"
    if isinstance(v, KeepOn):
        return f"KeepOn({py(v.op)})"
    if isinstance(v, tuple):
        return "(" + ", ".join(py(x) for x in v) + ("," if len(v) == 1 else "") + ")"
    if isinstance(v, list):
        return "[" + ", ".join(py(x) for x in v) + "]"
    if isinstance(v, (set, frozenset)):
        return "{" + ", ".join(py(x) for x in sorted(v, key=repr)) + "}" if v else "set()"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{py(k)}: {py(x)}" for k, x in v.items()) + "}"
    if dataclasses.is_dataclass(v):
        args = []
        for f in dataclasses.fields(v):
            if not f.init:
                continue
            x = getattr(v, f.name)
            if f.default is not dataclasses.MISSING and x == f.default:
                continue
            args.append(f"{f.name}={py(x)}")
        return f"{type(v).__name__}({', '.join(args)})"
    raise TypeError(f"no Python for {type(v).__name__}")


_NAMED = re.compile(r"^(turns\(.*\))\[('[^']*')\]$")
_names: dict | None = None


def to_python(script: Script, comments=(), rules=None) -> str:
    """The Python module whose SCRIPT is this whole script. A call that several regions
    come from (ratio_plan.turns) is made once, at the top; so are the rules, the
    constraints and the line the run compiled from, which the compiled stretches use.
    `rules`: those of the session (a script with a line or compiled stretches needs them)."""
    global _names
    _names = {}
    try:
        top = [f"CONSTRAINTS = {py(tuple(script.constraints))}"]
        base = [f"BASE = {py(script.base)}  # the line the run compiled from, its own edits made"] if script.base is not None else []
        names = {}  # each event once, named: the loops and what was true or fired name it
        events = [e for loop in script.loops for e in loop.events] + list(script.on_edit) + list(script.on_view)
        lines = []
        for e in events:
            if id(e) not in names:
                names[id(e)] = f"E{len(names) + 1}"
                lines.append(f"{names[id(e)]} = {py(e)}")
        body = _body(script, rules, names)
        calls = [f"{name} = {call}" for call, name in _names.items()]
    finally:
        _names = None
    head = [f"# {c}" for c in comments] + ([""] if comments else []) + [HEADER]
    return "\n".join(head + top + calls + base + [""] + lines + [""] + body) + "\n"


def _body(script: Script, rules, names: dict) -> list[str]:
    texts = {}  # an event's text -> its name, for what was true or fired
    for e in [e for loop in script.loops for e in loop.events] + list(script.on_edit) + list(script.on_view):
        texts.setdefault(str(e), names[id(e)])

    def told(seen: set) -> str:
        """The texts of events, as the events they are the text of (str(E1)), else as text."""
        if not seen:
            return "set()"
        return "{" + ", ".join(f"str({texts[x]})" if x in texts else py(x) for x in sorted(seen)) + "}"

    out = ["SCRIPT = Script("]
    out.append("    loops=[  # S_f (with s_f, `frame` below): each loop runs its events every `period` seconds")
    for loop in script.loops:
        out.append(f"        Loop({py(loop.period)}, (")
        out += [f"            {names[id(e)]}," for e in loop.events]
        out.append("        )),")
    out.append("    ],")
    for name, what in (("on_edit", "s_u: at each edit of the user"), ("on_view", "s_v: when the view moves or is resized")):
        events = getattr(script, name)
        out.append(f"    {name}=(  # {what}" if events else f"    {name}=(),  # {what}")
        if events:
            out += [f"        {names[id(e)]}," for e in events]
            out.append("    ),")
    # s_f: what each frame places, as the window made it (s_v makes it again when the view
    # moves or is resized); a frame written here is drawn until then
    out.append("    frame=[  # s_f: at each frame (s_v makes it again)" if script.frame else "    frame=[],  # s_f: at each frame")
    if script.frame:
        out += [f"        {py(pl)}," for pl in script.frame]
        out.append("    ],")
    if script.frame_given is not None:
        out.append(f"    frame_given={script.frame_given},  # (s_f written by the user, until s_v shows the line again)")
    out.append(f"    covered={py(list(script.covered))},  # (from, to, mode): where the mode rules are compiled ahead")
    out.append(f"    live={script.live},  # the mode rules apply at the chip elsewhere too")
    out.append("    constraints=CONSTRAINTS,  # what the script keeps after the chip")
    out.append(f"    origin={py(script.origin)},  # the chip's position the run compiled from")
    if script.base is not None:
        out.append("    base=BASE,")
    out.append(f"    true={told(script.true)},  # the events true when last read")
    out.append(f"    fired={told(script.fired)},  # and those that fired")
    out.append(f"    since={script.since},  # the mode transitions since it was put in place")
    out.append(")")
    return out


def from_python(text: str, previous: Script, rules=None, shown: int = 0) -> Script:
    """The SCRIPT of this Python text, the whole script. ScriptError if it does not run, or
    (with `rules`) a constraint keeps a kind the rules do not know. The constraints are
    bound to the rules (ScreenBreaks reads what they put), as the generator binds them."""
    ns: dict = {}
    try:
        exec(compile(text, "<generated script>", "exec"), ns)
    except SyntaxError as e:
        raise ScriptError([(e.lineno or 0, e.msg)]) from None
    except Exception as e:  # what the text runs is the user's: any error says why
        raise ScriptError([(_line(e), f"{type(e).__name__}: {e}")]) from None
    s = ns.get("SCRIPT")
    if not isinstance(s, Script):
        raise ScriptError([(0, "the text gives no SCRIPT = Script(...)")])
    if rules is not None:
        unknown = sorted(k for c in s.constraints if hasattr(c, "kinds") for k in c.kinds() if k not in rules.order)
        if unknown:
            raise ScriptError([(0, f"{k} is not in the rules") for k in unknown])
        for c in s.constraints:
            if hasattr(c, "bind"):
                c.bind(rules)
            if hasattr(c, "bind_all"):
                c.bind_all(s.constraints)
    # s_f as written: drawn until s_v makes it again (`shown`: how many times s_v has shown
    # the line so far, Simulation.shown), unless the text says otherwise
    frame = list(s.frame)
    given = s.frame_given if s.frame_given is not None else (shown if frame else None)
    # the mode rules the previous script's run compiled ahead (a cache, not in the text: the
    # chip computes the same itself), kept where the text still covers them the same way
    same = list(map(str, s.constraints)) == list(map(str, previous.constraints))
    kept = [st for st in previous.stretches if same and any(m in (st.mode, None) and a <= st.a and st.b <= b for a, b, m in s.covered)]
    return replace(s, loops=list(s.loops), on_edit=tuple(s.on_edit), on_view=tuple(s.on_view), frame=frame,
                   frame_given=given, stretches=kept, covered=list(s.covered),
                   constraints=tuple(s.constraints), true=set(s.true), fired=set(s.fired))


def _line(e: Exception) -> int:
    tb = e.__traceback__
    n = 0
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "<generated script>":
            n = tb.tb_lineno
        tb = tb.tb_next
    return n
