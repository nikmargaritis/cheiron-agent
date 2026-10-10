"""Deterministic aggregation: studies/counts → chart-neutral buckets with provenance.

Output types here are deliberately chart-agnostic. ``analysis.specs`` decides how a
bucket list becomes a bar chart vs. a time series vs. a grouped bar chart.
"""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import combinations, product
from typing import Any

from trialviz.ctgov.fields import (
    DIMENSIONS,
    NUMERIC_FIELDS,
    FieldValue,
    is_network_noise,
    nct_id,
)
from trialviz.schemas.common import Dimension, NumericField

Study = dict[str, Any]
Evidence = list[tuple[Study, FieldValue]]


@dataclass
class Bucket:
    """One category (optionally within one series) and the trials behind it."""

    key: str  # normalized bucket value, e.g. "PHASE3", "2019", "united states"
    label: str
    count: int  # exact server count when available, else number of trials in evidence
    series: str | None = None
    evidence: Evidence = field(default_factory=list)  # the citation pool for this bucket


@dataclass
class Bin:
    start: float
    end: float
    count: int
    evidence: Evidence = field(default_factory=list)
    overflow: bool = False  # the last, open-ended bin


@dataclass
class NumericPoint:
    study: Study
    x: float
    x_fv: FieldValue
    y: float | None = None
    y_fv: FieldValue | None = None


@dataclass
class GraphNode:
    id: str  # namespaced: "<dimension>:<key>"
    label: str
    group: Dimension
    evidence: Evidence


@dataclass
class GraphEdge:
    source: str
    target: str
    evidence: list[tuple[Study, FieldValue, FieldValue]]  # one entry per co-occurring trial


@dataclass
class Graph:
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    candidate_nodes: int  # before pruning, for notes


