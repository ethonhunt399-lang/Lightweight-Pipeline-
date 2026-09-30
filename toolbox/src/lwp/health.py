"""Model health checks on an imported package."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np

from .package import Package

M = 1000.0


@dataclass
class OpenEnd:
    key: str
    location: np.ndarray      # mm
    size_mm: float


@dataclass
class Health:
    counts: Counter
    classes: Counter                          # (cls, domain) → count
    unmatched: Counter                        # unmatched system signatures
    assumed_rules: list[tuple[str, int]]      # (rule, element count) for unconfirmed rules
    insulation: Counter                       # (cls, source, mm) → count
    missing_size: list[str]
    zero_length: list[str]
    open_ends: list[OpenEnd]
    open_ends_at_boundary: int
    duplicates: list[tuple[str, str]]
    diagnostics: Counter
    warnings: list[str] = field(default_factory=list)


def check(package: Package) -> Health:
    mep = package.mep()
    counts = Counter(e.kind for e in mep)
    counts.update(f"obstacle:{e.kind}" for e in package.obstacles())
    classes = Counter((e.cls, e.domain) for e in mep)

    rules_by_desc = {r.describe(): r for r in package.classifier.rules.classification}
    assumed = [(desc, n) for desc, n in package.classifier.hits.most_common()
               if desc in rules_by_desc and not rules_by_desc[desc].confirmed]

    insulation = Counter((e.cls, e.insulation_source, round(e.insulation_mm)) for e in mep if e.domain in ("pipe", "duct"))

    missing_size, zero_length = [], []
    for e in mep:
        if e.origin != "mep_curve":
            continue
        if e.solid is None:
            missing_size.append(e.key)
        if (e.record.get("length_m") or 0) * M < 1.0:
            zero_length.append(e.key)

    open_ends, at_boundary = _open_ends(package)
    duplicates = _duplicates(package)
    diagnostics = Counter((d.get("field"), (d.get("message") or "").splitlines()[0]) for d in package.manifest.get("diagnostics", []))

    return Health(counts, classes, package.classifier.unmatched, assumed, insulation, missing_size, zero_length,
                  open_ends, at_boundary, duplicates, diagnostics, list(package.warnings))


def _scope_box(package: Package):
    scope = package.manifest.get("scope") or {}
    if not scope.get("section_box_active"):
        return None
    t = np.array(scope["section_box_transform"]).reshape(4, 4)
    lo, hi = np.array(scope["section_box_min"]), np.array(scope["section_box_max"])
    return t, lo, hi


def _inside_scope(point_m: list[float], box, tol_m: float = 0.05) -> bool:
    if box is None:
        return True
    t, lo, hi = box
    local = np.linalg.inv(t) @ np.array([*point_m, 1.0])
    return bool(np.all(local[:3] >= lo + tol_m) and np.all(local[:3] <= hi - tol_m))


def _open_ends(package: Package) -> tuple[list[OpenEnd], int]:
    box = _scope_box(package)
    result, at_boundary = [], 0
    for e in package.mep():
        if e.origin != "mep_curve":
            continue
        for c in e.connectors:
            if c.get("connector_type") != "End" or c.get("connected") or not c.get("origin"):
                continue
            if not _inside_scope(c["origin"], box):
                at_boundary += 1
                continue
            size = max(c.get("diameter_m") or 0, c.get("width_m") or 0) * M
            result.append(OpenEnd(e.key, np.array(c["origin"]) * M, size))
    return result, at_boundary


def _duplicates(package: Package) -> list[tuple[str, str]]:
    """Curves with the same end points (either direction), size and system: usually modelled twice."""
    seen: dict[tuple, str] = {}
    pairs = []
    for e in package.mep():
        rec = e.record
        if e.origin != "mep_curve" or not rec.get("start") or not rec.get("end"):
            continue
        ends = sorted([tuple(round(v * M / 5) for v in rec["start"]), tuple(round(v * M / 5) for v in rec["end"])])
        size = (round((rec.get("outer_diameter_m") or 0) * M), round((rec.get("width_m") or 0) * M), round((rec.get("height_m") or 0) * M))
        key = (e.kind, tuple(ends), size)
        if key in seen:
            pairs.append((seen[key], e.key))
        else:
            seen[key] = e.key
    return pairs


def summarize_open_ends(items: list[OpenEnd], package: Package) -> Counter:
    by = defaultdict(int)
    for item in items:
        e = package.elements[item.key]
        by[(e.kind, e.cls)] += 1
    return Counter(by)
