"""Read a structure JSON produced from construction drawings (SLBH 施工图翻模) into a Package.

The structure is known before any MEP model exists, so this gives an early clear-height estimate:
beam bottoms and slab bottoms above the floor below. Format (units mm, plan origin at the project grid origin):

    z:          {col_bottom, col_top, slab_top, slab_th}      floor below = col_bottom
    beams:      [{a: [x, y], b: [x, y], w, h, top, mark, basis, key?, uuid?, depth_src?, inferred?}]
    verticals:  [{kind: column | steel | wall | wall_unconfirmed, pts: [[x, y], ...], layer?}]
    slab:       [{pts: [[x, y], ...]}]   outlines; a ring inside a larger ring is an opening
    grid:       [[[x, y], [x, y]], ...]  grid_labels: [[name, [x, y]], ...]   (bubble positions)
    sources:    {name: drawing}           optional, shown in the viewer title

A beam is `inferred` when the file says so; otherwise when its depth does not come from its own
annotation (depth_src not in label / label_h_drawn_w, or basis "无集中标注…" / "密排…").
Beams are exact boxes; columns and walls are boxes fitted to their outline (L-shaped walls are approximate).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .classify import Classifier
from .geometry import box_from_points, points_in_rings, swept_box
from .package import Element, Grid, Level, Package
from .rules import RuleSet

OWN_DEPTH = ("label", "label_h_drawn_w")
VERTICAL_KIND = {"column": "column", "steel": "column", "wall": "wall", "wall_unconfirmed": "wall"}
VERTICAL_LABEL = {"column": "柱", "steel": "钢柱", "wall": "墙", "wall_unconfirmed": "墙（待确认）"}
GRID_LABEL_TOLERANCE = 500.0   # mm off the grid line's extension; unlabelled lines are left out


def beam_inferred(b: dict) -> bool:
    if "inferred" in b:
        return bool(b["inferred"])
    basis = b.get("basis", "")
    if basis.startswith("无集中标注") or basis.startswith("密排"):
        return True
    return b.get("depth_src", "label") not in OWN_DEPTH


def ring_area(pts) -> float:
    p = np.asarray(pts, float)
    return 0.5 * float(np.dot(p[:, 0], np.roll(p[:, 1], -1)) - np.dot(p[:, 1], np.roll(p[:, 0], -1)))


def load_structure(path: str | Path, rules: RuleSet, title: str = "", floor_name: str = "1F",
                   upper_name: str = "2F") -> Package:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    z = data["z"]
    floor_z, col_top = float(z["col_bottom"]), float(z["col_top"])
    slab_top, slab_th = float(z["slab_top"]), float(z["slab_th"])
    elements: dict[str, Element] = {}

    for b in data.get("beams", []):
        w, h, top = float(b["w"]), float(b["h"]), float(b["top"])
        p0 = np.array([b["a"][0], b["a"][1], top - h / 2], float)
        p1 = np.array([b["b"][0], b["b"][1], top - h / 2], float)
        d = p1 - p0
        key = b.get("key") or b.get("uuid") or b.get("id") or f"{upper_name}|beam|{len(elements)}"
        mark = b.get("mark", "")
        elements[key] = Element(
            key=key, origin="obstacle", kind="beam", domain="structure", category=f"{upper_name} 梁",
            family="结构梁", type_name=f"{mark} {w:.0f}×{h:.0f}，顶 {top / 1000:.3f}", level=upper_name,
            system_type=mark, size_text=f"{w:.0f}×{h:.0f}", group="structure",
            solid=swept_box(p0, p1, np.array([-d[1], d[0], 0.0]), w / 2, h / 2),
            record={"uuid": b.get("uuid"), "depth_src": b.get("depth_src")},
            basis=b.get("basis", ""), inferred=beam_inferred(b),
        )

    for i, v in enumerate(data.get("verticals", [])):
        kind = VERTICAL_KIND.get(v.get("kind", ""), "wall")
        pts = np.asarray(v["pts"], float)
        pts3 = np.vstack([np.c_[pts, np.full(len(pts), floor_z)], np.c_[pts, np.full(len(pts), col_top)]])
        key = f"{floor_name}|{v.get('kind', kind)}|{i}"
        label = VERTICAL_LABEL.get(v.get("kind", ""), v.get("kind", kind))
        elements[key] = Element(
            key=key, origin="obstacle", kind=kind, domain="wall" if kind == "wall" else "structure",
            category=f"{floor_name} {label}", family=label, type_name=v.get("layer", ""), level=floor_name,
            group="wall" if kind == "wall" else "structure", solid=box_from_points(pts3),
            basis=data.get("sources", {}).get(floor_name, ""),
        )

    # Slabs: every outline not inside a larger one is a slab; outlines inside it are its openings.
    rings = [np.asarray(s["pts"], float) for s in data.get("slab", [])]
    sizes = [abs(ring_area(r)) for r in rings]
    parent = []
    for i, r in enumerate(rings):
        c = r.mean(axis=0)
        owners = [j for j in range(len(rings))
                  if j != i and sizes[j] > sizes[i] and points_in_rings(np.array([c[0]]), np.array([c[1]]), [rings[j]])[0]]
        parent.append(min(owners, key=lambda j: sizes[j]) if owners else None)
    for i, r in enumerate(rings):
        if parent[i] is not None:
            continue
        footprint = [r] + [rings[j] for j in range(len(rings)) if parent[j] == i]
        pts3 = np.vstack([np.c_[r, np.full(len(r), slab_top - slab_th)], np.c_[r, np.full(len(r), slab_top)]])
        key = f"{upper_name}|slab|{i}"
        elements[key] = Element(
            key=key, origin="obstacle", kind="slab", domain="structure", category=f"{upper_name} 楼板",
            family="楼板", type_name=f"板厚 {slab_th:.0f}，顶 {slab_top / 1000:.3f}", level=upper_name,
            size_text=f"板底 {(slab_top - slab_th) / 1000:.3f}", group="structure", solid=box_from_points(pts3),
            footprint=footprint, basis=data.get("sources", {}).get(upper_name, ""),
        )

    grids = []
    labels = [(n, np.asarray(p, float)) for n, p in data.get("grid_labels", [])]
    for seg in data.get("grid", []):
        p, q = np.asarray(seg[0], float), np.asarray(seg[1], float)
        length = float(np.linalg.norm(q - p))
        if length < 1.0:
            continue
        d = (q - p) / length
        best = None
        for name, lp in labels:
            r = lp - p
            off = abs(float(d[0] * r[1] - d[1] * r[0]))
            t = float(np.dot(r, d))
            if (t < 0 or t > length) and off <= GRID_LABEL_TOLERANCE and (best is None or off < best[0]):
                best = (off, name)
        if best:
            grids.append(Grid(name=best[1], start=p, end=q))

    manifest = {"project_name": title or path.stem, "view_name": "施工图翻模结构", "created_at_utc": "",
                "schema_version": "structure-json", "sources": data.get("sources", {})}
    levels = [Level(name=floor_name, elevation=floor_z, source="HOST"),
              Level(name=upper_name, elevation=slab_top, source="HOST")]
    return Package(path=path, manifest=manifest, elements=elements, levels=levels, grids=grids,
                   warnings=[], classifier=Classifier(rules))
