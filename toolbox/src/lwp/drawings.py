"""Read construction drawings (DXF) and bring what the layout needs into model coordinates.

  * alignment: the drawing's grid lines are matched to the model's grids by their spacing pattern (both
    families of parallel axes), giving a rigid 2D transform drawing → model;
  * parking stalls (outline of the car blocks), drive-lane centre lines, civil-defence (人防) walls
    and labels, fire compartments;
  * design-note paragraphs that carry layout rules (sprinkler heads, trays, busways, water over
    electrical equipment).

Needs `ezdxf` (`uv sync --extra drawings`). Drawings are large (tens of MB); only the sheets asked for are read.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

GRID_LAYER = "A-Grid"
STALL_BLOCK = "PMS-CAR"           # car blocks whose largest closed outline is the stall
LANE_LAYERS = ("车道流线",)
RF_WALL_LAYERS = ("RF-WALL",)
RF_TEXT_LAYERS = ("人防文字",)
FIRE_ZONE_LAYERS = ("防火分区填充线",)
STALL_NUMBER_LAYERS = ("车位编号",)


def _short(layer: str) -> str:
    """Bound xref layers are named PARENT$0$LAYER: keep the last part."""
    return layer.split("$0$")[-1]


def walk(doc, max_depth: int = 12):
    """(kind, entity, matrix to drawing WCS, insert path) through nested block references."""
    from ezdxf.math import Matrix44

    def rec(entities, m, path, depth):
        for e in entities:
            if e.dxftype() == "INSERT":
                try:
                    mi = e.matrix44() * m
                except Exception:
                    continue
                yield "insert", e, mi, path
                blk = doc.blocks.get(e.dxf.name)
                if blk is not None and depth < max_depth:
                    yield from rec(blk, mi, path + (e.dxf.name,), depth + 1)
                for a in e.attribs:
                    yield "entity", a, m, path
            else:
                yield "entity", e, m, path

    yield from rec(doc.modelspace(), Matrix44(), (), 0)


@dataclass
class Alignment:
    """model_xy = R @ drawing_xy + t (mm)."""
    R: np.ndarray
    t: np.ndarray
    matched: dict = field(default_factory=dict)   # family → (matched grids, model grids, residual mm)

    def __call__(self, p) -> np.ndarray:
        p = np.asarray(p, float)[..., :2]
        return p @ self.R.T + self.t

    def to_dict(self) -> dict:
        return {"R": self.R.tolist(), "t": self.t.tolist(), "matched": self.matched}


def grid_lines(doc) -> list[tuple[np.ndarray, np.ndarray]]:
    out = []
    for kind, e, m, _ in walk(doc):
        if kind == "entity" and e.dxftype() == "LINE" and _short(e.dxf.layer) == GRID_LAYER:
            a, b = m.transform(e.dxf.start), m.transform(e.dxf.end)
            out.append((np.array([a.x, a.y]), np.array([b.x, b.y])))
    return out


def align(lines, model_grids, tol: float = 20.0) -> Alignment:
    """Rigid transform from the drawing's grid lines to the model's grids (by spacing pattern).

    model_grids: [(name, start_xy, end_xy)] in mm. Only main axes (numbers, letters) are used.
    """
    main = re.compile(r"^(\d{1,2}|[A-Z]{1,2})$")
    fam_model = {"x": [], "y": []}             # grids at constant x (running along y) and at constant y
    for name, s, e in model_grids:
        if not main.match(name):
            continue
        d = np.asarray(e) - np.asarray(s)
        if abs(d[0]) < abs(d[1]):
            fam_model["x"].append(float(s[0]))
        else:
            fam_model["y"].append(float(s[1]))
    # Families of parallel drawing lines by direction.
    angs = defaultdict(list)
    for a, b in lines:
        d = b - a
        ang = float(np.degrees(np.arctan2(d[1], d[0])) % 180)
        angs[round(ang)].append((a, b, ang))
    fams = sorted(angs.values(), key=len, reverse=True)[:2]
    best = {}
    for fam in fams:
        ang = float(np.mean([x[2] for x in fam]))
        u = np.array([np.cos(np.radians(ang)), np.sin(np.radians(ang))])
        nrm = np.array([-u[1], u[0]])
        offs = sorted({round(float(np.dot(a, nrm)), 1) for a, _, _ in fam})
        D = np.array(offs)
        for key, R_ in fam_model.items():
            R_ = np.array(sorted(R_))
            if not len(R_):
                continue
            for sgn in (1.0, -1.0):
                for i in range(len(D)):
                    for j in range(len(R_)):
                        sh = R_[j] - sgn * D[i]
                        hits = np.min(np.abs(R_[None, :] - (sgn * D[:, None] + sh)), axis=1) < tol
                        n = int(hits.sum())
                        if n > best.get(key, (0,))[0]:
                            best[key] = (n, sgn, sh, nrm, ang, len(R_))
    if "x" not in best or "y" not in best:
        raise ValueError("grid alignment failed: the drawing's axes do not match the model's grids")
    nx, sx, hx, nrm_x, ang_x, tot_x = best["x"]
    ny, sy, hy, nrm_y, ang_y, tot_y = best["y"]
    R = np.vstack([sx * nrm_x, sy * nrm_y])
    t = np.array([hx, hy])
    if abs(np.linalg.det(R) - 1) > 0.01:
        raise ValueError(f"grid alignment is not a rotation (det {np.linalg.det(R):.3f})")
    return Alignment(R, t, {"x": [nx, tot_x], "y": [ny, tot_y], "angle_deg": round(ang_x, 3)})


def _poly_points(e, m) -> list[np.ndarray]:
    t = e.dxftype()
    if t == "LINE":
        pts = [e.dxf.start, e.dxf.end]
    elif t == "LWPOLYLINE":
        pts = [(x, y, 0) for x, y, *_ in e.get_points()]
        if e.closed and pts:
            pts.append(pts[0])
    elif t == "POLYLINE":
        pts = [v.dxf.location for v in e.vertices]
    else:
        return []
    out = []
    for p in pts:
        q = m.transform(p)
        out.append(np.array([q.x, q.y]))
    return out


def extract_plan(path: str | Path, model_grids) -> dict:
    """Stalls, lanes, civil-defence walls/labels and fire compartments of a basement plan, in model mm."""
    import ezdxf

    doc = ezdxf.readfile(str(path))
    al = align(grid_lines(doc), model_grids)
    stalls, lanes, rf_walls, rf_text, zones, numbers = [], [], [], [], [], []
    seen = set()
    frames: dict[str, list] = {}

    def frame(name: str):
        """The stall outline of a car block: its largest closed polyline (the car symbol is smaller)."""
        if name not in frames:
            best, area = None, 0.0
            for e in doc.blocks[name]:
                if e.dxftype() == "LWPOLYLINE" and e.closed:
                    p = np.array([q[:2] for q in e.get_points()])
                    a = float(np.ptp(p[:, 0]) * np.ptp(p[:, 1]))
                    if a > area:
                        best, area = [(x, y, 0.0) for x, y in p], a
            frames[name] = best
        return frames[name]

    for kind, e, m, path_ in walk(doc):
        if kind == "insert":
            name = e.dxf.name
            if STALL_BLOCK in name.upper():
                pts_local = frame(name)
                if not pts_local:
                    continue
                pts = al(np.array([[q.x, q.y] for q in (m.transform(p) for p in pts_local)]))
                w, l = sorted((float(np.linalg.norm(pts[1] - pts[0])), float(np.linalg.norm(pts[2] - pts[1]))))
                if not (1800 <= w <= 3500 and 3500 <= l <= 6500):
                    continue          # legend or scaled copy, not a stall
                key = tuple(np.round(pts.mean(axis=0) / 50).astype(int))
                if key in seen:
                    continue
                seen.add(key)
                stalls.append({"pts": np.round(pts, 1).tolist(), "type": "mini" if "16X38" in name else "standard"})
            continue
        layer = _short(e.dxf.layer)
        t = e.dxftype()
        if layer in LANE_LAYERS and t in ("LINE", "LWPOLYLINE"):
            lanes.append(np.round(al(np.array(_poly_points(e, m))), 1).tolist())
        elif layer in RF_WALL_LAYERS and t in ("LINE", "LWPOLYLINE", "POLYLINE"):
            rf_walls.append(np.round(al(np.array(_poly_points(e, m))), 1).tolist())
        elif layer in FIRE_ZONE_LAYERS and t in ("LWPOLYLINE",):
            zones.append(np.round(al(np.array(_poly_points(e, m))), 1).tolist())
        elif t in ("TEXT", "MTEXT") and (layer in RF_TEXT_LAYERS or layer in STALL_NUMBER_LAYERS):
            q = m.transform(e.dxf.insert)
            txt = e.plain_text() if t == "MTEXT" else e.dxf.text
            item = {"text": txt.strip(), "xy": np.round(al([q.x, q.y]), 1).tolist()}
            (rf_text if layer in RF_TEXT_LAYERS else numbers).append(item)
    return {"source": str(path), "alignment": al.to_dict(), "stalls": stalls, "lanes": lanes,
            "rf_walls": rf_walls, "rf_text": rf_text, "fire_zones": zones, "stall_numbers": numbers}


# Paragraphs of the design notes that carry layout rules.
RULE_PATTERNS = {
    "喷头安装": r"喷头的安装|溅水盘|障碍物的宽度",
    "喷头选型": r"喷头的选用",
    "支吊架与喷头": r"支吊架与喷头|吊架与喷头",
    "桥架敷设": r"桥架正常水平敷设|强弱电线槽或桥架|与风道交叉|桥架.{0,6}底边",
    "母线与水管": r"母线槽不应安装在水管|排管交叉|漏水点",
    "管道坡度与排气": r"自喷管道.{0,10}坡度|自动排气阀",
    "抗震支吊架": r"抗震支吊架",
}


def note_texts(path: str | Path) -> list[str]:
    """All text paragraphs of a drawing (model space and blocks)."""
    import ezdxf

    doc = ezdxf.readfile(str(path))
    out, seen = [], set()
    # Every text entity, wherever it sits (model space, paper space, block definitions, attributes:
    # some notes are written as block attributes).
    for e in doc.entitydb.values():
        t = e.dxftype()
        if t not in ("TEXT", "MTEXT", "ATTRIB") or not e.is_alive:
            continue
        txt = (e.plain_text() if t == "MTEXT" else e.dxf.get("text", "")).strip()
        txt = " ".join(txt.split())
        if txt and txt not in seen:
            seen.add(txt)
            out.append(txt)
    return out


def design_rules(text_items: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for s in text_items:
        for k, pat in RULE_PATTERNS.items():
            if re.search(pat, s) and s not in out[k]:
                out[k].append(s)
    return dict(out)


def write(data: dict, path: Path) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class ParkingZones:
    """Stall / lane classification of plan points (model mm), from an extracted plan."""

    def __init__(self, plan: dict):
        self.stalls = [np.array(st["pts"][:4], float) for st in plan.get("stalls", [])]
        self.lo = np.array([p.min(axis=0) for p in self.stalls]) if self.stalls else np.zeros((0, 2))
        self.hi = np.array([p.max(axis=0) for p in self.stalls]) if self.stalls else np.zeros((0, 2))

    def in_stall(self, xy) -> bool:
        if not len(self.lo):
            return False
        x, y = float(xy[0]), float(xy[1])
        m = (self.lo[:, 0] <= x) & (self.hi[:, 0] >= x) & (self.lo[:, 1] <= y) & (self.hi[:, 1] >= y)
        for i in np.nonzero(m)[0]:
            if _inside(self.stalls[i], (x, y)):
                return True
        return False


def _inside(poly: np.ndarray, p) -> bool:
    x, y = p
    n, inside = len(poly), False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
            inside = not inside
    return inside


STALL_SHARE = 0.5


def corridor_bands(zones: ParkingZones, corridor, step_v: float = 100.0, step_s: float = 500.0) -> list[dict]:
    """Across the corridor: intervals of v over drive lanes or over parking stalls.
    [{v_lo, v_hi, zone: lane|stall, stall_share}]"""
    bands = []
    v = corridor.v0
    while v < corridor.v1:
        ss = np.arange(corridor.s0 + step_s / 2, corridor.s1, step_s)
        hits = [zones.in_stall(corridor.point(s, v + step_v / 2, 0)) for s in ss]
        share = float(np.mean(hits)) if hits else 0.0
        # Stalls come in groups between columns: a strip over the stall rows is still "stall" where the
        # gaps between groups are short. Over half of the corridor length on stalls → stall.
        zone = "stall" if share >= STALL_SHARE else "lane"
        if bands and bands[-1]["zone"] == zone:
            bands[-1]["v_hi"] = v + step_v
            bands[-1]["n"] += 1
            bands[-1]["share_sum"] += share
        else:
            bands.append({"v_lo": v, "v_hi": v + step_v, "zone": zone, "n": 1, "share_sum": share})
        v += step_v
    for b in bands:
        b["stall_share"] = round(b.pop("share_sum") / b.pop("n"), 2)
    return bands
