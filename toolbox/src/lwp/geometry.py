"""Convex solids and distances. Units: millimetres, host internal coordinates.

Every element is represented by one convex solid:
  * a capsule (segment + radius) for round pipes, ducts and conduits — exact for straight runs;
  * an oriented box for rectangular ducts and cable trays — exact;
  * an oriented box fitted to the mesh for everything else (fittings, beams, columns) — approximate.

Distances between separated solids are exact for these shapes (alternating projections between
two convex sets converge to the closest pair). Overlap depth is estimated with the separating
axis test on the boxes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_EPS = 1e-9


@dataclass
class Solid:
    center: np.ndarray            # (3,)
    axes: np.ndarray              # (3, 3) rows are orthonormal axes
    half: np.ndarray              # (3,) half extents along axes
    segment: tuple[np.ndarray, np.ndarray] | None = None   # capsule axis
    radius: float = 0.0           # capsule radius
    exact: bool = True            # False when fitted to a mesh

    @property
    def is_capsule(self) -> bool:
        return self.segment is not None

    def aabb(self) -> tuple[np.ndarray, np.ndarray]:
        extent = np.abs(self.axes.T) @ self.half
        return self.center - extent, self.center + extent

    def bottom_z(self) -> float:
        return float(self.aabb()[0][2])

    def project(self, p: np.ndarray) -> np.ndarray:
        """Closest point of the solid to p."""
        if self.segment is not None:
            a, b = self.segment
            q = closest_point_on_segment(p, a, b)
            d = p - q
            n = float(np.linalg.norm(d))
            return p.copy() if n <= self.radius else q + d * (self.radius / n)
        local = self.axes @ (p - self.center)
        local = np.clip(local, -self.half, self.half)
        return self.center + self.axes.T @ local


def capsule(p0: np.ndarray, p1: np.ndarray, radius: float) -> Solid:
    p0 = np.asarray(p0, float)
    p1 = np.asarray(p1, float)
    d = p1 - p0
    length = float(np.linalg.norm(d))
    axis = d / length if length > _EPS else np.array([1.0, 0.0, 0.0])
    u, v = _perpendiculars(axis)
    return Solid(
        center=(p0 + p1) / 2,
        axes=np.vstack([axis, u, v]),
        half=np.array([length / 2 + radius, radius, radius]),
        segment=(p0, p1),
        radius=float(radius),
    )


def swept_box(p0: np.ndarray, p1: np.ndarray, x_axis: np.ndarray | None, half_w: float, half_h: float) -> Solid:
    """Box swept along p0→p1; width along x_axis (section X), height along axis × x_axis."""
    p0 = np.asarray(p0, float)
    p1 = np.asarray(p1, float)
    d = p1 - p0
    length = float(np.linalg.norm(d))
    axis = d / length if length > _EPS else np.array([1.0, 0.0, 0.0])
    u = None
    if x_axis is not None:
        u = np.asarray(x_axis, float) - axis * float(np.dot(x_axis, axis))
        u = u / np.linalg.norm(u) if np.linalg.norm(u) > 1e-6 else None
    if u is None:
        u, _ = _perpendiculars(axis)
    v = np.cross(axis, u)
    return Solid(center=(p0 + p1) / 2, axes=np.vstack([axis, u, v]), half=np.array([length / 2, half_w, half_h]))


def box_from_points(points: np.ndarray) -> Solid:
    """Oriented box fitted to points: principal axes, with a vertical axis kept when it fits better."""
    pts = np.asarray(points, float)
    candidates = [np.eye(3)]
    if len(pts) >= 4:
        centered = pts - pts.mean(axis=0)
        _, vecs = np.linalg.eigh(centered.T @ centered)
        candidates.append(vecs.T)
        # Building elements are usually vertical-aligned: principal axes in plan, Z kept vertical.
        xy = centered[:, :2]
        _, v2 = np.linalg.eigh(xy.T @ xy)
        a = np.array([v2[0, 1], v2[1, 1], 0.0])
        b = np.array([-a[1], a[0], 0.0])
        candidates.append(np.vstack([a, b, [0.0, 0.0, 1.0]]))
    best = None
    for axes in candidates:
        local = pts @ axes.T
        lo, hi = local.min(axis=0), local.max(axis=0)
        volume = float(np.prod(np.maximum(hi - lo, 1e-6)))
        if best is None or volume < best[0] - 1e-6:
            best = (volume, axes, lo, hi)
    _, axes, lo, hi = best
    center = axes.T @ ((lo + hi) / 2)
    return Solid(center=center, axes=axes, half=(hi - lo) / 2, exact=False)


def distance(a: Solid, b: Solid, iterations: int = 60, tol: float = 0.05) -> tuple[float, np.ndarray]:
    """Signed distance in mm and a location between the solids.

    Positive: clear distance (exact for capsules and boxes).
    Negative: overlap; the magnitude is an estimate of the penetration depth.
    """
    if a.is_capsule and b.is_capsule:
        p, q = closest_points_segments(a.segment[0], a.segment[1], b.segment[0], b.segment[1])
        return float(np.linalg.norm(p - q)) - a.radius - b.radius, (p + q) / 2

    # Alternating projections: converge to the closest pair when disjoint, to a common point when not.
    y = b.center.copy()
    x = a.project(y)
    for _ in range(iterations):
        y_new = b.project(x)
        x_new = a.project(y_new)
        moved = float(np.linalg.norm(x_new - x) + np.linalg.norm(y_new - y))
        x, y = x_new, y_new
        if moved < tol:
            break
    gap = float(np.linalg.norm(x - y))
    if gap > tol:
        return gap, (x + y) / 2
    return min(sat_separation(a, b), 0.0), (x + y) / 2


def sat_separation(a: Solid, b: Solid) -> float:
    """Largest separation over the 15 separating axes of two boxes (negative = overlap depth)."""
    axes = [a.axes[i] for i in range(3)] + [b.axes[i] for i in range(3)]
    for i in range(3):
        for j in range(3):
            c = np.cross(a.axes[i], b.axes[j])
            n = np.linalg.norm(c)
            if n > 1e-6:
                axes.append(c / n)
    t = b.center - a.center
    best = -np.inf
    for axis in axes:
        ra = float(np.sum(a.half * np.abs(a.axes @ axis)))
        rb = float(np.sum(b.half * np.abs(b.axes @ axis)))
        best = max(best, abs(float(np.dot(t, axis))) - ra - rb)
    return float(best)


def closest_point_on_segment(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    ab = b - a
    denom = float(np.dot(ab, ab))
    if denom < _EPS:
        return a.copy()
    t = float(np.clip(np.dot(p - a, ab) / denom, 0.0, 1.0))
    return a + t * ab


def closest_points_segments(p1, q1, p2, q2) -> tuple[np.ndarray, np.ndarray]:
    """Closest points between segments p1q1 and p2q2 (Ericson, Real-Time Collision Detection 5.1.9)."""
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a, e, f = float(np.dot(d1, d1)), float(np.dot(d2, d2)), float(np.dot(d2, r))
    if a <= _EPS and e <= _EPS:
        return p1.copy(), p2.copy()
    if a <= _EPS:
        s, t = 0.0, float(np.clip(f / e, 0.0, 1.0))
    else:
        c = float(np.dot(d1, r))
        if e <= _EPS:
            s, t = float(np.clip(-c / a, 0.0, 1.0)), 0.0
        else:
            b = float(np.dot(d1, d2))
            denom = a * e - b * b
            s = float(np.clip((b * f - c * e) / denom, 0.0, 1.0)) if denom > _EPS else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t, s = 0.0, float(np.clip(-c / a, 0.0, 1.0))
            elif t > 1.0:
                t, s = 1.0, float(np.clip((b - c) / a, 0.0, 1.0))
    return p1 + d1 * s, p2 + d2 * t


def _perpendiculars(axis: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    ref = np.array([0.0, 0.0, 1.0]) if abs(axis[2]) < 0.9 else np.array([1.0, 0.0, 0.0])
    u = np.cross(ref, axis)
    u /= np.linalg.norm(u)
    return u, np.cross(axis, u)
