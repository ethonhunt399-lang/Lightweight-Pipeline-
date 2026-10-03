"""Layered cross-section solver (OR-Tools CP-SAT).

Every movable strand is assigned to a layer (layer 0 is the top one) and a position across the
corridor. Strands in one layer sit on the same support: their outer bottoms are flush. Layers are
stacked below the lowest crossing beam; the lowest layer, plus the support reserve, must stay above
the required clear height.

Hard constraints: clearances within a layer (group matrix), vertical gap between adjacent layers,
beams running along the corridor, fixed strands, available width, headroom.
Soft preferences: trays above water, ducts on top, one system per layer.

Three schemes, each solved lexicographically:
  headroom  — maximise the lowest bottom, then preferences, then fewest changes
  changes   — fewest changes, then preferences
  supports  — fewest layers, then shortest total support length, then preferences and changes
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from ortools.sat.python import cp_model

from .rules import RuleSet
from .section import RISER_REACH, Section, Strand, overlaps

SCHEMES = {
    "headroom": "净高最优",
    "changes": "改动最少",
    "supports": "支吊架最省",
}
SCHEME_ORDER = ["headroom", "changes", "supports"]


@dataclass
class Placement:
    strand: str
    layer: int
    v: float
    z: float


@dataclass
class Layout:
    scheme: str
    status: str                       # OPTIMAL / FEASIBLE / INFEASIBLE
    placements: dict[str, Placement]
    layers: list[dict]                # per used layer: bottom, top, height, left, right
    metrics: dict
    stages: list[dict] = field(default_factory=list)
    diagnosis: list[str] = field(default_factory=list)
    crossings: dict[str, float] = field(default_factory=dict)   # crossing service key → bottom (mm)


def _i(x: float) -> int:
    return int(round(x))


class _Model:
    """One CP-SAT model of the section; stages add bounds and objectives."""

    def __init__(self, sec: Section, rules: RuleSet, relax: set[str] | None = None):
        relax = relax or set()
        # "riser" relaxed: crossing services simply stay under the beams on their path.
        self.riser = "riser" not in relax
        self.sec, self.rules = sec, rules
        lay = rules.layout
        m = self.m = cp_model.CpModel()
        items = self.items = sec.movable
        n, K = len(items), lay.max_layers
        self.K = K
        clear = rules.clearance_mm
        floor = _i(sec.floor_z)
        # Global bound only; every beam and crossing service limits the strands under it (below).
        top_bound = sec.ceiling_max if math.isfinite(sec.ceiling_max) else sec.ceiling
        ceiling_top = _i(top_bound - lay.beam_clearance_mm)
        min_bottom = _i(sec.floor_z + rules.headroom.min_clear_mm + lay.support_reserve_mm)
        if "headroom" in relax:
            min_bottom = floor
        self.ceiling_top, self.min_bottom = ceiling_top, min_bottom
        v_lo, v_hi = _i(sec.v_lo), _i(sec.v_hi)
        if "width" in relax:
            v_lo, v_hi = v_lo - 3000, v_hi + 3000

        def clr(a: Strand, b: Strand) -> int:
            if "clearance" in relax:
                return _i(clear.default)
            return _i(clear.required(a.group, b.group)[0])

        # Layer variables.
        self.used = [m.NewBoolVar(f"used{k}") for k in range(K)]
        self.top = [m.NewIntVar(floor, ceiling_top, f"top{k}") for k in range(K)]
        self.bot = [m.NewIntVar(floor - 5000, ceiling_top, f"bot{k}") for k in range(K)]
        hmax = max((_i(s.height) for s in items), default=0)
        self.H = [m.NewIntVar(0, hmax, f"H{k}") for k in range(K)]
        self.gap = [m.NewIntVar(0, 2000, f"gap{k}") for k in range(K)]
        step = _i(lay.elevation_step_mm)
        for k in range(K):
            m.Add(self.bot[k] == self.top[k] - self.H[k])
            if step > 1:
                # Layer bottoms on the standard elevation grid (relative to the floor).
                n_k = m.NewIntVar(-5000 // step - 1, (ceiling_top - floor) // step + 1, f"grid{k}")
                m.Add(self.bot[k] == floor + step * n_k)
            m.Add(self.H[k] == 0).OnlyEnforceIf(self.used[k].Not())
            m.Add(self.bot[k] >= min_bottom).OnlyEnforceIf(self.used[k])
            m.Add(self.gap[k] >= _i(lay.layer_gap_min_mm))
            if k + 1 < K:
                m.AddImplication(self.used[k + 1], self.used[k])
                m.Add(self.top[k + 1] <= self.bot[k] - self.gap[k])

        # Headroom by zone (drive lane / parking stall, from the drawings).
        hr = rules.headroom
        self.zone_clear = {}
        if sec.bands and "headroom" not in relax:
            for zone, req in (("lane", hr.lane_clear_mm), ("stall", hr.stall_clear_mm)):
                if req is not None and req > hr.min_clear_mm:
                    self.zone_clear[zone] = _i(sec.floor_z + req + lay.support_reserve_mm)

        # Assignment.
        self.x = [[m.NewBoolVar(f"x{i}_{k}") for k in range(K)] for i in range(n)]
        self.layer = [m.NewIntVar(0, K - 1, f"layer{i}") for i in range(n)]
        self.v = [m.NewIntVar(v_lo + _i(s.width / 2), v_hi - _i(s.width / 2), f"v{i}") for i, s in enumerate(items)]
        self.z = [m.NewIntVar(floor - 5000, ceiling_top, f"z{i}") for i in range(n)]
        for k in range(K):
            # A used layer carries at least one strand (no empty supports).
            m.Add(sum(self.x[i][k] for i in range(n)) >= 1).OnlyEnforceIf(self.used[k])
        for i, s in enumerate(items):
            m.AddExactlyOne(self.x[i])
            m.Add(self.layer[i] == sum(k * self.x[i][k] for k in range(K)))
            for k in range(K):
                m.AddImplication(self.x[i][k], self.used[k])
                m.Add(self.H[k] >= _i(s.height)).OnlyEnforceIf(self.x[i][k])
                m.Add(self.z[i] == self.bot[k]).OnlyEnforceIf(self.x[i][k])

        # A strand over a band with a higher required clear height sits above it (or keeps out of the band).
        for bi, band in enumerate(sec.bands):
            req = self.zone_clear.get(band["zone"])
            if req is None:
                continue
            for i, s in enumerate(items):
                o = [m.NewBoolVar(f"zb{bi}_{i}_{x}") for x in range(3)]
                m.Add(self.v[i] + _i(s.width / 2) <= _i(band["v_lo"])).OnlyEnforceIf(o[0])
                m.Add(self.v[i] - _i(s.width / 2) >= _i(band["v_hi"])).OnlyEnforceIf(o[1])
                m.Add(self.z[i] >= req).OnlyEnforceIf(o[2])
                m.AddBoolOr(o)

        def along(a: Strand, b: Strand) -> bool:
            """Strands that share part of the corridor length interact; others never meet."""
            return overlaps(a.s_lo, a.s_hi, b.s_lo, b.s_hi)

        # Vertical clearance between adjacent layers, by group.
        for i, a in enumerate(items):
            for j, b in enumerate(items):
                c = clr(a, b)
                if i == j or c <= lay.layer_gap_min_mm or not along(a, b):
                    continue
                for k in range(K - 1):
                    m.Add(self.gap[k] >= c).OnlyEnforceIf([self.x[i][k], self.x[j][k + 1]])

        # Cable laying space above trays: to the global ceiling and to the layer above.
        if tray_top_extra := max(0.0, lay.tray_top_clearance_mm - lay.beam_clearance_mm):
            for i, s in enumerate(items):
                if s.domain == "tray":
                    m.Add(self.z[i] + _i(s.height) <= ceiling_top - _i(tray_top_extra))
        for i, a in enumerate(items):
            if a.domain != "tray" or lay.tray_top_clearance_mm <= lay.layer_gap_min_mm:
                continue
            for k in range(1, K):
                m.Add(self.gap[k - 1] >= _i(lay.tray_top_clearance_mm)).OnlyEnforceIf(self.x[i][k])

        # Horizontal clearance within a layer.
        self.same, self.left = {}, {}
        for i in range(n):
            for j in range(i + 1, n):
                a, b = items[i], items[j]
                if not along(a, b):
                    continue
                same = m.NewBoolVar(f"same{i}_{j}")
                m.Add(self.layer[i] == self.layer[j]).OnlyEnforceIf(same)
                m.Add(self.layer[i] != self.layer[j]).OnlyEnforceIf(same.Not())
                left = m.NewBoolVar(f"left{i}_{j}")
                d = _i((a.width + b.width) / 2) + clr(a, b)
                m.Add(self.v[j] - self.v[i] >= d).OnlyEnforceIf([same, left])
                m.Add(self.v[i] - self.v[j] >= d).OnlyEnforceIf([same, left.Not()])
                self.same[(i, j)] = same
                self.left[(i, j)] = left

        # Overhead limits, each acting only on strands under it (across and along the corridor):
        #   beams — clearance to the beam bottom;
        #   crossing services up to the zone limit — they pass above the bundle and under the beams
        #   that run along the corridor, so the bundle stays below that level minus the service.
        limits = []      # (v_lo, v_hi, s_lo, s_hi, highest allowed top of a strand)
        tray_top = lay.tray_top_clearance_mm
        cb = _i(clear.required("structure", "water")[0])
        if "beams" not in relax:
            for bm in sec.beams:
                limits.append((bm.v_lo, bm.v_hi, bm.s_lo, bm.s_hi, bm.bottom - cb))
        seen = set()
        self.end_viol = []
        for v0, v1, s0, s1, top_limit in limits:
            key = (_i(v0 / 10), _i(v1 / 10), _i(s0 / 200), _i(s1 / 200), _i(top_limit / 10))
            if key in seen:
                continue
            seen.add(key)
            for i, s in enumerate(items):
                if not overlaps(s.s_full_lo if s.s_full_lo is not None else s.s_lo,
                                s.s_full_hi if s.s_full_hi is not None else s.s_hi, s0, s1):
                    continue
                top = top_limit - (max(0.0, tray_top - cb) if s.domain == "tray" else 0.0)   # cable laying space
                if _i(top) >= ceiling_top:
                    continue          # never binding
                opts = [m.NewBoolVar(f"o{i}_{len(seen)}_{o}") for o in range(3)]
                m.Add(self.v[i] + _i(s.width / 2) + cb <= _i(v0)).OnlyEnforceIf(opts[0])
                m.Add(self.v[i] - _i(s.width / 2) - cb >= _i(v1)).OnlyEnforceIf(opts[1])
                m.Add(self.z[i] + _i(s.height) <= _i(top)).OnlyEnforceIf(opts[2])
                if s1 < sec.corridor.s0 or s0 > sec.corridor.s1:
                    # A beam beyond the corridor end: the run may instead make its transition before it (N3);
                    # a clash there is minimised first rather than forbidden.
                    viol = m.NewBoolVar(f"ev{i}_{len(seen)}")
                    m.AddBoolOr(opts + [viol])
                    self.end_viol.append(viol)
                    continue
                m.AddBoolOr(opts)

        # Crossing services in the zone above the bundle: each gets a height (bottom) of its own. A strand
        # under it stays below (or beside it); with "electrical above water" as a hard rule, a tray under a
        # water crossing goes above it instead, so water never runs over a tray.
        tw = lay.tray_water
        hard_tw = tw.crossing_over_tray == "forbid" and "tray_water" not in relax      # crossings too
        hard_par = tw.parallel_over_tray == "forbid" and "tray_water" not in relax     # running along
        self.hard_tw, self.hard_par = hard_tw, hard_par
        self.cz: dict[str, cp_model.IntVar] = {}
        self.cdz = []
        self.wot = []                    # water crossing over a tray (only counted with the hard rule)
        self.beam_viol = []              # crossing services through an oblique beam
        if lay.crossing_zone == "top" and "crossing" not in relax:
            zone = sorted(sec.zone_crossings, key=lambda c: c.s)
            index = {st.id: i for i, st in enumerate(items)}
            fixed_v = {f.id: f.v for f in sec.fixed}
            for ci, c in enumerate(zone):
                h = _i(c.height)
                hi = _i(c.ceiling - lay.beam_clearance_mm) - h      # in the beam bay: up to the slab
                # Crossing services only go up (lowering one would take clear height away).
                lo_c = _i(c.z_lo)
                zc = m.NewIntVar(lo_c, max(lo_c, hi), f"cz{ci}")
                self.cz[c.key] = zc
                s0, s1 = c.s - c.height / 2, c.s + c.height / 2
                for i, st in enumerate(items):
                    if not overlaps(st.s_lo, st.s_hi, s0, s1):
                        continue
                    cc = _i(clear.required(st.group, c.group)[0])
                    opts = [m.NewBoolVar(f"c{ci}_{i}_{o}") for o in range(3)]
                    if c.attached and c.ends:
                        # It branches from a strand that may move: it runs from its outer end to that strand.
                        ends = [self.v[index[sid]] if sid in index else _i(fixed_v.get(sid, v)) for v, sid in c.ends]
                        for e_ in ends:
                            m.Add(self.v[i] + _i(st.width / 2) + cc <= e_).OnlyEnforceIf(opts[0])
                            m.Add(self.v[i] - _i(st.width / 2) - cc >= e_).OnlyEnforceIf(opts[1])
                        if st.id in c.attached:
                            m.Add(opts[0] == 0)
                            m.Add(opts[1] == 0)
                    else:
                        m.Add(self.v[i] + _i(st.width / 2) + cc <= _i(c.v_lo)).OnlyEnforceIf(opts[0])
                        m.Add(self.v[i] - _i(st.width / 2) - cc >= _i(c.v_hi)).OnlyEnforceIf(opts[1])
                    if c.group == "water" and st.domain == "tray":
                        cc = max(cc, _i(lay.tray_water.crossing_above_mm))   # water crossing over a tray
                    m.Add(self.z[i] + _i(st.height) + cc <= zc).OnlyEnforceIf(opts[2])
                    if hard_tw and c.group == "water" and st.domain == "tray":
                        # The tray goes above the water crossing; where the height does not allow it, the
                        # crossing over the tray is a violation, minimised before anything else (a local
                        # node: the crossing dips under the tray or the tray rises into the beam bay).
                        over = m.NewBoolVar(f"c{ci}_{i}_up")
                        m.Add(self.z[i] >= zc + h + cc).OnlyEnforceIf(over)
                        m.AddBoolOr(opts + [over])
                        self.wot.append(opts[2])
                    else:
                        m.AddBoolOr(opts)
                for f in sec.fixed:
                    if overlaps(f.s_lo, f.s_hi, s0, s1) and overlaps(f.v - f.width / 2, f.v + f.width / 2, c.v_lo, c.v_hi):
                        m.Add(zc >= _i(f.top + clear.required(f.group, c.group)[0]))
                # Beams along the corridor on the service's path: raised above such a beam only if no strand
                # under the service lies within a riser's reach of it (room to come down before the beam).
                reach = _i(RISER_REACH + c.height)
                for bi, (bv0, bv1, bb) in enumerate(c.beams):
                    under_beam = _i(bb - lay.beam_clearance_mm) - h
                    if under_beam >= hi:
                        continue
                    if not self.riser:
                        m.Add(zc <= max(under_beam, lo_c))
                        continue
                    low = m.NewBoolVar(f"cb{ci}_{bi}")
                    m.Add(zc <= under_beam).OnlyEnforceIf(low)
                    for i, st in enumerate(items):
                        if not overlaps(st.s_lo, st.s_hi, s0, s1):
                            continue
                        l_, r_ = m.NewBoolVar(f"cbl{ci}_{bi}_{i}"), m.NewBoolVar(f"cbr{ci}_{bi}_{i}")
                        m.Add(self.v[i] + _i(st.width / 2) <= _i(bv0) - reach).OnlyEnforceIf(l_)
                        m.Add(self.v[i] - _i(st.width / 2) >= _i(bv1) + reach).OnlyEnforceIf(r_)
                        m.AddBoolOr([low, l_, r_])
                if np.isfinite(c.beam_cap) and "beams" not in relax:
                    # Under an oblique crossing beam: keep below it where the height allows (minimised first).
                    cap = _i(c.beam_cap - lay.beam_clearance_mm) - h
                    under = m.NewBoolVar(f"bc{ci}")
                    m.Add(zc <= cap).OnlyEnforceIf(under)
                    self.beam_viol.append(under.Not())
                dz = m.NewIntVar(0, 20000, f"cdz{ci}")
                m.AddAbsEquality(dz, zc - _i(c.z_lo))
                self.cdz.append(dz)
            # Crossing services next to each other (overlapping along and across) are stacked.
            # Extent along the corridor: the service's own (its width for a tray or duct, more when oblique).
            for a_i, a in enumerate(zone):
                for b in zone[a_i + 1:]:
                    if not (overlaps(*_s_ext(a), *_s_ext(b), clear.default) and overlaps(a.v_lo, a.v_hi, b.v_lo, b.v_hi)):
                        continue
                    cc = _i(clear.required(a.group, b.group)[0])
                    below = m.NewBoolVar(f"cs{a.key}_{b.key}")
                    m.Add(self.cz[a.key] + _i(a.height) + cc <= self.cz[b.key]).OnlyEnforceIf(below)
                    m.Add(self.cz[b.key] + _i(b.height) + cc <= self.cz[a.key]).OnlyEnforceIf(below.Not())
                    # A water crossing running along a tray crossing (side by side in plan) stays under it.
                    forced = False
                    if hard_par and min(a.v_hi, b.v_hi) - max(a.v_lo, b.v_lo) > tw.parallel_max_mm:
                        if a.group == "water" and b.domain == "tray":
                            m.Add(below == 1)
                            forced = True
                        elif b.group == "water" and a.domain == "tray":
                            m.Add(below == 0)
                            forced = True
                    if not forced:
                        # Otherwise they keep their order: swapping two services that overlap in plan would
                        # make one cut through the other where it rises (N1).
                        if a.z_hi <= b.z_lo + 1:
                            m.Add(below == 1)
                        elif b.z_hi <= a.z_lo + 1:
                            m.Add(below == 0)

        # Large crossing services (ducts, wide trays) stay where they are: strands pass under, over or beside
        # them. A clash that cannot be avoided counts (minimised first) and is left to the node (N1).
        self.large_viol = []
        self.lz: dict[str, cp_model.IntVar] = {}
        self.ldrop = []
        self.ldz = []
        ctrl = sec.floor_z + lay.duct_control_bottom_mm if lay.duct_control_bottom_mm is not None else None
        if "crossing" not in relax:
            for li, c in enumerate(sec.large_crossings):
                # A duct above the control level may drop to it (all such ducts at one level): the bundle
                # then passes over it.
                zlo = _i(c.z_lo)
                if ctrl is not None and c.domain == "duct" and c.z_lo > ctrl + 1:
                    drop = m.NewBoolVar(f"ldrop{li}")
                    zlo = m.NewIntVar(_i(ctrl), _i(c.z_lo), f"lz{li}")
                    m.Add(zlo == _i(ctrl)).OnlyEnforceIf(drop)
                    m.Add(zlo == _i(c.z_lo)).OnlyEnforceIf(drop.Not())
                    self.lz[c.key] = zlo
                    self.ldrop.append(drop)
                    self.ldz.append(_i(c.z_lo) - zlo)
                s0c, s1c = (c.s_lo, c.s_hi) if np.isfinite(c.s_lo) else (c.s - c.height / 2, c.s + c.height / 2)
                index_ = {st.id: i for i, st in enumerate(items)}
                for i, st in enumerate(items):
                    cc = _i(clear.required(st.group, c.group)[0])
                    if not overlaps(st.s_lo, st.s_hi, s0c, s1c, cc) or st.id in c.attached:
                        continue
                    o = [m.NewBoolVar(f"lg{li}_{i}_{x}") for x in range(5)]
                    if c.attached and c.ends:
                        # Branches from a strand: it reaches that strand wherever it goes.
                        for v_, sid in c.ends:
                            e_ = self.v[index_[sid]] if sid in index_ else _i(v_)
                            m.Add(self.v[i] + _i(st.width / 2) + cc <= e_).OnlyEnforceIf(o[0])
                            m.Add(self.v[i] - _i(st.width / 2) - cc >= e_).OnlyEnforceIf(o[1])
                    else:
                        m.Add(self.v[i] + _i(st.width / 2) + cc <= _i(c.v_lo)).OnlyEnforceIf(o[0])
                        m.Add(self.v[i] - _i(st.width / 2) - cc >= _i(c.v_hi)).OnlyEnforceIf(o[1])
                    m.Add(self.z[i] + _i(st.height) + cc <= zlo).OnlyEnforceIf(o[2])
                    m.Add(self.z[i] >= zlo + _i(c.height) + cc).OnlyEnforceIf(o[3])
                    m.AddBoolOr(o)
                    self.large_viol.append(o[4])
                # Crossing services next to it pass over or under it (or the clash counts, minimised first).
                for zc_ in sec.zone_crossings:
                    if zc_.key not in self.cz or not (overlaps(*_s_ext(zc_), s0c, s1c, clear.default)
                                                      and overlaps(zc_.v_lo, zc_.v_hi, c.v_lo, c.v_hi)):
                        continue
                    cc = _i(clear.required(zc_.group, c.group)[0])
                    o = [m.NewBoolVar(f"lz{li}_{zc_.key}_{x}") for x in range(3)]
                    m.Add(self.cz[zc_.key] + _i(zc_.height) + cc <= zlo).OnlyEnforceIf(o[0])
                    m.Add(self.cz[zc_.key] >= zlo + _i(c.height) + cc).OnlyEnforceIf(o[1])
                    m.AddBoolOr(o)
                    self.large_viol.append(o[2])

        # MEP that stays in place (offsets of other lines, equipment connections, risers): strands pass beside,
        # under or over it; a clash that cannot be avoided counts (minimised first).
        self.fixed_viol = []
        if "obstacles" not in relax:
            for oi, (ok, os0, os1, ov0, ov1, oz0, oz1, og) in enumerate(sec.obstacles):
                for i, st in enumerate(items):
                    cc = _i(clear.required(st.group, og)[0])
                    if not overlaps(st.s_full_lo if st.s_full_lo is not None else st.s_lo,
                                    st.s_full_hi if st.s_full_hi is not None else st.s_hi, os0, os1, cc):
                        continue
                    o = [m.NewBoolVar(f"fx{oi}_{i}_{x}") for x in range(5)]
                    m.Add(self.v[i] + _i(st.width / 2) + cc <= _i(ov0)).OnlyEnforceIf(o[0])
                    m.Add(self.v[i] - _i(st.width / 2) - cc >= _i(ov1)).OnlyEnforceIf(o[1])
                    m.Add(self.z[i] + _i(st.height) + cc <= _i(oz0)).OnlyEnforceIf(o[2])
                    m.Add(self.z[i] >= _i(oz1) + cc).OnlyEnforceIf(o[3])
                    m.AddBoolOr(o)
                    if os1 < sec.corridor.s0 or os0 > sec.corridor.s1:
                        self.end_viol.append(o[4])      # beyond the ends: avoided where headroom allows (N3)
                    else:
                        self.fixed_viol.append(o[4])

        # Constraints learned from the clashes of earlier rounds (iterate.py), all soft.
        self.cut_viol = []
        idx = {st.id: i for i, st in enumerate(items)}
        for ci_, cut in enumerate(sec.cuts):
            v_ = m.NewBoolVar(f"cutv{ci_}")
            if cut.kind == "keepout" and cut.a in idx:
                i, st = idx[cut.a], items[idx[cut.a]]
                s0c, s1c, v0c, v1c, z0c, z1c = cut.box
                g = _i(cut.gap)
                o = [m.NewBoolVar(f"cut{ci_}_{x}") for x in range(4)]
                m.Add(self.v[i] + _i(st.width / 2) + g <= _i(v0c)).OnlyEnforceIf(o[0])
                m.Add(self.v[i] - _i(st.width / 2) - g >= _i(v1c)).OnlyEnforceIf(o[1])
                m.Add(self.z[i] + _i(st.height) + g <= _i(z0c)).OnlyEnforceIf(o[2])
                m.Add(self.z[i] >= _i(z1c) + g).OnlyEnforceIf(o[3])
                m.AddBoolOr(o + [v_])
            elif cut.kind == "crossing_keepout" and cut.a in self.cz:
                c = next(x for x in sec.zone_crossings if x.key == cut.a)
                _, _, _, _, z0c, z1c = cut.box
                g = _i(cut.gap)
                lo_, hi_ = m.NewBoolVar(f"cut{ci_}_l"), m.NewBoolVar(f"cut{ci_}_h")
                m.Add(self.cz[cut.a] + _i(c.height) + g <= _i(z0c)).OnlyEnforceIf(lo_)
                m.Add(self.cz[cut.a] >= _i(z1c) + g).OnlyEnforceIf(hi_)
                m.AddBoolOr([lo_, hi_, v_])
            elif cut.kind == "apart" and cut.a in idx and cut.b in idx:
                i, j = idx[cut.a], idx[cut.b]
                o = [m.NewBoolVar(f"cut{ci_}_{x}") for x in range(4)]
                m.Add(self.v[i] - self.v[j] >= _i(cut.need)).OnlyEnforceIf(o[0])
                m.Add(self.v[j] - self.v[i] >= _i(cut.need)).OnlyEnforceIf(o[1])
                m.Add(self.z[i] - self.z[j] >= _i(cut.gap)).OnlyEnforceIf(o[2])
                m.Add(self.z[j] - self.z[i] >= _i(cut.gap)).OnlyEnforceIf(o[3])
                m.AddBoolOr(o + [v_])
            elif cut.kind == "crossing_above" and cut.a in self.cz and cut.b in idx:
                ok_ = m.NewBoolVar(f"cut{ci_}_o")
                m.Add(self.cz[cut.a] - self.z[idx[cut.b]] >= _i(cut.need)).OnlyEnforceIf(ok_)
                m.AddBoolOr([ok_, v_])
            elif cut.kind == "crossing_apart" and cut.a in self.cz and cut.b in self.cz:
                o = [m.NewBoolVar(f"cut{ci_}_{x}") for x in range(2)]
                m.Add(self.cz[cut.a] - self.cz[cut.b] >= _i(cut.need)).OnlyEnforceIf(o[0])
                m.Add(self.cz[cut.b] - self.cz[cut.a] >= _i(cut.need)).OnlyEnforceIf(o[1])
                m.AddBoolOr(o + [v_])
            else:
                continue
            self.cut_viol.append(v_)

        # Leak-prone items (flanges, valves, unions, air vents) not directly above a tray: the tray keeps
        # out from under them, or runs above them. Where impossible it counts, minimised first.
        self.leak_viol = []
        if tw.leak_prone_over_tray == "forbid" and "tray_water" not in relax:
            mg = _i(tw.leak_prone_margin_mm)
            for li, lk in enumerate(sec.leaks):
                for i, st in enumerate(items):
                    if st.domain != "tray" or not (st.s_lo - 1 <= lk.s <= st.s_hi + 1):
                        continue
                    o = [m.NewBoolVar(f"lk{li}_{i}_{x}") for x in range(4)]
                    m.Add(self.v[i] + _i(st.width / 2) + mg <= _i(lk.v_lo)).OnlyEnforceIf(o[0])
                    m.Add(self.v[i] - _i(st.width / 2) - mg >= _i(lk.v_hi)).OnlyEnforceIf(o[1])
                    cc = _i(clear.required(st.group, "water")[0])
                    if lk.crossing in self.cz:
                        c = next(x for x in sec.crossings if x.key == lk.crossing)
                        m.Add(self.z[i] >= self.cz[lk.crossing] + _i(lk.z_hi - c.z_lo) + cc).OnlyEnforceIf(o[2])
                    else:
                        m.Add(self.z[i] >= _i(lk.z_hi) + cc).OnlyEnforceIf(o[2])
                    m.AddBoolOr(o)
                    self.leak_viol.append(o[3])

        # Fixed strands stay; movable ones keep clear of them.
        for f in sec.fixed:
            for i, s in enumerate(items):
                if not along(s, f):
                    continue
                c = clr(s, f)
                opts = [m.NewBoolVar(f"f{f.id}_{i}_{o}") for o in range(4)]
                m.Add(self.v[i] + _i((s.width + f.width) / 2) + c <= _i(f.v)).OnlyEnforceIf(opts[0])
                m.Add(self.v[i] - _i((s.width + f.width) / 2) - c >= _i(f.v)).OnlyEnforceIf(opts[1])
                m.Add(self.z[i] >= _i(f.top) + c).OnlyEnforceIf(opts[2])
                m.Add(self.z[i] + _i(s.height) + c <= _i(f.z)).OnlyEnforceIf(opts[3])
                long_ = min(s.s_hi, f.s_hi) - max(s.s_lo, f.s_lo) > tw.parallel_max_mm
                if hard_par and long_ and s.group == "water" and f.domain == "tray":
                    m.Add(opts[2] == 0)               # no water running along over a tray
                if hard_par and long_ and s.domain == "tray" and f.group == "water":
                    m.Add(opts[3] == 0)
                m.AddBoolOr(opts)

        # Measures.
        big = ceiling_top - floor + 5000
        self.low = m.NewIntVar(floor - 5000, ceiling_top, "low")
        for k in range(K):
            m.Add(self.low <= self.bot[k] + big * (1 - self.used[k]))
        self.n_layers = sum(self.used)

        # Drive lanes first: the lowest bottom of the strands that lie over a drive lane (drawing zones).
        lanes = [b for b in sec.bands if b["zone"] == "lane"]
        self.lane_low = None
        self.on_lane = []
        if lanes and any(b["zone"] == "stall" for b in sec.bands):
            self.lane_low = m.NewIntVar(floor - 5000, ceiling_top, "lane_low")
            for i, s in enumerate(items):
                on = m.NewBoolVar(f"onlane{i}")
                for bi, b in enumerate(lanes):
                    left = m.NewBoolVar(f"ll{i}_{bi}")
                    right = m.NewBoolVar(f"lr{i}_{bi}")
                    m.Add(self.v[i] + _i(s.width / 2) <= _i(b["v_lo"])).OnlyEnforceIf(left)
                    m.Add(self.v[i] - _i(s.width / 2) >= _i(b["v_hi"])).OnlyEnforceIf(right)
                    m.AddBoolOr([left, right, on])
                m.Add(self.lane_low <= self.z[i] + big * (1 - on))
                self.on_lane.append(on)
            # Gain over lanes counts only up to a margin above the overall lowest bottom: lanes come first
            # among equally good plans, not at any price (no stacking everything off the lanes).
            self.lane_gain = m.NewIntVar(floor - 5000, ceiling_top, "lane_gain")
            m.Add(self.lane_gain <= self.lane_low)
            m.Add(self.lane_gain <= self.low + _i(lay.lane_priority_max_mm))

        # Lane target (net-height first): shortfall of the strands over drive lanes below the main lane level.
        self.lane_short = []
        if lay.lane_target_mm is not None and any(b["zone"] == "lane" for b in sec.bands):
            target = _i(sec.floor_z + lay.lane_target_mm)
            all_lane = all(b["zone"] == "lane" for b in sec.bands)
            for i, s in enumerate(items):
                sh = m.NewIntVar(0, 5000, f"lshort{i}")
                if all_lane:
                    m.Add(sh >= target - self.z[i])
                else:
                    m.Add(sh >= target - self.z[i]).OnlyEnforceIf(self.on_lane[i])
                self.lane_short.append(sh)

        self.dv, self.dz, self.moved = [], [], []
        thr = _i(lay.moved_threshold_mm)
        for i, s in enumerate(items):
            dv = m.NewIntVar(0, 20000, f"dv{i}")
            dz = m.NewIntVar(0, 20000, f"dz{i}")
            m.AddAbsEquality(dv, self.v[i] - _i(s.v))
            m.AddAbsEquality(dz, self.z[i] - _i(s.z))
            mv = m.NewBoolVar(f"moved{i}")
            m.Add(dv + dz <= thr).OnlyEnforceIf(mv.Not())
            self.dv.append(dv)
            self.dz.append(dz)
            self.moved.append(mv)
        # Lifting a crossing service counts as a change too.
        self.change = sum(self.dv) + sum(self.dz) + sum(self.cdz) + sum(self.ldz)
        self.n_moved = sum(self.moved)

        self.span = []
        for k in range(K):
            left = m.NewIntVar(v_lo, v_hi, f"L{k}")
            right = m.NewIntVar(v_lo, v_hi, f"R{k}")
            for i, s in enumerate(items):
                m.Add(left <= self.v[i] - _i(s.width / 2)).OnlyEnforceIf(self.x[i][k])
                m.Add(right >= self.v[i] + _i(s.width / 2)).OnlyEnforceIf(self.x[i][k])
            sp = m.NewIntVar(0, v_hi - v_lo, f"span{k}")
            m.Add(sp == right - left).OnlyEnforceIf(self.used[k])
            m.Add(sp == 0).OnlyEnforceIf(self.used[k].Not())
            self.span.append(sp)
        self.total_span = sum(self.span)

        # Preferences.
        w = lay.preferences
        terms = []
        trays = [i for i, s in enumerate(items) if s.domain == "tray"]
        water = [i for i, s in enumerate(items) if s.group == "water"]
        self.tray_viol = []
        for t in trays:
            for q in water:
                a, b = items[t], items[q]
                if hard_par and min(a.s_hi, b.s_hi) - max(a.s_lo, b.s_lo) > tw.parallel_max_mm:
                    # Water in a layer above a tray must not lie over it in plan.
                    above = m.NewBoolVar(f"wa{t}_{q}")
                    m.Add(self.layer[q] < self.layer[t]).OnlyEnforceIf(above)
                    m.Add(self.layer[q] >= self.layer[t]).OnlyEnforceIf(above.Not())
                    sl, sr = m.NewBoolVar(f"wl{t}_{q}"), m.NewBoolVar(f"wr{t}_{q}")
                    m.Add(self.v[q] + _i(b.width / 2) <= self.v[t] - _i(a.width / 2)).OnlyEnforceIf(sl)
                    m.Add(self.v[q] - _i(b.width / 2) >= self.v[t] + _i(a.width / 2)).OnlyEnforceIf(sr)
                    m.AddBoolOr([above.Not(), sl, sr])
                viol = m.NewIntVar(0, K, f"tv{t}_{q}")
                m.Add(viol >= self.layer[t] - self.layer[q])
                self.tray_viol.append(viol)
        terms.append(w.trays_above_water * sum(self.tray_viol))
        ducts = [i for i, s in enumerate(items) if s.domain == "duct"]
        terms.append(w.ducts_top * sum(self.layer[i] for i in ducts))
        # Trays preferably with their bottom at or above the preferred height (electrical design notes).
        self.tray_short = []
        pref_bottom = _i(sec.floor_z + lay.tray_bottom_preferred_mm)
        for t in trays:
            sh = m.NewIntVar(0, 1000, f"tb{t}")
            m.Add(10 * sh >= pref_bottom - self.z[t])
            self.tray_short.append(sh)
        terms.append(w.tray_bottom * sum(self.tray_short))
        # Beams just beyond the corridor ends: avoided where the headroom allows (else N3 / N1 work).
        terms.append(w.end_beams * sum(self.end_viol))
        systems: dict[str, list[int]] = {}
        for i, s in enumerate(items):
            systems.setdefault(s.system, []).append(i)
        self.split = []
        for name, members in systems.items():
            if len(members) < 2:
                continue
            ys = [m.NewBoolVar(f"sys{name}_{k}") for k in range(K)]
            for i in members:
                for k in range(K):
                    m.AddImplication(self.x[i][k], ys[k])
            self.split.append(sum(ys) - 1)
        terms.append(w.system_together * sum(self.split))
        # Exit side: a strand leaving to one side should not have others between it and that side.
        self.exit_viol = []
        side = {i: (next(iter(s.exits)) if len(s.exits) == 1 else None) for i, s in enumerate(items)}
        for (i, j), same in self.same.items():
            left = self.left[(i, j)]           # true: j is to the right of i
            for a, b, a_left_of_b in ((i, j, left), (j, i, left.Not())):
                if side[a] and side[a] != side[b]:
                    # a wants its side free: violated when b lies on a's exit side in the same layer.
                    bad = m.NewBoolVar(f"exit{a}_{b}")
                    blocking = a_left_of_b if side[a] == "left" else a_left_of_b.Not()
                    m.AddBoolAnd([same, blocking]).OnlyEnforceIf(bad)
                    m.AddBoolOr([same.Not(), blocking.Not(), bad])
                    self.exit_viol.append(bad)
        terms.append(w.exit_side * sum(self.exit_viol))
        # Strands with side exits on the top layer: their risers then cross nothing above them.
        terms.append(w.exit_top * sum(self.layer[i] for i, s in enumerate(items) if s.exits))
        self.pref = sum(terms)

    def solve(self, objective, maximize=False, hint=None, seconds=10.0, seed=7):
        if maximize:
            self.m.Maximize(objective)
        else:
            self.m.Minimize(objective)
        if hint:
            self.m.ClearHints()
            for var, val in hint:
                self.m.AddHint(var, val)
        solver = cp_model.CpSolver()
        # Deterministic time keeps runs reproducible (same input, seed and limits → same plan).
        solver.parameters.max_deterministic_time = seconds
        solver.parameters.random_seed = seed
        solver.parameters.num_workers = 8
        solver.parameters.interleave_search = True    # deterministic with several workers
        status = solver.Solve(self.m)
        return solver, solver.StatusName(status)

    def snapshot(self, solver) -> list[tuple]:
        vs = [*self.used, *self.top, *self.bot, *self.H, *self.gap, *self.layer, *self.v, *self.z, *self.cz.values()]
        vs += [b for row in self.x for b in row]
        return [(v, solver.Value(v)) for v in vs]


def _s_ext(c) -> tuple[float, float]:
    if np.isfinite(c.s_lo) and np.isfinite(c.s_hi):
        return c.s_lo, c.s_hi
    return c.s - c.height / 2, c.s + c.height / 2


def solve(sec: Section, rules: RuleSet, scheme: str, seconds: float = 2.0) -> Layout:
    if not sec.movable:
        return Layout(scheme, "EMPTY", {}, [], {}, diagnosis=["走廊内没有可移动的管线"])
    model = _Model(sec, rules)
    stages = []
    relaxed_riser = False

    def stage(name, objective, maximize=False, hint=None):
        solver, status = model.solve(objective, maximize, hint, seconds)
        ok = status in ("OPTIMAL", "FEASIBLE")
        value = solver.Value(objective) if ok else None
        stages.append({"stage": name, "status": status, "value": value, "time_s": round(solver.WallTime(), 2)})
        return solver, status, value

    if scheme == "headroom":
        plan = [("最低层底标高最高", model.low, True, 0), ("排布原则 + 改动", 1000 * model.pref + model.change, False, None)]
    elif scheme == "changes":
        plan = [("改动量最小", model.change + 200 * model.n_moved, False, 0.02), ("排布原则", model.pref, False, None)]
    elif scheme == "supports":
        plan = [("层数最少", model.n_layers, False, 0), ("横担总长最短", model.total_span, False, 0.02),
                ("排布原则 + 改动", 1000 * model.pref + model.change, False, None)]
    else:
        raise ValueError(f"unknown scheme {scheme!r}")
    if model.lane_low is not None:
        # Drive lanes before stalls: right after the scheme's own first goal, the clear height over lanes is
        # raised (by at most lane_priority_max_mm above the overall lowest bottom).
        lane = ("车道上方管底最高", model.lane_gain, True, 0)
        plan.insert({"headroom": 1, "changes": 1, "supports": 2}[scheme], lane)

    if model.lane_short:
        # Net height first: the lane target comes before everything else (clashes included).
        lane_first = ("车道主档净高（不足量最小）", sum(model.lane_short), False, 0)
    else:
        lane_first = None
    if model.wot or model.leak_viol or model.large_viol or model.fixed_viol or model.beam_viol or model.cut_viol:
        plan.insert(0, ("与固定构件、大尺寸横穿、斜梁碰撞，节点碰撞（迭代约束），水管跨越桥架，桥架上方易漏节点最少",
                        sum(model.wot) + sum(model.leak_viol) + sum(model.large_viol) + sum(model.fixed_viol)
                        + sum(model.beam_viol) + sum(model.cut_viol), False, 0))
    if lane_first is not None:
        plan.insert(0, lane_first)
    solver, status, hint = None, "UNKNOWN", None
    for name, objective, maximize, tol in plan:
        solver, status, value = stage(name, objective, maximize, hint)
        if status not in ("OPTIMAL", "FEASIBLE"):
            layout = Layout(scheme, status, {}, [], {}, stages)
            layout.diagnosis = diagnose(sec, rules)
            return layout
        hint = model.snapshot(solver)
        if tol is not None:
            # Keep the optimum of this stage (within tolerance) while optimising the next one.
            slack = int(abs(value) * tol) + (0 if tol == 0 else 10)
            if maximize:
                model.m.Add(objective >= value - slack)
            else:
                model.m.Add(objective <= value + slack)

    layout = _layout(model, solver, scheme, status, stages)
    layout.metrics["crossing_riser_relaxed"] = relaxed_riser
    return layout


def _layout(model: _Model, solver, scheme: str, status: str, stages: list[dict]) -> Layout:
    sec = model.sec
    placements = {}
    for i, s in enumerate(model.items):
        placements[s.id] = Placement(s.id, solver.Value(model.layer[i]), float(solver.Value(model.v[i])),
                                     float(solver.Value(model.z[i])))
    layers = []
    for k in range(model.K):
        if not solver.Value(model.used[k]):
            continue
        members = [s for s in model.items if placements[s.id].layer == k]
        layers.append({
            "index": k, "bottom": solver.Value(model.bot[k]), "top": solver.Value(model.top[k]),
            "height": solver.Value(model.H[k]),
            "left": min(placements[s.id].v - s.width / 2 for s in members),
            "right": max(placements[s.id].v + s.width / 2 for s in members),
            "strands": [s.id for s in members],
        })
    low = min(l["bottom"] for l in layers)
    metrics = {
        "layers": len(layers),
        "lowest_bottom_above_floor_mm": round(low - sec.floor_z),
        "headroom_under_supports_mm": round(low - model.rules.layout.support_reserve_mm - sec.floor_z),
        "support_length_mm": round(sum(l["right"] - l["left"] for l in layers)),
        "moved": sum(solver.Value(v) for v in model.moved),
        "sum_dz_mm": sum(solver.Value(v) for v in model.dz),
        "sum_dv_mm": sum(solver.Value(v) for v in model.dv),
        "tray_below_water": sum(solver.Value(v) for v in model.tray_viol),
        "trays_below_preferred": sum(solver.Value(v) > 0 for v in model.tray_short),
        "system_splits": sum(solver.Value(v) for v in model.split),
        "exit_side_violations": sum(solver.Value(v) for v in model.exit_viol),
        "crossings_over_bundle": len(sec.zone_crossings),
        "crossings_lifted": sum(solver.Value(v) > 20 for v in model.cdz),
        "lane_lowest_bottom_above_floor_mm": (round(solver.Value(model.lane_low) - sec.floor_z)
                                              if model.lane_low is not None else None),
        "strands_over_lanes": sum(solver.Value(v) for v in model.on_lane),
        "water_crossing_over_tray": sum(solver.Value(v) for v in model.wot),
        "leak_prone_over_tray": sum(solver.Value(v) for v in model.leak_viol),
        "beams_beyond_ends": sum(solver.Value(v) for v in model.end_viol),
        "large_crossing_clashes": sum(solver.Value(v) for v in model.large_viol),
        "fixed_obstacle_clashes": sum(solver.Value(v) for v in model.fixed_viol),
        "fixed_obstacles": len(model.sec.obstacles),
        "crossings_through_oblique_beams": sum(solver.Value(v) for v in model.beam_viol),
        "lane_target_shortfall_mm": sum(solver.Value(v) for v in model.lane_short),
        "lane_strands_below_target": sum(solver.Value(v) > 0 for v in model.lane_short),
        "ducts_dropped_to_control": sum(solver.Value(v) for v in model.ldrop),
        "learned_cuts": len(model.cut_viol),
        "learned_cuts_violated": sum(solver.Value(v) for v in model.cut_viol),
        "tray_water": {"parallel": "forbid" if model.hard_par else "soft",
                       "crossing": "forbid" if model.hard_tw else "allow"},
    }
    crossings = {k: float(solver.Value(v)) for k, v in model.cz.items()}
    crossings.update({k: float(solver.Value(v)) for k, v in model.lz.items()})
    return Layout(scheme, status, placements, layers, metrics, stages, crossings=crossings)


def diagnose(sec: Section, rules: RuleSet) -> list[str]:
    """Which constraint group, when relaxed, makes the section feasible."""
    labels = {
        "headroom": f"净高要求（楼面以上 {rules.headroom.min_clear_mm:.0f} mm + 横担 {rules.layout.support_reserve_mm:.0f} mm）",
        "clearance": "分组净距（改用默认净距）",
        "beams": "顺走廊方向的梁",
        "width": "可用宽度（两侧各放宽 3 m）",
        "crossing": "顶部横穿层",
        "tray_water": "电上水下（硬约束改为偏好）",
    }
    found = []
    for key in labels:
        model = _Model(sec, rules, relax={key})
        _, status = model.solve(model.low, maximize=True, seconds=5.0)
        if status in ("OPTIMAL", "FEASIBLE"):
            found.append(f"放宽“{labels[key]}”后有解")
    stack = sum(sorted((s.height for s in sec.movable), reverse=True)[:1]) if sec.movable else 0
    avail = sec.ceiling - rules.layout.beam_clearance_mm - (sec.floor_z + rules.headroom.min_clear_mm + rules.layout.support_reserve_mm)
    found.append(f"最低横跨梁下可用高度 {avail:.0f} mm（梁底 − 净距 − 净高要求 − 横担），最高单件 {stack:.0f} mm")
    return found or ["单独放宽任一组约束都无解，需要组合放宽"]
