"""A generated script read back from its text (`Script.text`), changed by the user.

`parse_script(text, previous)` gives the script the text says, or raises ScriptError
saying which lines are not correctly written. `previous` is the script the text was
written from.

- An event ("if <trigger>, then <actions>.") can be changed, added or deleted. Triggers
  and actions are written as `Script.text` writes them.
- The compiled stretches ("if the chip is ..., mode is m and the line is the one
  compiled there, then ...") and the last instruction ("where no compiled stretch
  applies, ...") are what the run compiled, and the text only describes them: each can
  be kept as it is or deleted, not rewritten.
- The same for the constraints' edit: changed, it is a plain edit (without the text in
  parentheses).
- The lines at the top of the text, down to the first empty line, tell about the script
  and are not read.
"""

from __future__ import annotations

import re
from dataclasses import replace
from fractions import Fraction

from intervals import EPS, INF, Interval, IntervalSet, Pos
from line import Op
from region import ONE, Region
from script import ChipIn, ConstraintEditAction, Edited, EditAction, Event, RerunAction, Script, Start, TransitionCount, Uncovered

_POS = re.compile(r"(?P<x>-?\d+(?:/\d+)?)?(?:(?P<s>[+-])?(?P<k>\d*)ε)?$")
_COMPILED = re.compile(r"if the chip is .*, mode is \S+ and the line is the one compiled there, then ")
_OP = re.compile(r"put no (?P<no>\S+) on (?P<nw>.+)$|put (?P<k>\S+) (?P<v>[^ %]+)% on (?P<w>.+)$"
                 r"|take no (?P<tn>\S+) off (?P<tnw>.+)$|take (?P<t>\S+) off (?P<tw>.+)$")
_LATER = " (what the constraints ask for then"


class ScriptError(ValueError):
    """The text is not correctly written: `errors` is a list of (line number, what is wrong)."""

    def __init__(self, errors: list[tuple[int, str]]) -> None:
        super().__init__("\n".join(f"line {n}: {why}" for n, why in errors))
        self.errors = errors


def position(text: str):
    """x, x+kε, x-kε, kε, -kε, +inf or -inf."""
    t = text.strip().replace("−", "-")
    if t in ("+inf", "inf"):
        return INF
    if t == "-inf":
        return -INF
    m = _POS.match(t)
    if not t or not m:
        raise ValueError(f"a position is expected, not {t!r}")
    x, k = m["x"], 0
    if "ε" in t:
        k = int(m["k"] or 1) * (-1 if m["s"] == "-" else 1)
        if x is not None and m["s"] is None:  # 2ε
            x, k = None, int(x)
    try:
        return Pos(Fraction(x or 0), k)
    except ZeroDivisionError:
        raise ValueError(f"a position is expected, not {t!r}")


def interval(text: str) -> Interval:
    """[a, b], [a, b), (a, b], (a, b) or {a}."""
    t = text.strip()
    if len(t) > 2 and t[0] == "{" and t[-1] == "}":
        return Interval.point(position(t[1:-1]))
    if len(t) < 2 or t[0] not in "[(" or t[-1] not in "])" or t.count(",") != 1:
        raise ValueError(f"an interval is expected, not {t!r}")
    a, b = t[1:-1].split(",")
    return Interval(position(a), position(b), t[0] == "[", t[-1] == "]")


def _intervals(text: str) -> IntervalSet:
    t = text.strip()
    return IntervalSet() if t in ("", "{}") else IntervalSet([interval(p) for p in re.split(r"\s+U\s+", t)])


def _ungrouped(text: str) -> str:
    return text[1:-1] if _is_group(text) else text


def _is_group(text: str) -> bool:
    return text[:2] in ("((", "([", "({") and text[-2:] in ("))", "])", "})")


def _right(text: str, period: str) -> Region:
    """These intervals repeating every `period` to the right."""
    s, p = _intervals(_ungrouped(text)), position(period)
    if not s or type(s.intervals[-1].hi) is not Pos or type(s.intervals[0].lo) is not Pos or type(p) is not Pos or p.x <= 0:
        raise ValueError(f"what repeats is bounded, and its period > 0: {text!r} every {period}")
    h = s.intervals[0].lo
    if s.intervals[-1].hi >= h + p:
        raise ValueError(f"what repeats is shorter than its period: {text!r} every {period}")
    return Region(h, h, IntervalSet(), ONE, IntervalSet(), p, s.shift(-h))


def _left(text: str, period: str) -> Region:
    """These intervals repeating every `period` to the left."""
    s, p = _intervals(_ungrouped(text)), position(period)
    if not s or type(s.intervals[-1].hi) is not Pos or type(s.intervals[0].lo) is not Pos or type(p) is not Pos or p.x <= 0:
        raise ValueError(f"what repeats is bounded, and its period > 0: {text!r} every {period}")
    h = s.intervals[-1].hi + EPS
    if s.intervals[0].lo < h - p:
        raise ValueError(f"what repeats is shorter than its period: {text!r} every {period}")
    return Region(h, h, IntervalSet(), p, s.shift(p - h))


