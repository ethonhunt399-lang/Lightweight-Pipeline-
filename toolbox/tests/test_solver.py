from lwp.corridor import Corridor
from lwp.rules import load_rules
from lwp.section import Section, Strand
from lwp.solver import solve


def strand(i, kind, cls, group, domain, w, h, v, z=4100.0):
    return Strand(id=f"S{i:02d}", cls=cls, group=group, domain=domain, kind=kind, system=cls, abbreviation=cls,
                  size=f"{w}", pressure=True, width=w, height=h, v=v, z=z, s_lo=0, s_hi=20000)


def section(ceiling=4650.0, width=3000.0):
    c = Corridor("T", "x", 0, 20000, 0, width)
    strands = [
        strand(1, "cable_tray", "power_tray", "power", "tray", 400, 100, 500),
        strand(2, "cable_tray", "weak_tray", "weak", "tray", 200, 100, 900),
        strand(3, "pipe", "water_supply", "water", "pipe", 168, 168, 1300),
        strand(4, "pipe", "sprinkler", "water", "pipe", 168, 168, 1500),
    ]
    return Section(corridor=c, floor_z=1600, floor_name="B1F", ceiling=ceiling, ceiling_key="", v_lo=0, v_hi=width,
                   strands=strands, beams=[], crossings=[], blocked=[])


def test_headroom_scheme_is_feasible_and_respects_clearances():
    rules = load_rules()
    sec = section()
    layout = solve(sec, rules, "headroom", seconds=1.0)
    assert layout.status in ("OPTIMAL", "FEASIBLE")
    lay = rules.layout
    min_bottom = sec.floor_z + rules.headroom.min_clear_mm + lay.support_reserve_mm
    for s in sec.strands:
        p = layout.placements[s.id]
        assert p.z >= min_bottom
        assert p.z + s.height <= sec.ceiling - lay.beam_clearance_mm
    # Same-layer horizontal clearance by group.
    items = sec.strands
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            pa, pb = layout.placements[a.id], layout.placements[b.id]
            if pa.layer == pb.layer:
                gap = abs(pa.v - pb.v) - (a.width + b.width) / 2
                assert gap >= rules.clearance_mm.required(a.group, b.group)[0] - 1
    assert layout.metrics["tray_below_water"] == 0


def test_infeasible_section_is_diagnosed():
    rules = load_rules()
    sec = section(ceiling=4000.0)          # below the headroom line: nothing fits
    layout = solve(sec, rules, "headroom", seconds=1.0)
    assert layout.status == "INFEASIBLE"
    assert any("净高" in d for d in layout.diagnosis)


def test_deterministic():
    rules = load_rules()
    a = solve(section(), rules, "changes", seconds=1.0)
    b = solve(section(), rules, "changes", seconds=1.0)
    assert {k: (p.layer, p.v, p.z) for k, p in a.placements.items()} == {k: (p.layer, p.v, p.z) for k, p in b.placements.items()}


def test_layer_bottoms_on_grid():
    rules = load_rules()
    sec = section()
    layout = solve(sec, rules, "headroom", seconds=1.0)
    step = rules.layout.elevation_step_mm
    for layer in layout.layers:
        assert (layer["bottom"] - sec.floor_z) % step == 0


def test_no_water_running_over_a_tray():
    # Too narrow for one layer: something must go on a second layer, but never water over a tray.
    rules = load_rules()
    sec = section(ceiling=5200.0, width=900.0)
    layout = solve(sec, rules, "headroom", seconds=1.0)
    assert layout.status in ("OPTIMAL", "FEASIBLE")
    for t in sec.strands:
        if t.domain != "tray":
            continue
        for w in sec.strands:
            if w.group != "water":
                continue
            pt, pw = layout.placements[t.id], layout.placements[w.id]
            over = abs(pt.v - pw.v) < (t.width + w.width) / 2
            assert not (over and pw.z > pt.z), (t.id, w.id)
