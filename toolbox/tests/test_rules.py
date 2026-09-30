from lwp.classify import Classifier
from lwp.package import Element
from lwp.rules import load_rules


def element(domain, system_type="", abbreviation="", type_name="", system_code=""):
    return Element(key="k", origin="mep_curve", kind="pipe", domain=domain, category="", family="",
                   type_name=type_name, level="", system_type=system_type, abbreviation=abbreviation,
                   system_code=system_code)


def classify(**kw):
    e = element(**kw)
    Classifier(load_rules()).apply(e)
    return e.cls


def test_default_rules_load():
    rules = load_rules()
    assert rules.headroom.min_clear_mm == 2200
    assert rules.clearance_mm.required("water", "power")[0] == 300
    assert rules.clearance_mm.required("structure", "air")[0] == 50
    assert rules.clearance_mm.required("air", "air")[0] == rules.clearance_mm.default


def test_codes_by_domain():
    assert classify(domain="pipe", system_type="J2", abbreviation="J2") == "water_supply"
    assert classify(domain="duct", system_type="加压送风系统", abbreviation="JY") == "pressurization"
    assert classify(domain="pipe", system_type="YF", abbreviation="Y") == "pressure_drainage"
    assert classify(domain="pipe", system_type="喷淋系统", abbreviation="ZP") == "sprinkler"
    assert classify(domain="duct", system_type="排烟兼排风", abbreviation="PY/F") == "smoke_exhaust"


def test_trays_by_type_name():
    assert classify(domain="tray", type_name="火灾自动报警") == "weak_tray"
    assert classify(domain="tray", type_name="充电桩桥架") == "power_tray"
    assert classify(domain="tray", type_name="随便什么") == "power_tray"


def test_unknown():
    assert classify(domain="duct") == "unknown"
