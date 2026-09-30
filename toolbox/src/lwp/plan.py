"""Layout plans: apply a solved section to the model, serialise, and measure it."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path

import numpy as np

from . import __version__
from .corridor import Corridor
from .detect import CLEARANCE, HARD, Conflict, connection_graph, detect_conflicts, is_horizontal
from .geometry import Solid
from .package import Package
from .rules import RuleSet
from .section import Section
from .solver import SCHEMES, Layout


def translate(solid: Solid, t: np.ndarray) -> Solid:
    s = copy.copy(solid)
    s.center = solid.center + t
    if solid.segment is not None:
        s.segment = (solid.segment[0] + t, solid.segment[1] + t)
    return s


def apply(package: Package, sec: Section, layout: Layout, rules: RuleSet | None = None
          ) -> tuple[Package, dict[str, np.ndarray]]:
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

    if rules is not None and rules.layout.crossing_zone == "top" and sec.zone_crossings and layout.placements:
        tops = [layout.placements[s.id].z + s.height for s in sec.movable if s.id in layout.placements]
        tops += [s.top for s in sec.fixed]
        zone_bottom = max(tops) + rules.clearance_mm.default
        graph = connection_graph(package)
        for key, z_lo, z_hi, _ in sec.zone_crossings:
            if z_lo >= zone_bottom:
                continue
            t = np.array([0.0, 0.0, zone_bottom - z_lo])
            move(key, t)
            # Fittings of the crossing run inside the corridor go with it.
            for other in graph.get(key, ()):
                e = package.elements.get(other)
                if e is not None and e.origin == "mep_family" and e.solid is not None and in_corridor(e.solid.center, sec.corridor):
                    move(other, t)
    return _replace(package, elements), moves


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


def evaluate(before: Package, planned: dict[str, Package], gold: Package | None, rules: RuleSet,
             sec: Section) -> dict:
    """Clashes and cross-section statistics in the corridor: original, each scheme, and the human result."""
    rows = {}
    subjects = [("original", "原模型", before)] + [(k, SCHEMES[k], p) for k, p in planned.items()]
    if gold is not None:
        subjects.append(("gold", "人工排布", gold))
    for key, name, pkg in subjects:
        conflicts = corridor_conflicts(pkg, rules, sec.corridor)
        rows[key] = {
            "name": name,
            "hard": sum(c.type == HARD for c in conflicts),
            "clearance": sum(c.type == CLEARANCE for c in conflicts),
            "hard_structure": sum(c.type == HARD and not (pkg.elements[c.a].is_mep and pkg.elements[c.b].is_mep) for c in conflicts),
            **station_profile(pkg, sec.corridor, sec.floor_z),
        }
    return rows


def plan_json(sec: Section, layout: Layout, rules: RuleSet, moves: dict[str, np.ndarray], package: Package) -> dict:
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
