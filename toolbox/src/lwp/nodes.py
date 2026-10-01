"""Node library. N2 — side exit: a branch leaving a strand sideways rises above the strands it would cross.

At a tee or elbow of a strand, a horizontal leg pointing across the corridor is a side exit. When other
strands lie on that side at the same length and height, or the branch it feeds has already been lifted
(a crossing service above the bundle), the leg is turned upward: a riser from the fitting centre to the
exit level, then a horizontal arm at that level to where the branch connects. The branch is lifted with
it inside the corridor; where it meets a vertical run (a drop to sprinklers or equipment) the chain stops
and the drop is recorded as a re-connection to make.
"""

from __future__ import annotations

import copy
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .geometry import Solid, box_from_points, capsule, swept_box
from .package import Element, Package
from .rules import RuleSet
from .section import Section, overlaps

SIDEWAYS = 0.3        # |component| limits for a leg to count as horizontal and across the corridor
BRANCH_MARGIN = 1500  # mm beyond the corridor in which a lifted branch is followed


@dataclass
class Node:
    kind: str                 # "N2"
    strand: str               # strand id
    fitting: str              # fitting key
    side: str                 # "left" / "right"
    s: float                  # mm along the corridor
    location: np.ndarray      # fitting centre after the node (mm)
    rise_mm: float
    reason: str
    branch: list[str] = field(default_factory=list)
    drops: int = 0            # vertical runs where the lifted branch has to be re-connected

    def to_dict(self) -> dict:
        return {"kind": self.kind, "strand": self.strand, "fitting": self.fitting, "side": self.side,
                "s_mm": round(self.s), "rise_mm": round(self.rise_mm), "reason": self.reason,
                "branch": self.branch, "drops": self.drops,
                "location_mm": [round(float(x), 1) for x in self.location]}


def _leg_axis(part: Solid) -> tuple[np.ndarray, np.ndarray]:
    """(connector end, outward unit direction) of a leg built from the connector towards the centre."""
    if part.segment is not None:
        o, end = part.segment
    else:
        o = part.center - part.axes[0] * part.half[0]
        end = part.center + part.axes[0] * part.half[0]
    d = o - end
    n = float(np.linalg.norm(d))
    return o, (d / n if n > 1e-9 else np.array([0.0, 0.0, 1.0]))


def _half_height(part: Solid) -> float:
    if part.segment is not None:
        return part.radius
    return float(np.sum(part.half[1:] * np.abs(part.axes[1:, 2])))


def side_exits(package: Package, elements: dict[str, Element], sec: Section, layout, rules: RuleSet,
               moves: dict[str, np.ndarray], move, graph: dict[str, set[str]]) -> list[Node]:
    cor = sec.corridor
    member = {k for st in sec.strands for k in st.segments + st.fittings}
    placed = {}
    for st in sec.strands:
        p = layout.placements.get(st.id)
        v, z = (p.v, p.z) if p else (st.v, st.z)
        placed[st.id] = (v, z, z + st.height, st)
    clearance = rules.clearance_mm.default
    nodes: list[Node] = []

    for st in sec.strands:
        for fk in st.fittings:
            f = elements.get(fk)
            if f is None or not f.parts or f.leg_conns is None or f.centre is None:
                continue
            c = f.centre
            s_f, v_f = cor.s_of(c), cor.v_of(c)
            parts = list(f.parts)
            extra: list[Solid] = []
            for idx, (part, conn) in enumerate(zip(f.parts, f.leg_conns)):
                o, d = _leg_axis(part)
                if abs(d[2]) > SIDEWAYS or abs(float(np.dot(d, cor.u))) > SIDEWAYS:
                    continue
                branch = [r["key"] for r in conn.get("connected") or [] if r.get("key") and r["key"] not in member]
                if not branch:
                    continue          # joins another strand (e.g. a reducer between two strands): not an exit
                sign = 1.0 if float(np.dot(d, cor.w)) > 0 else -1.0
                hh = _half_height(part)
                leg_lo, leg_hi = c[2] - hh, c[2] + hh
                ahead = [(v, z, top, o2) for v, z, top, o2 in placed.values()
                         if o2 is not st and o2.s_lo - 1 <= s_f <= o2.s_hi + 1 and (v - v_f) * sign > 0]
                blockers = [a for a in ahead if overlaps(a[1], a[2], leg_lo - clearance, leg_hi + clearance)]
                lifted = [k for k in branch if k in moves and moves[k][2] > 1]
                if not blockers and not lifted:
                    continue
                if lifted:
                    b = elements[lifted[0]]
                    zc = float(b.solid.center[2])
                    reason = "支管已抬至横穿层"
                    chain, drops = [], 0
                else:
                    top = max(a[2] for a in ahead)
                    zc = top + clearance + hh
                    reason = f"跨越 {len(blockers)} 条管线"
                    chain, drops = _branch_chain(elements, branch, member | {fk}, moves, graph, cor)
                    dz = zc - c[2]
                    for k in chain:
                        move(k, np.array([0.0, 0.0, dz]))
                if zc - c[2] < 1:
                    continue
                top_c = np.array([c[0], c[1], zc])
                o_top = np.array([o[0], o[1], zc])
                if part.segment is not None:
                    parts[idx] = capsule(c, top_c, part.radius)
                    extra.append(capsule(top_c, o_top, part.radius))
                else:
                    x_axis = part.axes[1]
                    hw, hh2 = float(part.half[1]), float(part.half[2])
                    parts[idx] = swept_box(c, top_c + np.array([0.0, 0.0, hh2]), x_axis, hw, hh2)
                    extra.append(swept_box(top_c, o_top, x_axis, hw, hh2))
                nodes.append(Node("N2", st.id, fk, "right" if sign > 0 else "left", s_f, top_c,
                                  zc - c[2], reason, chain, drops))
            if extra:
                g = copy.copy(f)
                g.parts = parts + extra
                corners = np.vstack([np.vstack(p.aabb()) for p in g.parts])
                g.solid = box_from_points(corners)
                elements[fk] = g
                moves.setdefault(fk, np.zeros(3))
    return nodes


def _branch_chain(elements, start: list[str], stop: set[str], moves, graph, cor) -> tuple[list[str], int]:
    """Horizontal runs and fittings of a branch inside the corridor (plus margin), stopping at vertical runs."""
    seen, chain, drops = set(stop), [], 0
    queue = deque(start)
    while queue:
        k = queue.popleft()
        if k in seen:
            continue
        seen.add(k)
        e = elements.get(k)
        if e is None or e.solid is None or k in moves or not e.is_mep:
            continue
        c = e.solid.center
        if not (cor.s0 - BRANCH_MARGIN <= cor.s_of(c) <= cor.s1 + BRANCH_MARGIN
                and cor.v0 - BRANCH_MARGIN <= cor.v_of(c) <= cor.v1 + BRANCH_MARGIN):
            continue
        if e.origin == "mep_curve":
            r = e.record
            dz = abs((r.get("end") or [0, 0, 0])[2] - (r.get("start") or [0, 0, 0])[2]) * 1000
            if dz > 100:
                drops += 1
                continue
        elif e.kind not in ("fitting", "accessory"):
            drops += 1
            continue
        chain.append(k)
        queue.extend(graph.get(k, ()))
    return chain, drops
