"""Node geometry and buildability of the runs the node library creates (N0 transitions, N1 flips).

The node library builds straight pieces that meet at points. A real run turns through an elbow of a
certain bend radius, which needs a length of straight run on each side (its take-off,
T = R · tan(turn / 2)). Here, at every turn between created pieces (or between a piece and the run it
continues):

  * the elbow is added as geometry — the pieces are cut back by the take-off and the bend is drawn as
    short chords along the arc — so that clash detection and the viewer see it;
  * buildability is checked: between two turns a piece has to hold both take-offs plus a minimum
    straight length. If not, a 45° offset (smaller take-off) is tried; what does not fit either way is
    reported as an unbuildable transition.

Bend radii (rules.layout.construct): pipes R = factor × outside diameter, ducts R = factor × the side in
the plane of the bend, trays a fixed minimum radius (cable bending).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .connect import _piece_solid, _section
from .geometry import Solid, box_from_points
from .package import Element

ANGLE_MIN = math.radians(5)


@dataclass
class Turn:
    at: np.ndarray            # mm
    a: str                    # piece (or run) on one side
    b: str
    angle_deg: float
    radius_mm: float
    takeoff_mm: float


@dataclass
class Issue:
    key: str                  # the piece that is too short
    length_mm: float
    needed_mm: float
    needed_45_mm: float
    fix: str                  # "45°" (a 45° offset fits) or "无" (no)

    def to_dict(self) -> dict:
        return {"piece": self.key, "length_mm": round(self.length_mm), "needed_mm": round(self.needed_mm),
                "needed_45_mm": round(self.needed_45_mm), "fix": self.fix}


@dataclass
class NodeGeometry:
    turns: list[Turn] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"turns": len(self.turns), "unbuildable": [i.to_dict() for i in self.issues if i.fix == "无"],
                "offset": [i.to_dict() for i in self.issues if i.fix.endswith("°")],
                "doubling_back": [i.to_dict() for i in self.issues if i.fix == "折返"]}


def _axis(e: Element):
    r = e.record
    if e.origin != "mep_curve" or not r.get("start") or not r.get("end"):
        return None
    return np.array(r["start"]) * 1000.0, np.array(r["end"]) * 1000.0


def bend_radius(e: Element, sec_, u1: np.ndarray, u2: np.ndarray, construct) -> float:
    kind, hw, hh, xa = sec_
    if e.domain == "tray":
        return max(construct.tray_bend_radius_mm, hw)
    if kind == "round":
        return construct.pipe_bend_factor * 2 * hw
    # Rectangular duct: the side lying in the plane of the bend.
    n = np.cross(u1, u2)
    side = 2 * (hh if abs(n[2]) < 0.5 else hw)    # a vertical turn bends the height, a horizontal one the width
    return construct.duct_bend_factor * side


def elbowize(elements: dict[str, Element], pieces: list[str], construct) -> NodeGeometry:
    """Add elbows at the turns of the created pieces and check that they fit. Mutates `elements`."""
    out = NodeGeometry()
    created = set(pieces)
    cut: dict[str, dict[int, float]] = {}          # piece → end (0 start / 1 end) → length cut back
    arcs: dict[str, list[Solid]] = {}
    done = set()
    for k in pieces:
        e = elements.get(k)
        ax = _axis(e) if e is not None else None
        if ax is None:
            continue
        for end in (0, 1):
            p = ax[end]
            u1 = (ax[1 - end] - p)
            n1 = float(np.linalg.norm(u1))
            if n1 < 1:
                continue
            u1 /= n1
            conn = e.connectors[end] if end < len(e.connectors) else {}
            for ref in conn.get("connected") or []:
                o = elements.get(ref.get("key"))
                oax = _axis(o) if o is not None else None
                if oax is None:
                    continue           # a fitting: the elbow is already there
                pair = frozenset((k, o.key))
                if pair in done:
                    continue
                done.add(pair)
                o_end = 0 if np.linalg.norm(oax[0] - p) < np.linalg.norm(oax[1] - p) else 1
                if float(np.linalg.norm(oax[o_end] - p)) > 5:
                    continue
                u2 = oax[1 - o_end] - p
                n2 = float(np.linalg.norm(u2))
                if n2 < 1:
                    continue
                u2 /= n2
                turn = math.pi - math.acos(float(np.clip(np.dot(u1, u2), -1, 1)))
                if turn < ANGLE_MIN:
                    continue
                if turn > math.radians(170):
                    # The run doubles back on itself: not an elbow, a modelling fault of the transition.
                    out.issues.append(Issue(k, n1, 0.0, 0.0, "折返"))
                    continue
                sec_ = _section(e, conn)
                R = bend_radius(e, sec_, -u1, u2, construct)
                T = R * math.tan(turn / 2)
                out.turns.append(Turn(p, k, o.key, math.degrees(turn), R, T))
                cut.setdefault(k, {})[end] = T
                if o.key in created:
                    cut.setdefault(o.key, {})[o_end] = T
                # The bend: chords along a quadratic arc from the cut-back point on one leg to the other.
                a_pt, b_pt = p + u1 * min(T, n1 / 2), p + u2 * min(T, n2 / 2)
                pts = [(1 - t) ** 2 * a_pt + 2 * t * (1 - t) * p + t ** 2 * b_pt for t in (0.0, 0.5, 1.0)]
                arcs.setdefault(k, []).extend(_piece_solid(sec_, pts[i], pts[i + 1], None) for i in range(2))

    # Buildability and geometry of each piece.
    for k in pieces:
        e = elements.get(k)
        ax = _axis(e) if e is not None else None
        if ax is None:
            continue
        c = cut.get(k, {})
        L = float(np.linalg.norm(ax[1] - ax[0]))
        if len(c) == 2:
            need = c[0] + c[1] + construct.min_straight_mm
            if L + 1 < need:
                # Too short for two elbows of this turn: the steepest standard offset that fits instead
                # (the piece becomes a sloped run of length L / sin θ).
                R = max(c.values())
                need45 = 2 * R * math.tan(math.radians(22.5)) + construct.min_straight_mm
                ang = offset_angle(L, R, construct.min_straight_mm)
                out.issues.append(Issue(k, L, need, need45, f"{ang:g}°" if ang is not None and ang < 90 else "无"))
        if not c and k not in arcs:
            continue
        u = (ax[1] - ax[0]) / max(L, 1e-9)
        a = ax[0] + u * min(c.get(0, 0.0), L / 2)
        b = ax[1] - u * min(c.get(1, 0.0), L / 2)
        sec_ = _section(e, e.connectors[0] if e.connectors else {})
        parts = ([_piece_solid(sec_, a, b, None)] if float(np.linalg.norm(b - a)) > 1 else []) + arcs.get(k, [])
        if not parts:
            continue
        e.parts = parts
        e.solid = box_from_points(np.vstack([np.vstack(p_.aabb()) for p_ in parts]))
    return out


OFFSET_ANGLES = (90.0, 60.0, 45.0, 30.0, 22.5, 15.0)


def offset_angle(rise: float, radius: float, min_straight: float) -> float | None:
    """Steepest standard offset angle (deg) whose two elbows fit a change of level `rise`: the sloped piece
    rise / sin θ must hold both take-offs R·tan(θ/2) and the minimum straight. None: none fits."""
    rise = abs(rise)
    if rise < 1:
        return 90.0
    for a in OFFSET_ANGLES:
        th = math.radians(a)
        if rise / math.sin(th) + 1 >= 2 * radius * math.tan(th / 2) + min_straight:
            return a
    return None


def riser_radius(e: Element, sec_, construct) -> float:
    """Bend radius of a vertical turn of this run."""
    return bend_radius(e, sec_, np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]), construct)
