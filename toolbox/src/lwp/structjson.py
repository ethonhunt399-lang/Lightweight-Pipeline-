"""Read a structure JSON produced from construction drawings (SLBH 施工图翻模) into a Package.

The structure is known before any MEP model exists, so this gives an early clear-height estimate:
beam bottoms and slab bottoms above the floor below.

Two formats are read:

* **slbh.structure v1** (current; spec in docs/structure-v1.md, copied from the SLBH repo
  tools/struct/STRUCTURE_V1.md). Identity is the member `uuid`; "inferred" is taken from the file
  (`basis.inferred`), never guessed here. 1.1 adds optional slab `cuts` / `slope` and wall `top_profile`;
  cuts are read as openings, a sloped slab's box reaches its high end, `top_profile` is not needed here.
* the earlier ad-hoc structure JSON (kept for one version, removed once all projects are on v1):

    z:          {col_bottom, col_top, slab_top, slab_th}      floor below = col_bottom
    beams:      [{a: [x, y], b: [x, y], w, h, top, mark, basis, key?, uuid?, depth_src?, inferred?}]
    verticals:  [{kind: column | steel | wall | wall_unconfirmed, pts: [[x, y], ...], layer?}]
    slab:       [{pts: [[x, y], ...]}]   outlines; a ring inside a larger ring is an opening
    grid:       [[[x, y], [x, y]], ...]  grid_labels: [[name, [x, y]], ...]   (bubble positions)
    sources:    {name: drawing}           optional, shown in the viewer title

  In that format a beam is `inferred` when the file says so; otherwise when its depth does not come from its
  own annotation (depth_src not in label / label_h_drawn_w, or basis "无集中标注…" / "密排…").

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


V1_SCHEMA = "slbh.structure"
V1_TYPE_LABEL = {"column": "柱", "wall": "墙", "beam": "梁", "slab": "楼板"}


def load_structure(path: str | Path, rules: RuleSet, title: str = "", floor_name: str = "1F",
                   upper_name: str = "2F") -> Package:
    """floor_name / upper_name only apply to the earlier format; v1 files name their own levels."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("schema") == V1_SCHEMA:
        return _load_v1(path, data, rules, title)
    return _load_legacy(path, data, rules, title, floor_name, upper_name)


def _load_v1(path: Path, data: dict, rules: RuleSet, title: str) -> Package:
    if not str(data.get("version", "")).startswith("1."):
        raise ValueError(f"{path}: slbh.structure major version 1 expected, got {data.get('version')}")
    levels = sorted((Level(name=lv["name"], elevation=float(lv["elevation"]), source="HOST") for lv in data["levels"]),
                    key=lambda lv: lv.elevation)
    elements: dict[str, Element] = {}
    for m in data["members"]:
        t, g, b = m["type"], m["geom"], m["basis"]
        z0, z1 = float(g["z"][0]), float(g["z"][1])
        common = dict(key=m["uuid"], origin="obstacle", category=f"{m['level']} {V1_TYPE_LABEL.get(t, t)}",
                      level=m["level"], group="structure", basis=b["rule"], inferred=bool(b["inferred"]),
                      record={"key": m["key"], "mark": m["mark"], "status": m["status"], "source": b["source"],
                              "inferred_fields": b["inferred_fields"], "grade": m["grade"]})
        if t == "beam":
            p0 = np.array([g["a"][0], g["a"][1], (z0 + z1) / 2], float)
            p1 = np.array([g["b"][0], g["b"][1], (z0 + z1) / 2], float)
            d = p1 - p0
            w, h = float(g["b_w"]), float(g["h"])
            elements[m["uuid"]] = Element(
                kind="beam", domain="structure", family="结构梁",
                type_name=f"{m['mark']} {w:.0f}×{h:.0f}，顶 {z1 / 1000:.3f}", system_type=m["mark"],
                size_text=f"{w:.0f}×{h:.0f}", solid=swept_box(p0, p1, np.array([-d[1], d[0], 0.0]), w / 2, h / 2),
                **common)
        elif t in ("column", "wall"):
            pts = np.asarray(g["outline"], float)
            pts3 = np.vstack([np.c_[pts, np.full(len(pts), z0)], np.c_[pts, np.full(len(pts), z1)]])
            label = V1_TYPE_LABEL[t]
            size = (f"{g['b']:.0f}×{g['h']:.0f}" if g.get("shape") == "rect" else f"D{g['d']:.0f}" if g.get("shape") == "round"
                    else f"厚 {g['thickness']:.0f}" if "thickness" in g else "异形")
            elements[m["uuid"]] = Element(
                kind=t, domain="wall" if t == "wall" else "structure", family=label,
                type_name=f"{m['mark']} {size}".strip(), system_type=m["mark"], size_text=size,
                solid=box_from_points(pts3), **{**common, "group": "wall" if t == "wall" else "structure"})
        elif t == "slab":
            outer = np.asarray(g["outline"], float)
            # 1.1: `cuts` are areas taken out of this slab (other slabs' zones, the folded-slab area, strips over
            # members); they act like openings here and may reach past the outline.
            footprint = [outer] + [np.asarray(h_, float) for h_ in g.get("holes", []) + g.get("cuts", [])]
            # 1.1: a sloped slab gives its low end in `top` / `z`; the solid spans up to the high end. Clear height
            # takes the box bottom, i.e. the low end everywhere (conservative).
            z1_ = z1 + float(g["slope"]["rise"]) if g.get("slope") else z1
            pts3 = np.vstack([np.c_[outer, np.full(len(outer), z0)], np.c_[outer, np.full(len(outer), z1_)]])
            elements[m["uuid"]] = Element(
                kind="slab", domain="structure", family="楼板",
                type_name=f"板厚 {float(g['thickness']):.0f}，顶 {z1 / 1000:.3f}", system_type=m["mark"],
                size_text=f"板底 {z0 / 1000:.3f}", solid=box_from_points(pts3), footprint=footprint, **common)
    grids = [Grid(name=gd["name"], start=np.asarray(gd["a"], float), end=np.asarray(gd["b"], float))
             for gd in data.get("grids", [])]
    proj = data.get("project", {})
    manifest = {"project_name": title or f"{proj.get('name', proj.get('id', ''))} {proj.get('building', '')}".strip(),
                "view_name": f"施工图翻模结构 · {data.get('scope', '')} · {data.get('tier', '')}",
                "created_at_utc": "", "schema_version": f"{V1_SCHEMA} {data['version']}",
                "sources": {s_["id"]: s_.get("title", "") for s_ in data.get("sources", [])},
                "issues": len(data.get("issues", []))}
    return Package(path=path, manifest=manifest, elements=elements, levels=levels, grids=grids,
                   warnings=[], classifier=Classifier(rules))


def _load_legacy(path: Path, data: dict, rules: RuleSet, title: str, floor_name: str, upper_name: str) -> Package:
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
