"""Corridor cross-section: strands (logical runs), structural limits and crossing services."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .corridor import Corridor
from .detect import connection_graph, floor_level, is_horizontal
from .package import Element, Package
from .rules import RuleSet

PARALLEL = 0.995          # |cos| between run direction and corridor axis
MIN_OVERLAP = 2000.0      # mm of a run inside the corridor to take part
MERGE_TOL = 30.0          # mm: collinear segments within this in v and z form one strand
SIDE_EXTENSION = 2000.0   # mm the corridor may widen beyond the band before hitting walls/columns


@dataclass
class Strand:
    id: str
    cls: str
    group: str
    domain: str
    kind: str
    system: str
    abbreviation: str
    size: str
    pressure: bool
    width: float              # mm across the corridor, incl. insulation
    height: float             # mm, incl. insulation
    v: float                  # mm, centre across the corridor
    z: float                  # mm, bottom incl. insulation
    s_lo: float
    s_hi: float
    movable: bool = True
    reason: str = ""          # why not movable
    exits: set = field(default_factory=set)   # sides where the strand leaves the corridor: "left" (−v) / "right" (+v)
    segments: list[str] = field(default_factory=list)
    fittings: list[str] = field(default_factory=list)

    @property
    def top(self) -> float:
        return self.z + self.height

    def label(self) -> str:
        return f"{self.abbreviation or self.system} {self.size}".strip()


@dataclass
class Beam:
    key: str
    v_lo: float
    v_hi: float
    bottom: float
    crossing: bool            # spans across the corridor (else runs along it)
    label: str
    s_lo: float = -np.inf     # extent along the corridor (mm)
    s_hi: float = np.inf
    top: float = np.nan       # top of the beam (≈ top of the slab)


@dataclass
class Crossing:
    """A service crossing the corridor (perpendicular to it)."""
    key: str
    s: float                  # position along the corridor
    v_lo: float
    v_hi: float
    z_lo: float
    z_hi: float
    label: str
    ceiling: float = np.inf   # lowest bottom of beams it has to pass under (beams along the corridor)
    group: str = "other"
    domain: str = ""
    attached: set = field(default_factory=set)   # strands it branches from: it reaches them wherever they go
    ends: list = field(default_factory=list)      # per end: (v mm, strand id it connects to or None)

    @property
    def height(self) -> float:
        return self.z_hi - self.z_lo


def overlaps(a_lo: float, a_hi: float, b_lo: float, b_hi: float, margin: float = 0.0) -> bool:
    return a_lo < b_hi + margin and b_lo < a_hi + margin


@dataclass
class LeakItem:
    """A flange, valve, union or air vent on water pipework (not part of a strand)."""
    key: str
    s: float
    v_lo: float
    v_hi: float
    z_lo: float
    z_hi: float
    crossing: str | None = None   # the crossing service it sits on (moves with it)


def is_leak_prone(e: Element, pattern: str) -> bool:
    import re
    if e.group != "water" or e.origin != "mep_family":
        return False
    if e.record.get("builtin_category") == "OST_PipeAccessory":
        return True
    return bool(pattern) and re.search(pattern, f"{e.family} {e.type_name}") is not None


@dataclass
class Section:
    corridor: Corridor
    floor_z: float
    floor_name: str
    ceiling: float            # lowest bottom of beams crossing the corridor (mm) — reported
    ceiling_key: str
    v_lo: float               # available width (mm), walls/columns excluded
    v_hi: float
    strands: list[Strand]
    beams: list[Beam]         # every beam over the corridor; each limits only strands under it
    crossings: list[Crossing]
    blocked: list[tuple[float, float, str]]          # v intervals blocked by columns / walls
    notes: list[str] = field(default_factory=list)
    ceiling_max: float = np.inf   # highest crossing-beam bottom: upper bound for any layer
    leaks: list = field(default_factory=list)       # leak-prone water items not on a strand (LeakItem)
    bands: list = field(default_factory=list)       # across the corridor: drive lane / stall intervals (drawings)

    crossing_limit: float = 250.0

    @property
    def zone_crossings(self) -> list[Crossing]:
        """Crossing services that pass above the bundle (the rest are node items)."""
        return [c for c in self.crossings if c.height <= self.crossing_limit + 1]

    @property
    def large_crossings(self) -> list[Crossing]:
        return [c for c in self.crossings if c.height > self.crossing_limit + 1]

    @property
    def crossing_height(self) -> float:
        """Largest crossing service passing above the bundle (mm)."""
        return max((c.height for c in self.zone_crossings), default=0.0)

    @property
    def movable(self) -> list[Strand]:
        return [s for s in self.strands if s.movable]

    @property
    def fixed(self) -> list[Strand]:
        return [s for s in self.strands if not s.movable]


def _plan_extent(e: Element, corridor: Corridor) -> tuple[float, float, float, float]:
    lo, hi = e.solid.aabb()
    if corridor.axis == "x":
        return lo[0], hi[0], lo[1], hi[1]
    return lo[1], hi[1], lo[0], hi[0]


def _cross_extents(e: Element, corridor: Corridor) -> tuple[float, float]:
    """Width across the corridor and height of a run's section (incl. insulation)."""
    s = e.solid
    if s.is_capsule:
        return 2 * s.radius, 2 * s.radius
    w = corridor.w
    across = 2 * sum(s.half[k] * abs(float(np.dot(s.axes[k], w))) for k in (1, 2))
    height = 2 * sum(s.half[k] * abs(float(s.axes[k][2])) for k in (1, 2))
    return across, height


