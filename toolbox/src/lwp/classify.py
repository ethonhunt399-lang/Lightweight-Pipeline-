"""System classification: map exported system fields to the rule set's system classes."""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from .rules import RuleSet

if TYPE_CHECKING:
    from .package import Element


class Classifier:
    def __init__(self, rules: RuleSet):
        self.rules = rules
        self.hits: Counter[str] = Counter()      # rule description → element count
        self.unmatched: Counter[tuple] = Counter()

    def apply(self, e: "Element") -> None:
        code_fields = [e.system_type, e.abbreviation]
        for rule in self.rules.classification:
            if rule.matches(e.domain, code_fields, e.system_type or e.system_name, e.type_name, e.system_code):
                self._set(e, rule.cls, rule.describe(), rule.confirmed)
                self.hits[rule.describe()] += 1
                return
        self._set(e, "unknown", "", True)
        if e.domain in ("pipe", "duct", "tray"):
            self.unmatched[(e.domain, e.system_type or e.system_name, e.abbreviation, e.type_name if e.domain == "tray" else "")] += 1

    def _set(self, e: "Element", cls: str, rule: str, confirmed: bool) -> None:
        e.cls = cls
        e.group = self.rules.system_classes[cls].group
        e.rule = rule
        e.rule_confirmed = confirmed
