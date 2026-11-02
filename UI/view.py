"""What the window shows, kept from one frame to the next.

`items` lists what is to be drawn around the chip, a little further than the window shows.
`Retained` keeps these items on the canvas, and for each point of each item how fast it
goes with the chip: 0 for a point of the line, 1 for one that the chip drags, 1/2 for the
middle of a band it drags. These rates come from two lists, at p and a little further:
nothing here knows what the items are. So a frame moves what stays on the line with one
`canvas.move`, and sets the few items that change shape: that is s_f, the script's `frame`
(`Retained.frame`), which `Retained.move` executes. The list is built again only when what
it shows can change (App.draw): the zoom, the line, the script, or the chip past what is
listed.
"""

from __future__ import annotations

from fractions import Fraction as F

from script import Place

COLORS = ["#2e86de", "#10ac84", "#ee5253", "#ff9f43", "#5f27cd", "#01a3a4", "#c44569", "#8395a7", "#576574", "#b33939"]
MODE_COLORS = ["#dfe6e9", "#ffeaa7", "#c7ecee", "#fab1a0", "#d6a2e8", "#badc58"]
OWNERS = {"user": ("#2d3436", "you"), "generated": ("#e17055", "the generated script"), "mode": ("#8e44ad", "the mode rules")}
RIGHT, TOP = 20, 34
MODE_H, ROW_H, BAR_H = 20, 30, 16
CAPTION = ("TkDefaultFont", 10, "bold")
WORLD, TOPMOST, FIXED = "world", "topmost", "fixed"


def num(x) -> str:
    x = F(x)
    return str(x) if x.denominator <= 8 else f"{float(x):.2f}"


def tick(span: F) -> F:
    """A round distance between the axis labels: about 12 of them."""
    for t in (F(1, 4), F(1, 2)):
        if span / t <= 14:
            return t
    power = F(1)
    while True:
        for m in (1, 2, 5):
            if span / (m * power) <= 14:
                return m * power
        power *= 10


def blend(color: str, amount: float) -> str:
    """The color at this strength (1: itself, toward white below)."""
    k = 0.25 + 0.75 * amount
    r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(255 - (255 - v) * k) for v in (r, g, b))


def _ivs(reg, a, b):
    """The (lo, hi) real parts of the intervals of reg, cut to [a, b] (the graph)."""
    return [(max(iv.lo.x, a), min(iv.hi.x, b)) for iv in reg.on(a - 1, b + 1).intervals if iv.lo.x <= b and iv.hi.x >= a]


def bottom_of(kinds: list) -> int:
    return TOP + MODE_H + 8 + ROW_H * len(kinds)


