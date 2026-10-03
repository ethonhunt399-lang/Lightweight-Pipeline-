"""Read an SLBH export package (<name>.slbh/) into normalized elements (millimetres)."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .classify import Classifier
from .geometry import Solid, box_from_points, capsule, swept_box
from .rules import RuleSet

M = 1000.0  # metres → millimetres

CURVE_DOMAIN = {"pipe": "pipe", "flex_pipe": "pipe", "duct": "duct", "flex_duct": "duct",
                "cable_tray": "tray", "conduit": "tray"}
CATEGORY_DOMAIN = {
    "OST_PipeFitting": "pipe", "OST_PipeAccessory": "pipe", "OST_PlumbingFixtures": "pipe", "OST_Sprinklers": "pipe",
    "OST_DuctFitting": "duct", "OST_DuctAccessory": "duct", "OST_DuctTerminal": "duct", "OST_MechanicalEquipment": "duct",
    "OST_CableTrayFitting": "tray", "OST_ConduitFitting": "tray", "OST_ElectricalEquipment": "tray",
    "OST_ElectricalFixtures": "tray", "OST_LightingFixtures": "tray",
}


@dataclass
class Element:
    key: str
    origin: str               # mep_curve | mep_family | obstacle
    kind: str                 # pipe, duct, cable_tray, fitting, ..., beam, column, wall
    domain: str               # pipe | duct | tray | structure | wall | other
    category: str
    family: str
    type_name: str
    level: str
    system_name: str = ""
    system_type: str = ""
    abbreviation: str = ""
    system_code: str = ""
    cls: str = "unknown"
    group: str = "other"
    rule: str = ""            # classification rule that matched
    rule_confirmed: bool = True
    insulation_mm: float = 0.0
    insulation_source: str = "none"   # modeled | default | none
    size_text: str = ""
    solid: Solid | None = None
    parts: list[Solid] | None = None   # fittings: one leg per connector (more exact than the fitted box)
    leg_conns: list[dict] | None = None   # the connector of each leg (same order as parts)
    centre: np.ndarray | None = None      # fittings: point where the connector axes meet (mm)
    connectors: list[dict] = field(default_factory=list)
    record: dict = field(default_factory=dict, repr=False)
    basis: str = ""                       # where the element's geometry comes from (drawing, inferred rule, ...)
    inferred: bool = False                # dimensions inferred rather than given (reported separately)
    footprint: list[np.ndarray] | None = None   # slabs: plan rings in mm, [outer, hole, hole, ...]

    @property
    def is_mep(self) -> bool:
        return self.origin in ("mep_curve", "mep_family")

    def label(self) -> str:
        system = self.system_type or self.system_name or self.type_name
        size = self.size_text or ""
        return f"{self.category} {system} {size}".strip()


@dataclass
class Level:
    name: str
    elevation: float          # mm, host internal
    source: str


@dataclass
class Grid:
    name: str
    start: np.ndarray         # mm, XY
    end: np.ndarray


@dataclass
class Package:
    path: Path
    manifest: dict
    elements: dict[str, Element]
    levels: list[Level]
    grids: list[Grid]
    warnings: list[str]
    classifier: Classifier

    @property
    def schema_version(self) -> str:
        return self.manifest.get("schema_version", "")

    def mep(self) -> list[Element]:
        return [e for e in self.elements.values() if e.is_mep]

    def obstacles(self) -> list[Element]:
        return [e for e in self.elements.values() if e.origin == "obstacle"]

    def host_levels(self) -> list[Level]:
        return sorted((lv for lv in self.levels if lv.source == "HOST"), key=lambda lv: lv.elevation)


def load_json(path: Path):
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def load_package(path: str | Path, rules: RuleSet, with_meshes: bool = True) -> Package:
    path = Path(path)
    manifest_path = path / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"{path} has no manifest.json (incomplete export or not an MEP package)")
    manifest = load_json(manifest_path)
    mep = load_json(path / manifest["files"]["mep"])
    warnings: list[str] = []
    classifier = Classifier(rules)
    elements: dict[str, Element] = {}

    for rec in mep.get("curves", []):
        e = _curve_element(rec, classifier)
        elements[e.key] = e
    for rec in mep.get("family_instances", []):
        e = _family_element(rec, classifier)
        elements[e.key] = e

    if with_meshes:
        project = load_json(path / manifest["files"]["project"])
        wanted: dict[str, tuple[str, str, dict]] = {}
        for pe in project.get("elements", []):
            key = pe.get("stable_element_key")
            if key in elements:
                if elements[key].origin == "mep_family":
                    wanted[pe["obj_name"]] = (key, "mep_family", pe)
                continue
            kind = rules.obstacle_kind(pe.get("category", ""))
            if kind:
                wanted[pe["obj_name"]] = (key, kind, pe)
        meshes = read_obj_vertices(path / manifest["files"]["model_obj"], set(wanted))
        for obj_name, verts in meshes.items():
            key, kind, pe = wanted[obj_name]
            solid = box_from_points(verts * M)
            if kind == "mep_family":
                elements[key].solid = solid
            else:
                elements[key] = Element(
                    key=key, origin="obstacle", kind=kind, domain="wall" if kind == "wall" else "structure",
                    category=pe.get("category", ""), family=pe.get("family", ""), type_name=pe.get("type", ""),
                    level=pe.get("level", ""), group="wall" if kind == "wall" else "structure", solid=solid,
                    record=pe,
                )
        for e in elements.values():
            if e.origin == "mep_family" and e.kind == "fitting":
                e.parts = fitting_legs(e)
        missing = [k for k, e in elements.items() if e.origin == "mep_family" and e.solid is None]
        for key in missing:
            e = elements[key]
            lo, hi = e.record.get("bbox_min"), e.record.get("bbox_max")
            if lo and hi:
                e.solid = box_from_points(np.array([lo, hi]) * M)
        if missing:
            warnings.append(f"{len(missing)} 个管件/附件没有网格，使用包围盒代替")

    levels = [_level(lv, manifest) for lv in manifest.get("levels", [])]
    grids = _grids(manifest.get("grids", []))
    return Package(path=path, manifest=manifest, elements=elements, levels=levels, grids=grids, warnings=warnings,
                   classifier=classifier)


def _common(rec: dict, origin: str, domain: str, classifier: Classifier) -> Element:
    e = Element(
        key=rec["key"], origin=origin, kind=rec.get("kind", ""), domain=domain,
        category=rec.get("category") or "", family=rec.get("family") or "", type_name=rec.get("type") or "",
        level=rec.get("level") or "", system_name=rec.get("system_name") or "",
        system_type=rec.get("system_type_name") or "", abbreviation=rec.get("system_abbreviation") or "",
        system_code=rec.get("system_code") or "", size_text=rec.get("size_text") or "",
        connectors=rec.get("connectors") or [], record=rec,
    )
    classifier.apply(e)
    modeled = (rec.get("insulation_thickness_m") or 0.0) * M
    if modeled > 0:
        e.insulation_mm, e.insulation_source = modeled, "modeled"
    else:
        default = classifier.rules.system_classes[e.cls].insulation_mm if domain in ("pipe", "duct") else 0.0
        e.insulation_mm, e.insulation_source = (default, "default") if default > 0 else (0.0, "none")
    return e


def _curve_element(rec: dict, classifier: Classifier) -> Element:
    e = _common(rec, "mep_curve", CURVE_DOMAIN.get(rec.get("kind", ""), "other"), classifier)
    start, end = rec.get("start"), rec.get("end")
    if not start or not end:
        return e
    p0, p1 = np.array(start) * M, np.array(end) * M
    ins = e.insulation_mm
    if (rec.get("outer_diameter_m") or 0) > 0 and rec.get("shape") == "round":
        e.solid = capsule(p0, p1, rec["outer_diameter_m"] * M / 2 + ins)
    elif (rec.get("width_m") or 0) > 0 and (rec.get("height_m") or 0) > 0:
        x_axis = np.array(rec["section_x_axis"]) if rec.get("section_x_axis") else None
        e.solid = swept_box(p0, p1, x_axis, rec["width_m"] * M / 2 + ins, rec["height_m"] * M / 2 + ins)
    elif (rec.get("outer_diameter_m") or 0) > 0:
        e.solid = capsule(p0, p1, rec["outer_diameter_m"] * M / 2 + ins)
    return e


def _family_element(rec: dict, classifier: Classifier) -> Element:
    domain = CATEGORY_DOMAIN.get(rec.get("builtin_category", ""), "other")
    e = _common(rec, "mep_family", domain, classifier)
    return e


def fitting_legs(e: Element) -> list[Solid] | None:
    """A fitting as legs from each connector to the fitting centre (elbows, tees, crosses, reducers,
    tray fittings). The centre is the point closest to all connector axes."""
    conns = [c for c in e.connectors if c.get("origin") and c.get("direction")]
    if not conns:
        return None
    origins = np.array([c["origin"] for c in conns], float) * M
    dirs = np.array([c["direction"] for c in conns], float)
    dirs /= np.maximum(np.linalg.norm(dirs, axis=1, keepdims=True), 1e-9)
    A = np.zeros((3, 3))
    b = np.zeros(3)
    for o, d in zip(origins, dirs):
        P = np.eye(3) - np.outer(d, d)
        A += P
        b += P @ o
    if len(conns) >= 2 and np.linalg.cond(A) < 1e6:
        centre = np.linalg.solve(A, b)
        # Guard against axes that do not meet near the fitting (skewed data): fall back to the mean.
        if np.max(np.linalg.norm(origins - centre, axis=1)) > 3 * np.max(np.linalg.norm(origins - origins.mean(0), axis=1)) + 50:
            centre = origins.mean(axis=0)
    else:
        centre = origins.mean(axis=0)
    ins = e.insulation_mm
    inward = [(centre - o) / max(float(np.linalg.norm(centre - o)), 1e-9) for o in origins]
    legs, leg_conns = [], []
    for idx, (c, o) in enumerate(zip(conns, origins)):
        length = float(np.linalg.norm(centre - o))
        if (c.get("diameter_m") or 0) > 0:
            r = c["diameter_m"] * M / 2 + ins
            end = centre if length > 1 else o + np.array(c["direction"]) * -1.0
            legs.append(capsule(o, end, r))
            leg_conns.append(c)
        elif (c.get("width_m") or 0) > 0 and (c.get("height_m") or 0) > 0:
            hw, hh = c["width_m"] * M / 2 + ins, c["height_m"] * M / 2 + ins
            if length < 1:
                continue
            d = inward[idx]
            x_axis = np.array(c["x_axis"]) if c.get("x_axis") else None
            ext = 0.0
            if len(conns) == 2 and x_axis is not None:
                # Close the outer corner of an elbow: half the section in the bend plane × tan(turn / 2).
                other = inward[1 - idx]
                turn = float(np.arccos(np.clip(-np.dot(d, other), -1, 1)))
                n = np.cross(d, other)
                if np.linalg.norm(n) > 1e-6 and turn > 1e-3:
                    p = np.cross(n / np.linalg.norm(n), d)
                    y_axis = np.cross(d, x_axis)
                    half_in_plane = hw * abs(float(np.dot(x_axis, p))) + hh * abs(float(np.dot(y_axis, p)))
                    ext = half_in_plane * float(np.tan(min(turn, 2.0) / 2))
            legs.append(swept_box(o, centre + d * ext, x_axis, hw, hh))
            leg_conns.append(c)
    e.leg_conns, e.centre = leg_conns, centre
    return legs or None


def _level(rec: dict, manifest: dict) -> Level:
    # Schema 0.1.0 wrote Level.Elevation (relative to the elevation base) as elevation_m;
    # project_elevation_m held the internal elevation.
    if manifest.get("schema_version") == "0.1.0" and "project_elevation_m" in rec:
        z = rec["project_elevation_m"]
    else:
        z = rec["elevation_m"]
    source = "HOST" if rec.get("source_model_key") == "HOST" else "LINK"
    return Level(name=rec.get("name", ""), elevation=z * M, source=source)


def _grids(records: list[dict]) -> list[Grid]:
    grids: dict[tuple, Grid] = {}
    for rec in records:
        if rec.get("curve_kind") != "line" or not rec.get("start") or not rec.get("end"):
            continue
        start = np.array(rec["start"][:2]) * M
        end = np.array(rec["end"][:2]) * M
        key = (rec.get("name"), round(float(start[0] + end[0]), -2), round(float(start[1] + end[1]), -2))
        grids.setdefault(key, Grid(name=rec.get("name", ""), start=start, end=end))
    return list(grids.values())


def read_obj_vertices(path: Path, wanted: set[str]) -> dict[str, np.ndarray]:
    """Vertices (metres) of the wanted OBJ objects. Faces are not needed for fitted boxes."""
    cache = path.with_suffix(".lwpcache.npz")
    stat = path.stat()
    stamp = f"{stat.st_size}-{int(stat.st_mtime)}"
    if cache.exists():
        try:
            with np.load(cache, allow_pickle=False) as data:
                if str(data["__stamp__"]) == stamp:
                    names = [str(n) for n in data["__names__"]]
                    if wanted.issubset(names):
                        return {n: data[f"o{i}"] for i, n in enumerate(names) if n in wanted}
        except (OSError, KeyError, ValueError):
            pass

    buffers: dict[str, list[str]] = defaultdict(list)
    current: list[str] | None = None
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            c = line[:2]
            if c == "v ":
                if current is not None:
                    current.append(line[2:])
            elif c == "o ":
                name = line[2:].strip()
                current = buffers[name] if name in wanted else None
    result = {name: np.array(" ".join(lines).split(), dtype=float).reshape(-1, 3) for name, lines in buffers.items()}

    try:
        names = list(result)
        np.savez(cache, __stamp__=np.array(stamp), __names__=np.array(names),
                 **{f"o{i}": result[n] for i, n in enumerate(names)})
    except OSError:
        pass
    return result
