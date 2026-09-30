"""Command line: lwp check <package> [--rules FILE] [--out DIR]."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .detect import detect_conflicts, detect_headroom
from .health import check
from .package import load_package
from .report import write_outputs
from .rules import load_rules
from .viewer import build_data, write_viewer
from .solve_cmd import cmd_corridors, cmd_solve


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


def main(argv=None) -> int:
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
    c = sub.add_parser("corridors", help="列出平行管线密集的直线走廊候选")
    c.add_argument("package")
    c.add_argument("--rules")
    c.add_argument("--out", help="写出候选走廊 YAML")
    c.set_defaults(func=cmd_corridors)
    s = sub.add_parser("solve", help="走廊分层排布：三方案、断面图、三维预览、与人工排布对比")
    s.add_argument("package", help="排布前的导出包")
    s.add_argument("--corridor", required=True, help="走廊名称（lwp corridors 列出的 A、B…）或走廊 YAML")
    s.add_argument("--gold", help="人工排布后的导出包（用于对比）")
    s.add_argument("--rules")
    s.add_argument("--schemes", help="逗号分隔：headroom,changes,supports（默认全部）")
    s.add_argument("--effort", type=float, default=2.0, help="每阶段求解的确定性时间上限（默认 2）")
    s.add_argument("--out", help="输出目录")
    s.set_defaults(func=cmd_solve)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
