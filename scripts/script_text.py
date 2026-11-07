"""A generated script read from its text (`Script.text`), changed by the user.

`parse_script(text, previous, rules)` gives the script the text says, or raises
ScriptError saying which lines are not correctly written. Every line of the text can be
changed, added or deleted: what is written is what the script does. `previous` is the
script the text was written from, whose shortcuts are kept where they still apply.

The lines at the top of the text, down to the first empty line, tell about the script
and are not read. A line "every p s:" starts the loop of period p; the lines that do not
say their own part of the script (script.Edited, script.Viewed) are in the last one, or
in the STEP loop before any.
"""

from __future__ import annotations

import re
from dataclasses import replace
from fractions import Fraction

from constraints import AsEarlyAsPossible, Breaks, KeepOn, MaximumGap, MinimumGap, ScreenBreaks
from intervals import EPS, INF, Interval, IntervalSet, Pos
from line import Op
from region import ONE, Region
from script import MODE_RULES, ON_EDIT, ON_VIEW, STEP, ChangeAction, ChipIn, ConstraintEditAction, Edited, EditAction, Event, Loop, PresenceAbove, RerunAction, ScoreBelow, Script, ShowLine, Start, TransitionCount, Uncovered, Viewed, loop_name

_POS = re.compile(r"(?P<x>-?\d+(?:/\d+)?)?(?:(?P<s>[+-])?(?P<k>\d*)ε)?$")
_BREAKS = re.compile(r"keep (\S+) on \(chip, chip \+ (\S+)\] with (\S+), as early as possible, none within (\S+) after as much (\S+)$")
_APPLY = re.compile(rf"if the chip is in \[([^,]+), ([^,]+)\](?: and mode is (\w+))?, then {MODE_RULES}\.?$")
_LOOP = re.compile(r"every (\S+) s:$")
_CHANGE = re.compile(r'(add to|remove from) (every \S+ s|the user edit script|the view script): "(.+)"$')
_OUTSIDE_QUOTES = r'(?=(?:[^"]*"[^"]*")*[^"]*$)'
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
    if text == str(ShowLine()):
        return ShowLine()
    m = _CHANGE.match(text)
    if m:
        return ChangeAction(m[1] == "add to", m[2], _event(m[3]))
    return EditAction(tuple(_op(p) for p in re.split(r", (?=put |take )", text)))


_SCREEN = re.compile(r"keep (.+) with (\S+), as early as possible(?:; a chain of (.+) is its longest break)?$")
_SCREEN_KIND = re.compile(r"(\S+) of (\S+) \(none within (\S+) after as much (\S+)\)$")


def _screen_breaks(m, rules) -> ScreenBreaks:
    """keep K of L (none within P after as much NS), ... with NS, as early as possible[; a
    chain of K, ... is its longest break] (constraints.ScreenBreaks)."""
    ns = m[2]
    breaks = []
    for part in m[1].split("), "):
        k = _SCREEN_KIND.match(part if part.endswith(")") else part + ")")
        if not k:
            raise ValueError(f"a break like 'Break5 of 300 (none within 3600 after as much {ns})' is expected, not {part!r}")
        if k[4] != ns:
            raise ValueError(f"the same kind is expected, not {k[4]} and {ns}")
        breaks.append((k[1], _number(k[2]), _number(k[3])))
    chain = [c.strip() for c in m[3].split(",")] if m[3] else []
    for k in [b[0] for b in breaks] + [ns] + chain:
        if rules is not None and k not in rules.order:
            raise ValueError(f"{k} is not in the rules")
    if not set(chain) <= {b[0] for b in breaks}:
        raise ValueError("a chain is of the breaks kept")
    return ScreenBreaks(breaks, ns, chain, rules=rules)


_PULL = r"(?:, a gap ending inside (\S+) ending at its start)?$"
_GAP = re.compile(r"keep (\S+) at least (\S+) apart" + _PULL)
_GAP_AFTER = re.compile(r"keep (\S+) at least (\S+) after (\S+) or more of (\S+?)" + _PULL)


