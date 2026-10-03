"""`lwp lanes`: clear height over the drive lanes of a whole basement, optionally against a reference model."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path

from . import headroom_map, lanes
from .grids import GridLocator
from .package import load_package
from .rules import load_rules


def _run(path: str, rules, plan: dict, cell: float):
    package = load_package(path, rules)
    floor = package.host_levels()
    floor_z = next((lv.elevation for lv in floor if lv.name.upper().startswith("B")), floor[0].elevation)
    hmap = headroom_map.build(package, floor_z, cell)
    result = lanes.lane_headroom(hmap, plan, GridLocator(package.grids))
    for r in result["lanes"]:
        k = r["lowest_key"]
        r["lowest_element"] = package.elements[k].label() if k in package.elements else None
    return package, hmap, result


def cmd_lanes(args) -> int:
    rules = load_rules(args.rules)
    plan = json.loads(Path(args.drawings).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runs = [(args.name, args.package)] + ([(args.compare_name, args.compare)] if args.compare else [])
    results, maps = {}, []
    for name, path in runs:
        package, hmap, result = _run(path, rules, plan, args.cell)
        maps.append(lanes.plan_svg(hmap, result, f"{name}：车道净高"))
        result.pop("_owner")
        result["package"] = str(path)
        results[name] = result
        print(f"{name}: 车道 {len(result['lanes'])} 条，面积 {result['lane_area_m2']} m²")
    (out / "lanes.json").write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    (out / "index.html").write_text(_page(results, maps, args.min_clear), encoding="utf-8")
    print(f"page: {out / 'index.html'}")
    return 0


def _m(x) -> str:
    return "—" if x is None else f"{x / 1000:.2f}"


def _page(results: dict, maps: list[str], min_clear: float) -> str:
    names = list(results)
    main = results[names[0]]
    ref = results[names[1]] if len(names) > 1 else None
    by_line = {r["flow_line"]: r for r in ref["lanes"]} if ref else {}
    head = "<tr><th>车道</th><th>位置</th><th>长 m</th><th>最低</th><th>10%</th><th>中位</th><th>分级（≥5%）</th><th>变化次数</th><th>&lt; 2.2 m 长度</th><th>控制构件</th>"
    if ref:
        head += f"<th>{escape(names[1])} 最低 / 中位</th><th>中位变化</th>"
    rows = []
    for r in main["lanes"]:
        cls = ' class="bad"' if r["min_mm"] < min_clear else ""
        row = (f"<tr{cls}><td>{r['id']}</td><td>{escape(r['where'])}</td><td>{r['length_m']}</td><td>{_m(r['min_mm'])}</td>"
               f"<td>{_m(r['p10_mm'])}</td><td>{_m(r['median_mm'])}</td>"
               f"<td>{' / '.join(_m(x) for x in r['levels_mm'])}</td><td>{r['changes']}</td><td>{r['below_min_m']}</td>"
               f"<td>{escape(r.get('lowest_element') or '')}</td>")
        if ref:
            o = by_line.get(r["flow_line"])
            row += (f"<td>{_m(o['min_mm'])} / {_m(o['median_mm'])}</td><td>{(r['median_mm'] - o['median_mm']) / 1000:+.2f}</td>"
                    if o else "<td>—</td><td>—</td>")
        rows.append(row + "</tr>")
    bands = "".join(f"<tr><td>{b['band']}</td>" + "".join(
        f"<td>{results[n]['bands'][i]['area_m2']}</td>" for n in names) + "</tr>" for i, b in enumerate(main["bands"]))
    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>车道净高</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{font:14px/1.5 system-ui,sans-serif;margin:16px;color:#222;background:#fff}}table{{border-collapse:collapse;margin:8px 0}}
td,th{{border:1px solid #ccc;padding:2px 6px;text-align:right}}td:nth-child(2),td:last-child{{text-align:left}}
tr.bad td{{background:#fde8e8}}.plan{{width:100%;max-width:900px;display:block;margin:8px 0}}.t{{font-size:14px;font-weight:600}}
.ln{{fill:none;stroke:#1565c0;stroke-width:1;stroke-dasharray:4 3}}.id{{font-size:9px;fill:#0d47a1;font-weight:600}}
.sw{{display:inline-block;width:12px;height:12px;margin:0 4px 0 12px;vertical-align:middle}}</style></head><body>
<h1>全地下室车道净高</h1>
<p>净高 = 地下一层楼面至上方最低的机电（含保温、管件、喷头）或结构底。车道 = 图纸车道流线两侧各 {main['half_width_mm'] / 1000:.2f} m、扣除车位；
沿车道每 {main['step_mm'] / 1000:.0f} m 取横向最低值。分级：50 mm 一档、占车道长度 ≥ 5 % 的净高；变化次数：100 mm 一档，短于 3 m 的段并入相邻段。</p>
<h2>车道面积按净高分档（m²）</h2><table><tr><th>净高 m</th>{''.join(f'<th>{escape(n)}</th>' for n in names)}</tr>{bands}</table>
<p>{lanes.legend_html()}</p>
{''.join(maps)}
<h2>各车道（{escape(names[0])}）</h2><table>{head}</tr>{''.join(rows)}</table>
</body></html>"""
