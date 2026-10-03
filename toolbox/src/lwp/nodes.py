"""Node library. N2 — side exit: a branch leaving a strand sideways is routed out of the bundle.

At a tee or elbow of a strand, a horizontal leg pointing across the corridor is a side exit. The leg is
routed in the cross-section through the fitting: from the fitting centre, optionally sideways at the
strand's level, then up or down to an exit level, then out past every strand, crossing service and beam
on that side — each kept at its clearance. Exit levels tried: the strand's own level, above or below every
obstacle (over the top of the bundle, or through the gap between two layers). The route with the fewest
bends wins, then the smallest change of level.

The branch follows: its horizontal runs and fittings inside the corridor (plus a margin) are moved to
the exit level; where it meets a vertical run the chain stops, and the re-connection (a stretched drop
or a jog) is made by `connect.reconnect`.
"""

from __future__ import annotations

import copy
import math
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .geometry import Solid, box_from_points, capsule, swept_box
from .package import Element, Package
from .rules import RuleSet
from .section import Section

SIDEWAYS = 0.3        # |component| limits for a leg to count as horizontal and across the corridor
BRANCH_MARGIN = 1500  # mm beyond the corridor in which a moved branch is followed


@dataclass
class Node:
    kind: str                 # "N2"
    strand: str               # strand id
    fitting: str              # fitting key
    side: str                 # "left" / "right"
    s: float                  # mm along the corridor
    location: np.ndarray      # top of the riser (mm)
    rise_mm: float
    reason: str
    branch: list[str] = field(default_factory=list)
    drops: int = 0            # vertical runs where the moved branch has to be re-connected
    routed: bool = True       # False: no clear route found, a plain riser over everything was used
    bends: int = 0

    def to_dict(self) -> dict:
        return {"kind": self.kind, "strand": self.strand, "fitting": self.fitting, "side": self.side,
                "s_mm": round(self.s), "rise_mm": round(self.rise_mm), "reason": self.reason,
                "branch": self.branch, "drops": self.drops, "routed": self.routed, "bends": self.bends,
                "location_mm": [round(float(x), 1) for x in self.location]}


@dataclass
class Rect:
    """An obstacle in the cross-section plane (v across the corridor, z up), mm."""
    v_lo: float
    v_hi: float
    z_lo: float
    z_hi: float
    group: str
    beam: bool = False


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


def _half_along(part: Solid, u: np.ndarray) -> float:
    if part.segment is not None:
        return part.radius
    return float(np.sum(part.half[1:] * np.abs(part.axes[1:] @ u)))


def section_obstacles(sec: Section, layout, s: float, half: float, own: str, skip: set[str],
                      crossing_z: dict[str, float]) -> list[Rect]:
    """Strands (at their planned place), crossing services and beams in the section plane at s ± half."""
    rects = []
    for st in sec.strands:
        if st.id == own or not (st.s_lo - half <= s <= st.s_hi + half):
            continue
        p = layout.placements.get(st.id)
        v, z = (p.v, p.z) if p else (st.v, st.z)
        rects.append(Rect(v - st.width / 2, v + st.width / 2, z, z + st.height, st.group))
    for c in sec.crossings:
        if c.key in skip or abs(c.s - s) > half + c.height / 2 + 50:
            continue
        z = crossing_z.get(c.key, c.z_lo)
        rects.append(Rect(c.v_lo, c.v_hi, z, z + c.height, c.group))
    for b in sec.beams:
        if b.s_lo - half <= s <= b.s_hi + half:
            rects.append(Rect(b.v_lo, b.v_hi, b.bottom, math.inf, "structure", beam=True))
    return rects