def region(text: str) -> Region:
    """A region, as `Region.__str__` writes it."""
    t, out = text.strip(), Region.empty()
    m = re.match(r"\.\.\. (.+?) repeating every (\S+) to the left(?: U (.*))?$", t)
    if m:
        out, t = _left(m[1], m[2]), m[3] or ""
    m = re.match(r"(.*) repeating every (\S+) to the right \.\.\.$", t)
    if m:
        body = m[1]
        grouped = body[-2:] in ("))", "])", "})")
        at = max(body.rfind(g) for g in ("((", "([", "({")) if grouped else body.rfind(" U ") + 3 if " U " in body else 0
        out, t = out | _right(body[at:], m[2]), body[:at].removesuffix(" U ")
    return out | Region.of(_intervals(t))


def _op(text: str) -> Op:
    m = _OP.match(text)
    if not m:
        raise ValueError(f"an edit is expected (put K 100% on ..., put no K on ..., take K off ..., take no K off ...), not {text!r}")
    if m["no"]:
        return Op.put_no(m["no"], region(m["nw"]))
    if m["k"]:
        try:
            value = Fraction(m["v"])
        except (ValueError, ZeroDivisionError):
            raise ValueError(f"a percentage is expected, not {m['v']!r}")
        if not 0 < value <= 100:
            raise ValueError("a percentage is in (0, 100]")
        return Op.put(m["k"], region(m["w"]), value)
    if m["tn"]:
        return Op.take_no(m["tn"], region(m["tnw"]))
    return Op.take(m["t"], region(m["tw"]))


def _action(text: str, known: dict):
    if text in known:
        return known[text]
    if text == "run the generator again":
        return RerunAction()
    if text == "run the generator once, as soon as possible":
        return RerunAction(first=True)
    if _LATER in text:
        raise ValueError("the constraints' edit cannot be changed; without the text in parentheses it is a plain edit")
    return EditAction(tuple(_op(p) for p in re.split(r", (?=put |take )", text)))


def _number(text: str) -> Fraction:
    p = position(text)
    if type(p) is not Pos or p.k:
        raise ValueError(f"a number is expected, not {text!r}")
    return p.x


def _trigger(text: str):
    if text == str(Start()):
        return Start()
    if text == str(Edited()):
        return Edited()
    if text == str(Uncovered()):
        return Uncovered()
    m = re.fullmatch(r"(\d+) mode transitions? happened", text)
    if m:
        if int(m[1]) < 1:
            raise ValueError("at least 1 mode transition")
        return TransitionCount(int(m[1]))
    m = re.fullmatch(r"the chip is (?:in \[([^,]+), ([^,]+)\)|at or after ([^,]+?))(?: and mode is (\w+))?", text)
    if not m:
        raise ValueError(f"this condition is not understood: {text!r}")
    lo, hi = _number(m[3] or m[1]), INF if m[3] else _number(m[2])
    if not lo < hi:
        raise ValueError(f"no position is in [{lo}, {hi})")
    return ChipIn(lo, hi, m[4])


def _event(line: str, known: dict) -> Event:
    m = re.fullmatch(r"if (.+?), then (.+)", line)
    if not m:
        raise ValueError('an instruction is written "if <condition>, then <what to do>."')
    body = m[2]
    if body.endswith("....") or (body.endswith(".") and not body.endswith("...")):
        body = body[:-1]  # the end of the sentence
    return Event(_trigger(m[1]), tuple(_action(a.strip(), known) for a in body.split("; ")))


def parse_script(text: str, previous: Script) -> Script:
    lines = text.splitlines()
    first = 0
    if lines and lines[0].startswith("Generated script at time"):
        first = next((i for i, s in enumerate(lines) if not s.strip()), len(lines))
    known = {str(a): a for ev in previous.events for a in ev.actions}
    compiled = {st.text(): st for st in previous.stretches}
    events, kept, live, errors = [], [], None, []
    for i, raw in enumerate(lines[first:], first + 1):
        line = raw.strip()
        if not line or line == "(empty script)":
            continue
        if line in compiled:
            kept.append(compiled[line])
        elif previous.live is not None and line == previous.live_text():
            live = previous.live
        elif _COMPILED.match(line) or line.startswith(Script.LIVE):
            errors.append((i, "what a run compiled can be kept as it is or deleted, not rewritten"))
        else:
            try:
                events.append(_event(line, known))
            except ValueError as e:
                errors.append((i, str(e)))
    if errors:
        raise ScriptError(errors)
    return replace(previous, events=events, stretches=[st for st in previous.stretches if any(st is k for k in kept)], live=live)
