"""Rule set loading. All engineering numbers live in YAML rule files, not in code."""

from __future__ import annotations

import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field

BUILTIN_DIR = Path(__file__).parent / "rules"
DEFAULT_RULES = BUILTIN_DIR / "residential_basement.yaml"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SystemClass(_Strict):
    label: str
    group: str
    pressure: bool
    insulation_mm: float = 0.0


class ClassificationRule(_Strict):
    cls: str = Field(alias="class")
    domain: str | None = None
    code: str | None = None
    system_type: str | None = None
    type_name: str | None = None
    system_code: str | None = None
    confirmed: bool = False
    note: str | None = None

    def matches(self, domain: str, code_fields: list[str], system_type: str, type_name: str, system_code: str) -> bool:
        if self.domain and self.domain != domain:
            return False
        if self.code and not any(_search(self.code, f) for f in code_fields):
            return False
        if self.system_type and not _search(self.system_type, system_type):
            return False
        if self.type_name and not _search(self.type_name, type_name):
            return False
        if self.system_code and not _search(self.system_code, system_code):
            return False
        return True

    def describe(self) -> str:
        parts = [f"{k}={v}" for k, v in (("domain", self.domain), ("code", self.code), ("system_type", self.system_type),
                                          ("type_name", self.type_name), ("system_code", self.system_code)) if v]
        return " ".join(parts) + f" → {self.cls}"


class ClearancePair(_Strict):
    a: str
    b: str
    mm: float
    confirmed: bool = False
    note: str | None = None


class Clearance(_Strict):
    default: float
    pairs: list[ClearancePair] = []

    def required(self, group_a: str, group_b: str) -> tuple[float, ClearancePair | None]:
        for pair in self.pairs:
            if _pair_matches(pair, group_a, group_b):
                return pair.mm, pair
        return self.default, None

    @property
    def max_mm(self) -> float:
        return max([self.default] + [p.mm for p in self.pairs])


class Headroom(_Strict):
    floor_level: str = "auto"
    min_clear_mm: float
    lane_clear_mm: float | None = None    # driving lanes (None: min_clear_mm); needs lane / stall zones
    stall_clear_mm: float | None = None   # parking stalls
    confirmed: bool = False
    note: str | None = None


class LayoutPreferences(_Strict):
    trays_above_water: int = 50
    ducts_top: int = 30
    system_together: int = 20
    exit_side: int = 40
    exit_top: int = 30
    tray_bottom: int = 5                  # per 10 mm a tray bottom lies below tray_bottom_preferred_mm


class TrayWater(_Strict):
    """Electrical above water (project rule, 2026-10-01)."""
    parallel_over_tray: str = "forbid"     # forbid: water may not run along directly above a tray; soft: preference
    parallel_max_mm: float = 500           # plan overlap along the tray longer than this counts as running along
    crossing_over_tray: str = "allow"      # allow: a short crossing over a tray is accepted; forbid
    leak_prone_over_tray: str = "forbid"   # flanges, valves, unions, air vents not directly above a tray
    leak_prone_margin_mm: float = 100      # widening of the tray footprint for leak-prone items
    leak_prone_pattern: str = "法兰|活接|由任|排气|阀"   # family/type names of leak-prone pipe fittings
    crossing_above_mm: float = 200         # water crossing above a tray: clear distance (plus a drip shield)
    confirmed: bool = False


class Layout(_Strict):
    max_layers: int = 4
    support_reserve_mm: float = 100
    layer_gap_min_mm: float = 100
    beam_clearance_mm: float = 50
    moved_threshold_mm: float = 20
    crossing_zone: str = "top"
    crossing_zone_max_mm: float = 250
    tray_water: TrayWater = TrayWater()
    tray_top_clearance_mm: float = 50     # tray top to beam / slab / the layer above (cable laying space)
    elevation_step_mm: float = 50         # layer bottoms on a grid relative to the floor (0: free)
    lane_priority_max_mm: float = 300     # lanes may be kept up to this much higher than the lowest bottom
    slab_thickness_mm: float = 250        # roof slab: crossing services may rise between beams to its underside
    tray_bottom_preferred_mm: float = 2500   # tray bottom above the floor, preferred (hard limit: headroom)
    confirmed: bool = False
    preferences: LayoutPreferences = LayoutPreferences()


class RuleSet(_Strict):
    name: str
    version: str
    system_classes: dict[str, SystemClass]
    classification: list[ClassificationRule]
    clearance_mm: Clearance
    headroom: Headroom
    obstacles: dict[str, list[str]]
    connected_hops_ignored: int = 3
    layout: Layout = Layout()
    source: str = ""

    def obstacle_kind(self, category: str) -> str | None:
        for kind, names in self.obstacles.items():
            if category in names:
                return kind
        return None


def load_rules(path: str | Path | None = None) -> RuleSet:
    path = Path(path) if path else DEFAULT_RULES
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rules = RuleSet.model_validate({**data, "source": str(path)})
    unknown = {r.cls for r in rules.classification} - set(rules.system_classes)
    if unknown:
        raise ValueError(f"classification refers to undefined system classes: {sorted(unknown)}")
    if "unknown" not in rules.system_classes:
        raise ValueError("rule set must define an 'unknown' system class")
    return rules


def _search(pattern: str, value: str | None) -> bool:
    return bool(value) and re.search(pattern, value, re.IGNORECASE) is not None


def _pair_matches(pair: ClearancePair, a: str, b: str) -> bool:
    def one(p: str, g: str) -> bool:
        return p == "any" or p == g
    return (one(pair.a, a) and one(pair.b, b)) or (one(pair.a, b) and one(pair.b, a))
