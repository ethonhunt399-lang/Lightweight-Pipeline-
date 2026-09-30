"""Single-file, offline, read-only 3D viewer (three.js) for a package and its check results."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from .. import headroom_map
from ..detect import Conflict, HeadroomItem, floor_level
from ..grids import GridLocator
from ..package import Package
from ..rules import RuleSet

HERE = Path(__file__).parent

# Display colours per system class; obstacles are neutral.
COLORS = {
    "fire_hydrant": "#e0342b", "sprinkler": "#f59e0b", "fire_other": "#be185d",
    "water_supply": "#16a34a", "hot_water": "#db2777", "pressure_drainage": "#92400e",
    "gravity_drainage": "#ca8a04", "smoke_exhaust": "#8b5cf6", "exhaust_air": "#6366f1",
    "supply_air": "#0ea5e9", "pressurization": "#06b6d4", "power_tray": "#2563eb",
    "weak_tray": "#14b8a6", "unknown": "#9ca3af",
    "beam": "#b9b4aa", "column": "#b9b4aa", "foundation": "#b9b4aa", "wall": "#d6d3cd",
}
OBSTACLE_LABELS = {"beam": "梁", "column": "柱", "foundation": "基础", "wall": "墙"}


MOVED_MM = 20.0   # displacement below this counts as unchanged in the comparison


def build_data(package: Package, rules: RuleSet, conflicts: list[Conflict] | None = None,
               headroom: list[HeadroomItem] | None = None, reference: Package | None = None,
               reference_name: str = "调整前", region: tuple[float, float, float, float] | None = None,
               title_suffix: str = "") -> dict:
    """region: (xmin, ymin, xmax, ymax) in mm — show only elements whose plan extent touches it."""

    def inside(e) -> bool:
        if region is None:
            return True
        lo, hi = e.solid.aabb()
        return hi[0] >= region[0] and lo[0] <= region[2] and hi[1] >= region[1] and lo[1] <= region[3]

    elements = [e for e in package.elements.values() if e.solid is not None and inside(e)]
    if not elements:
        raise ValueError("package has no geometry")
    lows = np.array([e.solid.aabb()[0] for e in elements])
    highs = np.array([e.solid.aabb()[1] for e in elements])
    origin = np.round((lows.min(axis=0) + highs.max(axis=0)) / 2 / 1000) * 1000
    origin[2] = 0.0

    def m(p) -> list[float]:
        return [round(float(v) / 1000, 4) for v in (np.asarray(p) - origin)]

    def geometry(s) -> dict:
        if s.is_capsule:
            return {"t": "cyl", "g": m(s.segment[0]) + m(s.segment[1]) + [round(s.radius / 1000, 4)]}
        return {"t": "box", "g": m(s.center) + [round(float(v), 5) for v in s.axes.ravel()]
                + [round(float(v) / 1000, 4) for v in s.half]}

    matcher = _RunMatcher(reference) if reference is not None else None
    index: dict[str, int] = {}
    items = []
    for e in elements:
        s = e.solid
        cls = e.cls if e.is_mep else e.kind
        geom = geometry(s)
        lo, hi = s.aabb()
        level = floor_level(package, rules, float(lo[2]))
        index[e.key] = len(items)
        items.append({
            **geom, "c": cls, "k": e.key, "l": e.label(), "fam": e.family, "typ": e.type_name,
            "sys": e.system_type or e.system_name, "ins": round(e.insulation_mm), "insrc": e.insulation_source,
            "bot": round(float(lo[2])), "top": round(float(hi[2])),
            "lv": level.name if level else "", "clr": round(float(lo[2]) - level.elevation) if level else None,
            "ex": s.exact, "o": {"mep_curve": "c", "mep_family": "f"}.get(e.origin, "x"),
        })
        if reference is not None and e.is_mep:
            items[-1].update(_compare(e, reference.elements.get(e.key), matcher))

    classes = {}
    for cls, count in _count(items).items():
        if cls in rules.system_classes:
            label, kind = rules.system_classes[cls].label, "mep"
        else:
            label, kind = OBSTACLE_LABELS.get(cls, cls), "obstacle"
        classes[cls] = {"label": label, "color": COLORS.get(cls, "#9ca3af"), "count": count, "kind": kind}

    locator = GridLocator(package.grids)
    out_conflicts = []
    for c in conflicts or []:
        if c.a in index and c.b in index:
            out_conflicts.append({"t": c.type, "a": index[c.a], "b": index[c.b], "d": round(c.distance_mm),
                                  "r": round(c.required_mm), "p": m(c.location), "g": locator.describe(c.location)})

    host_levels = package.host_levels()
    floor_z = host_levels[0].elevation if host_levels else float(lows[:, 2].min())
    for lv in host_levels:
        if lv.elevation <= float(np.median(lows[:, 2])):
            floor_z = lv.elevation

    hmap = headroom_map.build(package, floor_z)
    lowest = hmap.lowest()
    clear_cells = np.where(np.isnan(hmap.clear), -1, np.round(hmap.clear / 10)).astype(int)   # centimetres
    hm = {
        "x0": round((hmap.x0 - origin[0]) / 1000, 4), "y0": round((hmap.y0 - origin[1]) / 1000, 4),
        "cell": hmap.cell / 1000, "nx": hmap.nx, "ny": hmap.ny, "cm": clear_cells.ravel().tolist(),
        "src": [index.get(hmap.keys[k], -1) if k >= 0 else -1 for k in hmap.source.ravel().tolist()],
        "lowest": None if lowest is None else {
            "clear": round(lowest[0]), "p": m([lowest[1], lowest[2], floor_z])[:2],
            "e": index.get(lowest[3], -1), "g": GridLocator(package.grids).describe((lowest[1], lowest[2])),
        },
    }

    ref_items = []
    if reference is not None:
        for e in reference.mep():
            if e.solid is None or not inside(e):
                continue
            ref_items.append({**geometry(e.solid), "c": e.cls, "l": e.label(),
                              "st": "kept" if e.key in package.elements else "removed"})
    # Grid lines span the whole building: clip them to the exported range plus a margin.
    margin = 3000.0
    rect = (lows[:, 0].min() - margin, lows[:, 1].min() - margin, highs[:, 0].max() + margin, highs[:, 1].max() + margin)
    grids = []
    for g in package.grids:
        clipped = _clip_segment(g.start, g.end, rect)
        if clipped is None:
            continue
        a, b = clipped
        grids.append({"n": g.name, "a": m([a[0], a[1], floor_z])[:2], "b": m([b[0], b[1], floor_z])[:2]})

    m_ = package.manifest
    return {
        "meta": {
            "project": m_.get("project_name", ""), "view": m_.get("view_name", "") + title_suffix,
            "exported": m_.get("created_at_utc", ""), "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "rules": f"{rules.name} {rules.version}", "min_clear": rules.headroom.min_clear_mm,
            "floor_z": round((floor_z - origin[2]) / 1000, 4), "origin_mm": [float(v) for v in origin],
        },
        "classes": classes,
        "elements": items,
        "conflicts": out_conflicts,
        "grids": grids,
        "levels": [{"n": lv.name, "z": round((lv.elevation - origin[2]) / 1000, 4)} for lv in host_levels],
        "headroom": hm,
        "reference": {"name": reference_name, "elements": ref_items} if reference is not None else None,
    }


class _RunMatcher:
    """Finds the reference run a new run was cut from: same system and kind, parallel, within 150 mm in plan."""

    CELL = 5000.0

    def __init__(self, reference: Package):
        self.buckets: dict[tuple, list] = {}
        for e in reference.mep():
            r = e.record
            if e.origin != "mep_curve" or not r.get("start") or not r.get("end"):
                continue
            a, b = np.array(r["start"]) * 1000, np.array(r["end"]) * 1000
            lo, hi = np.minimum(a, b)[:2], np.maximum(a, b)[:2]
            for i in range(int(lo[0] // self.CELL), int(hi[0] // self.CELL) + 1):
                for j in range(int(lo[1] // self.CELL), int(hi[1] // self.CELL) + 1):
                    self.buckets.setdefault((i, j), []).append((e, a, b))

    def match(self, e, mid: np.ndarray, direction: np.ndarray):
        best = None
        for other, a, b in self.buckets.get((int(mid[0] // self.CELL), int(mid[1] // self.CELL)), []):
            if other.kind != e.kind or (other.system_type or other.system_name) != (e.system_type or e.system_name):
                continue
            d = b - a
            n = float(np.linalg.norm(d[:2]))
            if n < 1e-6 or abs(float(np.dot(d[:2] / n, direction[:2]))) < 0.98:
                continue
            t = float(np.clip(np.dot(mid[:2] - a[:2], d[:2]) / (n * n), 0, 1))
            p = a + t * d
            plan = float(np.linalg.norm(mid[:2] - p[:2]))
            if plan <= 150 and (best is None or plan < best[0]):
                best = (plan, mid - p)
        return best


def _compare(e, before, matcher: _RunMatcher | None = None) -> dict:
    """Movement of an element relative to the same element (UniqueId) in the reference model.

    Runs without a UniqueId match (split or re-drawn during coordination) are matched by position.
    """
    if before is None or before.solid is None:
        r = e.record
        if matcher is not None and e.origin == "mep_curve" and r.get("start") and r.get("end"):
            a, b = np.array(r["start"]) * 1000, np.array(r["end"]) * 1000
            direction = b - a
            if np.linalg.norm(direction[:2]) > 1e-6:
                found = matcher.match(e, (a + b) / 2, direction / np.linalg.norm(direction))
                if found is not None:
                    _, delta = found
                    if float(np.linalg.norm(delta)) < MOVED_MM:
                        return {"st": "same", "dz": 0, "dh": 0, "pm": 1}
                    return {"st": "moved", "dz": round(float(delta[2])), "dh": round(float(np.hypot(delta[0], delta[1]))), "pm": 1}
        return {"st": "new"}
    if e.record.get("start") and e.record.get("end") and before.record.get("start") and before.record.get("end"):
        # Runs may be split or extended: measure the new midpoint against the old centreline.
        mid = (np.array(e.record["start"]) + np.array(e.record["end"])) * 500
        a, b = np.array(before.record["start"]) * 1000, np.array(before.record["end"]) * 1000
        ab = b - a
        t = float(np.clip(np.dot(mid - a, ab) / max(float(ab @ ab), 1e-9), 0, 1))
        closest = a + t * ab
        delta = mid - closest
    else:
        delta = e.solid.center - before.solid.center
    disp = float(np.linalg.norm(delta))
    if disp < MOVED_MM:
        return {"st": "same", "dz": 0, "dh": 0}
    return {"st": "moved", "dz": round(float(delta[2])), "dh": round(float(np.hypot(delta[0], delta[1])))}


def write_viewer(path: Path, data: dict, title: str | None = None) -> Path:
    template = (HERE / "template.html").read_text(encoding="utf-8")
    three = (HERE / "three.min.js").read_text(encoding="utf-8")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    # Insert three.js last so that nothing inside the library is mistaken for a placeholder.
    html = (template
            .replace("__TITLE__", _escape(title or f"{data['meta']['project']} 管综预览"))
            .replace("__DATA__", payload)
            .replace("/*__THREE__*/", three))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path


def _clip_segment(p, q, rect):
    """Liang–Barsky clip of segment pq (XY) to rect (xmin, ymin, xmax, ymax)."""
    x0, y0, x1, y1 = float(p[0]), float(p[1]), float(q[0]), float(q[1])
    dx, dy = x1 - x0, y1 - y0
    t0, t1 = 0.0, 1.0
    for pk, qk in ((-dx, x0 - rect[0]), (dx, rect[2] - x0), (-dy, y0 - rect[1]), (dy, rect[3] - y0)):
        if abs(pk) < 1e-12:
            if qk < 0:
                return None
            continue
        t = qk / pk
        if pk < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return (x0 + t0 * dx, y0 + t0 * dy), (x0 + t1 * dx, y0 + t1 * dy)


def _count(items: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for it in items:
        counts[it["c"]] = counts.get(it["c"], 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