def _label_for(dim: Dimension, key: str, excerpts: Counter[str]) -> str:
    spec = DIMENSIONS[dim]
    if key in spec.labels:
        return spec.labels[key]
    if excerpts:  # most common verbatim spelling; ties broken alphabetically
        return min(excerpts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
    return key


def count_by_dimension(
    studies: list[Study], dim: Dimension, series: str | None = None
) -> list[Bucket]:
    """Group studies by ``dim``. Multi-valued fields count a trial once per value."""
    extract = DIMENSIONS[dim].extract
    evidence: dict[str, Evidence] = defaultdict(list)
    spellings: dict[str, Counter[str]] = defaultdict(Counter)
    for s in studies:
        for fv in extract(s):
            evidence[fv.value].append((s, fv))
            spellings[fv.value][fv.excerpt] += 1
    return [
        Bucket(key, _label_for(dim, key, spellings[key]), len(ev), series, ev)
        for key, ev in evidence.items()
    ]


def apply_exact_counts(
    buckets: list[Bucket], exact: dict[str, int], dim: Dimension, series: str | None = None
) -> list[Bucket]:
    """Replace sample counts with exact server-side counts.

    The exact bucket set is authoritative: every exact bucket appears (even with no
    downloaded evidence), and evidence for values outside it is discarded.
    """
    by_key = {b.key: b for b in buckets}
    out = []
    for key, n in exact.items():
        b = by_key.get(key) or Bucket(key, _label_for(dim, key, Counter()), 0, series)
        out.append(Bucket(b.key, b.label, n, series, b.evidence))
    return out


def order_buckets(buckets: list[Bucket], dim: Dimension) -> list[Bucket]:
    """Phase/enum order for ordinal dims, chronological for years, else count desc."""
    spec = DIMENSIONS[dim]
    if dim == Dimension.START_YEAR:
        return sorted(buckets, key=lambda b: int(b.key))
    if spec.field_type == "ordinal" and spec.enum_values:
        rank = {v: i for i, v in enumerate(spec.enum_values)}
        return sorted(buckets, key=lambda b: rank.get(b.key, len(rank)))
    return sorted(buckets, key=lambda b: (-b.count, b.label.casefold()))


def top_keys(buckets: list[Bucket], n: int) -> list[str]:
    """The N keys with the largest count, summed across series (stable tie-break)."""
    totals: Counter[str] = Counter()
    labels: dict[str, str] = {}
    for b in buckets:
        totals[b.key] += b.count
        labels[b.key] = b.label
    ranked = sorted(totals, key=lambda k: (-totals[k], labels[k].casefold()))
    return ranked[:n]


def numeric_values(
    studies: list[Study], x: NumericField, y: NumericField | None = None
) -> tuple[list[NumericPoint], int]:
    """Per-study numeric points; returns (points, number of studies skipped as missing)."""
    fx = NUMERIC_FIELDS[x].extract
    fy = NUMERIC_FIELDS[y].extract if y else None
    points: list[NumericPoint] = []
    for s in studies:
        xv = fx(s)
        yv = fy(s) if fy else None
        if xv is None or (fy and yv is None):
            continue
        points.append(NumericPoint(s, xv[0], xv[1], *(yv if yv else (None, None))))
    return points, len(studies) - len(points)


def _nice_step(span: float, target_bins: int) -> float:
    raw = span / max(target_bins, 1)
    if raw <= 0:
        return 1.0
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


def histogram_bins(
    points: list[NumericPoint], max_bins: int = 20, overflow_quantile: float = 0.95
) -> list[Bin]:
    """Equal-width integer bins with "nice" edges over [min, p95]; values above p95 go into
    one overflow bin so heavy right tails (enrollment) don't squash the chart.
    """
    if not points:
        return []
    values = sorted(p.x for p in points)
    lo, hi = values[0], values[-1]
    cap = values[min(len(values) - 1, int(overflow_quantile * len(values)))]
    use_overflow = cap < hi and len(values) >= 20
    top = cap if use_overflow else hi

    step = _nice_step(top - lo, max_bins - (1 if use_overflow else 0))
    step = max(1.0, math.ceil(step))  # every numeric field is integer-valued
    start = math.floor(lo / step) * step
    n_bins = int((top - start) // step) + 1  # bins are [start, end); the last one holds `top`
    bins = [Bin(start + i * step, start + (i + 1) * step, 0) for i in range(n_bins)]
    edge = bins[-1].end
    overflow = Bin(edge, hi, 0, overflow=True) if use_overflow and hi >= edge else None
    for p in sorted(points, key=lambda p: nct_id(p.study)):
        if overflow and p.x >= edge:
            b = overflow
        else:
            b = bins[min(int((p.x - start) // step), len(bins) - 1)]
        b.count += 1
        b.evidence.append((p.study, p.x_fv))
    return bins + ([overflow] if overflow else [])


def cooccurrence_graph(
    studies: list[Study], source: Dimension, target: Dimension,
    min_edge_trials: int, max_nodes: int,
) -> Graph:
    """Bipartite (source != target) or co-occurrence (source == target) network.

    Node weight = trials mentioning the value; edge weight = trials mentioning both.
    Pruning: drop edges below ``min_edge_trials``, keep the ``max_nodes`` nodes with the
    highest weighted degree, then drop edges/nodes left dangling.
    """
    same = source == target
    node_ev: dict[tuple[Dimension, str], Evidence] = defaultdict(list)
    spellings: dict[tuple[Dimension, str], Counter[str]] = defaultdict(Counter)
    edge_ev: dict[tuple[tuple[Dimension, str], tuple[Dimension, str]], list] = defaultdict(list)

    def values(s: Study, dim: Dimension) -> list[FieldValue]:
        return [fv for fv in DIMENSIONS[dim].extract(s) if not is_network_noise(dim, fv.value)]

    for s in studies:
        src_vals = values(s, source)
        tgt_vals = src_vals if same else values(s, target)
        sides = [(source, src_vals)] if same else [(source, src_vals), (target, tgt_vals)]
        for dim, vals in sides:
            for fv in vals:
                node_ev[(dim, fv.value)].append((s, fv))
                spellings[(dim, fv.value)][fv.excerpt] += 1
        if same:
            pairs = combinations(sorted(src_vals, key=lambda fv: fv.value), 2)
        else:
            pairs = product(src_vals, tgt_vals)
        for a, b in pairs:
            edge_ev[((source, a.value), (target, b.value))].append((s, a, b))

    edges = {k: v for k, v in edge_ev.items() if len(v) >= min_edge_trials}
    degree: Counter[tuple[Dimension, str]] = Counter()
    for (a, b), ev in edges.items():
        degree[a] += len(ev)
        degree[b] += len(ev)
    keep = set(
        sorted(degree, key=lambda n: (-degree[n], -len(node_ev[n]), n[0].value, n[1]))[:max_nodes]
    )

    def node_id(n: tuple[Dimension, str]) -> str:
        return f"{n[0].value}:{n[1]}"

    kept_edges = [
        GraphEdge(node_id(a), node_id(b), ev)
        for (a, b), ev in sorted(edges.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        if a in keep and b in keep
    ]
    used = {e.source for e in kept_edges} | {e.target for e in kept_edges}
    nodes = [
        GraphNode(node_id(n), _label_for(n[0], n[1], spellings[n]), n[0], node_ev[n])
        for n in sorted(keep, key=lambda n: (-degree[n], n[0].value, n[1]))
        if node_id(n) in used
    ]
    return Graph(nodes, kept_edges, candidate_nodes=len(node_ev))