def route(rects: list[Rect], v_f: float, z_c: float, hh: float, sign: float, v_out: float, clr,
          z_min: float, z_max: float, z_fixed: float | None = None) -> tuple[float, float, int] | None:
    """Best (v_a, z_e, bends): sideways at z_c to v_a, vertical to z_e, then out at z_e past v_out.

    clr(group) → clearance to an obstacle of that group. None when no route is clear.
    """
    ahead = [r for r in rects if not r.beam and ((r.v_lo + r.v_hi) / 2 - v_f) * sign > 0]
    v_end = v_out
    for r in ahead:
        far = r.v_hi if sign > 0 else r.v_lo
        v_end = max(v_end, far) if sign > 0 else min(v_end, far)
    v_end += sign * (hh + 50)

    def clear_box(v0, v1, z0, z1) -> bool:
        lo_v, hi_v = min(v0, v1), max(v0, v1)
        for r in rects:
            c = clr(r.group)
            if r.v_lo < hi_v + c and lo_v < r.v_hi + c and r.z_lo < z1 + c and z0 < r.z_hi + c:
                return False
        return True

    def ok(v_a: float, z_e: float) -> bool:
        if abs(v_a - v_f) > 1:
            start = v_f + sign * hh           # the fitting's own body is not an obstacle
            if not clear_box(start, v_a + sign * hh, z_c - hh, z_c + hh):
                return False
        if abs(z_e - z_c) > 1 and not clear_box(v_a - hh, v_a + hh, min(z_c, z_e) - hh, max(z_c, z_e) + hh):
            return False
        return clear_box(v_a, v_end, z_e - hh, z_e + hh)

    v_cands = {v_f}
    for r in ahead:
        for edge in (r.v_lo, r.v_hi):
            c = clr(r.group) + hh + 1
            for v in (edge - c, edge + c):
                if (v - v_f) * sign > 0 and (v_end - v) * sign > 0:
                    v_cands.add(v)
    if z_fixed is not None:
        z_cands = {z_fixed}
    else:
        z_cands = {z_c}
        for r in rects:
            c = clr(r.group) + hh + 1
            z_cands.add(r.z_lo - c)
            if math.isfinite(r.z_hi):
                z_cands.add(r.z_hi + c)
        z_cands = {z for z in z_cands if z_min <= z <= z_max}
    best = None
    for z_e in z_cands:
        for v_a in v_cands:
            side, up = abs(v_a - v_f) > 1, abs(z_e - z_c) > 1
            bends = (2 if up else 0) + (1 if side and up else 0)
            # Going under a neighbour costs headroom: prefer rising over it.
            cost = 1000 * bends + abs(z_e - z_c) * (3 if z_e < z_c else 1) + 0.2 * abs(v_a - v_f)
            if best is not None and cost >= best[0]:
                continue
            if ok(v_a, z_e):
                best = (cost, v_a, z_e, bends)
    return None if best is None else best[1:]


