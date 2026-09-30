"""Summarize an SLBH MEP export package (<name>.slbh/ with manifest.json + mep.json).

Usage:
    python tools/inspect_mep_package.py path/to/name.slbh [--out report.md]

Standard library only. Prints a Markdown report of what the exporter produced and which
fields are missing, so the first exports can be checked field by field.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def load_json(path: Path):
    # DataContractJsonSerializer writes UTF-8 without BOM; tolerate a BOM anyway.
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def pct(part: int, whole: int) -> str:
    return "-" if whole == 0 else f"{part / whole:.0%}"


def table(headers, rows):
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def has_size(curve) -> bool:
    return (curve.get("outer_diameter_m") or 0) > 0 or (
        (curve.get("width_m") or 0) > 0 and (curve.get("height_m") or 0) > 0
    )


def inspect(package: Path) -> str:
    manifest = load_json(package / "manifest.json")
    data = load_json(package / manifest["files"]["mep"])
    curves = data.get("curves") or []
    families = data.get("family_instances") or []
    keys = {c["key"] for c in curves} | {f["key"] for f in families}

    out = [f"# MEP 导出包检查：{package.name}", ""]
    out.append(table(
        ["项", "值"],
        [
            ["schema", f'{manifest.get("schema_name")} {manifest.get("schema_version")}'],
            ["导出工具版本", manifest.get("exporter_version")],
            ["Revit 版本", manifest.get("revit_version")],
            ["项目 / 视图", f'{manifest.get("project_name")} / {manifest.get("view_name")}'],
            ["剖面框", "启用" if manifest["scope"].get("section_box_active") else "未启用（整个视图）"],
            ["包含链接", manifest["scope"].get("include_linked_models")],
            ["耗时 ms", json.dumps(manifest.get("timings_ms"), ensure_ascii=False)],
        ],
    ))

    # Curves
    out += ["", "## 管线", ""]
    rows = []
    for kind, n in sorted(Counter(c.get("kind") for c in curves).items()):
        group = [c for c in curves if c.get("kind") == kind]
        rows.append([
            kind, n,
            pct(sum(has_size(c) for c in group), n),
            pct(sum((c.get("insulation_thickness_m") or 0) > 0 for c in group), n),
            pct(sum(bool(c.get("system_name")) for c in group), n),
            pct(sum(c.get("section_x_axis") is not None for c in group), n),
            pct(sum(len(c.get("connectors") or []) >= 2 for c in group), n),
            sum(bool(c.get("crosses_scope_boundary")) for c in group),
            sum(abs(c.get("slope") or 0) > 1e-6 for c in group),
        ])
    out.append(table(["类型", "数量", "有尺寸", "有保温", "有系统", "有截面朝向", "≥2连接件", "跨边界", "有坡度"], rows))

    # Family instances
    out += ["", "## 管件、附件、设备、支架", ""]
    rows = []
    for kind, n in sorted(Counter(f.get("kind") for f in families).items()):
        group = [f for f in families if f.get("kind") == kind]
        rows.append([
            kind, n,
            pct(sum(bool(f.get("part_type")) for f in group), n),
            pct(sum(len(f.get("connectors") or []) > 0 for f in group), n),
            pct(sum(f.get("transform") is not None for f in group), n),
        ])
    out.append(table(["类型", "数量", "有管件类型", "有连接件", "有变换"], rows))
    part_types = Counter(f.get("part_type") for f in families if f.get("part_type"))
    if part_types:
        out += ["", "管件类型分布：" + "，".join(f"{k} {v}" for k, v in part_types.most_common())]

    # Systems
    out += ["", "## 系统", ""]
    systems = Counter((e.get("system_code"), e.get("system_type_name") or e.get("service_type") or "")
                      for e in curves + families)
    out.append(table(["system_code", "系统类型 / 服务类型", "构件数"],
                     [[code, name, n] for (code, name), n in systems.most_common(40)]))

    # Topology
    refs = [(e["key"], r["key"]) for e in curves + families
            for c in (e.get("connectors") or []) for r in (c.get("connected") or [])]
    dangling = [r for r in refs if r[1] not in keys]
    unconnected_ends = sum(
        1 for e in curves for c in (e.get("connectors") or [])
        if c.get("connector_type") == "End" and not c.get("connected")
    )
    out += ["", "## 连接关系", ""]
    out.append(table(["项", "值"], [
        ["连接引用总数", len(refs)],
        ["指向包外构件的引用（范围边界处属正常）", len(dangling)],
        ["管线未连接的端部连接件", unconnected_ends],
    ]))

    # Context
    counts = manifest.get("counts", {})
    out += ["", "## 出图与坐标", ""]
    loc = manifest.get("project_location") or {}
    out.append(table(["项", "值"], [
        ["标高", len(manifest.get("levels") or [])],
        ["轴网", len(manifest.get("grids") or [])],
        ["房间", len(manifest.get("rooms") or [])],
        ["链接", len(manifest.get("links") or [])],
        ["基点", ", ".join(b.get("kind", "") for b in manifest.get("base_points") or [])],
        ["internal→shared 变换", "有" if loc.get("internal_to_shared") else "无"],
    ]))

    # Diagnostics
    diags = manifest.get("diagnostics") or []
    out += ["", "## 诊断信息", ""]
    if not diags:
        out.append("无。")
    else:
        by_field = Counter((d.get("field"), d.get("message")) for d in diags)
        out.append(table(["字段", "信息", "次数"], [[f, m, n] for (f, m), n in by_field.most_common(30)]))
        if counts.get("diagnostics_dropped"):
            out.append(f"\n另有 {counts['diagnostics_dropped']} 条超出上限未记录。")

    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("package", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    if not (args.package / "manifest.json").exists():
        print(f"manifest.json not found in {args.package} (export incomplete or not an MEP package)", file=sys.stderr)
        return 1
    report = inspect(args.package)
    if args.out:
        args.out.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
