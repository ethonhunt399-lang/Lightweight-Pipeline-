"""Clear height over the drive lanes of a whole basement (drawing lanes on the clear-height map).

Lanes come from the drive-lane flow lines of the architectural plan (`lwp drawings`). A cell of the
clear-height map belongs to a lane when its centre lies within half a lane width of a flow line and not
on a parking stall; it is assigned to the nearest flow line. Along each lane the clear height is taken
per station (every `step` mm, the lowest cell across the lane), which gives:

  * lowest, 10th percentile and median clear height;
  * levels: clear heights (50 mm steps) holding at least 5 % of the lane length — lanes should be as even
    as possible, several levels are acceptable;
  * changes: the number of level changes along the lane (station levels in 100 mm steps, runs shorter
    than `min_run` merged into their neighbours).

Clear height is measured from the floor level to the lowest MEP (incl. insulation, fittings, heads) or
structure above, as in `headroom_map`.
"""

from __future__ import annotations

import struct
import zlib
from html import escape

import numpy as np

from .drawings import ParkingZones
from .headroom_map import HeadroomMap

HALF_WIDTH = 2750.0      # mm, half the lane width (5.5 m lanes between stall rows)
MIN_LINE = 3000.0        # flow-line pieces shorter than this are arrows / ramps, not lanes
BANDS = (2200.0, 2400.0, 2600.0, 2800.0)


def flow_lines(plan: dict) -> list[np.ndarray]:
    out = []
    for p in plan.get("lanes", []):
        a = np.asarray(p, float)[:, :2]
        if len(a) >= 2 and float(np.sum(np.linalg.norm(np.diff(a, axis=0), axis=1))) >= MIN_LINE:
            out.append(a)
    return out