def side_exits(package: Package, elements: dict[str, Element], sec: Section, layout, rules: RuleSet,
               moves: dict[str, np.ndarray], move, graph: dict[str, set[str]]) -> list[Node]:
    cor = sec.corridor
    member = {k for st in sec.strands for k in st.segments + st.fittings}
    crossing_keys = {c.key for c in sec.crossings}
    crossing_z = dict(layout.crossings or {})
    for c in sec.crossings:
        if c.key in moves and c.key not in crossing_z:
            crossing_z[c.key] = c.z_lo + float(moves[c.key][2])
    # Routes may pass under neighbours, but never below the bundle's lowest bottom (no clear height lost).
    bottoms = [layout.placements[st.id].z if st.id in layout.placements else st.z for st in sec.strands]
    z_min = max(sec.floor_z + rules.headroom.min_clear_mm, min(bottoms, default=-math.inf))
    z_max = sec.ceiling_max if math.isfinite(sec.ceiling_max) else sec.ceiling
    nodes: list[Node] = []

    def clr_for(group: str):
        return lambda g: rules.clearance_mm.required(group, g)[0]

    for st in sec.strands:
        for fk in st.fittings:
            f = elements.get(fk)
            orig = package.elements.get(fk)
            if f is None or orig is None or not f.parts or f.leg_conns is None or f.centre is None:
                continue
            c = f.centre
            s_f, v_f = cor.s_of(c), cor.v_of(c)
            parts = list(f.parts)
            extra: list[Solid] = []
            new_origin: dict[int, np.ndarray] = {}
            orig_conns = {cc.get("id", i): cc for i, cc in enumerate(orig.connectors)}
            for idx, (part, conn) in enumerate(zip(f.parts, f.leg_conns)):
                o, d = _leg_axis(part)
                if abs(d[2]) > SIDEWAYS or abs(float(np.dot(d, cor.u))) > SIDEWAYS:
                    continue
                branch = [r["key"] for r in conn.get("connected") or [] if r.get("key") and r["key"] not in member]
                if not branch:
                    continue          # joins another strand (e.g. a reducer between two strands): not an exit
                oc = orig_conns.get(conn.get("id"))
                z_b = float(oc["origin"][2]) * 1000 if oc and oc.get("origin") else float(c[2])
                sign = 1.0 if float(np.dot(d, cor.w)) > 0 else -1.0
                hh = _half_height(part)
                # A branch that is itself a crossing service (or already moved) keeps the level the solver gave it.
                lifted = [k for k in branch if k in crossing_keys or (k in moves and abs(moves[k][2]) > 1)]
                z_fixed = _axis_z(elements[lifted[0]], z_b + float(moves.get(lifted[0], np.zeros(3))[2])) if lifted else None
                chain, drops = (([], 0) if lifted else
                                _branch_chain(elements, branch, member | crossing_keys | {fk}, moves, graph, cor))
                skip = set(chain) | set(branch)
                rects = section_obstacles(sec, layout, s_f, _half_along(part, cor.u), st.id, skip, crossing_z)
                v_o = cor.v_of(o)
                found = route(rects, v_f, float(c[2]), hh, sign, v_o, clr_for(st.group), z_min + hh, z_max - hh, z_fixed)
                routed = found is not None
                if found is None:
                    # No clear route: rise over everything on that side (reported as unresolved).
                    tops = [r.z_hi for r in rects if not r.beam and ((r.v_lo + r.v_hi) / 2 - v_f) * sign > 0]
                    z_e = z_fixed if z_fixed is not None else max(tops + [float(c[2])]) + rules.clearance_mm.default + hh
                    found = (v_f, z_e, 2)
                v_a, z_e, bends = found
                if not lifted and abs(z_e - z_b) > 1:
                    for k in chain:
                        move(k, np.array([0.0, 0.0, z_e - z_b]))
                if abs(z_e - c[2]) < 1 and abs(v_a - v_f) < 1:
                    continue          # straight out at the strand's level: the branch just follows
                # Leg geometry: centre → (v_a, z_c) → (v_a, z_e) → out to the connector's place at z_e.
                dh = np.array([d[0], d[1], 0.0])
                dh /= max(float(np.linalg.norm(dh)), 1e-9)
                k_w = max(abs(float(np.dot(dh, cor.w))), 1e-6)
                leg_len = abs(v_o - v_f) / k_w
                a_len = abs(v_a - v_f) / k_w
                pts = [c.copy()]
                if a_len > 1:
                    pts.append(c + dh * a_len)
                top = pts[-1].copy()
                top[2] = z_e
                if abs(z_e - c[2]) > 1:
                    pts.append(top)
                if a_len + 1 < leg_len:
                    end = c + dh * leg_len
                    end[2] = z_e
                    pts.append(end)
                q = pts[-1]
                solids = []
                for p0, p1 in zip(pts, pts[1:]):
                    if part.segment is not None:
                        solids.append(capsule(p0, p1, part.radius))
                    else:
                        solids.append(swept_box(p0, p1, part.axes[1], float(part.half[1]), float(part.half[2])))
                if not solids:
                    continue
                parts[idx] = solids[0]
                extra.extend(solids[1:])
                new_origin[id(conn)] = q
                reason = ("支管为横穿管，按其标高引出" if lifted else
                          "经层间空档引出" if routed and any(z_e < r.z_lo for r in rects if not r.beam) and z_e > c[2] else
                          "侧移后引出" if routed and abs(v_a - v_f) > 1 else
                          "升至管线上方引出" if routed else "无净空路由，暂按直升处理")
                nodes.append(Node("N2", st.id, fk, "right" if sign > 0 else "left", s_f, top,
                                  z_e - c[2], reason, chain, drops, routed, bends))
            if new_origin:
                g = copy.copy(f)
                g.parts = parts + extra
                corners = np.vstack([np.vstack(p.aabb()) for p in g.parts])
                g.solid = box_from_points(corners)
                g.connectors = [{**cc, "origin": list(new_origin[id(cc)] / 1000)} if id(cc) in new_origin else cc
                                for cc in f.connectors]
                g.leg_conns = [next((n for o_, n in zip(f.connectors, g.connectors) if o_ is cc), cc) for cc in f.leg_conns]
                elements[fk] = g
                moves.setdefault(fk, np.zeros(3))
    return nodes


