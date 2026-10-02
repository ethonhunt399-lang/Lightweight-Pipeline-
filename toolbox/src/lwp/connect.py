"""Connections after a plan: find joints that came apart and re-connect them (node N0 — transitions).

Every element carries its connectors (origin in metres, `connected` → other element and connector id).
Moving an element moves its connectors; a joint is open when the two connectors no longer coincide.

Re-connection, at each open joint:
  * a straight run at the joint is stretched or shortened along its own axis (a vertical drop follows a
    lifted branch; a branch across the corridor follows a strand moved sideways);
  * what is left across the run's axis becomes a jog: one or two straight pieces, vertical and horizontal,
    with the run's section. Of the two orders (up then across, across then up) the one with fewer clashes
    is kept.
The run that is stretched and jogged is the one that did not move when there is one (the transition is
built outside the corridor), otherwise the moved one.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

import numpy as np

from .geometry import Solid, capsule, distance, swept_box
from .package import Element, Package

M = 1000.0
OPEN_MM = 1.0          # connectors farther apart than this are an open joint
SHIFTS_MM = (300.0, 600.0, 1000.0, 1500.0, 2000.0)   # set-backs tried for a transition (N3)
MIN_RUN_MM = 1.0       # a run may be shortened down to this (two strands now side by side)


@dataclass
class Joint:
    a: str
    ca: int               # connector id on a
    b: str
    cb: int
    pa: np.ndarray        # mm
    pb: np.ndarray

    @property
    def gap(self) -> float:
        return float(np.linalg.norm(self.pa - self.pb))

    def to_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "gap_mm": round(self.gap), "at_mm": [round(float(x)) for x in self.pa]}


@dataclass
class Repair:
    host: str                 # run stretched / jogged
    other: str
    stretch_mm: float
    pieces: list[str]
    gap_mm: float

    def to_dict(self) -> dict:
        return {"host": self.host, "other": self.other, "stretch_mm": round(self.stretch_mm),
                "pieces": self.pieces, "gap_mm": round(self.gap_mm)}


def _conn(e: Element, cid) -> dict | None:
    for i, c in enumerate(e.connectors):
        if c.get("id", i) == cid:
            return c
    return None


def open_joints(elements: dict[str, Element], keys: set[str] | None = None) -> list[Joint]:
    """Joints whose two connectors no longer coincide. keys: only joints touching these elements."""
    out, seen = [], set()
    for e in elements.values():
        if not e.is_mep or (keys is not None and e.key not in keys and not _touches(e, keys)):
            continue
        for i, c in enumerate(e.connectors):
            if not c.get("origin"):
                continue
            for ref in c.get("connected") or []:
                k, cid = ref.get("key"), ref.get("connector_id")
                other = elements.get(k)
                if other is None or k == e.key:
                    continue
                pair = tuple(sorted(((e.key, c.get("id", i)), (k, cid)), key=str))
                if pair in seen:
                    continue
                seen.add(pair)
                oc = _conn(other, cid)
                if oc is None or not oc.get("origin"):
                    continue
                pa, pb = np.array(c["origin"]) * M, np.array(oc["origin"]) * M
                if float(np.linalg.norm(pa - pb)) > OPEN_MM:
                    out.append(Joint(e.key, c.get("id", i), k, cid, pa, pb))
    return out


def _touches(e: Element, keys: set[str]) -> bool:
    return any(ref.get("key") in keys for c in e.connectors for ref in c.get("connected") or [])


def _axis(e: Element) -> tuple[np.ndarray, np.ndarray] | None:
    r = e.record
    if e.origin != "mep_curve" or not r.get("start") or not r.get("end"):
        return None
    return np.array(r["start"]) * M, np.array(r["end"]) * M


def _section(e: Element, conn: dict) -> tuple[str, float, float, np.ndarray | None]:
    """('round', r, r, None) or ('rect', half_w, half_h, x_axis) incl. insulation."""
    ins = e.insulation_mm
    r = e.record
    if e.origin == "mep_curve":
        if (r.get("outer_diameter_m") or 0) > 0 and r.get("shape") != "rectangular":
            rr = r["outer_diameter_m"] * M / 2 + ins
            return "round", rr, rr, None
        if (r.get("width_m") or 0) > 0:
            xa = np.array(r["section_x_axis"]) if r.get("section_x_axis") else None
            return "rect", r["width_m"] * M / 2 + ins, r["height_m"] * M / 2 + ins, xa
    if (conn.get("diameter_m") or 0) > 0:
        rr = conn["diameter_m"] * M / 2 + ins
        return "round", rr, rr, None
    if (conn.get("width_m") or 0) > 0:
        xa = np.array(conn["x_axis"]) if conn.get("x_axis") else None
        return "rect", conn["width_m"] * M / 2 + ins, conn["height_m"] * M / 2 + ins, xa
    rr = e.solid.radius if e.solid is not None and e.solid.is_capsule else 25.0
    return "round", rr, rr, None


def _piece_solid(sec, p0: np.ndarray, p1: np.ndarray, run_axis: np.ndarray | None) -> Solid:
    kind, hw, hh, xa = sec
    if kind == "round":
        return capsule(p0, p1, hw)
    d = p1 - p0
    d = d / max(float(np.linalg.norm(d)), 1e-9)
    if abs(d[2]) > 0.9:
        x_axis = xa if xa is not None and abs(float(np.dot(xa, d))) < 0.5 else np.array([1.0, 0.0, 0.0])
        return swept_box(p0, p1, x_axis, hw, hh)
    # Horizontal jog of a rectangular run: it turns flat, width along the run's own direction.
    x_axis = run_axis if run_axis is not None and abs(float(np.dot(run_axis, d))) < 0.5 else np.cross([0.0, 0.0, 1.0], d)
    return swept_box(p0, p1, x_axis, hw, hh)


def _set_axis(e: Element, start: np.ndarray, end: np.ndarray) -> Element:
    """A copy of a straight run with new end points (connectors follow)."""
    g = copy.copy(e)
    r = dict(e.record)
    old_s, old_e = np.array(r["start"]) * M, np.array(r["end"]) * M
    r["start"], r["end"] = list(start / M), list(end / M)
    g.record = r
    s = e.solid
    if s.is_capsule:
        g.solid = capsule(start, end, s.radius)
    else:
        x_axis = s.axes[1] if s.axes is not None else None
        g.solid = swept_box(start, end, x_axis, float(s.half[1]), float(s.half[2]))
    conns = []
    for c in e.connectors:
        if c.get("origin"):
            o = np.array(c["origin"]) * M
            if np.linalg.norm(o - old_s) < np.linalg.norm(o - old_e):
                c = {**c, "origin": list(start / M)}
            else:
                c = {**c, "origin": list(end / M)}
        conns.append(c)
    g.connectors = conns
    return g


def reconnect(package: Package, elements: dict[str, Element], moves: dict[str, np.ndarray],
              obstacles: list[Element] | None = None) -> tuple[list[Repair], list[Joint]]:
    """Close the open joints touching moved elements. Mutates `elements` (stretched runs, new pieces).

    Returns the repairs made and the joints that stay open.
    """
    def worse(j: Joint) -> bool:
        """Opened by the plan (the model may already have small gaps of its own)."""
        a, b = package.elements.get(j.a), package.elements.get(j.b)
        if a is None or b is None:
            return True
        ca, cb = _conn(a, j.ca), _conn(b, j.cb)
        if ca is None or cb is None or not ca.get("origin") or not cb.get("origin"):
            return True
        before = float(np.linalg.norm(np.array(ca["origin"]) - np.array(cb["origin"]))) * M
        return j.gap > before + OPEN_MM

    joints = [j for j in open_joints(elements, set(moves)) if worse(j)]
    # A run open at both ends (e.g. a short pipe between two strands that both moved) is first set along its
    # own axis to span between the two new places; jogs then only take what is left across the axis.
    ends: dict[str, list[np.ndarray]] = {}
    for j in joints:
        for k, cid, ok, ocid in ((j.a, j.ca, j.b, j.cb), (j.b, j.cb, j.a, j.ca)):
            if _axis(elements[k]) is not None and not (k in moves and float(np.linalg.norm(moves[k][:2])) > 0.5):
                oc = _conn(elements[ok], ocid)
                if oc is not None and oc.get("origin"):
                    ends.setdefault(k, []).append(np.array(oc["origin"]) * M)
    for k, targets in ends.items():
        if len(targets) < 2:
            continue
        s0, s1 = _axis(elements[k])
        length = float(np.linalg.norm(s1 - s0))
        ax = (s1 - s0) / max(length, 1e-9)
        t = sorted(float(np.dot(p - s0, ax)) for p in targets)
        lo, hi = t[0], t[-1]
        if hi - lo < MIN_RUN_MM:
            mid = (lo + hi) / 2
            lo, hi = mid - MIN_RUN_MM / 2, mid + MIN_RUN_MM / 2
        elements[k] = _set_axis(elements[k], s0 + ax * lo, s0 + ax * hi)
    repairs: list[Repair] = []
    left: list[Joint] = []
    near = list(obstacles) if obstacles is not None else [e for e in elements.values() if e.solid is not None]
    boxes = np.array([np.concatenate(e.solid.aabb()) for e in near]) if near else np.zeros((0, 6))
    slot = {e.key: i for i, e in enumerate(near)}

    def seen(e: Element) -> None:
        """Keep the obstacle list current: later transitions must avoid earlier ones."""
        nonlocal boxes
        box = np.concatenate(e.solid.aabb())
        if e.key in slot:
            near[slot[e.key]] = e
            boxes[slot[e.key]] = box
        else:
            slot[e.key] = len(near)
            near.append(e)
            boxes = np.vstack([boxes, box]) if len(boxes) else box[None, :]
    count = 0
    for j in joints:
        ea, eb = elements[j.a], elements[j.b]
        # Current connector positions (earlier repairs may have stretched one of the two runs).
        ca, cb = _conn(ea, j.ca), _conn(eb, j.cb)
        pa, pb = np.array(ca["origin"]) * M, np.array(cb["origin"]) * M
        if float(np.linalg.norm(pa - pb)) <= OPEN_MM:
            continue
        cand = [(j.a, ca, pa, j.b, pb), (j.b, cb, pb, j.a, pa)]
        # Host: a straight run, preferably one that did not move (the transition stays outside the corridor).
        def rank(c):
            key = c[0]
            e = elements[key]
            moved = key in moves and float(np.linalg.norm(moves[key])) > 0.5
            return (_axis(e) is None, moved)
        cand.sort(key=rank)
        host_key, hconn, end, other_key, target = cand[0]
        host = elements[host_key]
        sec = _section(host, hconn)
        stretch = 0.0
        axis = _axis(host)
        run_axis = None
        if axis is not None:
            s0, s1 = axis
            at_end = np.linalg.norm(end - s1) < np.linalg.norm(end - s0)
            out = (s1 - s0) / max(float(np.linalg.norm(s1 - s0)), 1e-9)
            run_axis = out
            if not at_end:
                out = -out
            d_ax = float(np.dot(target - end, out))
            length = float(np.linalg.norm(s1 - s0))
            if abs(d_ax) > OPEN_MM and length + d_ax >= MIN_RUN_MM:
                stretch = d_ax
                new_end = end + out * d_ax
                host = _set_axis(host, s0 if at_end else new_end, new_end if at_end else s1)
                elements[host_key] = host
                seen(host)
                end = new_end
        d = target - end
        pieces: list[str] = []
        if float(np.linalg.norm(d)) > OPEN_MM:
            # Where along the run the transition is made (N3): at the joint, or set back along the run so that
            # it falls where nothing is in the way; then the order of the vertical and horizontal pieces.
            shifts = [0.0]
            if axis is not None:
                s0_, s1_ = _axis(host)
                room = float(np.linalg.norm(s1_ - s0_)) - 100.0
                shifts += [x for x in SHIFTS_MM if x < room]
                out_dir = (end - (s0_ if np.linalg.norm(end - s1_) < 1 else s1_))
                out_dir = out_dir / max(float(np.linalg.norm(out_dir)), 1e-9)
            best = None
            for shift in shifts:
                start = end - out_dir * shift if shift else end
                dd = target - start
                ax = float(np.dot(dd, out_dir)) if shift else 0.0
                perp = dd - (out_dir * ax if shift else 0.0)
                dz = np.array([0.0, 0.0, perp[2]])
                dh = perp - dz
                if np.linalg.norm(dz) > OPEN_MM and np.linalg.norm(dh) > OPEN_MM:
                    orders = [[start, start + dz, start + perp], [start, start + dh, start + perp]]
                else:
                    orders = [[start, start + perp]]
                for pts in orders:
                    if shift:
                        pts = pts + [target]
                    pts = [p for i, p in enumerate(pts) if i == 0 or float(np.linalg.norm(p - pts[i - 1])) > OPEN_MM]
                    if len(pts) < 2:
                        continue
                    solids = [_piece_solid(sec, pts[i], pts[i + 1], run_axis) for i in range(len(pts) - 1)]
                    hits = _hits(solids, near, boxes, {host_key, other_key})
                    rank_ = (hits, shift, len(solids))
                    if best is None or rank_ < best[0]:
                        best = (rank_, pts, solids, shift)
                if best is not None and best[0][0] == 0:
                    break
            _, pts, solids, shift = best
            if shift:
                s0_, s1_ = _axis(host)
                at_end_ = np.linalg.norm(end - s1_) < np.linalg.norm(end - s0_)
                host = _set_axis(host, s0_ if at_end_ else pts[0], pts[0] if at_end_ else s1_)
                elements[host_key] = host
                seen(host)
                stretch -= shift
            prev_key, prev_cid = host_key, hconn.get("id")
            for i, sol in enumerate(solids):
                count += 1
                key = f"{host_key}#j{count}"
                last = i == len(solids) - 1
                nxt_key = other_key if last else f"{host_key}#j{count + 1}"
                d0 = pts[i + 1] - pts[i]
                d0 = d0 / max(float(np.linalg.norm(d0)), 1e-9)
                piece = copy.copy(elements[host_key])
                piece.key = key
                piece.origin, piece.parts, piece.leg_conns, piece.centre = "mep_curve", None, None, None
                piece.kind = host.kind if host.origin == "mep_curve" else {"pipe": "pipe", "duct": "duct"}.get(host.domain, "cable_tray")
                piece.solid = sol
                rec = {k: v for k, v in host.record.items() if k not in ("connectors",)}
                rec.update({"start": list(pts[i] / M), "end": list(pts[i + 1] / M), "slope": 0,
                            "node_piece": "N0", "key": key})
                if sec[0] == "round":
                    rec.update({"shape": "round", "outer_diameter_m": (sec[1] - host.insulation_mm) * 2 / M})
                else:
                    rec.update({"shape": "rectangular", "width_m": (sec[1] - host.insulation_mm) * 2 / M,
                                "height_m": (sec[2] - host.insulation_mm) * 2 / M})
                piece.record = rec
                piece.connectors = [
                    {"id": 0, "origin": list(pts[i] / M), "direction": list(-d0),
                     "connected": [{"key": prev_key, "connector_id": prev_cid}]},
                    {"id": 1, "origin": list(pts[i + 1] / M), "direction": list(d0),
                     "connected": [{"key": nxt_key, "connector_id": cb_id(j, other_key) if last else 0}]},
                ]
                elements[key] = piece
                seen(piece)
                pieces.append(key)
                prev_key, prev_cid = key, 1
        repairs.append(Repair(host_key, other_key, stretch, pieces, float(np.linalg.norm(target - end))))
    # What is still open. A joint bridged by jog pieces stays "open" between its two original ends.
    bridged = {frozenset((r.host, r.other)) for r in repairs if r.pieces}
    for j in open_joints(elements, set(moves) | {r.host for r in repairs}):
        if frozenset((j.a, j.b)) not in bridged and worse(j):
            left.append(j)
    return repairs, left


def cb_id(j: Joint, other_key: str):
    return j.cb if other_key == j.b else j.ca


def _hits(solids: list[Solid], near: list[Element], boxes: np.ndarray, skip: set[str]) -> int:
    n = 0
    for s in solids:
        lo, hi = s.aabb()
        if not len(boxes):
            continue
        mask = np.all(boxes[:, :3] <= hi, axis=1) & np.all(boxes[:, 3:] >= lo, axis=1)
        for idx in np.nonzero(mask)[0]:
            e = near[idx]
            if e.key in skip:
                continue
            parts = e.parts or [e.solid]
            if min(distance(s, p)[0] for p in parts) < 0:
                n += 1
    return n
