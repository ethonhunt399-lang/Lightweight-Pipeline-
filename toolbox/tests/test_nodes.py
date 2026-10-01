import numpy as np

from lwp.classify import Classifier
from lwp.corridor import Corridor
from lwp.detect import element_distance
from lwp.geometry import capsule
from lwp.package import Element, Package, fitting_legs
from lwp.plan import apply
from lwp.rules import load_rules
from lwp.section import Section, Strand
from lwp.solver import Layout, Placement

R = 0.084   # m, DN150 outer radius


def pipe(key, a, b, connectors=()):
    e = Element(key=key, origin="mep_curve", kind="pipe", domain="pipe", category="管道", family="", type_name="",
                level="", system_type="J1", abbreviation="J1", cls="water_supply", group="water",
                solid=capsule(np.array(a) * 1000, np.array(b) * 1000, R * 1000),
                connectors=list(connectors), record={"start": list(a), "end": list(b), "slope": 0})
    return e


def test_side_exit_rises_over_neighbour():
    rules = load_rules()
    z = 4.2
    # Strand A along X at y=1.0 with a tee at x=5 whose branch goes +Y (right) over strand B at y=1.5.
    a1 = pipe("A1", (0, 1.0, z), (4.9, 1.0, z))
    a2 = pipe("A2", (5.1, 1.0, z), (10, 1.0, z))
    branch = pipe("BR", (5.0, 1.1, z), (5.0, 4.0, z))
    b = pipe("B", (0, 1.5, z), (10, 1.5, z))
    tee = Element(key="T", origin="mep_family", kind="fitting", domain="pipe", category="管件", family="", type_name="",
                  level="", system_type="J1", cls="water_supply", group="water",
                  connectors=[
                      {"connector_type": "End", "origin": [4.9, 1.0, z], "direction": [-1, 0, 0], "diameter_m": 2 * R, "connected": [{"key": "A1"}]},
                      {"connector_type": "End", "origin": [5.1, 1.0, z], "direction": [1, 0, 0], "diameter_m": 2 * R, "connected": [{"key": "A2"}]},
                      {"connector_type": "End", "origin": [5.0, 1.1, z], "direction": [0, 1, 0], "diameter_m": 2 * R, "connected": [{"key": "BR"}]},
                  ], record={})
    tee.parts = fitting_legs(tee)
    tee.solid = capsule(np.array([4900, 1000, z * 1000]), np.array([5100, 1000, z * 1000]), R * 1000)
    elements = {e.key: e for e in (a1, a2, branch, b, tee)}
    pkg = Package(path=None, manifest={}, elements=elements, levels=[], grids=[], warnings=[], classifier=Classifier(rules))

    cor = Corridor("T", "x", 0, 10000, 0, 3000)
    zb = z * 1000 - R * 1000
    sa = Strand("S01", "water_supply", "water", "pipe", "pipe", "J1", "J1", "DN150", True, 168, 168, 1000, zb, 0, 10000,
                segments=["A1", "A2"], fittings=["T"], exits={"right"})
    sb = Strand("S02", "water_supply", "water", "pipe", "pipe", "J1", "J1", "DN150", True, 168, 168, 1500, zb, 0, 10000,
                segments=["B"])
    sec = Section(corridor=cor, floor_z=1600, floor_name="B1F", ceiling=4650 + 1000, ceiling_key="", v_lo=0, v_hi=3000,
                  strands=[sa, sb], beams=[], crossings=[], blocked=[], ceiling_max=5650)
    layout = Layout("headroom", "OPTIMAL", {"S01": Placement("S01", 0, 1000, zb), "S02": Placement("S02", 0, 1500, zb)}, [], {})

    assert element_distance(branch, b)[0] < 0  # the branch runs through strand B
    moved, moves, nodes, links = apply(pkg, sec, layout, rules)
    assert not links.open
    assert len(nodes) == 1 and nodes[0].side == "right"
    assert nodes[0].rise_mm > 2 * R * 1000      # rises above strand B
    t2 = moved.elements["T"]
    assert element_distance(t2, moved.elements["B"])[0] > 0
    assert "BR" in moves and moves["BR"][2] > 0  # the branch is lifted with the node
    assert element_distance(moved.elements["BR"], moved.elements["B"])[0] > 0
    assert "B" not in moves or not np.any(moves["B"])
