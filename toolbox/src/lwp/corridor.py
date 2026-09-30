"""Straight corridors: definition, local coordinates and automatic candidates.

A corridor is a straight plan rectangle: `s` runs along the corridor axis, `v` across it (both in mm,
host internal coordinates). Corridors are axis-aligned with the model (the exported models are).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from .detect import is_horizontal, scope_test
from .grids import GridLocator
from .package import Package


@dataclass
class Corridor:
    name: str
    axis: str                 # "x" or "y": direction of the runs
    s0: float                 # mm along the axis
    s1: float
    v0: float                 # mm across the axis (band)
    v1: float

    @property
    def length(self) -> float:
        return self.s1 - self.s0

    @property
    def u(self) -> np.ndarray:
        return np.array([1.0, 0.0, 0.0]) if self.axis == "x" else np.array([0.0, 1.0, 0.0])

    @property
    def w(self) -> np.ndarray:
        """Unit vector across the corridor (positive v)."""
        return np.array([0.0, 1.0, 0.0]) if self.axis == "x" else np.array([1.0, 0.0, 0.0])

    def s_of(self, p) -> float:
        return float(p[0] if self.axis == "x" else p[1])

    def v_of(self, p) -> float:
        return float(p[1] if self.axis == "x" else p[0])

    def point(self, s: float, v: float, z: float) -> np.ndarray:
        return np.array([s, v, z]) if self.axis == "x" else np.array([v, s, z])

    def to_dict(self) -> dict:
        return {"name": self.name, "axis": self.axis, "s_m": [round(self.s0 / 1000, 3), round(self.s1 / 1000, 3)],
                "band_m": [round(self.v0 / 1000, 3), round(self.v1 / 1000, 3)]}


def load_corridor(path: str | Path) -> Corridor:
    with Path(path).open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    c = data.get("corridor", data)
    return Corridor(name=str(c.get("name", "corridor")), axis=c["axis"], s0=c["s_m"][0] * 1000, s1=c["s_m"][1] * 1000,
                    v0=c["band_m"][0] * 1000, v1=c["band_m"][1] * 1000)


def candidates(package: Package, band_mm: float = 6000.0, min_runs: int = 8, min_length_mm: float = 15000.0,
               limit: int = 6) -> list[tuple[Corridor, float, str]]:
    """Dense straight bundles of parallel horizontal runs: (corridor, mean parallel runs, location)."""
    in_scope = scope_test(package)
    locator = GridLocator(package.grids)
    found: list[tuple[Corridor, float, str]] = []
    for axis in ("x", "y"):
        ai, oi = (0, 1) if axis == "x" else (1, 0)
        runs = []
        for e in package.mep():
            r = e.record
            if e.origin != "mep_curve" or not is_horizontal(e):
                continue
            a, b = np.array(r["start"]) * 1000, np.array(r["end"]) * 1000
            if abs(b[oi] - a[oi]) > 1 or abs(b[ai] - a[ai]) < 1:
                continue
            # Parametric runs extend past the exported range: keep the part inside the section box.
            s = np.linspace(0, 1, 41)
            pts = [a + t * (b - a) for t in s]
            inside = [p for p in pts if in_scope(p)]
            if len(inside) < 2:
                continue
            lo = min(p[ai] for p in inside)
            hi = max(p[ai] for p in inside)
            runs.append((lo, hi, a[oi]))
        if not runs:
            continue
        lo = min(r[0] for r in runs)
        hi = max(r[1] for r in runs)
        olo = min(r[2] for r in runs)
        ohi = max(r[2] for r in runs)
        cell, step = 1000.0, 500.0
        nb = int((hi - lo) // cell) + 1
        mb = int((ohi - olo) // step) + 1
        grid = np.zeros((mb, nb))
        for s0, s1, o in runs:
            grid[int((o - olo) // step), int((s0 - lo) // cell):int((s1 - lo) // cell) + 1] += 1
        width = int(band_mm // step)
        best = []
        for j in range(max(mb - width, 1)):
            count = grid[j:j + width].sum(0)
            start, run = 0, 0
            for i, c in enumerate(list(count) + [0]):
                if c >= min_runs:
                    if run == 0:
                        start = i
                    run += 1
                else:
                    if run * cell >= min_length_mm:
                        best.append((run * float(count[start:start + run].mean()), float(count[start:start + run].mean()), j, start, run))
                    run = 0
        best.sort(key=lambda x: -x[0])
        taken: list[Corridor] = []
        for _, mean, j, start, run in best:
            c = Corridor(name="", axis=axis, s0=lo + start * cell, s1=lo + (start + run) * cell,
                         v0=olo + j * step, v1=olo + j * step + band_mm)
            if any(t.axis == axis and t.v0 < c.v1 and c.v0 < t.v1 and t.s0 < c.s1 and c.s0 < t.s1 for t in taken):
                continue
            taken.append(c)
            mid = c.point((c.s0 + c.s1) / 2, (c.v0 + c.v1) / 2, 0)
            found.append((c, mean, locator.describe(mid)))
    found.sort(key=lambda f: -f[0].length * f[1])
    for i, (c, _, _) in enumerate(found[:limit]):
        c.name = chr(ord("A") + i)
    return found[:limit]
