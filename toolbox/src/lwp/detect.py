"""Clash, clearance and headroom detection."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

import numpy as np

from .geometry import distance
from .package import Element, Package
from .rules import RuleSet

HARD = "hard"                # solids overlap
CLEARANCE = "clearance"      # clear distance below the required value
PENETRATION = "penetration"  # MEP passes through a wall (normally sleeved, listed separately)
JOINT = "joint"              # same system, an open end sits inside the other element: unconnected junction
OVERLAP = "overlap"          # same system, overlapping but not connected: modelling issue to review
MODEL_ISSUES = (JOINT, OVERLAP)

TOLERANCE_MM = 1.0
IGNORED_KINDS = {"support", "slab"}  # supports touch what they carry by design; slabs only bound the headroom map


@dataclass
class Conflict:
    type: str
    a: str
    b: str
    distance_mm: float        # negative = overlap depth estimate
    required_mm: float
    location: np.ndarray      # mm, host internal
    exact: bool               # False when a mesh-fitted box is involved
    rule_note: str = ""


@dataclass
class HeadroomItem:
    key: str
    level: str
    bottom_mm: float          # host internal z of the lowest point incl. insulation
    clear_mm: float           # above the floor level
    location: np.ndarray


def connection_graph(package: Package) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for e in package.mep():
        for c in e.connectors:
            for ref in c.get("connected") or []:
                other = ref.get("key")
                if other and other != e.key:
                    graph[e.key].add(other)
                    graph[other].add(e.key)
        host = e.record.get("host_key")
        if host:
            graph[e.key].add(host)
            graph[host].add(e.key)
    return graph


def near_pairs(graph: dict[str, set[str]], hops: int) -> set[frozenset]:
    """Pairs of elements within `hops` connections of each other."""
    pairs: set[frozenset] = set()
    for start in graph:
        seen = {start: 0}
        queue = deque([start])
        while queue:
            node = queue.popleft()
            if seen[node] >= hops:
                continue
            for nxt in graph[node]:
                if nxt not in seen:
                    seen[nxt] = seen[node] + 1
                    queue.append(nxt)
        for other in seen:
            if other != start:
                pairs.add(frozenset((start, other)))
    return pairs


def candidate_pairs(elements: list[Element], margin: float) -> list[tuple[int, int]]:
    """Sweep and prune on X over AABBs inflated by margin."""
    boxes = np.array([np.concatenate(e.solid.aabb()) for e in elements])
    boxes[:, :3] -= margin / 2
    boxes[:, 3:] += margin / 2
    order = np.argsort(boxes[:, 0])
    pairs = []
    active: list[int] = []
    for i in order:
        lo = boxes[i, 0]
        active = [j for j in active if boxes[j, 3] >= lo]
        for j in active:
            if (boxes[i, 1] <= boxes[j, 4] and boxes[j, 1] <= boxes[i, 4]
                    and boxes[i, 2] <= boxes[j, 5] and boxes[j, 2] <= boxes[i, 5]):
                pairs.append((j, i))
        active.append(i)
    return pairs


def detect_conflicts(package: Package, rules: RuleSet) -> list[Conflict]:
    in_scope = scope_test(package)
    elements = [e for e in package.elements.values() if e.solid is not None and e.kind not in IGNORED_KINDS]
    near = near_pairs(connection_graph(package), rules.connected_hops_ignored)
    margin = max(rules.clearance_mm.max_mm, rules.layout.tray_water.crossing_above_mm)
    conflicts: list[Conflict] = []

    for i, j in candidate_pairs(elements, margin):
        a, b = elements[i], elements[j]
        if not (a.is_mep or b.is_mep):
            continue
        if frozenset((a.key, b.key)) in near:
            continue
        wall = a.kind == "wall" or b.kind == "wall"
        required, pair_rule = (0.0, None) if wall else rules.clearance_mm.required(a.group, b.group)
        if not wall and _water_over_tray(a, b):
            required = max(required, rules.layout.tray_water.crossing_above_mm)
        # Cheap reject before the exact distance.
        lo_a, hi_a = a.solid.aabb()
        lo_b, hi_b = b.solid.aabb()
        gap = np.maximum(np.maximum(lo_a - hi_b, lo_b - hi_a), 0.0)
        gap_mm = float(np.linalg.norm(gap))
        if gap_mm > 0 and gap_mm >= required:
            continue
        d, where = element_distance(a, b)
        # 1 mm tolerance: modelled "exactly 50 mm" spacings come out as 49.99.
        if d >= required - TOLERANCE_MM or not in_scope(where):
            continue
        if wall:
            if d < -TOLERANCE_MM:
                conflicts.append(Conflict(PENETRATION, a.key, b.key, d, 0.0, where, a.solid.exact and b.solid.exact))
            continue
        kind = CLEARANCE
        if d < -TOLERANCE_MM:
            kind = same_system_issue(a, b) or HARD
        conflicts.append(Conflict(
            kind, a.key, b.key, d, required, where,
            a.solid.exact and b.solid.exact, (pair_rule.note or "") if pair_rule else "",
        ))
    order = {HARD: 0, CLEARANCE: 1, JOINT: 2, OVERLAP: 3, PENETRATION: 4}
    conflicts.sort(key=lambda c: (order[c.type], c.distance_mm))
    return conflicts


def _water_over_tray(a: Element, b: Element) -> bool:
    """Water above a tray with overlapping plan footprints (stricter clearance: water crossing over a tray)."""
    if a.group == "water" and b.domain == "tray":
        w, t = a, b
    elif b.group == "water" and a.domain == "tray":
        w, t = b, a
    else:
        return False
    wl, wh = w.solid.aabb()
    tl, th = t.solid.aabb()
    return wl[2] >= th[2] - 1 and wl[0] < th[0] and tl[0] < wh[0] and wl[1] < th[1] and tl[1] < wh[1]


def element_distance(a: Element, b: Element):
    """Distance between two elements, using fitting legs where available."""
    best = None
    for pa in a.parts or [a.solid]:
        for pb in b.parts or [b.solid]:
            d, where = distance(pa, pb)
            if best is None or d < best[0]:
                best = (d, where)
    return best


def project_element(e: Element, p: np.ndarray) -> np.ndarray:
    pts = [s.project(p) for s in (e.parts or [e.solid])]
    return min(pts, key=lambda q: float(np.linalg.norm(q - p)))


def same_system_issue(a: Element, b: Element) -> str | None:
    """Overlaps inside one system are modelling issues, not coordination clashes."""
    if not (a.is_mep and b.is_mep) or a.cls != b.cls:
        return None
    if (a.system_type or a.system_name) != (b.system_type or b.system_name):
        return None
    for x, y in ((a, b), (b, a)):
        for c in x.connectors:
            if c.get("connector_type") != "End" or c.get("connected") or not c.get("origin"):
                continue
            p = np.array(c["origin"]) * 1000
            if np.linalg.norm(project_element(y, p) - p) <= 50:
                return JOINT
    return OVERLAP


def scope_test(package: Package):
    """Point-in-section-box test for mm points; parametric runs extend past the exported range."""
    scope = package.manifest.get("scope") or {}
    if not scope.get("section_box_active"):
        return lambda p: True
    to_local = np.linalg.inv(np.array(scope["section_box_transform"]).reshape(4, 4))
    lo, hi = np.array(scope["section_box_min"]) * 1000, np.array(scope["section_box_max"]) * 1000

    def inside(p) -> bool:
        local = (to_local @ np.array([p[0] / 1000, p[1] / 1000, p[2] / 1000, 1.0]))[:3] * 1000
        return bool(np.all(local >= lo - 1.0) and np.all(local <= hi + 1.0))
    return inside


def cluster_points(conflicts: list[Conflict], radius_mm: float = 1000.0) -> int:
    """Number of conflict locations after merging those within radius (greedy)."""
    centers: list[np.ndarray] = []
    for c in conflicts:
        if not any(np.linalg.norm(c.location - x) <= radius_mm for x in centers):
            centers.append(c.location)
    return len(centers)


def floor_level(package: Package, rules: RuleSet, z: float):
    levels = package.host_levels()
    if rules.headroom.floor_level != "auto":
        for lv in levels:
            if lv.name == rules.headroom.floor_level:
                return lv
        raise ValueError(f"floor level {rules.headroom.floor_level!r} not found in host levels")
    below = [lv for lv in levels if lv.elevation <= z + 1.0]
    return below[-1] if below else None


def is_horizontal(e: Element, max_slope: float = 0.1) -> bool:
    start, end = e.record.get("start"), e.record.get("end")
    if not start or not end:
        return False
    dx, dy, dz = (end[i] - start[i] for i in range(3))
    run = (dx * dx + dy * dy) ** 0.5
    return run > 1e-6 and abs(dz) / run <= max_slope


def detect_headroom(package: Package, rules: RuleSet) -> list[HeadroomItem]:
    items = []
    in_scope = scope_test(package)
    for e in package.mep():
        # Horizontal runs only: risers and drops to equipment reach the floor by design.
        if e.solid is None or e.origin != "mep_curve" or not is_horizontal(e):
            continue
        lo, hi = e.solid.aabb()
        bottom = float(lo[2])
        level = floor_level(package, rules, bottom)
        if level is None:
            continue
        where = (lo + hi) / 2
        if in_scope(where):
            items.append(HeadroomItem(e.key, level.name, bottom, bottom - level.elevation, where))
    items.sort(key=lambda h: h.clear_mm)
    return items
