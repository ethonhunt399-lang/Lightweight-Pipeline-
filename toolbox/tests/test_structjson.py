import json

import numpy as np
import pytest

from lwp import headroom_map
from lwp.detect import detect_conflicts
from lwp.geometry import points_in_rings
from lwp.rules import load_rules
from lwp.structjson import beam_inferred, load_structure
from lwp.viewer import build_data

FLOOR, SLAB_TOP, SLAB_TH = -60.0, 4940.0, 120.0


@pytest.fixture()
def structure(tmp_path):
    """10 m × 10 m slab with a 2 m × 2 m opening, one drawn beam and one inferred beam, one column."""
    data = {
        "z": {"col_bottom": FLOOR, "col_top": SLAB_TOP, "slab_top": SLAB_TOP, "slab_th": SLAB_TH},
        "beams": [
            {"a": [0, 2000], "b": [10000, 2000], "w": 300, "h": 800, "top": 4940, "mark": "KL1(1)",
             "basis": "图纸", "depth_src": "label", "key": "B1"},
            {"a": [0, 8000], "b": [10000, 8000], "w": 400, "h": 1300, "top": 4940, "mark": "L?",
             "basis": "无集中标注，按支座定类型、默认截面", "depth_src": "default", "key": "B2"},
        ],
        "verticals": [{"kind": "column", "pts": [[0, 0], [500, 0], [500, 500], [0, 500]]}],
        "slab": [
            {"pts": [[0, 0], [10000, 0], [10000, 10000], [0, 10000]]},
            {"pts": [[4000, 4000], [4000, 6000], [6000, 6000], [6000, 4000]]},
        ],
        "grid": [[[0, 0], [0, 10000]], [[0, 0], [10000, 0]]],
        "grid_labels": [["1", [0, -2000]], ["A", [-2000, 0]]],
    }
    path = tmp_path / "structure.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def cell_at(hmap, x, y):
    i = int((x - hmap.x0) // hmap.cell)
    j = int((y - hmap.y0) // hmap.cell)
    return hmap.clear[j, i], hmap.keys[hmap.source[j, i]] if hmap.source[j, i] >= 0 else None


def test_points_in_rings_with_hole():
    outer = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], float)
    hole = np.array([[4, 4], [6, 4], [6, 6], [4, 6]], float)
    X, Y = np.array([1.0, 5.0, 11.0]), np.array([1.0, 5.0, 5.0])
    assert points_in_rings(X, Y, [outer, hole]).tolist() == [True, False, False]


def test_beam_inferred_rules():
    assert not beam_inferred({"basis": "图纸", "depth_src": "label"})
    assert not beam_inferred({"basis": "图纸(推定支座)", "depth_src": "label_h_drawn_w"})
    assert beam_inferred({"basis": "图纸", "depth_src": "same_beam"})
    assert beam_inferred({"basis": "无集中标注，就近相似：L21(1)", "depth_src": "label"})
    assert beam_inferred({"basis": "图纸", "depth_src": "label", "inferred": True})


def test_load_structure(structure):
    rules = load_rules()
    pkg = load_structure(structure, rules)
    kinds = sorted(e.kind for e in pkg.elements.values())
    assert kinds == ["beam", "beam", "column", "slab"]
    slab = next(e for e in pkg.elements.values() if e.kind == "slab")
    assert len(slab.footprint) == 2                      # outline + opening
    assert pkg.elements["B2"].inferred and not pkg.elements["B1"].inferred
    assert pkg.elements["B1"].solid.bottom_z() == pytest.approx(4140)
    assert [g.name for g in pkg.grids] == ["1", "A"]


def test_headroom_map_beams_slab_and_opening(structure):
    rules = load_rules()
    pkg = load_structure(structure, rules)
    hmap = headroom_map.build(pkg, FLOOR)
    clear, key = cell_at(hmap, 7250, 5250)               # plain slab
    assert clear == pytest.approx(SLAB_TOP - SLAB_TH - FLOOR) and key.endswith("|slab|0")
    clear, key = cell_at(hmap, 5250, 2250)               # under KL1: 4940 − 800 + 60
    assert clear == pytest.approx(4200) and key == "B1"
    clear, key = cell_at(hmap, 5250, 7750)               # under the inferred 1300 beam
    assert clear == pytest.approx(3700) and key == "B2"
    clear, key = cell_at(hmap, 5250, 5250)               # opening, nothing above
    assert np.isnan(clear) and key is None

    s = headroom_map.summary(hmap, pkg, 3800)
    assert s["lowest"]["key"] == "B2" and s["lowest"]["inferred"]
    assert s["bands"][0]["area_m2"] == s["bands"][0]["inferred_m2"] > 0
    assert s["covered_m2"] == pytest.approx(100 - 4, abs=1.0)


def test_slab_not_a_clash_and_not_drawn(structure):
    rules = load_rules()
    pkg = load_structure(structure, rules)
    assert all("slab" not in c.a and "slab" not in c.b for c in detect_conflicts(pkg, rules))
    data = build_data(pkg, rules)
    assert all("slab" not in it["k"] for it in data["elements"])
    assert set(data["classes"]) == {"beam", "beam_inferred", "column"}
    assert data["classes"]["beam_inferred"]["label"] == "梁（推定）"
    b2 = next(it for it in data["elements"] if it["k"] == "B2")
    assert b2["inf"] == 1 and b2["bs"].startswith("无集中标注")
    hm = data["headroom"]
    assert hm["others"] and any(s <= -2 for s in hm["src"])   # slab cells point to a label
    assert sum(hm["inf"]) > 0
