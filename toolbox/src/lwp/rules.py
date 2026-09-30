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
    confirmed: bool = False
    note: str | None = None


class RuleSet(_Strict):
    name: str
    version: str
    system_classes: dict[str, SystemClass]
    classification: list[ClassificationRule]
    clearance_mm: Clearance
    headroom: Headroom
    obstacles: dict[str, list[str]]
    connected_hops_ignored: int = 2
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
