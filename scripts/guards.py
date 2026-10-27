"""What a generated script may never do, checked on the result of each of its edits:

N1  change the left side of the chip or what is at the chip's position: the line and
    the claims on (-inf, chip] are unchanged;
N2  remove (or change) an element added by the user: the user's claims (their
    removals included) are unchanged. An edit of the script never makes one give way
    (resolve.py refuses it), and the user's edits always come before the script's.

The script also carries out the mode rules (script.ModeRulesAction, mode_rules.py).
Each of their edits acts at the chip's position x only, so N1 becomes:

M1  the line and the claims on (-inf, x) are unchanged, and the claims outside {x}
    are unchanged (no older element is removed away from the chip);

and N2 holds as for any edit of the script: edits from the user always win.

The element rules hold in every state by construction (resolve.py), so an edit can
never leave a rule unapplied.
"""

from __future__ import annotations

from intervals import INF, Interval
from line import USER, Claims, LineState
from region import Region


def left_side(chip, closed: bool = True) -> Region:
    return Region.of(Interval(-INF, chip, False, closed))


def claims_on(c: Claims, region: Region) -> dict:
    out = {}
    for k, d in c.pos.items():
        for key, r in d.items():
            if r & region:
                out[("pos", k, key)] = r & region
    for store, d0 in (("neg", c.neg), ("off", c.taken)):
        for k, d in d0.items():
            for key, r in d.items():
                if r & region:
                    out[(store, k, key)] = r & region
    return out


def user_claims(c: Claims) -> dict:
    out = {}
    for k, d in c.pos.items():
        for (v, o), r in d.items():
            if o == USER and r:
                out[("pos", k, v)] = r
    for store, d0 in (("neg", c.neg), ("off", c.taken)):
        for k, d in d0.items():
            if d.get(USER):
                out[(store, k)] = d[USER]
    return out


def same_dict(a: dict, b: dict) -> bool:
    return set(a) == set(b) and all(a[k] == b[k] for k in a)


def check_script_edit(before: LineState, after: LineState, chip) -> str | None:
    """None if the edit respects N1 and N2, else the reason."""
    left = left_side(chip)
    if not before.line.same_on(after.line, left) or not same_dict(claims_on(before.claims, left), claims_on(after.claims, left)):
        return f"N1: would change the line at or left of the chip ({chip})"
    if not same_dict(user_claims(before.claims), user_claims(after.claims)):
        return "N2: would remove or change an element added by the user"
    return None


def check_mode_edit(before: LineState, after: LineState, at: Region, chip) -> str | None:
    """None if an edit of the mode rules on `at` (the chip's position {chip}, or the
    positions a continuous change goes through from chip on) respects M1 and N2, else
    the reason."""
    left = left_side(chip, closed=False)
    if not before.line.same_on(after.line, left) or not same_dict(claims_on(before.claims, left), claims_on(after.claims, left)):
        return f"M1: would change the line left of the chip ({chip})"
    away = at.complement()
    if not same_dict(claims_on(before.claims, away), claims_on(after.claims, away)):
        return "M1: would change elements away from the chip"
    if not same_dict(user_claims(before.claims), user_claims(after.claims)):
        return "N2: would remove or change an element added by the user"
    return None