def _axis_z(e: Element, default: float) -> float:
    r = e.record
    if e.origin == "mep_curve" and r.get("start") and r.get("end"):
        return (r["start"][2] + r["end"][2]) * 500
    return default


def _branch_chain(elements, start: list[str], stop: set[str], moves, graph, cor) -> tuple[list[str], int]:
    """Horizontal runs and fittings of a branch reaching into the corridor (plus margin), stopping at
    vertical runs."""
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
        lo, hi = e.solid.aabb()
        s_lo, s_hi = sorted((cor.s_of(lo), cor.s_of(hi)))
        v_lo, v_hi = sorted((cor.v_of(lo), cor.v_of(hi)))
        if s_hi < cor.s0 - BRANCH_MARGIN or s_lo > cor.s1 + BRANCH_MARGIN \
                or v_hi < cor.v0 - BRANCH_MARGIN or v_lo > cor.v1 + BRANCH_MARGIN:
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


# ---------------------------------------------------------------------------------------------------------
# N3 — end transition: where a moved strand continues beyond the corridor, its continuation (elbows,
# couplings and horizontal runs) is carried along with the same offset as far as needed, so that the
# transition back to the original route is made where it is clear, not in the crowd at the corridor end.

END_REACH = 6000.0   # mm beyond the corridor end a continuation may be carried
END_DEPTH = 6        # elements of a continuation that may be carried


@dataclass
class EndTransition:
    strand: str
    end: str                  # "start" / "end" of the corridor
    carried: list[str]
    hits_before: int
    hits_after: int

    def to_dict(self) -> dict:
        return {"strand": self.strand, "end": self.end, "carried": self.carried,
                "hits_before": self.hits_before, "hits_after": self.hits_after}


def _continuation(elements, start: str, member: set[str], cor, beyond, graph) -> list[str]:
    """Elements following `start` away from the corridor: 2-connector fittings and horizontal runs."""
    chain, seen, k = [], set(member), start
    while k is not None and len(chain) < END_DEPTH:
        seen.add(k)
        e = elements.get(k)
        if e is None or e.solid is None or not e.is_mep:
            break
        c = e.solid.center
        if not beyond(cor.s_of(c)) or abs(cor.s_of(c) - (cor.s0 if beyond(cor.s0 - 1) else cor.s1)) > END_REACH:
            break
        if e.origin == "mep_curve":
            r = e.record
            if abs((r["end"][2] - r["start"][2]) * 1000) > 100:
                break          # a vertical run: the transition happens here anyway
        elif len([cc for cc in e.connectors if cc.get("connected")]) > 2:
            break              # a tee: its other branches stay; transition before it
        chain.append(k)
        nxt = [n for n in graph.get(k, ()) if n not in seen]
        k = nxt[0] if len(nxt) == 1 else None
    return chain


