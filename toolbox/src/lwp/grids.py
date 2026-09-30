"""Describe plan positions by the nearest grid lines, e.g. "1-12 轴 +1.2 m / 1-C 轴 -0.8 m"."""

from __future__ import annotations

import math

import numpy as np

from .package import Grid


class GridLocator:
    def __init__(self, grids: list[Grid], angle_tol_deg: float = 5.0):
        self.families: list[list[tuple[Grid, np.ndarray, np.ndarray]]] = []
        reps: list[float] = []
        for g in grids:
            d = g.end - g.start
            n = float(np.linalg.norm(d))
            if n < 1.0:
                continue
            direction = d / n
            angle = math.degrees(math.atan2(direction[1], direction[0])) % 180.0
            normal = np.array([-direction[1], direction[0]])
            for i, rep in enumerate(reps):
                if min(abs(angle - rep), 180 - abs(angle - rep)) <= angle_tol_deg:
                    self.families[i].append((g, g.start, normal))
                    break
            else:
                reps.append(angle)
                self.families.append([(g, g.start, normal)])
        # Largest families first: usually the two orthogonal main grid directions.
        self.families.sort(key=len, reverse=True)

    def describe(self, point) -> str:
        if not self.families:
            return f"X {point[0] / 1000:.1f} m, Y {point[1] / 1000:.1f} m"
        p = np.asarray(point[:2], float)
        parts = []
        for family in self.families[:2]:
            best = None
            for g, origin, normal in family:
                offset = float(np.dot(p - origin, normal))
                if best is None or abs(offset) < abs(best[1]):
                    best = (g, offset)
            g, offset = best
            parts.append(f"{g.name} 轴 {offset / 1000:+.1f} m")
        return " / ".join(parts)