def extract(package: Package, corridor: Corridor, rules: RuleSet) -> Section:
    notes: list[str] = []
    members: dict[tuple, list[Element]] = defaultdict(list)
    crossings = []
    for e in package.mep():
        r = e.record
        if e.origin != "mep_curve" or e.solid is None or not r.get("start") or not r.get("end"):
            continue
        a, b = np.array(r["start"]) * 1000, np.array(r["end"]) * 1000
        d = b - a
        n = float(np.linalg.norm(d[:2]))
        if n < 1:
            continue
        cos = abs(float(np.dot(d[:2] / n, corridor.u[:2])))
        s_lo, s_hi, v_lo, v_hi = _plan_extent(e, corridor)
        if not is_horizontal(e):
            continue
        vc = (v_lo + v_hi) / 2
        if cos >= PARALLEL:
            overlap = min(s_hi, corridor.s1) - max(s_lo, corridor.s0)
            if overlap < MIN_OVERLAP or not (corridor.v0 <= vc <= corridor.v1):
                continue
            width, height = _cross_extents(e, corridor)
            z = float(e.solid.aabb()[0][2])
            key = (e.kind, e.system_type or e.system_name, round(width), round(height))
            members[key].append(e)
        elif cos < 0.05:
            # Perpendicular run crossing the band inside the corridor length.
            sc = (s_lo + s_hi) / 2
            if corridor.s0 <= sc <= corridor.s1 and v_lo < corridor.v1 and v_hi > corridor.v0:
                lo, hi = e.solid.aabb()
                crossings.append(Crossing(e.key, sc, v_lo, v_hi, float(lo[2]), float(hi[2]), e.label(),
                                          group=e.group, domain=e.domain))

    # Split each (kind, system, size) group into strands of collinear segments.
    strands: list[Strand] = []
    for (kind, system, width, height), elems in members.items():
        clusters: list[list[Element]] = []
        for e in sorted(elems, key=lambda x: (_v(x, corridor), x.solid.aabb()[0][2])):
            v, z = _v(e, corridor), float(e.solid.aabb()[0][2])
            for c in clusters:
                if abs(_v(c[0], corridor) - v) <= MERGE_TOL and abs(float(c[0].solid.aabb()[0][2]) - z) <= MERGE_TOL:
                    c.append(e)
                    break
            else:
                clusters.append([e])
        for c in clusters:
            first = c[0]
            ext = [_plan_extent(e, corridor) for e in c]
            slope = max(abs(e.record.get("slope") or 0) for e in c)
            st = Strand(
                id=f"S{len(strands) + 1:02d}", cls=first.cls, group=first.group, domain=first.domain, kind=kind,
                system=system, abbreviation=first.abbreviation or first.system_type, size=_size_text(first),
                pressure=package.classifier.rules.system_classes[first.cls].pressure,
                width=float(width), height=float(height), v=float(np.mean([_v(e, corridor) for e in c])),
                z=float(min(e.solid.aabb()[0][2] for e in c)),
                s_lo=max(min(x[0] for x in ext), corridor.s0), s_hi=min(max(x[1] for x in ext), corridor.s1),
                segments=[e.key for e in c],
            )
            if st.domain == "pipe" and (slope > 1e-3 or not st.pressure):
                st.movable, st.reason = False, "重力管或有坡度，保持原位"
            strands.append(st)
    strands.sort(key=lambda s: (-s.z, s.v))
    for i, s in enumerate(strands, 1):
        s.id = f"S{i:02d}"

    _attach_fittings(package, corridor, strands)
    graph = connection_graph(package)
    owner = {k: st.id for st in strands for k in st.fittings + st.segments}
    for c in crossings:
        near = graph.get(c.key, set())
        c.attached = {owner[k] for k in near if k in owner}
        e = package.elements[c.key]
        for conn in e.connectors:
            if not conn.get("origin"):
                continue
            o = np.array(conn["origin"]) * 1000
            sid = next((owner[r["key"]] for r in conn.get("connected") or [] if r.get("key") in owner), None)
            c.ends.append((corridor.v_of(o), sid))

    # Structure.
    beams, ceiling, ceiling_key, ceiling_max = [], np.inf, "", -np.inf
    blocked = []
    for e in package.obstacles():
        s_lo, s_hi, v_lo, v_hi = _plan_extent(e, corridor)
        if s_hi < corridor.s0 or s_lo > corridor.s1:
            continue
        lo, hi = e.solid.aabb()
        if e.kind == "beam":
            if v_hi < corridor.v0 - SIDE_EXTENSION or v_lo > corridor.v1 + SIDE_EXTENSION:
                continue
            long_axis = max((1, 2, 0), key=lambda k: e.solid.half[k] * (1 - abs(e.solid.axes[k][2])))
            along = abs(float(np.dot(e.solid.axes[long_axis], corridor.u)))
            crossing = along < 0.95
            # Every beam limits only the strands under it (overlapping across and along the corridor).
            beams.append(Beam(e.key, v_lo, v_hi, float(lo[2]), crossing, e.type_name, s_lo, s_hi, float(hi[2])))
            if crossing and v_lo < corridor.v1 and v_hi > corridor.v0:
                if lo[2] < ceiling:
                    ceiling, ceiling_key = float(lo[2]), e.key
                ceiling_max = max(ceiling_max, float(lo[2]))
        elif e.kind in ("column", "wall"):
            if v_hi < corridor.v0 - SIDE_EXTENSION or v_lo > corridor.v1 + SIDE_EXTENSION:
                continue
            blocked.append((v_lo, v_hi, e.kind))

    floor = floor_level(package, rules, min((s.z for s in strands), default=0.0))
    floor_z = floor.elevation if floor else 0.0
    if not np.isfinite(ceiling):
        top = max((s.top for s in strands), default=floor_z + 3000)
        ceiling = top + 100
        notes.append("走廊范围内没有横跨的梁，顶部限制取现有管线顶 + 100 mm")

    # Available width: the free interval around the current bundle, limited by walls and columns.
    if strands:
        env_lo = min(s.v - s.width / 2 for s in strands)
        env_hi = max(s.v + s.width / 2 for s in strands)
    else:
        env_lo, env_hi = corridor.v0, corridor.v1
    v_lo, v_hi = min(corridor.v0, env_lo) - SIDE_EXTENSION, max(corridor.v1, env_hi) + SIDE_EXTENSION
    centre = (env_lo + env_hi) / 2
    for b_lo, b_hi, kind in blocked:
        if b_hi <= centre:
            v_lo = max(v_lo, b_hi)
        elif b_lo >= centre:
            v_hi = min(v_hi, b_lo)
        else:
            notes.append(f"{'柱' if kind == 'column' else '墙'}位于管线束中间（v {b_lo / 1000:.2f}–{b_hi / 1000:.2f} m），宽度限制未计入")

    if not np.isfinite(ceiling_max):
        ceiling_max = ceiling
    # Crossing services run between the crossing beams; they pass under beams running along the corridor.
    # Only beams over the part where the bundle can be matter: outside it the service can drop locally (N1).
    # Between the crossing beams the service can rise to the underside of the slab.
    slab = rules.layout.slab_thickness_mm
    across = sorted((b for b in beams if b.crossing and np.isfinite(b.top)), key=lambda b: b.s_lo)
    for c in crossings:
        lo_v, hi_v = max(c.v_lo, v_lo), min(c.v_hi, v_hi)
        under = [b.bottom for b in beams if not b.crossing and overlaps(b.v_lo, b.v_hi, lo_v, hi_v)
                 and b.s_lo - 1 <= c.s <= b.s_hi + 1]
        if under:
            c.ceiling = min(under)
            continue
        before = [b for b in across if b.s_hi <= c.s]
        after = [b for b in across if b.s_lo >= c.s]
        bay = ([before[-1]] if before else []) + ([after[0]] if after else [])
        c.ceiling = min(b.top for b in bay) - slab if bay and slab > 0 else ceiling_max
    members = {k for st in strands for k in st.segments + st.fittings}
    pattern = rules.layout.tray_water.leak_prone_pattern
    zone_keys = {c.key for c in crossings}
    leaks = []
    for e in package.mep():
        if e.key in members or e.solid is None or not is_leak_prone(e, pattern):
            continue
        cpt = e.solid.center
        if not (corridor.s0 <= corridor.s_of(cpt) <= corridor.s1 and corridor.v0 - 1000 <= corridor.v_of(cpt) <= corridor.v1 + 1000):
            continue
        lo, hi = e.solid.aabb()
        _, _, lv, hv = _plan_extent(e, corridor)
        on = next((k for k in graph.get(e.key, ()) if k in zone_keys), None)
        leaks.append(LeakItem(e.key, corridor.s_of(cpt), lv, hv, float(lo[2]), float(hi[2]), on))
    sec = Section(corridor=corridor, floor_z=floor_z, floor_name=floor.name if floor else "", ceiling=ceiling,
                  ceiling_key=ceiling_key, v_lo=v_lo, v_hi=v_hi, strands=strands, beams=beams,
                  crossings=crossings, blocked=blocked, notes=notes, crossing_limit=rules.layout.crossing_zone_max_mm,
                  ceiling_max=ceiling_max, leaks=leaks)
    if sec.large_crossings:
        sec.notes.append(f"{len(sec.large_crossings)} 根横穿管高度超过 {rules.layout.crossing_zone_max_mm:.0f} mm"
                         f"（{'、'.join(sorted({c.label for c in sec.large_crossings}))}），不从管线束上方通过，按节点冲突处理")
    return sec