_EARLY = re.compile(r"keep (\S+) as early as possible(?:, (\S+) each)?$")
_EACH = r"(?:, (\S+) each)?$"
_AT_MOST = re.compile(r"keep (\S+) at most (\S+) apart" + _EACH)
_AT_MOST_AFTER = re.compile(r"keep (\S+) at most (\S+) after (\S+) or more of (\S+?)" + _EACH)


def _constraint(line: str, rules):
    m = _EARLY.match(line)
    if m:
        if rules is not None and m[1] not in rules.order:
            raise ValueError(f"{m[1]} is not in the rules")
        return AsEarlyAsPossible(m[1], None if m[2] is None else _number(m[2]))
    m = _AT_MOST.match(line) or _AT_MOST_AFTER.match(line)
    if m:
        after = m.re is _AT_MOST_AFTER
        for k in [m[1]] + ([m[4]] if after else []):
            if rules is not None and k not in rules.order:
                raise ValueError(f"{k} is not in the rules")
        each = m[5] if after else m[3]
        length = None if each is None else _number(each)
        if after:
            return MaximumGap(m[1], _number(m[2]), after=m[4], at_least=_number(m[3]), length=length)
        return MaximumGap(m[1], _number(m[2]), length=length)
    m = _GAP.match(line) or _GAP_AFTER.match(line)
    if m:
        pull = m[5] if m.re is _GAP_AFTER else m[3]
        kinds = [m[1]] + ([m[4]] if m.re is _GAP_AFTER else []) + ([pull] if pull else [])
        for k in kinds:
            if rules is not None and k not in rules.order:
                raise ValueError(f"{k} is not in the rules")
        if m.re is _GAP_AFTER:
            return MinimumGap(m[1], _number(m[2]), after=m[4], at_least=_number(m[3]), pull=pull)
        return MinimumGap(m[1], _number(m[2]), pull=pull)
    m = _SCREEN.match(line)
    if m and " (none within " in line:
        return _screen_breaks(m, rules)
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
        return Breaks(kind, ns, length, pause)
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
    if text == str(Edited(True)):
        return Edited(True)
    if text == str(Viewed()):
        return Viewed()
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
    return Event(_trigger(m[1]), tuple(_action(a.strip()) for a in re.split("; " + _OUTSIDE_QUOTES, body)))


def parse_script(text: str, previous: Script, rules=None) -> Script:
    lines = text.splitlines()
    first = 0
    if lines and lines[0].startswith("Generated script at time"):
        first = next((i for i, s in enumerate(lines) if not s.strip()), len(lines))
    was = {str(k): k for k in previous.constraints}
    parts, covered, constraints, live, errors = {STEP: []}, [], [], False, []
    on_edit, on_view, period = [], [], STEP
    for i, raw in enumerate(lines[first:], first + 1):
        line = raw.strip()
        if not line or line == "(empty script)":
            continue
        try:
            m = _LOOP.match(line)
            if m:
                period = _number(m[1])
                if period <= 0:
                    raise ValueError("a loop's period is > 0")
                parts.setdefault(period, [])
                continue
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
                e = _event(line)
                part = on_edit if isinstance(e.trigger, Edited) else on_view if isinstance(e.trigger, Viewed) else parts[period]
                name = ON_EDIT if part is on_edit else ON_VIEW if part is on_view else loop_name(period)
                if any(isinstance(a, ChangeAction) and a.target == name for a in e.actions):
                    raise ValueError(f"a script does not change itself: {name}")
                part.append(e)
        except ValueError as e:
            errors.append((i, str(e)))
    if errors:
        raise ScriptError(errors)
    for c in constraints:  # (as the generator binds them: what they read of the rules and of each other)
        if hasattr(c, "bind") and rules is not None:
            c.bind(rules)
        if hasattr(c, "bind_all"):
            c.bind_all(constraints)
    same = list(map(str, constraints)) == list(was)
    kept = [st for st in previous.stretches if same and any(m in (st.mode, None) and a <= st.a and st.b <= b for a, b, m in covered)]
    loops = [Loop(p, tuple(evs)) for p, evs in parts.items() if evs or p == STEP]
    return replace(previous, loops=loops, on_edit=tuple(on_edit), on_view=tuple(on_view), frame=[], stretches=kept, covered=covered,
                   live=live, constraints=tuple(constraints))
