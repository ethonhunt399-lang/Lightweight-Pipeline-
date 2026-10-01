"""`lwp corridors` and `lwp solve`: corridor candidates, three schemes, evaluation and review pages."""

from __future__ import annotations

import time
from html import escape
from pathlib import Path

import yaml

from .corridor import Corridor, candidates, load_corridor
from .detect import detect_conflicts
from .package import Package, load_package
from .grids import GridLocator
from .plan import apply, evaluate, plan_json, review_human, write_json
from .rules import RuleSet, load_rules
from .section import Section, extract
from .section_svg import draw
from .solver import SCHEME_ORDER, SCHEMES, Layout, solve
from .viewer import build_data, write_viewer


def cmd_corridors(args) -> int:
    rules = load_rules(args.rules)
    package = load_package(args.package, rules)
    found = candidates(package)
    if not found:
        print("没有找到平行管线密集的直线区段")
        return 1
    docs = []
    for c, mean, where in found:
        sec = extract(package, c, rules)
        print(f"{c.name}: 沿 {c.axis.upper()} {c.length / 1000:.0f} m，平均 {mean:.1f} 根平行管线，"
              f"管线 {len(sec.strands)} 条（可移动 {len(sec.movable)}），横穿 {len(sec.crossings)}，{where}")
        docs.append({"corridor": c.to_dict(), "note": f"{where}；平均 {mean:.1f} 根平行管线"})
    if args.out:
        Path(args.out).write_text(yaml.safe_dump(docs, allow_unicode=True, sort_keys=False), encoding="utf-8")
        print(f"已写入 {args.out}")
    return 0


def _corridor(arg: str, package: Package) -> Corridor:
    if Path(arg).exists():
        return load_corridor(arg)
    for c, _, _ in candidates(package):
        if c.name == arg:
            return c
    raise SystemExit(f"找不到走廊 {arg!r}：给出 lwp corridors 列出的名称，或走廊 YAML 文件")


def cmd_solve(args) -> int:
    t0 = time.perf_counter()
    rules = load_rules(args.rules)
    package = load_package(args.package, rules)
    corridor = _corridor(args.corridor, package)
    gold = load_package(args.gold, rules) if args.gold else None
    out = Path(args.out) if args.out else Path(args.package).with_name(f"solve_{corridor.name}")
    out.mkdir(parents=True, exist_ok=True)

    sec = extract(package, corridor, rules)
    print(f"走廊 {corridor.name}：管线 {len(sec.strands)} 条（可移动 {len(sec.movable)}），横穿 {len(sec.crossings)}")
    (out / "section_original.svg").write_text(draw(sec, rules, None, "原模型断面"), encoding="utf-8")

    layouts: dict[str, Layout] = {}
    planned: dict[str, Package] = {}
    all_moves: dict[str, dict] = {}
    node_counts: dict[str, int] = {}
    handled: dict[str, set] = {}
    schemes = args.schemes.split(",") if args.schemes else SCHEME_ORDER
    region = _region(corridor)
    for scheme in schemes:
        t = time.perf_counter()
        layout = solve(sec, rules, scheme, seconds=args.effort)
        layouts[scheme] = layout
        print(f"  {SCHEMES[scheme]}：{layout.status}，{time.perf_counter() - t:.1f} s，{layout.metrics}")
        if not layout.placements:
            for d in layout.diagnosis:
                print("    " + d)
            continue
        moved, moves, nodes = apply(package, sec, layout, rules)
        node_counts[scheme] = len(nodes)
        handled[scheme] = {n.fitting for n in nodes}
        planned[scheme] = moved
        all_moves[scheme] = moves
        write_json(out / f"plan_{scheme}.json", plan_json(sec, layout, rules, moves, package, nodes))
        (out / f"section_{scheme}.svg").write_text(draw(sec, rules, layout, f"方案：{SCHEMES[scheme]}"), encoding="utf-8")
        conflicts = detect_conflicts(moved, rules)
        data = build_data(moved, rules, conflicts, None, package, "原模型", region=region,
                          title_suffix=f" · 走廊 {corridor.name} · {SCHEMES[scheme]}")
        write_viewer(out / f"view_{scheme}.html", data, f"走廊 {corridor.name} · {SCHEMES[scheme]}")

    evaluation = evaluate(package, planned, gold, rules, sec, all_moves, handled)
    for k, n in node_counts.items():
        evaluation[k]["n2_nodes"] = n
    review = None
    if gold is not None:
        gold_sec = extract(gold, corridor, rules)
        (out / "section_gold.svg").write_text(draw(gold_sec, rules, None, "人工方案断面（调整后模型）"), encoding="utf-8")
        review = review_human(gold, gold_sec, rules, evaluation, layouts, GridLocator(gold.grids))
        evaluation["review"] = review
    write_json(out / "evaluation.json", evaluation)
    (out / "index.html").write_text(_page(sec, rules, layouts, evaluation, gold is not None, package, out, review),
                                    encoding="utf-8")
    print(f"完成，{time.perf_counter() - t0:.0f} s：{out / 'index.html'}")
    return 0