def _tree_layers(elements, start: str, stop: set[str], cor, beyond, graph, moves) -> list[list[str]]:
    """What follows `start` away from the corridor, layer by layer (tees with all their branches); vertical runs,
    elements beyond reach or already moved end it."""
    end_s = cor.s0 if beyond(cor.s0 - 1) else cor.s1
    layers, seen, frontier = [], set(stop), [start]
    while frontier and len(layers) < END_DEPTH:
        layer = []
        for k in frontier:
            if k in seen:
                continue
            seen.add(k)
            e = elements.get(k)
            if e is None or e.solid is None or not e.is_mep or k in moves:
                continue
            c = e.solid.center
            if not beyond(cor.s_of(c)) or abs(cor.s_of(c) - end_s) > END_REACH:
                continue
            if e.origin == "mep_curve" and abs((e.record["end"][2] - e.record["start"][2]) * 1000) > 100:
                continue
            layer.append(k)
        if not layer:
            break
        layers.append(layer)
        frontier = [n for k in layer for n in graph.get(k, ()) if n not in seen]
    return layers


def end_transitions(package: Package, elements: dict[str, Element], sec: Section, moves: dict[str, np.ndarray],
                    move, graph: dict[str, set[str]]) -> list[EndTransition]:
    from .connect import _hits, _piece_solid, _section

    cor = sec.corridor
    member = {k for st in sec.strands for k in st.segments + st.fittings}
    near = [e for e in elements.values() if e.solid is not None]
    boxes = np.array([np.concatenate(e.solid.aabb()) for e in near])
    out: list[EndTransition] = []

    def jog_hits(a: Element, b: Element, t: np.ndarray, skip: set[str]) -> int:
        """Clashes of the simplest jog between a (moved by t) and b (in place) at their joint."""
        for c in a.connectors:
            if any(r.get("key") == b.key for r in c.get("connected") or []) and c.get("origin"):
                p = np.array(c["origin"]) * 1000
                q = p + t
                sec_ = _section(a, c)
                dz = np.array([0.0, 0.0, t[2]])
                best = None
                for pts in ([q, q - dz, p], [q, q - (t - dz), p]):
                    pts = [x for i, x in enumerate(pts) if i == 0 or np.linalg.norm(x - pts[i - 1]) > 1]
                    if len(pts) < 2:
                        return 0
                    h = _hits([_piece_solid(sec_, pts[i], pts[i + 1], None) for i in range(len(pts) - 1)], near, boxes, skip)
                    best = h if best is None else min(best, h)
                return best or 0
        return 0

    for st in sec.strands:
        keys = st.segments + st.fittings
        t = next((moves[k] for k in keys if k in moves and np.linalg.norm(moves[k]) > 0.5), None)
        if t is None:
            continue
        for side, beyond in (("start", lambda s: s < cor.s0), ("end", lambda s: s > cor.s1)):
            # Joints of the strand to elements beyond this end of the corridor.
            for k in keys:
                for n in graph.get(k, ()):
                    if n in member or n in moves:
                        continue
                    e = elements.get(n)
                    if e is None or e.solid is None or not beyond(cor.s_of(e.solid.center)):
                        continue
                    layers = _tree_layers(elements, n, member | {k}, cor, beyond, graph, moves)
                    if not layers:
                        continue
                    every = {x for layer in layers for x in layer}
                    skip = set(keys) | every
                    from .plan import translate
                    # Candidate: carry the first d layers of what follows (d = 0: transition at the corridor
                    # end). Tees go with their branches; each branch end left behind gets a transition.
                    best = None
                    for d in range(0, len(layers) + 1):
                        carried = [x for layer in layers[:d] for x in layer]
                        moved_hits = 0
                        for x in carried:
                            el = elements[x]
                            moved_hits += _hits([translate(p_, t) for p_ in (el.parts or [el.solid])], near, boxes, skip)
                        jh = 0
                        frontier = [(k, n)] if not d else [
                            (x, y) for x in carried for y in graph.get(x, ())
                            if y not in carried and y not in member and y != k and y not in moves]
                        for x, y in frontier:
                            if elements.get(y) is not None:
                                jh += jog_hits(elements[x], elements[y], t, skip)
                        score = (moved_hits + jh, d)
                        if best is None or score < best[0]:
                            best = (score, d, carried)
                    (hits, _), d, carried = best
                    if d:
                        for x in carried:
                            move(x, t)
                        out.append(EndTransition(st.id, side, carried, -1, hits))
    return out


