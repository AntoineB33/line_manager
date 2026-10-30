"""A generated script read from its text (`Script.text`), changed by the user.

`parse_script(text, previous, rules)` gives the script the text says, or raises
ScriptError saying which lines are not correctly written. Every line of the text can be
changed, added or deleted: what is written is what the script does. `previous` is the
script the text was written from, whose shortcuts are kept where they still apply.

The lines at the top of the text, down to the first empty line, tell about the script
and are not read.
"""

from __future__ import annotations

import re
from dataclasses import replace
from fractions import Fraction

from constraints import FifteenMinuteBreaks, KeepOn
from intervals import EPS, INF, Interval, IntervalSet, Pos
from line import Op
from region import ONE, Region
from script import MODE_RULES, ChipIn, ConstraintEditAction, Edited, EditAction, Event, PresenceAbove, RerunAction, ScoreBelow, Script, Start, TransitionCount, Uncovered

_POS = re.compile(r"(?P<x>-?\d+(?:/\d+)?)?(?:(?P<s>[+-])?(?P<k>\d*)ε)?$")
_BREAKS = re.compile(r"keep (\S+) on \(chip, chip \+ (\S+)\] with (\S+), as early as possible, none within (\S+) after as much (\S+)$")
_APPLY = re.compile(rf"if the chip is in \[([^,]+), ([^,]+)\](?: and mode is (\w+))?, then {MODE_RULES}\.?$")
_OP = re.compile(r"put no (?P<no>\S+) on (?P<nw>.+)$|put (?P<k>\S+) (?P<v>[^ %]+)% on (?P<w>.+)$"
                 r"|take no (?P<tn>\S+) off (?P<tnw>.+)$|take (?P<t>\S+) off (?P<tw>.+)$")


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


def _action(text: str):
    if text == "run the generator again":
        return RerunAction()
    if text == "run the generator once, as soon as possible":
        return RerunAction(first=True)
    if text == str(ConstraintEditAction()):
        return ConstraintEditAction()
    return EditAction(tuple(_op(p) for p in re.split(r", (?=put |take )", text)))


def _constraint(line: str, rules):
    m = _BREAKS.match(line)
    if m:
        kind, length, ns, pause, ns2 = m.groups()
        if ns != ns2:
            raise ValueError(f"the same kind is expected, not {ns} and {ns2}")
        for k in (kind, ns):
            if rules is not None and k not in rules.order:
                raise ValueError(f"{k} is not in the rules")
        length, pause = _number(length), _number(pause)
        if length <= 0 or pause < 0:
            raise ValueError("a length > 0, and a pause >= 0")
        return FifteenMinuteBreaks(kind, ns, length, pause)
    op = _op(line[len("keep "):].replace(" on chip + ", " on ", 1))
    return KeepOn(op)


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
    m = re.fullmatch(r"the optimization score becomes less than (\S+)", text)
    if m:
        return ScoreBelow(_number(m[1]))
    m = re.fullmatch(r"(\S+) ([^ %]+)% exceeds a presence ratio of (\S+) over the next (\S+) when the chip is in \[([^,]+), ([^,]+)\)(?: and mode is (\w+))?", text)
    if m:
        y, H, lo, hi = _number(m[3]), _number(m[4]), _number(m[5]), _number(m[6])
        if H <= 0 or not lo < hi:
            raise ValueError(f"a length > 0, and positions in [{lo}, {hi}), are expected: {text!r}")
        return PresenceAbove(m[1], _number(m[2]), y, H, lo, hi, m[7])
    m = re.fullmatch(r"the chip is (?:in \[([^,]+), ([^,]+)\)|at or after ([^,]+?))(?: and mode is (\w+))?", text)
    if not m:
        raise ValueError(f"this condition is not understood: {text!r}")
    lo, hi = _number(m[3] or m[1]), INF if m[3] else _number(m[2])
    if not lo < hi:
        raise ValueError(f"no position is in [{lo}, {hi})")
    return ChipIn(lo, hi, m[4])


def _event(line: str) -> Event:
    m = re.fullmatch(r"if (.+?), then (.+)", line)
    if not m:
        raise ValueError('an instruction is written "if <condition>, then <what to do>."')
    body = m[2]
    if body.endswith("....") or (body.endswith(".") and not body.endswith("...")):
        body = body[:-1]  # the end of the sentence
    return Event(_trigger(m[1]), tuple(_action(a.strip()) for a in body.split("; ")))


def parse_script(text: str, previous: Script, rules=None) -> Script:
    lines = text.splitlines()
    first = 0
    if lines and lines[0].startswith("Generated script at time"):
        first = next((i for i, s in enumerate(lines) if not s.strip()), len(lines))
    was = {str(k): k for k in previous.constraints}
    events, covered, constraints, live, errors = [], [], [], False, []
    for i, raw in enumerate(lines[first:], first + 1):
        line = raw.strip()
        if not line or line == "(empty script)":
            continue
        try:
            m = _APPLY.match(line)
            if m:
                a, b = position(m[1]), position(m[2])
                if type(a) is not Pos or type(b) is not Pos or b < a:
                    raise ValueError(f"no position is in [{m[1]}, {m[2]}]")
                covered.append((a, b, m[3]))
            elif line.rstrip(".") == f"elsewhere, {MODE_RULES}":
                live = True
            elif line.startswith("keep "):
                constraints.append(was.get(line) or _constraint(line, rules))
            else:
                events.append(_event(line))
        except ValueError as e:
            errors.append((i, str(e)))
    if errors:
        raise ScriptError(errors)
    same = list(map(str, constraints)) == list(was)
    kept = [st for st in previous.stretches if same and any(m in (st.mode, None) and a <= st.a and st.b <= b for a, b, m in covered)]
    return replace(previous, events=events, stretches=kept, covered=covered, live=live, constraints=tuple(constraints))
