"""Markdown report and JSON outputs for the R0 check."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from . import __version__
from .detect import CLEARANCE, HARD, JOINT, OVERLAP, PENETRATION, Conflict, HeadroomItem, cluster_points
from .grids import GridLocator
from .health import Health, summarize_open_ends
from .package import Package
from .rules import RuleSet

LIST_LIMIT = 60


def _table(headers, rows) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(" --- " for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(c).replace("|", "/") for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _mm(v: float) -> str:
    text = f"{v:.0f}"
    return "0" if text == "-0" else text


def write_outputs(out_dir: Path, package: Package, rules: RuleSet, health: Health,
                  conflicts: list[Conflict], headroom: list[HeadroomItem]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    locator = GridLocator(package.grids)
    md = render(package, rules, health, conflicts, headroom, locator)
    (out_dir / "report.md").write_text(md, encoding="utf-8")

    def conflict_json(c: Conflict) -> dict:
        a, b = package.elements[c.a], package.elements[c.b]
        return {
            "type": c.type, "a": c.a, "b": c.b, "a_label": a.label(), "b_label": b.label(),
            "a_class": a.cls, "b_class": b.cls, "distance_mm": round(c.distance_mm, 1),
            "required_mm": c.required_mm, "exact": c.exact,
            "location_mm": [round(float(v), 1) for v in c.location], "grid": locator.describe(c.location),
        }

    (out_dir / "conflicts.json").write_text(
        json.dumps([conflict_json(c) for c in conflicts], ensure_ascii=False, indent=1), encoding="utf-8")
    (out_dir / "headroom.json").write_text(json.dumps([
        {"key": h.key, "label": package.elements[h.key].label(), "level": h.level,
         "clear_mm": round(h.clear_mm), "grid": locator.describe(h.location)}
        for h in headroom], ensure_ascii=False, indent=1), encoding="utf-8")
    return out_dir / "report.md"


def render(package: Package, rules: RuleSet, health: Health, conflicts: list[Conflict],
           headroom: list[HeadroomItem], locator: GridLocator) -> str:
    m = package.manifest
    classes = rules.system_classes
    out: list[str] = []
    w = out.append

    w(f"# 模型体检与检测报告：{m.get('project_name', '')}")
    w("")
    w(_table(["项", "值"], [
        ["视图", m.get("view_name", "")],
        ["导出", f"{m.get('exporter_version')} / schema {m.get('schema_version')} / {m.get('created_at_utc', '')}"],
        ["检测工具", f"lwp {__version__}"],
        ["规则集", f"{rules.name} {rules.version}"],
        ["范围", "剖面框" if (m.get('scope') or {}).get('section_box_active') else "整个视图"],
    ]))

    hard = [c for c in conflicts if c.type == HARD]
    clear = [c for c in conflicts if c.type == CLEARANCE]
    pen = [c for c in conflicts if c.type == PENETRATION]
    joints = [c for c in conflicts if c.type == JOINT]
    overlaps = [c for c in conflicts if c.type == OVERLAP]
    low = [h for h in headroom if h.clear_mm < rules.headroom.min_clear_mm]
    w("\n## 1. 摘要\n")
    w(_table(["项", "数量"], [
        ["机电构件", sum(v for k, v in health.counts.items() if not k.startswith("obstacle:"))],
        ["障碍物（梁、柱、墙、基础）", sum(v for k, v in health.counts.items() if k.startswith("obstacle:"))],
        ["硬碰撞（构件对 / 碰撞点）", f"{len(hard)} / {cluster_points(hard)}"],
        ["净距不足（构件对 / 位置）", f"{len(clear)} / {cluster_points(clear)}"],
        ["穿墙", len(pen)],
        ["未连接接驳（同系统，端口插入另一构件未连接）", len(joints)],
        ["同系统重叠（未连接）", len(overlaps)],
        [f"净高低于 {rules.headroom.min_clear_mm / 1000:.1f} m 的水平管段", len(low)],
        ["未识别系统的构件", sum(n for (cls, _), n in health.classes.items() if cls == "unknown")],
        ["管线内部未连接端口", len(health.open_ends)],
        ["疑似重复建模", len(health.duplicates)],
    ]))

    # ------------------------------------------------------------------ systems
    w("\n## 2. 系统识别\n")
    rows = []
    for (cls, domain), n in sorted(health.classes.items(), key=lambda x: -x[1]):
        c = classes[cls]
        rows.append([c.label, cls, domain, c.group, "有压" if c.pressure else "无压", n])
    w(_table(["系统", "代码", "专业", "净距分组", "压力", "构件数"], rows))
    if health.assumed_rules:
        w("\n**以下分类规则为推定，尚未确认：**\n")
        notes = {r.describe(): r.note or "" for r in rules.classification}
        w(_table(["规则", "说明", "构件数"], [[d, notes.get(d, ""), n] for d, n in health.assumed_rules]))
    if health.unmatched:
        w("\n**未识别的系统：**\n")
        w(_table(["专业", "系统类型", "缩写", "类型名", "构件数"],
                 [[d, s, a, t, n] for (d, s, a, t), n in health.unmatched.most_common(30)]))

    w("\n### 保温\n")
    rows = [[classes[cls].label, {"modeled": "模型", "default": "默认", "none": "无"}[src], mm, n]
            for (cls, src, mm), n in sorted(health.insulation.items(), key=lambda x: -x[1])]
    w(_table(["系统", "来源", "厚度 mm", "构件数"], rows))

    # ------------------------------------------------------------------ conflicts
    w("\n## 3. 碰撞与净距\n")
    w("距离为外轮廓（含保温）之间的净距；负值为重叠深度估算。"
      "“精确”列为否时涉及管件或梁柱，其外形按拟合包围盒计算。"
      "构件对按 Navisworks 口径逐对计数；碰撞点把 1 m 内的构件对合并为一处。只统计剖面框范围内。\n")
    pair_counts = Counter((c.type, *sorted((classes[package.elements[c.a].cls].label if package.elements[c.a].is_mep
                                            else package.elements[c.a].kind,
                                            classes[package.elements[c.b].cls].label if package.elements[c.b].is_mep
                                            else package.elements[c.b].kind))) for c in hard + clear)
    w(_table(["类型", "构件 A", "构件 B", "数量"],
             [[{"hard": "硬碰撞", "clearance": "净距不足"}[t], x, y, n] for (t, x, y), n in pair_counts.most_common(25)]))
    for title, items in (("硬碰撞", hard), ("净距不足", clear)):
        w(f"\n### {title}（{len(items)}，列出前 {min(len(items), LIST_LIMIT)} 条）\n")
        rows = []
        for i, c in enumerate(items[:LIST_LIMIT], 1):
            a, b = package.elements[c.a], package.elements[c.b]
            rows.append([i, locator.describe(c.location), a.label(), b.label(), _mm(c.distance_mm),
                         _mm(c.required_mm), "是" if c.exact else "否"])
        w(_table(["#", "位置", "构件 A", "构件 B", "距离", "要求", "精确"], rows) if rows else "无。")

    w(f"\n### 穿墙（{len(pen)}）\n")
    w("机电穿墙一般设套管，这里只列数量，明细见 conflicts.json。")

    # ------------------------------------------------------------------ headroom
    w(f"\n## 4. 净高\n")
    w(f"地面取主模型楼层标高，净高计至机电外轮廓（含保温）底；只统计水平管段。要求 ≥ {rules.headroom.min_clear_mm:.0f} mm"
      f"{'（推定，待确认）' if not rules.headroom.confirmed else ''}。\n")
    if headroom:
        w(f"最低净高：{headroom[0].clear_mm:.0f} mm（{locator.describe(headroom[0].location)}）。\n")
    rows = [[i, locator.describe(h.location), package.elements[h.key].label(), h.level, _mm(h.clear_mm)]
            for i, h in enumerate(low[:LIST_LIMIT], 1)]
    w(_table(["#", "位置", "构件", "楼层", "净高 mm"], rows) if rows else "无低于要求的管段。")

    # ------------------------------------------------------------------ model quality
    w("\n## 5. 模型质量\n")
    w("同一系统内的重叠不计入硬碰撞：端口插入另一构件而未连接的记为“未连接接驳”，其余记为“同系统重叠”，需在模型中修正。\n")
    for title, items in (("未连接接驳", joints), ("同系统重叠", overlaps)):
        rows = [[i, locator.describe(c.location), package.elements[c.a].label(), package.elements[c.b].label(), _mm(c.distance_mm)]
                for i, c in enumerate(items[:30], 1)]
        w(f"\n### {title}（{len(items)}，列出前 {min(len(items), 30)} 条）\n")
        w(_table(["#", "位置", "构件 A", "构件 B", "重叠"], rows) if rows else "无。")
    w("")
    oe = summarize_open_ends(health.open_ends, package)
    w(f"管线内部未连接的端口 {len(health.open_ends)} 处（另有 {health.open_ends_at_boundary} 处位于范围边界，属正常）。\n")
    if oe:
        w(_table(["构件类型", "系统", "数量"], [[k, classes[c].label, n] for (k, c), n in oe.most_common()]))
        rows = [[i, locator.describe(o.location), package.elements[o.key].label(), _mm(o.size_mm)]
                for i, o in enumerate(sorted(health.open_ends, key=lambda o: -o.size_mm)[:30], 1)]
        w("\n按尺寸从大到小列出前 30 处：\n")
        w(_table(["#", "位置", "构件", "尺寸 mm"], rows))
    w("")
    w(_table(["项", "数量"], [
        ["无有效截面尺寸的管线", len(health.missing_size)],
        ["长度小于 1 mm 的管线", len(health.zero_length)],
        ["疑似重复建模（端点、尺寸相同）", len(health.duplicates)],
    ]))
    if health.diagnostics:
        w("\n导出诊断：\n")
        w(_table(["字段", "信息", "次数"], [[f, msg, n] for (f, msg), n in health.diagnostics.most_common(15)]))
    if health.warnings:
        w("\n" + "\n".join(f"- {x}" for x in health.warnings))
    return "\n".join(out) + "\n"