# ---------------------------------------------------------------------------------------------------------
# N1 — crossing flip: a crossing service is raised only where it passes over the bundle. It rises just before
# the bundle, crosses at the raised level and comes down again just after it; elsewhere it keeps its level
# (so it stays under the beams beyond the bundle and its ends stay connected where they were).

HUMP_MARGIN = 50.0    # mm between the bundle's side and the riser (plus the service's own half size)


def _piece(base: Element, key: str, p0: np.ndarray, p1: np.ndarray, sec_, links: list[str], tag: str) -> Element:
    from .connect import M, _piece_solid
    d0 = p1 - p0
    d0 = d0 / max(float(np.linalg.norm(d0)), 1e-9)
    e = copy.copy(base)
    e.key = key
    e.origin, e.parts, e.leg_conns, e.centre = "mep_curve", None, None, None
    e.solid = _piece_solid(sec_, p0, p1, None)
    rec = {k: v for k, v in base.record.items() if k != "connectors"}
    rec.update({"start": list(p0 / M), "end": list(p1 / M), "slope": 0, "node_piece": tag, "key": key})
    e.record = rec
    e.connectors = [{"id": 0, "origin": list(p0 / M), "direction": list(-d0), "connected": [{"key": links[0]}]},
                    {"id": 1, "origin": list(p1 / M), "direction": list(d0), "connected": [{"key": links[1]}]}]
    return e


