"""Iterative solve: solve → build nodes → detect clashes → learn local constraints → solve again.

The section solver places strands and crossing services; the node library then builds the geometry
around them (risers, flips, transitions). Clashes found in that geometry are turned into local
constraints ("cuts") on the placement that produced them, and the section is solved again:

  strand  vs fixed / structure  → the strand keeps out of that element's box (beside, under or over)
  crossing vs fixed / structure → the crossing service keeps out of it vertically
  strand  vs strand (their nodes) → the two are kept further apart, across or in height
  crossing vs strand            → the crossing service passes higher above that strand
  crossing vs crossing          → the two services are stacked further apart

Cuts are soft: they are minimised first, together with the other clash counts, so an unsatisfiable cut
never makes the section infeasible. The iteration with the fewest new hard clashes is kept (then the higher
lowest bottom); it stops when two rounds bring no improvement or after `rounds` rounds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .detect import HARD
from .package import Package
from .plan import apply, corridor_conflicts
from .rules import RuleSet
from .section import Section
from .solver import Layout, solve


@dataclass
class Cut:
    kind: str                 # keepout / crossing_keepout / apart / crossing_above / crossing_apart
    a: str                    # strand id or crossing key
    b: str | None = None
    box: tuple | None = None  # (s0, s1, v0, v1, z0, z1)
    need: float = 0.0         # mm (apart / above)
    gap: float = 0.0          # clearance for keep-outs

    def key(self) -> tuple:
        return (self.kind, self.a, self.b, tuple(round(x / 100) for x in self.box) if self.box else None)


@dataclass
class Round:
    index: int
    new_hard: int
    lowest: float | None
    cuts: int

    def to_dict(self) -> dict:
        return {"round": self.index, "new_hard": self.new_hard, "lowest_bottom_mm": self.lowest, "cuts": self.cuts}


def _owners(sec: Section, nodes, links, graph) -> dict[str, tuple[str, str]]:
    """element key → ("S", strand id) or ("C", crossing key)."""
    own: dict[str, tuple[str, str]] = {}
    for st in sec.strands:
        for k in st.segments + st.fittings:
            own[k] = ("S", st.id)
    for c in sec.crossings:
        own[c.key] = ("C", c.key)
    for n in nodes:
        own[n.fitting] = ("S", n.strand)
        for k in n.branch:
            own.setdefault(k, ("S", n.strand))
    for e in getattr(links, "ends", []):
        for k in e.carried:
            own.setdefault(k, ("S", e.strand))
    for r in links.repairs:
        o = own.get(r.host) or own.get(r.other)
        if o:
            for p in r.pieces:
                own[p] = o
    return own


def _owner(k: str, own: dict, sec: Section, graph) -> tuple[str, str] | None:
    if k in own:
        return own[k]
    base = k.split("#")[0]
    if base in own:
        return own[base]
    # Fittings moved with a crossing service.
    for n in graph.get(base, ()):
        if n in own and own[n][0] == "C":
            return own[n]
    return None


def _box(e, cor) -> tuple:
    lo, hi = e.solid.aabb()
    s0, s1 = sorted((cor.s_of(lo), cor.s_of(hi)))
    v0, v1 = sorted((cor.v_of(lo), cor.v_of(hi)))
    return (s0, s1, v0, v1, float(lo[2]), float(hi[2]))


def learn(pkg: Package, sec: Section, layout: Layout, conflicts, own, graph, rules: RuleSet) -> list[Cut]:
    cor = sec.corridor
    cuts = []
    clr = rules.clearance_mm
    for c in conflicts:
        oa, ob = _owner(c.a, own, sec, graph), _owner(c.b, own, sec, graph)
        depth = max(0.0, -c.distance_mm) + 50.0
        ea, eb = pkg.elements[c.a], pkg.elements[c.b]
        if oa == ob:
            continue                                   # within one strand / one service
        for (o1, e1), (o2, e2) in (((oa, ea), (ob, eb)), ((ob, eb), (oa, ea))):
            if o1 is None:
                continue
            gap = clr.required(e1.group, e2.group)[0]
            if o2 is None:
                cuts.append(Cut("keepout" if o1[0] == "S" else "crossing_keepout", o1[1], box=_box(e2, cor), gap=gap))
            elif o1[0] == "S" and o2[0] == "S" and o1[1] < o2[1]:
                pa, pb = layout.placements.get(o1[1]), layout.placements.get(o2[1])
                if pa and pb:
                    cuts.append(Cut("apart", o1[1], o2[1], need=abs(pa.v - pb.v) + depth, gap=abs(pa.z - pb.z) + depth))
            elif o1[0] == "C" and o2[0] == "S":
                z_c = layout.crossings.get(o1[1])
                p = layout.placements.get(o2[1])
                if z_c is not None and p is not None:
                    cuts.append(Cut("crossing_above", o1[1], o2[1], need=(z_c - p.z) + depth))
            elif o1[0] == "C" and o2[0] == "C" and o1[1] < o2[1]:
                za, zb = layout.crossings.get(o1[1]), layout.crossings.get(o2[1])
                if za is not None and zb is not None:
                    cuts.append(Cut("crossing_apart", o1[1], o2[1], need=abs(za - zb) + depth))
    return cuts


def new_hard(before_hard: set, pkg: Package, rules: RuleSet, sec: Section):
    base = lambda k: k.split("#")[0]
    return [c for c in corridor_conflicts(pkg, rules, sec.corridor)
            if c.type == HARD and frozenset((base(c.a), base(c.b))) not in before_hard]


def solve_iterative(package: Package, sec: Section, rules: RuleSet, scheme: str, seconds: float = 2.0,
                    rounds: int = 4, log=print):
    """Returns (layout, (pkg, moves, nodes, links), history)."""
    from .detect import connection_graph
    graph = connection_graph(package)
    before_hard = {frozenset((c.a, c.b)) for c in corridor_conflicts(package, rules, sec.corridor) if c.type == HARD}
    cuts: dict[tuple, Cut] = {}
    history: list[Round] = []
    best = None
    stale = 0
    for i in range(rounds):
        sec.cuts = list(cuts.values())
        layout = solve(sec, rules, scheme, seconds=seconds)
        if not layout.placements:
            if best is None:
                return layout, None, history
            break
        result = apply(package, sec, layout, rules)
        pkg, moves, nodes, links = result
        hard = new_hard(before_hard, pkg, rules, sec)
        low = layout.metrics.get("lowest_bottom_above_floor_mm")
        history.append(Round(i + 1, len(hard), low, len(cuts)))
        log(f"    第 {i + 1} 轮：新增硬碰撞 {len(hard)}，最低管底 {low}，约束 {len(cuts)}")
        score = (len(hard), -(low or 0))
        if best is None or score < best[0]:
            best = (score, layout, result)
            stale = 0
        else:
            stale += 1
            if stale >= 2:
                break
        if not hard:
            break
        own = _owners(sec, nodes, links, graph)
        added = 0
        for c in learn(pkg, sec, layout, hard, own, graph, rules):
            if c.key() not in cuts:
                cuts[c.key()] = c
                added += 1
        if not added:
            break
    sec.cuts = []
    _, layout, result = best
    layout.metrics["iterations"] = [r.to_dict() for r in history]
    return layout, result, history