def items(sn: dict, line, claims, x, left, span, a, b, scale: float, width: int) -> dict:
    """{key: (shape, [(position or None, pixels, y)], options)}: a point is at that position
    plus these pixels, or (None) at these pixels. On [a, b] of the line; the chip at x."""
    kinds = sn["kinds"]
    right, bottom = left + span, bottom_of(kinds)
    out = {}

    def bar(key, lo, hi, y1, y2, fill):
        if float(hi - lo) * scale < 2:  # a point, or almost
            out[key] = ("rectangle", [(lo, -1, y1), (lo, 1, y2)], {"fill": fill, "outline": ""})
        else:
            out[key] = ("rectangle", [(lo, 0, y1), (hi, 0, y2)], {"fill": fill, "outline": ""})

    # the modes: revealed up to the chip, unknown after
    names = sorted({m for _, _, m in sn["modes"]})
    for i, (lo, hi, m) in enumerate(sn["modes"]):
        if i == len(sn["modes"]) - 1:  # the mode at the chip goes on up to where the frame shows it
            hi = max(hi, x)
        lo, hi = max(lo, a), min(hi, b)
        if lo < hi:
            out["mode", lo if i else "first"] = ("rectangle", [(lo, 0, TOP), (hi, 0, TOP + MODE_H)],
                                                 {"fill": MODE_COLORS[names.index(m) % len(MODE_COLORS)], "outline": "#b2bec3"})
            if float(hi - lo) * scale > 14:
                out["mode text", lo if i else "first"] = ("text", [((lo + hi) / 2, 0, TOP + MODE_H / 2)], {"text": m})
    if x < right:
        first = max(x, left)
        out["hidden"] = ("rectangle", [(first, 0, TOP), (right, 0, TOP + MODE_H)], {"fill": "#f5f6fa", "outline": "#dcdde1", "stipple": "gray25"})
        out["hidden text"] = ("text", [((first + right) / 2, 0, TOP + MODE_H / 2)], {"text": "not revealed yet", "fill": "#7f8fa6"})
    # one row per kind: its percentages, and under them who placed it
    for i, k in enumerate(kinds):
        y = TOP + MODE_H + 8 + i * ROW_H
        color = COLORS[i % len(COLORS)]
        for v, reg in line.vals.get(k, {}).items():
            fill = blend(color, float(v) / 100)
            for n, (lo, hi) in enumerate(_ivs(reg, a, b)):
                bar(("bar", k, v, n), lo, hi, y, y + BAR_H, fill)
        for (v, owner), reg in claims.pos.get(k, {}).items():
            for n, (lo, hi) in enumerate(_ivs(reg, a, b)):
                bar(("owner", k, v, owner, n), lo, hi, y + BAR_H + 2, y + BAR_H + 6, OWNERS.get(owner, ("#636e72", owner))[0])
        for name, d in (("no", claims.neg.get(k, {})), ("taken", claims.taken.get(k, {}))):
            for owner, reg in d.items():
                for n, (lo, hi) in enumerate(_ivs(reg, a, b)):
                    wide = float(hi - lo) * scale >= 2
                    out[name, k, owner, n] = ("line", [(lo, 0, y + BAR_H + 4), (hi if wide else lo, 0 if wide else 2, y + BAR_H + 4)],
                                              {"fill": "#d63031", "width": 2, "dash": (3, 2)})
    # the axis
    step = tick(span)
    p = (a // step + 1) * step
    while p < b:
        out["tick", p] = ("line", [(p, 0, bottom), (p, 0, bottom + 5)], {"fill": "#636e72"})
        out["tick text", p] = ("text", [(p, 0, bottom + 14)], {"text": num(p), "fill": "#636e72"})
        p += step
    # where the generator ran, and where it runs again
    for p in sn["past"]:
        if a <= p <= b:
            out["past", p] = ("polygon", [(p, -5, bottom + 24), (p, 5, bottom + 24), (p, 0, bottom + 17)], {"fill": "#0984e3"})
    for p in sn["reruns"]:
        if a <= p <= b:
            out["rerun", p] = ("line", [(p, 0, TOP - 4), (p, 0, bottom)], {"fill": "#0984e3", "dash": (6, 4), "width": 2})
            out["rerun text", p] = ("text", [(p, 4, TOP - 12)], {"text": "generator runs again", "anchor": "w", "fill": "#0984e3"})
        elif p > b:
            out["rerun far", p] = ("text", [(None, width - RIGHT, TOP - 12)],
                                   {"text": f"generator runs again at {num(p)} →", "anchor": "e", "fill": "#0984e3"})
    # the chip
    if left <= x <= right:
        out["chip"] = ("line", [(x, 0, TOP - 2), (x, 0, bottom)], {"fill": "#d63031", "width": 2})
        out["chip top"] = ("polygon", [(x, -7, TOP - 16), (x, 7, TOP - 16), (x, 0, TOP - 4)], {"fill": "#d63031"})
    return out


def fixed_items(c, kinds: list, edge: int, width: int, height: int) -> None:
    """What does not move: over the line, the captions and what hides the line's ends."""
    c.delete(FIXED)
    bottom = bottom_of(kinds)
    tag = (FIXED,)
    for x1, x2 in ((0, edge), (width - RIGHT, width + 400)):
        c.create_rectangle(x1, 0, x2, bottom + 26, fill="white", outline="", tags=tag)
    c.create_text(edge - 6, TOP + MODE_H / 2, text="modes", anchor="e", tags=tag)
    for i, k in enumerate(kinds):
        y = TOP + MODE_H + 8 + i * ROW_H
        c.create_text(edge - 6, y + BAR_H / 2, text=k, anchor="e", font=CAPTION, tags=tag)
    c.create_line(edge, bottom, width - RIGHT, bottom, fill="#636e72", tags=tag)
    y = bottom + 40
    c.create_text(edge, y, anchor="w", fill="#2d3436", text="bar: lighter = lower percentage   under the bar, who placed it:", tags=tag)
    xl = edge + 390
    for color, who in OWNERS.values():
        c.create_rectangle(xl, y - 3, xl + 18, y + 3, fill=color, outline="", tags=tag)
        c.create_text(xl + 22, y, anchor="w", text=who, tags=tag)
        xl += 30 + 7 * len(who)
    c.create_line(xl, y, xl + 18, y, fill="#d63031", width=2, dash=(3, 2), tags=tag)
    c.create_text(xl + 22, y, anchor="w", text='"no K" or removed', tags=tag)
    c.create_polygon(edge, y + 22, edge + 10, y + 22, edge + 5, y + 15, fill="#0984e3", tags=tag)
    c.create_text(edge + 14, y + 18, anchor="w", text="the generator ran here      dashed blue: where it runs again      red: the chip", tags=tag)


class Retained:
    """The items on the canvas, and how they go with the chip."""

    def __init__(self, canvas) -> None:
        self.c = canvas
        self.ids: dict = {}  # key -> [id, shape, coords, options, world]
        self.reshaped: list = []  # Place: the items that change shape
        self.p0, self.scale, self.moved = F(0), 0.0, 0.0

    def sync(self, now: dict, later: dict | None, delta, p, X, scale: float) -> None:
        """The canvas shows `now`, with the chip at p; `later` is the list delta further
        (None: nothing goes with the chip until the next one)."""
        c = self.c
        self.p0, self.scale, self.moved = p, scale, 0.0
        self.reshaped = []
        created = False
        for key in [k for k in self.ids if k not in now]:
            c.delete(self.ids.pop(key)[0])
        for key, (shape, points, options) in now.items():
            then = None if later is None else later.get(key)
            if then is not None and len(then[1]) != len(points):
                then = None
            coords, rates = [], []
            for i, (at, px, y) in enumerate(points):
                if at is None:
                    coords += [px, y]
                    rates.append(1.0)
                else:
                    coords += [X(at) + px, y]
                    rates.append(0.0 if then is None or then[1][i][0] is None else float((then[1][i][0] - at) / delta))
            world = all(r == 0 for r in rates)
            mine = self.ids.get(key)
            if mine is None or mine[1] != shape:
                if mine is not None:
                    c.delete(mine[0])
                tags = (WORLD,) if world else ()
                make = getattr(c, f"create_{shape}")
                self.ids[key] = [make(*coords, tags=tags + ((TOPMOST,) if key in ("chip", "chip top") else ()), **options), shape, coords, options, world]
                created = True
            else:
                if mine[2] != coords:
                    c.coords(mine[0], *coords)
                    mine[2] = coords
                if mine[3] != options:
                    c.itemconfigure(mine[0], **options)
                    mine[3] = options
                if mine[4] != world:
                    (c.addtag_withtag if world else c.dtag)(*((WORLD, mine[0]) if world else (mine[0], WORLD)))
                    mine[4] = world
            if not world and any(r != 1 for r in rates):
                self.reshaped.append(Place(self.ids[key][0], shape, tuple(coords), tuple(rates)))
        if created:
            c.tag_raise(TOPMOST)
            c.tag_raise(FIXED)

    def frame(self) -> list:
        """s_f for what is on the canvas."""
        return [Place(WORLD, "line")] + self.reshaped

    def move(self, p, frame: list) -> None:
        """The chip at p: s_f, the view going with it."""
        d = float(p - self.p0)
        if d == self.moved:
            return
        for place in frame:
            place.run(self.c, self.scale, d, self.moved)
        self.moved = d