def crossing_hump(package: Package, elements: dict[str, Element], sec: Section, layout, key: str, dz: float,
                  moves: dict[str, np.ndarray], move, graph, extra: float = 0.0, bends=None) -> list[str] | None:
    """Raise the crossing service `key` by dz over the bundle only. None: it lies entirely over the bundle
    (raise it whole). Returns the keys of the pieces made."""
    from .connect import _section
    from .fittings import offset_angle, riser_radius
    e = elements.get(key)
    r = e.record if e is not None else {}
    if e is None or not r.get("start") or not r.get("end"):
        return None
    cor = sec.corridor
    a, b = np.array(r["start"]) * 1000, np.array(r["end"]) * 1000
    L = float(np.linalg.norm(b - a))
    va, vb = cor.v_of(a), cor.v_of(b)
    if L < 1 or abs(vb - va) < 1:
        return None
    s_c = cor.s_of((a + b) / 2)
    spans = []
    for st in sec.strands:
        if st.s_lo - 1 <= s_c <= st.s_hi + 1:
            p = layout.placements.get(st.id)
            v = p.v if p else st.v
            spans.append((v - st.width / 2, v + st.width / 2))
    if not spans:
        return None
    half = float(np.max(e.solid.half[1:])) if e.solid is not None else 50.0
    m = HUMP_MARGIN + half + extra
    lo_v, hi_v = min(x for x, _ in spans) - m, max(x for _, x in spans) + m
    t_of = lambda v: (v - va) / (vb - va) * L
    t0, t1 = sorted((t_of(lo_v), t_of(hi_v)))
    t0, t1 = max(t0, 0.0), min(t1, L)
    # A service branching from a strand reaches that strand wherever it moved: the end joining it is raised
    # too (the run is stretched to the strand at the raised level afterwards).
    cr = next((x for x in sec.crossings if x.key == key), None)
    if cr is not None and cr.attached:
        for v_end, sid in cr.ends:
            if sid is None:
                continue
            t_att = 0.0 if abs(v_end - va) < abs(v_end - vb) else L
            if t1 - t0 < 50:
                t0, t1 = (0.0, min(L, m)) if t_att == 0.0 else (max(0.0, L - m), L)
            t0, t1 = min(t0, t_att), max(t1, t_att)
    if t0 <= 50 and t1 >= L - 50:
        return None
    if t1 - t0 < 50:
        return []                                 # does not reach the bundle: stays where it is
    # The risers go where their vertical path is free (another service crossing next to this one, a large
    # duct, an earlier riser): moved outward along the service, step by step.
    t0, t1 = _free_risers(elements, e, key, graph, a, b, L, t0, t1, dz)
    u = (b - a) / L
    up = np.array([0.0, 0.0, dz])
    p0, p1 = a + u * t0, a + u * t1
    conn0 = e.connectors[0] if e.connectors else {}
    sec_ = _section(e, conn0)
    g = copy.copy(e)
    m0 = (p0 if t0 > 50 else a) + up
    m1 = (p1 if t1 < L - 50 else b) + up
    g.solid = _piece(e, key, m0, m1, sec_, [key, key], "N1").solid
    rec = dict(r)
    rec["start"], rec["end"] = list(m0 / 1000), list(m1 / 1000)
    g.record = rec
    elements[key] = g
    moves[key] = up
    made = []
    conns = []
    for idx, c in enumerate(e.connectors):
        cid = c.get("id", idx)
        if not c.get("origin"):
            conns.append(c)
            continue
        o = np.array(c["origin"]) * 1000
        at_a = np.linalg.norm(o - a) < np.linalg.norm(o - b)
        lifted = (at_a and t0 <= 50) or (not at_a and t1 >= L - 50)
        if lifted:
            conns.append({**c, "origin": list((o + up) / 1000)})
            continue
        # This end stays at its level: a run at the old level and a riser take over its joint.
        side = "a" if at_a else "b"
        q_in, m_in = (p0, m0) if at_a else (p1, m1)
        if bends is not None:
            # A short rise is made as a sloped offset: start it further out so that both elbows fit.
            ang = offset_angle(dz, riser_radius(e, sec_, bends), bends.min_straight_mm)
            if ang is not None and ang < 90:
                run_out = abs(dz) / math.tan(math.radians(ang))
                out_dir = (a - p0) if at_a else (b - p1)
                n_ = float(np.linalg.norm(out_dir))
                if n_ > run_out + 50:
                    q_in = q_in + out_dir / n_ * run_out
        k_run, k_rise = f"{key}#h{side}1", f"{key}#h{side}2"
        run = _piece(e, k_run, o, q_in, sec_, [key, key], "N1")
        rise = _piece(e, k_rise, q_in, m_in, sec_, [key, key], "N1")
        run.connectors = [{**run.connectors[0], "connected": c.get("connected") or []},
                          {**run.connectors[1], "connected": [{"key": k_rise, "connector_id": 0}]}]
        rise.connectors = [{**rise.connectors[0], "connected": [{"key": k_run, "connector_id": 1}]},
                           {**rise.connectors[1], "connected": [{"key": key, "connector_id": cid}]}]
        elements[k_run], elements[k_rise] = run, rise
        made += [k_run, k_rise]
        conns.append({**c, "origin": list(m_in / 1000), "connected": [{"key": k_rise, "connector_id": 1}]})
        # The neighbours now meet the run instead of the service.
        for ref in c.get("connected") or []:
            nb = elements.get(ref.get("key"))
            if nb is None:
                continue
            nb2 = copy.copy(nb)
            nb2.connectors = [{**nc, "connected": [({"key": k_run, "connector_id": 0}
                                                    if (rr.get("key") == key and rr.get("connector_id") in (cid, None)) else rr)
                                                   for rr in nc.get("connected") or []]}
                              for nc in nb.connectors]
            elements[nb.key] = nb2
    g.connectors = conns
    # Everything of this line on the raised part goes up with it: fittings, and offsets of the line itself
    # (short runs and elbows over the bundle); the rest stays.
    v_lo_r, v_hi_r = sorted((cor.v_of(m0), cor.v_of(m1)))
    members = {k for st in sec.strands for k in st.segments + st.fittings}
    crossings = {c.key for c in sec.crossings}
    queue, seen = list(graph.get(key, ())), {key}
    while queue:
        other = queue.pop()
        if other in seen:
            continue
        seen.add(other)
        f = elements.get(other)
        if f is None or f.solid is None or not f.is_mep or other in members or other in crossings or other in moves:
            continue
        lo, hi = f.solid.aabb()
        vs = sorted((cor.v_of(lo), cor.v_of(hi)))
        if vs[0] < min(v_lo_r, lo_v) - 1 or vs[1] > max(v_hi_r, hi_v) + 1:
            continue                                   # beyond the bundle: stays at its level
        if f.origin == "mep_curve" and abs((f.record["end"][2] - f.record["start"][2]) * 1000) > 100:
            continue                                   # a drop: it is stretched where it joins
        move(other, up)
        queue.extend(graph.get(other, ()))
    return made


