"""Cross-section drawing (SVG) of a corridor, before and after a layout."""

from __future__ import annotations

from html import escape

from .rules import RuleSet
from .section import Section
from .solver import Layout
from .viewer import COLORS

W, H = 980, 430
MARGIN_L, MARGIN_R, MARGIN_T, MARGIN_B = 70, 30, 46, 42


def draw(sec: Section, rules: RuleSet, layout: Layout | None, title: str) -> str:
    lay = rules.layout
    z_lo = sec.floor_z + rules.headroom.min_clear_mm - 400
    z_hi = max(sec.ceiling_max, sec.ceiling) + 300
    v_lo, v_hi = sec.v_lo - 200, sec.v_hi + 200
    sx = (W - MARGIN_L - MARGIN_R) / (v_hi - v_lo)
    sz = (H - MARGIN_T - MARGIN_B) / (z_hi - z_lo)
    scale = min(sx, sz * 3)          # keep sections readable; vertical may be exaggerated up to 3×
    sx = scale
    sz = min(sz, scale * 3)

    def X(v):
        return MARGIN_L + (v - v_lo) * sx

    def Y(z):
        return H - MARGIN_B - (z - z_lo) * sz

    def rel(z):
        return f"+{(z - sec.floor_z) / 1000:.2f}"

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
           'font-family="system-ui, Microsoft YaHei, sans-serif" font-size="11">',
           '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
           '<line x1="0" y1="0" x2="0" y2="6" stroke="#9aa3ad" stroke-width="1"/></pattern></defs>',
           f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
           f'<text x="{MARGIN_L}" y="20" font-size="14" font-weight="600" fill="#1f2937">{escape(title)}</text>']
    c = sec.corridor
    out.append(f'<text x="{MARGIN_L}" y="36" fill="#6b7280">走廊 {escape(c.name)}（沿 {c.axis.upper()}，长 {c.length / 1000:.1f} m）'
               f' · 断面宽 {(sec.v_hi - sec.v_lo) / 1000:.2f} m · 竖向比例放大 {sz / sx:.1f}×</text>')

    # Limits: every beam over the corridor as its own silhouette (crossing beams light — they are
    # behind the section plane at intervals; beams along the corridor dark), clipped to the width.
    roof = z_hi - 30
    for b in sorted(sec.beams, key=lambda b: (not b.crossing, -b.bottom)):
        x0, x1 = X(max(b.v_lo, sec.v_lo)), X(min(b.v_hi, sec.v_hi))
        if x1 <= x0 or b.bottom >= roof:
            continue
        fill = "#e5e7eb" if b.crossing else "url(#hatch)"
        out.append(f'<rect x="{x0:.1f}" y="{Y(roof):.1f}" width="{x1 - x0:.1f}" height="{(roof - b.bottom) * sz:.1f}" '
                   f'fill="{fill}" stroke="#9ca3af" stroke-width="0.6" opacity="0.9"/>')
    out.append(f'<text x="{X(sec.v_hi) - 4:.1f}" y="{Y(roof) + 12:.1f}" text-anchor="end" fill="#6b7280">'
               '浅灰：横跨梁（各梁只限制其下方的管线）；斜线：顺走廊梁</text>')
    out.append(_hline(X(sec.v_lo), X(sec.v_hi), Y(sec.ceiling), "#dc2626", f"最低横跨梁底 {rel(sec.ceiling)}", dash="6 3"))
    min_bottom = sec.floor_z + rules.headroom.min_clear_mm + lay.support_reserve_mm
    out.append(_hline(X(sec.v_lo), X(sec.v_hi), Y(min_bottom), "#ea580c",
                      f"净高控制 {rel(min_bottom)}（{rules.headroom.min_clear_mm / 1000:.1f} m + 横担 {lay.support_reserve_mm:.0f}）", dash="4 3"))
    for v in (sec.v_lo, sec.v_hi):
        out.append(f'<line x1="{X(v):.1f}" y1="{Y(z_lo):.1f}" x2="{X(v):.1f}" y2="{Y(roof):.1f}" stroke="#6b7280" stroke-width="1.5"/>')
    if sec.zone_crossings:
        out.append(f'<text x="{X(sec.v_lo) + 4:.1f}" y="{Y(z_lo) - 6:.1f}" fill="#92400e">'
                   f'{len(sec.zone_crossings)} 根横穿管线从管线束上方通过（≤ {sec.crossing_limit:.0f} mm）'
                   + (f'，{len(sec.large_crossings)} 根大尺寸横穿按节点处理' if sec.large_crossings else '') + '</text>')

    # Strands.
    placements = layout.placements if layout else {}
    for st in sec.strands:
        colour = COLORS.get(st.cls, "#9ca3af")
        if layout:
            out.append(_shape(st, X, Y, sx, sz, st.v, st.z, "none", "#9ca3af", dash="3 2"))
        p = placements.get(st.id)
        v, z = (p.v, p.z) if p else (st.v, st.z)
        out.append(_shape(st, X, Y, sx, sz, v, z, colour, "#111827" if st.movable else "#6b7280", opacity=0.9))
        cx, top = X(v), Y(z + st.height)
        out.append(f'<text transform="translate({cx + 3:.1f},{top - 3:.1f}) rotate(-90)" font-size="9.5" fill="#111827">'
                   f'{escape(st.label())}</text>')

    # Supports (one per layer) and layer labels.
    if layout:
        for k, l in enumerate(layout.layers, 1):
            y = Y(l["bottom"]) + 3
            out.append(f'<line x1="{X(l["left"] - 50):.1f}" y1="{y:.1f}" x2="{X(l["right"] + 50):.1f}" y2="{y:.1f}" '
                       'stroke="#374151" stroke-width="3"/>')
            out.append(f'<text x="{X(l["left"] - 60):.1f}" y="{y + 4:.1f}" text-anchor="end" fill="#374151">'
                       f'第{k}层 底 {rel(l["bottom"])}</text>')

    # Axis.
    out.append(f'<line x1="{MARGIN_L}" y1="{H - MARGIN_B}" x2="{W - MARGIN_R}" y2="{H - MARGIN_B}" stroke="#d1d5db"/>')
    step = 500 if (v_hi - v_lo) < 8000 else 1000
    v = (int(v_lo // step) + 1) * step
    while v < v_hi:
        out.append(f'<text x="{X(v):.1f}" y="{H - MARGIN_B + 14}" text-anchor="middle" fill="#9ca3af" font-size="9">'
                   f'{(v - c.v0) / 1000:.1f}</text>')
        v += step
    out.append(f'<text x="{W - MARGIN_R}" y="{H - 8}" text-anchor="end" fill="#9ca3af" font-size="9">'
               '横向坐标：相对走廊边界（m）；虚线框为原位置</text>')
    out.append("</svg>")
    return "\n".join(out)


def _shape(st, X, Y, sx, sz, v, z, fill, stroke, dash=None, opacity=1.0) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    if st.kind in ("pipe", "conduit", "flex_pipe") or (st.domain == "duct" and abs(st.width - st.height) < 1 and "Ø" in st.size):
        rx, ry = st.width / 2 * sx, st.height / 2 * sz
        return (f'<ellipse cx="{X(v):.1f}" cy="{Y(z + st.height / 2):.1f}" rx="{max(rx, 1.2):.1f}" ry="{max(ry, 1.2):.1f}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="0.8" opacity="{opacity}"{d}/>')
    return (f'<rect x="{X(v - st.width / 2):.1f}" y="{Y(z + st.height):.1f}" width="{max(st.width * sx, 1.5):.1f}" '
            f'height="{max(st.height * sz, 1.5):.1f}" fill="{fill}" stroke="{stroke}" stroke-width="0.8" opacity="{opacity}"{d}/>')


def _hline(x0, x1, y, colour, label, dash=None) -> str:
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<line x1="{x0:.1f}" y1="{y:.1f}" x2="{x1:.1f}" y2="{y:.1f}" stroke="{colour}" stroke-width="1.2"{d}/>'
            f'<text x="{x0 + 4:.1f}" y="{y - 4:.1f}" fill="{colour}">{escape(label)}</text>')