def _dist_to_line(X: np.ndarray, Y: np.ndarray, line: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Distance of each point to the polyline, and its station (mm along the line)."""
    best = np.full(X.shape, np.inf)
    station = np.zeros(X.shape)
    s0 = 0.0
    for a, b in zip(line[:-1], line[1:]):
        d = b - a
        L = float(np.linalg.norm(d))
        if L < 1:
            continue
        t = np.clip(((X - a[0]) * d[0] + (Y - a[1]) * d[1]) / (L * L), 0, 1)
        dist = np.hypot(X - (a[0] + t * d[0]), Y - (a[1] + t * d[1]))
        m = dist < best
        best[m] = dist[m]
        station[m] = s0 + t[m] * L
        s0 += L
    return best, station


def lane_cells(hmap: HeadroomMap, plan: dict, half_width: float = HALF_WIDTH):
    """(lane index per cell or -1, station per cell) over the map grid."""
    zones = ParkingZones(plan)
    lines = flow_lines(plan)
    xs = hmap.x0 + (np.arange(hmap.nx) + 0.5) * hmap.cell
    ys = hmap.y0 + (np.arange(hmap.ny) + 0.5) * hmap.cell
    X, Y = np.meshgrid(xs, ys)
    owner = np.full(X.shape, -1, dtype=np.int32)
    best = np.full(X.shape, np.inf)
    station = np.zeros(X.shape)
    for k, line in enumerate(lines):
        lo, hi = line.min(axis=0) - half_width, line.max(axis=0) + half_width
        i0, i1 = max(int((lo[0] - hmap.x0) // hmap.cell), 0), min(int((hi[0] - hmap.x0) // hmap.cell) + 1, hmap.nx)
        j0, j1 = max(int((lo[1] - hmap.y0) // hmap.cell), 0), min(int((hi[1] - hmap.y0) // hmap.cell) + 1, hmap.ny)
        if i0 >= i1 or j0 >= j1:
            continue
        d, st = _dist_to_line(X[j0:j1, i0:i1], Y[j0:j1, i0:i1], line)
        m = (d <= half_width) & (d < best[j0:j1, i0:i1])
        best[j0:j1, i0:i1][m] = d[m]
        owner[j0:j1, i0:i1][m] = k
        station[j0:j1, i0:i1][m] = st[m]
    for j, i in zip(*np.nonzero(owner >= 0)):
        if zones.in_stall((X[j, i], Y[j, i])):
            owner[j, i] = -1
    return lines, owner, station


def _changes(levels: np.ndarray, step: float, min_run: float) -> int:
    """Level changes along a profile (NaN stations skipped), runs shorter than min_run merged."""
    v = levels[~np.isnan(levels)]
    if len(v) < 2:
        return 0
    runs = []                                   # [level, length]
    for x in v:
        if runs and runs[-1][0] == x:
            runs[-1][1] += step
        else:
            runs.append([x, step])
    changed = True
    while changed and len(runs) > 1:
        changed = False
        i = min(range(len(runs)), key=lambda k: runs[k][1])
        if runs[i][1] < min_run:
            # Merge into the longer neighbour.
            nb = [k for k in (i - 1, i + 1) if 0 <= k < len(runs)]
            k = max(nb, key=lambda k: runs[k][1])
            runs[k][1] += runs[i][1]
            del runs[i]
            merged = []
            for r in runs:
                if merged and merged[-1][0] == r[0]:
                    merged[-1][1] += r[1]
                else:
                    merged.append(r)
            runs = merged
            changed = True
    return len(runs) - 1


def lane_headroom(hmap: HeadroomMap, plan: dict, locator=None, step: float = 1000.0, min_run: float = 3000.0,
                  half_width: float = HALF_WIDTH) -> dict:
    lines, owner, station = lane_cells(hmap, plan, half_width)
    lanes = []
    for k, line in enumerate(lines):
        m = (owner == k) & ~np.isnan(hmap.clear)
        if m.sum() < 4:
            continue
        L = float(np.sum(np.linalg.norm(np.diff(line, axis=0), axis=1)))
        nst = int(np.ceil(L / step))
        prof = np.full(nst, np.nan)
        src = np.full(nst, -1)
        idx = np.minimum((station[m] // step).astype(int), nst - 1)
        vals, srcs = hmap.clear[m], hmap.source[m]
        order = np.argsort(-vals)               # write high first so the lowest per station stays
        prof[idx[order]] = vals[order]
        src[idx[order]] = srcs[order]
        ok = ~np.isnan(prof)
        if ok.sum() < 2:
            continue
        v = prof[ok]
        steps50 = np.floor(v / 50) * 50
        u, n = np.unique(steps50, return_counts=True)
        levels = [round(float(x)) for x, c in zip(u, n) if c >= 0.05 * len(v)]
        low = int(np.nanargmin(prof))
        mid = line[len(line) // 2] if len(line) > 2 else line.mean(axis=0)
        d = line[-1] - line[0]
        lanes.append({
            "flow_line": k, "length_m": round(L / 1000, 1), "covered_m": round(float(ok.sum()) * step / 1000, 1),
            "axis": "X" if abs(d[0]) >= abs(d[1]) else "Y",
            "where": locator.describe(mid) if locator else f"X {mid[0] / 1000:.1f} / Y {mid[1] / 1000:.1f} m",
            "min_mm": round(float(v.min())), "p10_mm": round(float(np.percentile(v, 10))),
            "median_mm": round(float(np.median(v))), "levels_mm": levels,
            "changes": _changes(np.where(ok, np.floor(prof / 100) * 100, np.nan), step, min_run),
            "below_min_m": round(float((v < BANDS[0]).sum()) * step / 1000, 1),
            "lowest_key": hmap.keys[src[low]] if src[low] >= 0 else None,
            "lowest_station_m": round(low * step / 1000, 1),
            "profile_mm": [None if np.isnan(x) else round(float(x)) for x in prof],
            "line": np.round(line).tolist(),
        })
    # Name lanes by position: X-lanes from south to north, then Y-lanes from west to east.
    lanes.sort(key=lambda r: (r["axis"], np.mean(np.array(r["line"])[:, 1 if r["axis"] == "X" else 0])))
    for i, r in enumerate(lanes):
        r["id"] = f"{r['axis']}{i + 1:02d}"
    cells = hmap.clear[(owner >= 0) & ~np.isnan(hmap.clear)]
    cell_m2 = (hmap.cell / 1000) ** 2
    edges = (-np.inf,) + BANDS + (np.inf,)
    bands = [{"band": _band_label(lo, hi), "area_m2": round(float(((cells >= lo) & (cells < hi)).sum()) * cell_m2)}
             for lo, hi in zip(edges[:-1], edges[1:])]
    return {"cell_mm": hmap.cell, "half_width_mm": half_width, "step_mm": step, "floor_z_mm": hmap.floor_z,
            "lane_area_m2": round(float(len(cells)) * cell_m2), "bands": bands, "lanes": lanes,
            "_owner": owner}


def _band_label(lo: float, hi: float) -> str:
    if lo == -np.inf:
        return f"< {hi / 1000:.1f}"
    if hi == np.inf:
        return f"≥ {lo / 1000:.1f}"
    return f"{lo / 1000:.1f}–{hi / 1000:.1f}"


# ---------------------------------------------------------------- plan map (SVG with an embedded PNG)

COLORS = [(198, 40, 40), (239, 108, 0), (251, 192, 45), (124, 179, 66), (46, 125, 50)]   # by BANDS
STALL_GREY = (225, 225, 225)
OPEN = (179, 214, 240)    # lane cell with no MEP or beam above (open to the slab)
EMPTY = (255, 255, 255)


def _png(rgb: np.ndarray) -> bytes:
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].astype(np.uint8).tobytes() for y in range(h))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def plan_svg(hmap: HeadroomMap, result: dict, title: str, px_per_m: float = 3.0) -> str:
    import base64
    owner = result["_owner"]
    rgb = np.full((hmap.ny, hmap.nx, 3), EMPTY, dtype=np.uint8)
    rgb[~np.isnan(hmap.clear)] = STALL_GREY
    c = hmap.clear
    rgb[(owner >= 0) & np.isnan(c)] = OPEN
    lane = (owner >= 0) & ~np.isnan(c)
    edges = (-np.inf,) + BANDS + (np.inf,)
    for col, lo, hi in zip(COLORS, edges[:-1], edges[1:]):
        rgb[lane & (c >= lo) & (c < hi)] = col
    rgb = rgb[::-1]                              # image rows top = north
    W, H = hmap.nx * hmap.cell / 1000 * px_per_m, hmap.ny * hmap.cell / 1000 * px_per_m
    img = base64.b64encode(_png(rgb)).decode()
    sx = lambda x: (x - hmap.x0) / 1000 * px_per_m
    sy = lambda y: H - (y - hmap.y0) / 1000 * px_per_m
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.0f} {H + 40:.0f}" class="plan">',
             f'<text x="4" y="16" class="t">{escape(title)}</text>',
             f'<g transform="translate(0,30)"><image href="data:image/png;base64,{img}" width="{W:.0f}" height="{H:.0f}" '
             f'preserveAspectRatio="none" style="image-rendering:pixelated"/>']
    for r in result["lanes"]:
        pts = " ".join(f"{sx(x):.1f},{sy(y):.1f}" for x, y in r["line"])
        parts.append(f'<polyline points="{pts}" class="ln"/>')
        x, y = np.array(r["line"]).mean(axis=0)
        parts.append(f'<text x="{sx(x):.1f}" y="{sy(y):.1f}" class="id">{r["id"]}</text>')
    parts.append("</g></svg>")
    return "\n".join(parts)


def legend_html() -> str:
    edges = (-np.inf,) + BANDS + (np.inf,)
    items = [f'<span class="sw" style="background:rgb{col}"></span>{_band_label(lo, hi)} m'
             for col, lo, hi in zip(COLORS, edges[:-1], edges[1:])]
    items.append(f'<span class="sw" style="background:rgb{OPEN}"></span>车道上方无机电、梁')
    items.append(f'<span class="sw" style="background:rgb{STALL_GREY}"></span>车位及其他')
    return " ".join(items)