def _region(c: Corridor, margin: float = 4000.0):
    a = c.point(c.s0 - margin, c.v0 - margin, 0)
    b = c.point(c.s1 + margin, c.v1 + margin, 0)
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]), max(a[1], b[1]))


def _page(sec: Section, rules: RuleSet, layouts: dict[str, Layout], ev: dict, has_gold: bool, package: Package,
          out: Path, review: dict | None = None) -> str:
    c = sec.corridor
    rows = []
    order = ["original"] + [k for k in SCHEME_ORDER if k in ev] + (["gold"] if has_gold else [])
    for key in order:
        r = ev[key]
        lay = layouts.get(key)
        m = lay.metrics if lay else {}
        rows.append("<tr>" + "".join(f"<td>{x}</td>" for x in [
            f"<b>{escape(r['name'])}</b>",
            r["hard"], (f"{r['hard'] - r['hard_node']} / {r['hard_node']}" if key in layouts else "—"),
            r["hard_structure"], r["clearance"],
            _m(r.get("lowest_mm")), _m(r.get("median_lowest_mm")), r.get("median_levels", "—"),
            m.get("layers", "—"), m.get("moved", "—"),
            f"{m['support_length_mm'] / 1000:.1f} m" if m else "—",
            m.get("tray_below_water", "—"),
        ]) + "</tr>")
    table = ("<table><tr><th>方案</th><th>硬碰撞</th><th>排布 / 待节点</th><th>其中与梁柱</th><th>净距不足</th><th>最低管底</th>"
             "<th>管底中位</th><th>断面层数中位</th><th>排布层数</th><th>移动管线</th><th>横担总长</th><th>电在水下</th></tr>"
             + "".join(rows) + "</table>")
    sections = [("原模型", "section_original.svg", None)]
    sections += [(SCHEMES[k], f"section_{k}.svg", k) for k in SCHEME_ORDER if k in layouts and layouts[k].placements]
    if has_gold:
        sections.append(("人工方案（调整后模型）", "section_gold.svg", None))
    figs = []
    for name, svg, key in sections:
        link = f' · <a href="view_{key}.html">三维预览</a> · <a href="plan_{key}.json">方案文件</a>' if key else ""
        # Inline the drawing so that the page is a single self-contained file.
        figs.append(f"<h3>{escape(name)}{link}</h3><div class='fig'>{(out / svg).read_text(encoding='utf-8')}</div>")
    strands = "".join(
        f"<tr><td>{s.id}</td><td>{escape(s.label())}</td><td>{escape(rules.system_classes[s.cls].label)}</td>"
        f"<td>{s.width:.0f}×{s.height:.0f}</td><td>{(s.v - c.v0) / 1000:.2f}</td><td>{(s.z - sec.floor_z) / 1000:.2f}</td>"
        + "".join(f"<td>{_pos(layouts[k], s, sec)}</td>" for k in SCHEME_ORDER if k in layouts)
        + f"<td>{(s.s_hi - s.s_lo) / 1000:.1f}</td><td>{'' if s.movable else escape(s.reason)}</td></tr>"
        for s in sec.strands)
    heads = "".join(f"<th>{SCHEMES[k]}</th>" for k in SCHEME_ORDER if k in layouts)
    notes = "".join(f"<li>{escape(n)}</li>" for n in sec.notes)
    diag = "".join(f"<li>{escape(SCHEMES[k])}：{escape(d)}</li>" for k, l in layouts.items() for d in l.diagnosis)
    lay = rules.layout
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>走廊 {escape(c.name)} 分层排布</title>
<style>
body{{font:14px/1.6 system-ui,"Microsoft YaHei",sans-serif;margin:0;background:#f6f7f9;color:#1f2937}}
main{{max-width:1040px;margin:0 auto;padding:24px 16px}} h1{{font-size:20px}} h2{{font-size:16px;margin-top:28px}}
h3{{font-size:14px;margin:18px 0 6px}} table{{border-collapse:collapse;width:100%;background:#fff;font-size:13px}}
th,td{{border:1px solid #e5e7eb;padding:4px 6px;text-align:left}} th{{background:#f3f4f6}}
.fig svg{{max-width:100%;height:auto;background:#fff;border:1px solid #e5e7eb}} .sub{{color:#6b7280}} .wrap{{overflow-x:auto}}
</style></head><body><main>
<h1>走廊 {escape(c.name)} 分层排布（R1 v0）</h1>
<p class="sub">{escape(package.manifest.get('project_name', ''))} · 沿 {c.axis.upper()} {c.length / 1000:.1f} m ·
断面宽 {(sec.v_hi - sec.v_lo) / 1000:.2f} m · 楼面 {escape(sec.floor_name)} · 横跨梁底 +{(sec.ceiling - sec.floor_z) / 1000:.2f}～+{(sec.ceiling_max - sec.floor_z) / 1000:.2f} m ·
管线 {len(sec.strands)} 条（可移动 {len(sec.movable)}）· 横穿 {len(sec.crossings)} 根 · 规则 {escape(rules.name)} {escape(rules.version)}</p>
<h2>方案对比</h2><div class="wrap">{table}</div>
<p class="sub">“排布 / 待节点”：自动方案只做平移，不建模翻弯；管线三通、弯头向侧面引出跨过相邻管线、大尺寸横穿管、
横穿管与其自身管件的冲突属于节点工作（N1 交叉翻弯、N2 侧向引出），单独计数。碰撞与净距只统计走廊范围内；最低管底、层数为沿走廊每 1 m 断面采样。横穿管线抬到其经过的管线上方；
它们在走廊外的翻弯、以及管线拐出走廊处的冲突属于节点问题（N1、N2），在节点库中处理。
约束：同层外底平齐；层间净空 ≥ {lay.layer_gap_min_mm:.0f} mm 且满足分组净距；顶部距梁底 {lay.beam_clearance_mm:.0f} mm
各梁只限制其下方（横向与沿走廊均重叠）的管线；{len(sec.zone_crossings)} 根横穿管线从管线束上方通过（其上方的顺走廊梁限制管线束高度）；
最下层底 ≥ 楼面 + {rules.headroom.min_clear_mm:.0f} + 横担 {lay.support_reserve_mm:.0f} mm。</p>
{f"<ul>{notes}{diag}</ul>" if notes or diag else ""}
{_review_html(review) if review else ""}
<h2>断面</h2>{''.join(figs)}
<h2>管线明细</h2><div class="wrap"><table><tr><th>编号</th><th>管线</th><th>系统</th><th>宽×高 mm</th><th>原 v (m)</th>
<th>原底 (m)</th>{heads}<th>长度 m</th><th>备注</th></tr>{strands}</table></div>
<p class="sub">方案列：层号 / 横向位置 v / 底标高（均相对走廊边界和楼面，m）。</p>
</main></body></html>"""


def _review_html(r: dict) -> str:
    def rows(items, cols):
        if not items:
            return "<p class='sub'>无。</p>"
        head = "".join(f"<th>{escape(c[0])}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td>{escape(str(it.get(c[1], '')))}</td>" for c in cols) + "</tr>" for it in items)
        return f"<div class='wrap'><table><tr>{head}</tr>{body}</table></div>"
    cols = [("位置", "where"), ("构件 A", "a"), ("构件 B", "b"), ("距离 mm", "distance_mm"), ("要求 mm", "required_mm")]
    findings = "".join(f"<li>{escape(f)}</li>" for f in r["findings"]) or "<li>未发现可改进项。</li>"
    return f"""<h2>人工方案复核</h2>
<p class="sub">人工方案不作为标准答案：硬规则以规范、图集和已确认的项目规则为准。以下用同一套规则检查人工方案，并与自动方案比较。</p>
<ul>{findings}</ul>
<h3>硬碰撞（{len(r['hard'])}）</h3>{rows(r['hard'], cols)}
<h3>净距不足（{r['clearance_total']}，列出相差最大的 {len(r['clearance'])} 条）</h3>{rows(r['clearance'], cols)}
<h3>桥架位于水管下方（{len(r['tray_under_water'])}）</h3>{rows(r['tray_under_water'], [("位置", "where"), ("桥架", "tray"), ("水管", "water")])}"""


def _pos(layout: Layout, s, sec: Section) -> str:
    p = layout.placements.get(s.id)
    if p is None:
        return "原位"
    return f"L{p.layer + 1} / {(p.v - sec.corridor.v0) / 1000:.2f} / {(p.z - sec.floor_z) / 1000:.2f}"


def _m(v) -> str:
    return "—" if v is None else f"{v / 1000:.2f} m"
