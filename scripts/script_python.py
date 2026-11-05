"""A generated script as the Python that builds it (`to_python`), and back (`from_python`).

The text is a Python module: imports, then `SCRIPT = Script(...)`, the very objects the
simulation runs (script.py): its loops and their events, what happens at a user edit and
when the view moves, the stretches where the mode rules are compiled (`covered`), and
the constraints it keeps. A region a generator run built from a pattern is written as
the call that builds it (ratio_plan.cycle, ratio_plan.turns), not as its intervals.

`from_python(text, previous)` runs the text and gives its SCRIPT, with what `previous`
compiled ahead kept where the text still says the same (as script_text.parse_script).
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import replace
from fractions import Fraction

from constraints import FifteenMinuteBreaks, KeepOn, ScreenBreaks
from intervals import INF, Interval, IntervalSet, Pos
from line import Op
from region import Region
from script import Script
from script_text import ScriptError

HEADER = """from fractions import Fraction as F

from constraints import FifteenMinuteBreaks, KeepOn, ScreenBreaks
from intervals import INF, Interval, IntervalSet, Pos
from line import Op
from ratio_plan import cycle, turns
from region import Region
from script import (ChangeAction, ChipIn, ConstraintEditAction, Edited, EditAction, Event, Loop, Place, PresenceAbove,
                    RerunAction, ScoreBelow, Script, ShowLine, Start, TransitionCount, Uncovered, Viewed)
"""


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
        if src:
            m = _NAMED.match(src)
            if m and _names is not None:  # (a call several regions come from: once, at the top)
                name = _names.setdefault(m[1], f"TURNS{len(_names) + 1}")
                return f"{name}[{m[2]}]"
            return src
        if v.is_bounded() or (not v.left and not v.right):
            return f"Region.of({py(v.core)})"
        return f"Region({py(v.lo)}, {py(v.hi)}, {py(v.core)}, {py(v.pl)}, {py(v.left)}, {py(v.pr)}, {py(v.right)})"
    if isinstance(v, Op):
        name = {"put": "put", "put_no": "put_no", "take": "take", "take_no": "take_no"}[v.action]
        value = f", {py(v.value)}" if v.action == "put" and v.value != 100 else ""
        return f"Op.{name}({v.kind!r}, {py(v.region)}{value})"
    if isinstance(v, FifteenMinuteBreaks):
        return f"FifteenMinuteBreaks({v.brk!r}, {v.ns!r}, length={py(v.length)}, pause={py(v.pause)})"
    if isinstance(v, ScreenBreaks):
        breaks = ", ".join(f"({k!r}, {py(L)}, {py(P)})" for k, L, P in v.kinds_)
        return f"ScreenBreaks([{breaks}], {v.ns!r}, chain={tuple(sorted(v.chain))!r})"
    if isinstance(v, KeepOn):
        return f"KeepOn({py(v.op)})"
    if isinstance(v, tuple):
        return "(" + ", ".join(py(x) for x in v) + ("," if len(v) == 1 else "") + ")"
    if isinstance(v, list):
        return "[" + ", ".join(py(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ", ".join(f"{k!r}: {py(x)}" for k, x in v.items()) + "}"
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


def to_python(script: Script, comments=()) -> str:
    """The Python module whose SCRIPT is this script (the parts the text of a script
    gives: what it compiled ahead and its line are kept by `from_python`). A call that
    several regions come from (ratio_plan.turns) is made once, at the top."""
    global _names
    _names = {}
    try:
        body = _body(script)
        calls = [f"{name} = {call}" for call, name in _names.items()]
    finally:
        _names = None
    head = [f"# {c}" for c in comments] + ([""] if comments else []) + [HEADER]
    return "\n".join(head + calls + ([""] if calls else []) + body) + "\n"


def _body(script: Script) -> list[str]:
    out = ["SCRIPT = Script("]
    out.append("    loops=[")
    for loop in script.loops:
        out.append(f"        Loop({py(loop.period)}, (")
        out += [f"            {py(e)}," for e in loop.events]
        out.append("        )),")
    out.append("    ],")
    for name in ("on_edit", "on_view"):
        events = getattr(script, name)
        out.append(f"    {name}=(" + ("" if events else "),"))
        if events:
            out += [f"        {py(e)}," for e in events]
            out.append("    ),")
    # s_f: what each frame places, as the window made it (s_v makes it again when the view
    # moves or is resized); a frame written here is drawn until then
    out.append("    frame=[" + ("" if script.frame else "],"))
    if script.frame:
        out += [f"        {py(pl)}," for pl in script.frame]
        out.append("    ],")
    out.append(f"    covered={py(list(script.covered))},")
    if script.live:
        out.append("    live=True,")
    if script.constraints:
        out.append(f"    constraints={py(tuple(script.constraints))},")
    out.append(")")
    return out


def from_python(text: str, previous: Script, rules=None, shown: int = 0) -> Script:
    """The SCRIPT of this Python text, keeping `previous`'s compiled stretches where the
    text still covers them with the same constraints. ScriptError if it does not run, or
    (with `rules`) a constraint keeps a kind the rules do not know."""
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
    same = list(map(str, s.constraints)) == list(map(str, previous.constraints))
    kept = [st for st in previous.stretches if same and any(m in (st.mode, None) and a <= st.a and st.b <= b for a, b, m in s.covered)]
    # s_f as written: drawn until s_v makes it again (`shown`: how many times s_v has shown
    # the line so far, Simulation.shown)
    frame = list(s.frame)
    return replace(previous, loops=list(s.loops), on_edit=tuple(s.on_edit), on_view=tuple(s.on_view), frame=frame,
                   frame_given=shown if frame else None, stretches=kept, covered=list(s.covered), live=s.live,
                   constraints=tuple(s.constraints))


def _line(e: Exception) -> int:
    tb = e.__traceback__
    n = 0
    while tb is not None:
        if tb.tb_frame.f_code.co_filename == "<generated script>":
            n = tb.tb_lineno
        tb = tb.tb_next
    return n