def _v(e: Element, corridor: Corridor) -> float:
    return corridor.v_of(e.solid.center)


def _size_text(e: Element) -> str:
    r = e.record
    if (r.get("width_m") or 0) > 0 and (r.get("height_m") or 0) > 0:
        return f"{round(r['width_m'] * 1000)}×{round(r['height_m'] * 1000)}"
    if (r.get("nominal_diameter_m") or 0) > 0:
        return f"DN{round(r['nominal_diameter_m'] * 1000)}"
    if (r.get("outer_diameter_m") or 0) > 0:
        return f"Ø{round(r['outer_diameter_m'] * 1000)}"
    return ""


def _attach_fittings(package: Package, corridor: Corridor, strands: list[Strand]) -> None:
    """Fittings and accessories connected to a strand inside the corridor move with it.

    Also records on which side each strand leaves the corridor: a run across the corridor connected
    to one of its fittings or segments points to the side it goes to.
    """
    graph = connection_graph(package)
    owner = {k: s for s in strands for k in s.segments}
    for e in package.mep():
        if e.origin != "mep_family" or e.solid is None:
            continue
        c = e.solid.center
        if not (corridor.s0 <= corridor.s_of(c) <= corridor.s1):
            continue
        linked = [owner[n] for n in graph.get(e.key, ()) if n in owner]
        if linked:
            # A fitting joining two strands (e.g. a reducer) goes with the first; transitions are node work.
            linked[0].fittings.append(e.key)
    # A strand occupies the corridor length of its fittings too (elbows and tees reach past the run).
    for s in strands:
        for key in s.fittings:
            f = package.elements[key]
            s_lo, s_hi, _, _ = _plan_extent(f, corridor)
            s.s_lo = max(min(s.s_lo, s_lo), corridor.s0)
            s.s_hi = min(max(s.s_hi, s_hi), corridor.s1)
    members = {k for s in strands for k in s.segments + s.fittings}
    for s in strands:
        for key in s.segments + s.fittings:
            for other in graph.get(key, ()):
                if other in members:
                    continue
                side = _branch_side(package, corridor, other, s.v, graph, members)
                if side:
                    s.exits.add(side)


def _branch_side(package: Package, corridor: Corridor, key: str, v_from: float, graph, members, depth: int = 3):
    """Side (left/right) a branch goes to, following up to `depth` connections away from the strand."""
    seen = {key}
    frontier = [key]
    for _ in range(depth):
        nxt = []
        for k in frontier:
            e = package.elements.get(k)
            if e is not None and e.origin == "mep_curve" and e.solid is not None and e.record.get("start"):
                a, b = np.array(e.record["start"]) * 1000, np.array(e.record["end"]) * 1000
                d = b - a
                n = float(np.linalg.norm(d[:2]))
                if n > 1 and abs(float(np.dot(d[:2] / n, corridor.u[:2]))) < 0.3 and is_horizontal(e):
                    far = max((corridor.v_of(a), corridor.v_of(b)), key=lambda v: abs(v - v_from))
                    if abs(far - v_from) > 300:
                        return "left" if far < v_from else "right"
            for n2 in graph.get(k, ()):
                if n2 not in seen and n2 not in members:
                    seen.add(n2)
                    nxt.append(n2)
        frontier = nxt
    return None
