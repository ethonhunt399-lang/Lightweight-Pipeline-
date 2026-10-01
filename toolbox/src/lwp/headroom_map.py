"""Clear-height map: for each floor cell, the lowest obstruction above it (MEP incl. insulation, beams)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .detect import is_horizontal, scope_test
from .package import Element, Package

VERTICAL = 0.95          # |axis.z| above this: the box stands upright (footprint = the other two axes)
FLOOR_STANDING = 300.0   # obstructions whose bottom is this close to the floor are equipment / risers


@dataclass
class HeadroomMap:
    x0: float                 # mm, cell (0, 0) lower-left corner
    y0: float
    cell: float               # mm
    nx: int
    ny: int
    floor_z: float            # mm
    clear: np.ndarray         # (ny, nx) mm above floor; NaN = nothing above / outside scope
    source: np.ndarray        # (ny, nx) index into `keys`, -1 = none
    keys: list[str]

    def lowest(self) -> tuple[float, float, float, str] | None:
        if np.all(np.isnan(self.clear)):
            return None
        j, i = np.unravel_index(np.nanargmin(self.clear), self.clear.shape)
        x = self.x0 + (i + 0.5) * self.cell
        y = self.y0 + (j + 0.5) * self.cell
        return float(self.clear[j, i]), x, y, self.keys[self.source[j, i]]


def obstructions(package: Package) -> list[Element]:
    result = []
    for e in package.elements.values():
        if e.solid is None:
            continue
        if e.origin == "mep_curve":
            if is_horizontal(e):
                result.append(e)
        elif e.origin == "mep_family":
            if e.kind in ("fitting", "accessory", "terminal"):
                result.append(e)
        elif e.kind == "beam":
            result.append(e)
    return result


def build(package: Package, floor_z: float, cell: float = 500.0) -> HeadroomMap:
    items = [e for e in obstructions(package) if e.solid.aabb()[0][2] > floor_z + FLOOR_STANDING]
    in_scope = scope_test(package)
    lo = np.min([e.solid.aabb()[0][:2] for e in items], axis=0)
    hi = np.max([e.solid.aabb()[1][:2] for e in items], axis=0)
    scope = package.manifest.get("scope") or {}
    if scope.get("section_box_active"):
        t = np.array(scope["section_box_transform"]).reshape(4, 4)
        corners = [t @ np.array([x, y, scope["section_box_min"][2], 1.0])
                   for x in (scope["section_box_min"][0], scope["section_box_max"][0])
                   for y in (scope["section_box_min"][1], scope["section_box_max"][1])]
        box_lo = np.min([c[:2] for c in corners], axis=0) * 1000
        box_hi = np.max([c[:2] for c in corners], axis=0) * 1000
        lo, hi = np.maximum(lo, box_lo), np.minimum(hi, box_hi)
    x0, y0 = np.floor(lo / cell) * cell
    nx = int(np.ceil((hi[0] - x0) / cell))
    ny = int(np.ceil((hi[1] - y0) / cell))
    bottom = np.full((ny, nx), np.inf)
    source = np.full((ny, nx), -1, dtype=np.int32)
    keys = []

    for idx, e in enumerate(items):
        keys.append(e.key)
        s = e.solid
        elo, ehi = s.aabb()
        i0, i1 = max(int((elo[0] - x0) // cell), 0), min(int((ehi[0] - x0) // cell) + 1, nx)
        j0, j1 = max(int((elo[1] - y0) // cell), 0), min(int((ehi[1] - y0) // cell) + 1, ny)
        if i0 >= i1 or j0 >= j1:
            continue
        xs = x0 + (np.arange(i0, i1) + 0.5) * cell
        ys = y0 + (np.arange(j0, j1) + 0.5) * cell
        X, Y = np.meshgrid(xs, ys)
        z = np.min([_bottom_at(p, X, Y, cell / 2) for p in (e.parts or [s])], axis=0)
        block = bottom[j0:j1, i0:i1]
        better = z < block
        block[better] = z[better]
        source[j0:j1, i0:i1][better] = idx

    clear = np.where(np.isfinite(bottom), bottom - floor_z, np.nan)
    # Blank cells outside the exported section box.
    for j in range(ny):
        for i in range(nx):
            if np.isfinite(clear[j, i]) and not in_scope((x0 + (i + .5) * cell, y0 + (j + .5) * cell, bottom[j, i])):
                clear[j, i] = np.nan
                source[j, i] = -1
    return HeadroomMap(float(x0), float(y0), cell, nx, ny, floor_z, clear, source, keys)


def _bottom_at(s, X: np.ndarray, Y: np.ndarray, half_cell: float) -> np.ndarray:
    """Bottom z of the solid on the vertical line through each (X, Y); inf where it does not cover.

    A cell counts as covered when its centre lies within the footprint grown by half a cell,
    so that pipes narrower than a cell still register.
    """
    if s.is_capsule:
        a, b = s.segment
        d = b[:2] - a[:2]
        dd = float(d @ d)
        px, py = X - a[0], Y - a[1]
        t = np.clip((px * d[0] + py * d[1]) / dd, 0, 1) if dd > 1e-9 else np.zeros_like(X)
        cx, cy = a[0] + t * d[0], a[1] + t * d[1]
        dist = np.hypot(X - cx, Y - cy)
        z = a[2] + t * (b[2] - a[2]) - s.radius
        return np.where(dist <= s.radius + half_cell, z, np.inf)
    vertical = np.argmax(np.abs(s.axes[:, 2]))
    if abs(s.axes[vertical, 2]) >= VERTICAL:
        plan = [k for k in range(3) if k != vertical]
        inside = np.ones_like(X, dtype=bool)
        for k in plan:
            u = s.axes[k, :2]
            n = np.linalg.norm(u)
            proj = ((X - s.center[0]) * u[0] + (Y - s.center[1]) * u[1]) / (n if n > 1e-9 else 1)
            inside &= np.abs(proj) <= s.half[k] * n + half_cell
        z = s.aabb()[0][2]
        return np.where(inside, z, np.inf)
    lo, hi = s.aabb()
    inside = (X >= lo[0] - half_cell) & (X <= hi[0] + half_cell) & (Y >= lo[1] - half_cell) & (Y <= hi[1] + half_cell)
    return np.where(inside, lo[2], np.inf)