RISER_STEP = 100.0       # mm, outward steps tried for a riser whose vertical path is taken
RISER_SEARCH = 2500.0    # mm, how far out


def _free_risers(elements: dict[str, Element], e: Element, key: str, graph, a: np.ndarray, b: np.ndarray, L: float,
                 t0: float, t1: float, dz: float) -> tuple[float, float]:
    from .connect import _section
    from .geometry import distance
    own = {key} | set(graph.get(key, ()))
    half = float(np.max(e.solid.half[1:])) if e.solid is not None else 50.0
    u = (b - a) / L
    z0 = min(a[2], b[2]) - half
    z1 = max(a[2], b[2]) + max(dz, 0.0) + half
    z0 += min(dz, 0.0)
    lo = np.minimum(a, b) - RISER_SEARCH - half
    hi = np.maximum(a, b) + RISER_SEARCH + half
    near = []
    for k, o in elements.items():
        if o.solid is None or k.split("#")[0] in own:
            continue
        olo, ohi = o.solid.aabb()
        if np.all(olo[:2] <= hi[:2]) and np.all(ohi[:2] >= lo[:2]) and olo[2] <= z1 and ohi[2] >= z0:
            near.append(o)
    if not near:
        return t0, t1
    sec_ = _section(e, e.connectors[0] if e.connectors else {})

    # A short rise is made as a sloped offset reaching outward (up to about its own height): check that
    # reach as well as the vertical line.
    reach = min(abs(dz), 600.0)

    def free_at(t: float) -> bool:
        p = a + u * t
        sol = _piece_solid_v(sec_, np.array([p[0], p[1], z0 + half]), np.array([p[0], p[1], z1 - half]))
        slo, shi = sol.aabb()
        for o in near:
            olo, ohi = o.solid.aabb()
            if np.any(olo > shi) or np.any(ohi < slo):
                continue
            if min(distance(sol, q)[0] for q in (o.parts or [o.solid])) < 0:
                return False
        return True

    def free(t: float, side: float = 0.0) -> bool:
        ts = [t] + ([t + side * reach / 2, t + side * reach] if side else [])
        return all(free_at(min(max(x, 0.0), L)) for x in ts)

    def search(t: float, step: float, limit: float) -> float:
        # A free vertical line (the sloped reach of a short rise is not checked: requiring it free too
        # moved risers less often and left more clashes on the three test corridors).
        for side in (0.0,):
            x = t
            for _ in range(int(RISER_SEARCH // RISER_STEP) + 1):
                if (step < 0 and x < limit) or (step > 0 and x > limit):
                    break
                if free(x, side):
                    return x
                x += step
        return t

    if t0 > 50:
        t0 = search(t0, -RISER_STEP, 100.0)
    if t1 < L - 50:
        t1 = search(t1, RISER_STEP, L - 100.0)
    return t0, t1


def _piece_solid_v(sec_, p0: np.ndarray, p1: np.ndarray):
    from .connect import _piece_solid
    return _piece_solid(sec_, p0, p1, None)


def riser_offsets(sec: Section, heights: dict[str, float], clearance: float) -> dict[str, float]:
    """Services stacked over one another rise at staggered places: the higher one further out."""
    zone = [c for c in sec.zone_crossings if c.key in heights]
    out = {c.key: 0.0 for c in zone}
    for c in zone:
        below = [o for o in zone if o is not c and abs(o.s - c.s) < (o.height + c.height) / 2 + clearance
                 and min(o.v_hi, c.v_hi) > max(o.v_lo, c.v_lo) and heights[o.key] < heights[c.key]]
        out[c.key] = sum(o.height + clearance for o in below)
    return out

