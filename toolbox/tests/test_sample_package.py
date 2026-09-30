"""Runs on a real export when LWP_SAMPLE points to a <name>.slbh directory; skipped otherwise."""

import os
from pathlib import Path

import pytest

from lwp.detect import detect_conflicts, detect_headroom
from lwp.health import check
from lwp.package import load_package
from lwp.rules import load_rules

SAMPLE = os.environ.get("LWP_SAMPLE")
pytestmark = pytest.mark.skipif(not SAMPLE, reason="set LWP_SAMPLE to an exported .slbh directory")


def test_sample_runs_end_to_end():
    rules = load_rules()
    package = load_package(Path(SAMPLE), rules)
    mep = package.mep()
    assert mep, "no MEP elements"
    curves = [e for e in mep if e.origin == "mep_curve"]
    assert sum(e.solid is not None for e in curves) / len(curves) > 0.99
    assert package.host_levels(), "no host levels"
    check(package)
    detect_conflicts(package, rules)
    detect_headroom(package, rules)
