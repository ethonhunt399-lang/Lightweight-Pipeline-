"""Layout plans: apply a solved section to the model, serialise, and measure it."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np

from . import __version__
from .corridor import Corridor
from .detect import CLEARANCE, HARD, Conflict, connection_graph, detect_conflicts, is_horizontal
from .geometry import Solid
from .package import Package
from .rules import RuleSet
from .connect import reconnect
from .nodes import side_exits
from .section import Section, is_leak_prone, overlaps
from .solver import SCHEMES, Layout


def translate(solid: Solid, t: np.ndarray) -> Solid:
    s = copy.copy(solid)
    s.center = solid.center + t
    if solid.segment is not None:
        s.segment = (solid.segment[0] + t, solid.segment[1] + t)
    return s


def apply(package: Package, sec: Section, layout: Layout, rules: RuleSet | None = None, nodes: bool = True
          ) -> tuple[Package, dict[str, np.ndarray], list, "Links"]:
    """A copy of the package with the strands (segments and their fittings) moved.

    With a top crossing zone, services crossing the corridor are lifted into it (above the bundle);
    their transitions where they leave the corridor are node work (N1) and not modelled here.
    """
    elements = dict(package.elements)
    moves: dict[str, np.ndarray] = {}

    def move(key: str, t: np.ndarray) -> None:
        e = elements.get(key)
        if e is None or e.solid is None or key in moves:
            return
        moved = copy.copy(e)
        moved.solid = translate(e.solid, t)
        if e.parts:
            moved.parts = [translate(p, t) for p in e.parts]
        if e.centre is not None:
            moved.centre = e.centre + t
        conns = [{**c, "origin": list(np.array(c["origin"]) + t / 1000)} if c.get("origin") else c for c in e.connectors]
        if e.leg_conns is not None:
            by_id = {id(old): new for old, new in zip(e.connectors, conns)}
            moved.leg_conns = [by_id.get(id(c), c) for c in e.leg_conns]
        moved.connectors = conns
        rec = dict(e.record)
        for field in ("start", "end"):
            if rec.get(field):
                rec[field] = list(np.array(rec[field]) + t / 1000)
        moved.record = rec
        elements[key] = moved
        moves[key] = t

    for st in sec.movable:
        p = layout.placements.get(st.id)
        if p is None:
            continue
        t = sec.corridor.w * (p.v - st.v) + np.array([0.0, 0.0, p.z - st.z])
        if float(np.linalg.norm(t)) < 0.5:
            continue
        for key in st.segments + st.fittings:
            move(key, t)

    graph = connection_graph(package)

    def move_crossing(key: str, t: np.ndarray) -> None:
        move(key, t)
        # Fittings of the crossing run inside the corridor go with it.
        for other in graph.get(key, ()):
            f = package.elements.get(other)
            if f is not None and f.origin == "mep_family" and f.solid is not None and in_corridor(f.solid.center, sec.corridor):
                move(other, t)

    if layout.crossings:
        # Heights chosen by the solver (stacked, and under the trays where water must not run over them).
        for c in sec.zone_crossings:
            z = layout.crossings.get(c.key)
            if z is not None and abs(z - c.z_lo) > 0.5:
                move_crossing(c.key, np.array([0.0, 0.0, z - c.z_lo]))
    elif rules is not None and rules.layout.crossing_zone == "top" and sec.zone_crossings and layout.placements:
        clr = rules.clearance_mm.default
        cor = sec.corridor
        placed: list[tuple[float, float, float, float, float, float]] = []   # s_lo, s_hi, v_lo, v_hi, z_lo, z_hi
        for c in sorted(sec.zone_crossings, key=lambda c: c.s):
            e = package.elements.get(c.key)
            if e is None or e.solid is None:
                continue
            lo, hi = e.solid.aabb()
            c_slo, c_shi = sorted((cor.s_of(lo), cor.s_of(hi)))
            # Just above the strands this service passes over …
            tops = [(layout.placements[st.id].z if st.id in layout.placements else st.z) + st.height
                    for st in sec.strands if st.s_lo - 1 <= c.s <= st.s_hi + 1]
            if not tops:
                continue
            z = max(tops) + clr
            # … and above crossing services already placed next to it (stacked, not overlapping).
            for _ in range(len(placed) + 1):
                hit = [p for p in placed if overlaps(p[0], p[1], c_slo, c_shi, clr) and overlaps(p[2], p[3], c.v_lo, c.v_hi)
                       and overlaps(p[4], p[5], z, z + c.height, clr)]
                if not hit:
                    break
                z = max(p[5] for p in hit) + clr
            if c.z_lo >= z and not any(overlaps(p[0], p[1], c_slo, c_shi, clr) and overlaps(p[2], p[3], c.v_lo, c.v_hi)
                                       and overlaps(p[4], p[5], c.z_lo, c.z_hi, clr) for p in placed):
                placed.append((c_slo, c_shi, c.v_lo, c.v_hi, c.z_lo, c.z_hi))
                continue
            move_crossing(c.key, np.array([0.0, 0.0, z - c.z_lo]))
            placed.append((c_slo, c_shi, c.v_lo, c.v_hi, z, z + c.height))
    made = []
    if nodes and rules is not None and layout.placements:
        made = side_exits(package, elements, sec, layout, rules, moves, move, graph)
    links = Links()
    if nodes:
        links.repairs, links.open = reconnect(package, elements, moves)
    return _replace(package, elements), moves, made, links


@dataclass
class Links:
    """Re-connections made after the moves (N0) and joints left open."""
    repairs: list = field(default_factory=list)
    open: list = field(default_factory=list)

    @property
    def pieces(self) -> int:
        return sum(len(r.pieces) for r in self.repairs)

    def to_dict(self) -> dict:
        return {"repairs": [r.to_dict() for r in self.repairs], "open": [j.to_dict() for j in self.open],
                "pieces": self.pieces, "stretched": sum(abs(r.stretch_mm) > 1 for r in self.repairs)}


def _replace(package: Package, elements: dict) -> Package:
    p = copy.copy(package)
    p.elements = elements
    return p


def in_corridor(point, corridor: Corridor, margin: float = 0.0) -> bool:
    s, v = corridor.s_of(point), corridor.v_of(point)
    return corridor.s0 - margin <= s <= corridor.s1 + margin and corridor.v0 - margin <= v <= corridor.v1 + margin


def corridor_conflicts(package: Package, rules: RuleSet, corridor: Corridor) -> list[Conflict]:
    return [c for c in detect_conflicts(package, rules)
            if c.type in (HARD, CLEARANCE) and in_corridor(c.location, corridor)]


def station_profile(package: Package, corridor: Corridor, floor_z: float, step: float = 1000.0) -> dict:
    """Sample cross-sections along the corridor: lowest bottom and number of levels of parallel runs."""
    runs = []
    for e in package.mep():
        r = e.record
        if e.origin != "mep_curve" or e.solid is None or not is_horizontal(e):
            continue
        a, b = np.array(r["start"]) * 1000, np.array(r["end"]) * 1000
        d = b - a
        n = float(np.linalg.norm(d[:2]))
        if n < 1 or abs(float(np.dot(d[:2] / n, corridor.u[:2]))) < 0.995:
            continue
        lo, hi = e.solid.aabb()
        v = corridor.v_of(e.solid.center)
        if not (corridor.v0 <= v <= corridor.v1):
            continue
        runs.append((min(corridor.s_of(lo), corridor.s_of(hi)), max(corridor.s_of(lo), corridor.s_of(hi)), float(lo[2])))
    lows, levels = [], []
    s = corridor.s0 + step / 2
    while s < corridor.s1:
        zs = sorted(z for s0, s1, z in runs if s0 <= s <= s1)
        if zs:
            lows.append(zs[0] - floor_z)
            clusters = 1
            for i in range(1, len(zs)):
                if zs[i] - zs[i - 1] > 30:
                    clusters += 1
            levels.append(clusters)
        s += step
    if not lows:
        return {"stations": 0}
    return {"stations": len(lows), "lowest_mm": round(min(lows)), "median_lowest_mm": round(float(np.median(lows))),
            "median_levels": float(np.median(levels)), "max_levels": max(levels)}


def water_over_tray(package: Package, corridor: Corridor, rules: RuleSet | None = None,
                    margin: float = 500.0) -> dict:
    """Water over trays in the corridor (plan footprints overlap, water above).

    parallel: water elements running along over a tray (overlap along the tray longer than the limit);
    crossing: short crossings (allowed by the project rule); leak: leak-prone items over a tray.
    """
    tw = rules.layout.tray_water if rules is not None else None
    limit = tw.parallel_max_mm if tw else 500.0
    pad = tw.leak_prone_margin_mm if tw else 100.0
    pattern = tw.leak_prone_pattern if tw else ""
    trays, water = [], []
    for e in package.mep():
        if e.solid is None or not in_corridor(e.solid.center, corridor, margin):
            continue
        if e.domain == "tray":
            lo, hi = e.solid.aabb()
            trays.append((e.key, lo, hi, _plan_dir(e)))
        elif e.group == "water":
            water.append((e, *e.solid.aabb()))
    parallel, crossing, leak = set(), set(), set()
    for tk, tl, th, t_dir in trays:
        for w, wl, wh in water:
            if wl[2] < th[2] - 1:
                continue
            leaky = is_leak_prone(w, pattern)
            p = pad if leaky else -5.0
            ox = min(wh[0], th[0] + p) - max(wl[0], tl[0] - p)
            oy = min(wh[1], th[1] + p) - max(wl[1], tl[1] - p)
            if ox <= 0 or oy <= 0:
                continue
            if leaky:
                leak.add(w.key)
            else:
                # Running along: the overlap along the tray is long and the water run is not across it.
                w_dir = _plan_dir(w)
                across = t_dir is not None and w_dir is not None and abs(float(np.dot(t_dir, w_dir))) < 0.5
                if t_dir is not None:
                    length = ox if abs(t_dir[0]) >= abs(t_dir[1]) else oy
                else:
                    length = min(ox, oy)          # tray fitting: both directions must be long
                if length > limit and not across:
                    parallel.add(w.key)
                else:
                    crossing.add(w.key)
                continue
    return {"parallel": parallel, "crossing": crossing - parallel, "leak": leak}


def _plan_dir(e) -> np.ndarray | None:
    r = e.record
    if e.origin != "mep_curve" or not r.get("start") or not r.get("end"):
        return None
    d = (np.array(r["end"]) - np.array(r["start"]))[:2]
    n = float(np.linalg.norm(d))
    return d / n if n > 1e-6 else None


NODE_EXIT = "出线引出"            # a strand's tee/elbow leg crossing a neighbour: rise-and-cross node (N2)
NODE_LARGE = "大尺寸横穿"         # crossing service too large for the zone above the bundle (N1)
NODE_CROSSING = "横穿翻弯"        # a lifted crossing service against its own fittings or risers (N1)


def node_kind(c: Conflict, sec: Section, moves: dict, handled: set | None = None) -> str | None:
    """Conflicts that a layout leaves to the node library by construction (transitions are not modelled)."""
    strand_of = {k: st.id for st in sec.strands for k in st.segments}
    fitting_of = {k: st.id for st in sec.strands for k in st.fittings}
    large = {x.key for x in sec.large_crossings}
    zone = {x.key for x in sec.zone_crossings}
    a, b = c.a, c.b
    handled = handled or set()
    if a in handled or b in handled:
        return None               # an N2 node was built here: what remains is a real clash
    for x, y in ((a, b), (b, a)):
        if x in fitting_of and (y in strand_of or y in fitting_of) and fitting_of[x] != (strand_of.get(y) or fitting_of.get(y)):
            return NODE_EXIT
    if a in large or b in large:
        return NODE_LARGE
    if (a in zone or b in zone) and (a in moves) != (b in moves):
        return NODE_CROSSING
    return None


def evaluate(before: Package, planned: dict[str, Package], gold: Package | None, rules: RuleSet,
             sec: Section, moves: dict[str, dict] | None = None, handled: dict[str, set] | None = None) -> dict:
    """Clashes and cross-section statistics in the corridor: original, each scheme, and the human plan.

    For schemes, hard clashes that are node work by construction are counted separately (hard_node).
    """
    rows = {}
    subjects = [("original", "原模型", before)] + [(k, SCHEMES[k], p) for k, p in planned.items()]
    if gold is not None:
        subjects.append(("gold", "人工方案", gold))
    for key, name, pkg in subjects:
        conflicts = corridor_conflicts(pkg, rules, sec.corridor)
        hard = [c for c in conflicts if c.type == HARD]
        nodes = {}
        if key in planned and moves is not None:
            for c in hard:
                kind = node_kind(c, sec, moves.get(key, {}), (handled or {}).get(key))
                if kind:
                    nodes[kind] = nodes.get(kind, 0) + 1
        fittings_with_node = (handled or {}).get(key, set())
        rows[key] = {
            "name": name,
            "hard": len(hard),
            "n2_unresolved": sum(c.a in fittings_with_node or c.b in fittings_with_node for c in hard),
            "hard_node": sum(nodes.values()),
            "nodes": nodes,
            "clearance": sum(c.type == CLEARANCE for c in conflicts),
            "hard_structure": sum(not (pkg.elements[c.a].is_mep and pkg.elements[c.b].is_mep) for c in hard),
            **{f"water_{k}_tray": len(v) for k, v in water_over_tray(pkg, sec.corridor, rules).items()},
            **station_profile(pkg, sec.corridor, sec.floor_z),
        }
    return rows


def review_human(gold: Package, gold_sec: Section, rules: RuleSet, ev: dict, layouts: dict, locator) -> dict:
    """Where the human plan can still be improved: rule checks plus comparison with the schemes."""
    conflicts = corridor_conflicts(gold, rules, gold_sec.corridor)
    hard = [c for c in conflicts if c.type == HARD]
    short = sorted((c for c in conflicts if c.type == CLEARANCE), key=lambda c: c.distance_mm - c.required_mm)

    def item(c):
        a, b = gold.elements[c.a], gold.elements[c.b]
        return {"where": locator.describe(c.location), "a": a.label(), "b": b.label(),
                "distance_mm": round(c.distance_mm), "required_mm": round(c.required_mm)}

    # Electrical below water: a tray under a water pipe whose footprints overlap.
    tray_under = []
    strands = gold_sec.strands
    for t in strands:
        if t.domain != "tray":
            continue
        for w in strands:
            if w.group != "water" or not overlaps(t.s_lo, t.s_hi, w.s_lo, w.s_hi):
                continue
            if overlaps(t.v - t.width / 2, t.v + t.width / 2, w.v - w.width / 2, w.v + w.width / 2) and t.top <= w.z:
                tray_under.append({"tray": t.label(), "water": w.label(),
                                   "where": locator.describe(gold_sec.corridor.point((max(t.s_lo, w.s_lo) + min(t.s_hi, w.s_hi)) / 2, t.v, t.z))})
    levels = sorted({round(s.z / 30) for s in strands})
    findings = []
    g = ev.get("gold", {})
    best = {k: v for k, v in ev.items() if k in layouts and layouts[k].placements}
    if best and g.get("lowest_mm") is not None:
        k = max(best, key=lambda k: best[k].get("lowest_mm") or -1)
        gain = (best[k].get("lowest_mm") or 0) - g["lowest_mm"]
        if gain > 50:
            findings.append(f"净高可提高约 {gain:.0f} mm：{SCHEMES[k]}方案最低管底 {best[k]['lowest_mm'] / 1000:.2f} m，人工方案 {g['lowest_mm'] / 1000:.2f} m")
        elif gain < -50:
            findings.append(f"人工方案净高比自动方案高 {-gain:.0f} mm（自动方案在此项不如人工）")
    if best:
        k = min(best, key=lambda k: layouts[k].metrics.get("layers", 99))
        if layouts[k].metrics.get("layers", 99) < len(levels):
            findings.append(f"人工方案管底有 {len(levels)} 个不同标高；{SCHEMES[k]}方案只需 {layouts[k].metrics['layers']} 层，"
                            "支吊架层数可减少、排布更规整")
    if hard:
        findings.append(f"人工方案在走廊内仍有 {len(hard)} 处硬碰撞（见下表）")
    if short:
        findings.append(f"人工方案有 {len(short)} 处净距低于规则要求（按已确认的校准净距）")
    if tray_under:
        findings.append(f"人工方案有 {len(tray_under)} 处桥架位于水管正下方（违反电上水下）")
    return {"findings": findings, "hard": [item(c) for c in hard], "clearance": [item(c) for c in short[:15]],
            "clearance_total": len(short), "tray_under_water": tray_under[:15], "levels": len(levels)}


def plan_json(sec: Section, layout: Layout, rules: RuleSet, moves: dict[str, np.ndarray], package: Package,
              nodes: list | None = None, links: "Links | None" = None) -> dict:
    strands = []
    for st in sec.strands:
        p = layout.placements.get(st.id)
        strands.append({
            "id": st.id, "label": st.label(), "class": st.cls, "system": st.system, "kind": st.kind,
            "width_mm": round(st.width), "height_mm": round(st.height), "movable": st.movable, "reason": st.reason,
            "from": {"v_mm": round(st.v), "z_mm": round(st.z)},
            "to": {"layer": p.layer, "v_mm": round(p.v), "z_mm": round(p.z)} if p else None,
            "segments": st.segments, "fittings": st.fittings,
        })
    return {
        "schema": "lwp.layout_plan", "schema_version": "0.1.0", "tool": f"lwp {__version__}",
        "created": datetime.now().isoformat(timespec="seconds"),
        "source": {"project": package.manifest.get("project_name"), "exported": package.manifest.get("created_at_utc"),
                   "package": str(package.path)},
        "rules": {"name": rules.name, "version": rules.version, "file": rules.source},
        "corridor": sec.corridor.to_dict(), "scheme": layout.scheme, "scheme_name": SCHEMES.get(layout.scheme, ""),
        "status": layout.status, "stages": layout.stages, "metrics": layout.metrics, "diagnosis": layout.diagnosis,
        "section": {"floor": sec.floor_name, "floor_z_mm": round(sec.floor_z), "ceiling_z_mm": round(sec.ceiling),
                    "v_lo_mm": round(sec.v_lo), "v_hi_mm": round(sec.v_hi), "notes": sec.notes,
                    "crossings": len(sec.crossings)},
        "layers": layout.layers, "strands": strands,
        "nodes": [n.to_dict() for n in nodes or []],
        "crossings": {k: round(z) for k, z in layout.crossings.items()},
        "links": links.to_dict() if links is not None else None,
        "moves": {k: [round(float(x), 1) for x in t] for k, t in moves.items()},
    }


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=_default), encoding="utf-8")


def _default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o))
