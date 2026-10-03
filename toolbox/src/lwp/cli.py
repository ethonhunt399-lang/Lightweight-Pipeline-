"""Command line: lwp check <package> [--rules FILE] [--out DIR]; lwp struct <structure.json> …"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

from . import headroom_map
from .detect import detect_conflicts, detect_headroom
from .health import check
from .package import load_package
from .report import write_outputs
from .rules import load_rules
from .viewer import build_data, write_viewer
from .lanes_cmd import cmd_lanes
from .solve_cmd import cmd_corridors, cmd_drawings, cmd_solve
from .structjson import load_structure


def cmd_check(args) -> int:
    t0 = time.perf_counter()
    rules = load_rules(args.rules)
    package = load_package(args.package, rules)
    t1 = time.perf_counter()
    health = check(package)
    conflicts = detect_conflicts(package, rules)
    headroom = detect_headroom(package, rules)
    t2 = time.perf_counter()
    out = Path(args.out) if args.out else Path(args.package).with_suffix(".lwp")
    report = write_outputs(out, package, rules, health, conflicts, headroom)
    reference = load_package(args.compare, rules) if args.compare else None
    viewer = write_viewer(out / "view.html", build_data(package, rules, conflicts, headroom, reference, args.compare_name))
    print(f"viewer: {viewer}")
    print(f"elements {len(package.elements)}  conflicts {len(conflicts)}  "
          f"load {t1 - t0:.1f}s  detect {t2 - t1:.1f}s")
    print(f"report: {report}")
    return 0


def cmd_view(args) -> int:
    rules = load_rules(args.rules)
    package = load_package(args.package, rules)
    conflicts = [] if args.no_check else detect_conflicts(package, rules)
    reference = load_package(args.compare, rules) if args.compare else None
    out = Path(args.out) if args.out else Path(args.package).with_suffix(".view.html")
    data = build_data(package, rules, conflicts, None, reference, args.compare_name)
    print(f"viewer: {write_viewer(out, data)}")
    return 0


def cmd_struct(args) -> int:
    """Clear-height estimate from a structure drawn up from construction drawings (no MEP yet)."""
    rules = load_rules(args.rules)
    if args.min_clear is not None:
        rules = rules.model_copy(update={
            "version": f"{rules.version} · 分档基准 {args.min_clear / 1000:.1f} m",
            "headroom": rules.headroom.model_copy(update={"min_clear_mm": args.min_clear})})
    package = load_structure(args.structure, rules, title=args.title or "", floor_name=args.floor,
                             upper_name=args.upper)
    out = Path(args.out) if args.out else Path(args.structure).with_suffix(".lwp")
    out.mkdir(parents=True, exist_ok=True)
    viewer = write_viewer(out / "view.html", build_data(package, rules), title=args.title or None)
    floor = package.host_levels()[0].elevation
    hmap = headroom_map.build(package, floor, args.cell)
    stats = headroom_map.summary(hmap, package, rules.headroom.min_clear_mm)
    beams = [e for e in package.elements.values() if e.kind == "beam"]
    stats = {"title": package.manifest["project_name"], "structure": Path(args.structure).name,
             "note": "净高 = 梁底 / 板底 − 下层楼面（结构面），未扣机电、吊顶、面层",
             "beams": len(beams), "beams_inferred": sum(e.inferred for e in beams), **stats}
    (out / "headroom.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    np.savez_compressed(out / "headroom_grid.npz", clear=hmap.clear, source=hmap.source, x0=hmap.x0, y0=hmap.y0,
                        cell=hmap.cell, keys=np.array(hmap.keys),
                        inferred=np.array([package.elements[k].inferred for k in hmap.keys], dtype=bool))
    low = stats["lowest"]
    print(f"viewer: {viewer}")
    print(f"beams {stats['beams']} (inferred {stats['beams_inferred']})  covered {stats['covered_m2']} m²  "
          + (f"lowest {low['clear_mm'] / 1000:.2f} m  {low['element']}" if low else "no headroom cells"))
    return 0


def main(argv=None) -> int:
    # Reproducible plans: the solver model is built by iterating sets of keys, whose order depends on the
    # string hash seed. Fix it (re-run the command once with a fixed seed).
    if argv is None and os.environ.get("PYTHONHASHSEED") != "0":
        os.environ["PYTHONHASHSEED"] = "0"
        os.execv(sys.executable, [sys.executable, "-m", "lwp.cli", *sys.argv[1:]])
    parser = argparse.ArgumentParser(prog="lwp", description="管综工具箱")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check", help="体检 + 碰撞、净距、净高检测")
    p.add_argument("package", help="导出包目录 <name>.slbh")
    p.add_argument("--rules", help="规则文件（默认：小区地下室默认规则）")
    p.add_argument("--out", help="输出目录（默认：<package>.lwp）")
    p.add_argument("--compare", help="对比参照包（如管综调整前的导出包）")
    p.add_argument("--compare-name", default="调整前", help="参照包在预览中的名称")
    p.set_defaults(func=cmd_check)
    v = sub.add_parser("view", help="生成三维预览 HTML")
    v.add_argument("package", help="导出包目录 <name>.slbh")
    v.add_argument("--rules", help="规则文件")
    v.add_argument("--out", help="输出 HTML 路径（默认：<package>.view.html）")
    v.add_argument("--no-check", action="store_true", help="不做碰撞检测，只显示模型")
    v.add_argument("--compare", help="对比参照包（如管综调整前的导出包）")
    v.add_argument("--compare-name", default="调整前", help="参照包在预览中的名称")
    v.set_defaults(func=cmd_view)
    st = sub.add_parser("struct", help="施工图翻模结构（structure JSON）→ 结构净高预估分区图 + 三维预览")
    st.add_argument("structure", help="structure JSON（梁、柱墙、板、轴网，单位 mm）")
    st.add_argument("--rules", help="规则文件")
    st.add_argument("--out", help="输出目录（默认：<structure>.lwp）")
    st.add_argument("--title", help="预览标题")
    st.add_argument("--floor", default="1F", help="下层（楼面）名称")
    st.add_argument("--upper", default="2F", help="上层（梁板）名称")
    st.add_argument("--min-clear", type=float, help="分区图分档基准 mm（默认取规则文件的最小净高）")
    st.add_argument("--cell", type=float, default=500.0, help="分区图网格 mm")
    st.set_defaults(func=cmd_struct)
    c = sub.add_parser("corridors", help="列出平行管线密集的直线走廊候选")
    c.add_argument("package")
    c.add_argument("--rules")
    c.add_argument("--out", help="写出候选走廊 YAML")
    c.set_defaults(func=cmd_corridors)
    s = sub.add_parser("solve", help="走廊分层排布：三方案、断面图、三维预览、与人工方案对比")
    s.add_argument("package", help="排布前的导出包")
    s.add_argument("--corridor", required=True, help="走廊名称（lwp corridors 列出的 A、B…）或走廊 YAML")
    s.add_argument("--gold", help="人工方案（调整后）的导出包，用于对比与复核")
    s.add_argument("--rules")
    s.add_argument("--schemes", help="逗号分隔：headroom,changes,supports（默认全部）")
    s.add_argument("--effort", type=float, default=2.0, help="每阶段求解的确定性时间上限（默认 2）")
    s.add_argument("--out", help="输出目录")
    s.add_argument("--drawings", help="lwp drawings 输出的图纸 JSON（车位 / 车道分区）")
    s.add_argument("--rounds", type=int, default=4, help="迭代求解轮数：求解—生成节点—碰撞检测—加约束（默认 4；1 为不迭代）")
    s.set_defaults(func=cmd_solve)
    dr = sub.add_parser("drawings", help="解析施工图 DXF：与模型轴网对齐，提取车位、车道、人防墙、设计说明中的排布要求")
    dr.add_argument("package", help="导出包（提供轴网用于对齐）")
    dr.add_argument("--plan", help="地下室建筑平面 DXF（车位、车道、人防）")
    dr.add_argument("--notes", nargs="*", default=[], help="设计说明 DXF（可多个）")
    dr.add_argument("--rules")
    dr.add_argument("--out", required=True, help="输出 JSON")
    dr.set_defaults(func=cmd_drawings)
    ln = sub.add_parser("lanes", help="全地下室车道净高：按图纸车道统计每条车道的净高、分级，可与参照模型对比")
    ln.add_argument("package", help="导出包（整个地下室）")
    ln.add_argument("--drawings", required=True, help="lwp drawings 输出的图纸 JSON（车位、车道流线）")
    ln.add_argument("--name", default="调整后", help="本模型名称")
    ln.add_argument("--compare", help="参照导出包（如调整前）")
    ln.add_argument("--compare-name", default="调整前")
    ln.add_argument("--rules")
    ln.add_argument("--cell", type=float, default=500.0, help="净高网格 mm")
    ln.add_argument("--min-clear", type=float, default=2200.0, help="车道最低净高 mm（标红）")
    ln.add_argument("--out", required=True, help="输出目录")
    ln.set_defaults(func=cmd_lanes)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
