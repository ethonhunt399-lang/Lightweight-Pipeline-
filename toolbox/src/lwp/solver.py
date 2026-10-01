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

from ortools.sat.python import cp_model

from .rules import RuleSet
from .section import Section, Strand, overlaps

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


def _i(x: float) -> int:
    return int(round(x))


class _Model:
    """One CP-SAT model of the section; stages add bounds and objectives."""

    def __init__(self, sec: Section, rules: RuleSet, relax: set[str] | None = None):
        relax = relax or set()
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
        for k in range(K):
            m.Add(self.bot[k] == self.top[k] - self.H[k])
            m.Add(self.H[k] == 0).OnlyEnforceIf(self.used[k].Not())
            m.Add(self.bot[k] >= min_bottom).OnlyEnforceIf(self.used[k])
            m.Add(self.gap[k] >= _i(lay.layer_gap_min_mm))
            if k + 1 < K:
                m.AddImplication(self.used[k + 1], self.used[k])
                m.Add(self.top[k + 1] <= self.bot[k] - self.gap[k])

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
        cb = _i(clear.required("structure", "water")[0])
        if "beams" not in relax:
            for bm in sec.beams:
                limits.append((bm.v_lo, bm.v_hi, bm.s_lo, bm.s_hi, bm.bottom - cb))
        if lay.crossing_zone == "top" and "crossing" not in relax:
            for c in sec.zone_crossings:
                top = c.ceiling - lay.beam_clearance_mm - c.height - clear.default
                limits.append((c.v_lo, c.v_hi, c.s - c.height / 2, c.s + c.height / 2, top))
        seen = set()
        for v0, v1, s0, s1, top in limits:
            key = (_i(v0 / 10), _i(v1 / 10), _i(s0 / 200), _i(s1 / 200), _i(top / 10))
            if key in seen:
                continue
            seen.add(key)
            for i, s in enumerate(items):
                if not overlaps(s.s_lo, s.s_hi, s0, s1):
                    continue
                if _i(top) >= ceiling_top:
                    continue          # never binding
                opts = [m.NewBoolVar(f"o{i}_{len(seen)}_{o}") for o in range(3)]
                m.Add(self.v[i] + _i(s.width / 2) + cb <= _i(v0)).OnlyEnforceIf(opts[0])
                m.Add(self.v[i] - _i(s.width / 2) - cb >= _i(v1)).OnlyEnforceIf(opts[1])
                m.Add(self.z[i] + _i(s.height) <= _i(top)).OnlyEnforceIf(opts[2])
                m.AddBoolOr(opts)

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
                m.AddBoolOr(opts)

        # Measures.
        big = ceiling_top - floor + 5000
        self.low = m.NewIntVar(floor - 5000, ceiling_top, "low")
        for k in range(K):
            m.Add(self.low <= self.bot[k] + big * (1 - self.used[k]))
        self.n_layers = sum(self.used)

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
        self.change = sum(self.dv) + sum(self.dz)
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
                viol = m.NewIntVar(0, K, f"tv{t}_{q}")
                m.Add(viol >= self.layer[t] - self.layer[q])
                self.tray_viol.append(viol)
        terms.append(w.trays_above_water * sum(self.tray_viol))
        ducts = [i for i, s in enumerate(items) if s.domain == "duct"]
        terms.append(w.ducts_top * sum(self.layer[i] for i in ducts))
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
        vs = [*self.used, *self.top, *self.bot, *self.H, *self.gap, *self.layer, *self.v, *self.z]
        vs += [b for row in self.x for b in row]
        return [(v, solver.Value(v)) for v in vs]


def solve(sec: Section, rules: RuleSet, scheme: str, seconds: float = 2.0) -> Layout:
    if not sec.movable:
        return Layout(scheme, "EMPTY", {}, [], {}, diagnosis=["走廊内没有可移动的管线"])
    model = _Model(sec, rules)
    stages = []

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

    return _layout(model, solver, scheme, status, stages)


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
        "system_splits": sum(solver.Value(v) for v in model.split),
        "exit_side_violations": sum(solver.Value(v) for v in model.exit_viol),
        "crossings_over_bundle": len(sec.zone_crossings),
    }
    return Layout(scheme, status, placements, layers, metrics, stages)


def diagnose(sec: Section, rules: RuleSet) -> list[str]:
    """Which constraint group, when relaxed, makes the section feasible."""
    labels = {
        "headroom": f"净高要求（楼面以上 {rules.headroom.min_clear_mm:.0f} mm + 横担 {rules.layout.support_reserve_mm:.0f} mm）",
        "clearance": "分组净距（改用默认净距）",
        "beams": "顺走廊方向的梁",
        "width": "可用宽度（两侧各放宽 3 m）",
        "crossing": "顶部横穿层",
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
