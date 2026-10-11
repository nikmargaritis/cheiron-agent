import pytest

from tests.conftest import make_study
from trialviz.analysis.aggregate import (
    MAX_PAIRED_VALUES,
    Bucket,
    apply_exact_counts,
    cooccurrence_graph,
    count_by_dimension,
    histogram_bins,
    numeric_values,
    order_buckets,
    top_keys,
)
from trialviz.ctgov.fields import normalize_intervention
from trialviz.schemas.common import Dimension, NumericField


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Pembrolizumab 50 MG Injection [Keytruda]", "pembrolizumab"),
        ("Gemcitabine Hydrochloride", "gemcitabine"),
        ("Leucovorin Calcium", "leucovorin"),
        ("Nab-paclitaxel", "nab paclitaxel"),
        ("Erlotinib 100 mg orally days 1-28", "erlotinib"),
        ("Paclitaxel 175 mg/m2 IV", "paclitaxel"),
        ("Sodium bicarbonate", "sodium bicarbonate"),  # leading salt word is the drug
        ("MK-3475", "mk-3475"),  # letter-digit hyphens are kept
        ("Placebo", "placebo"),
    ],
)
def test_normalize_intervention(raw, expected):
    assert normalize_intervention(raw) == expected


def test_count_by_dimension_multi_valued_and_labels():
    studies = [
        make_study(1, countries=["Germany", "Germany", "France"]),
        make_study(2, countries=["Germany"]),
        make_study(3),
    ]
    buckets = {b.key: b for b in count_by_dimension(studies, Dimension.COUNTRY)}
    assert {k: b.count for k, b in buckets.items()} == {"Germany": 2, "France": 1}


def test_count_by_dimension_uses_most_common_spelling():
    studies = [make_study(1, drugs=["Gemcitabine"]), make_study(2, drugs=["Gemcitabine"]),
               make_study(3, drugs=["gemcitabine hydrochloride"])]
    (b,) = count_by_dimension(studies, Dimension.DRUG)
    assert (b.key, b.label, b.count) == ("gemcitabine", "Gemcitabine", 3)


def test_drug_dimension_excludes_non_drug_interventions():
    s = make_study(1, drugs=["Temozolomide", ("MRI", "DIAGNOSTIC_TEST"), ("Surgery", "PROCEDURE"),
                             ("Nivolumab", "BIOLOGICAL")])
    assert {b.key for b in count_by_dimension([s], Dimension.DRUG)} == {"temozolomide", "nivolumab"}
    assert len(count_by_dimension([s], Dimension.INTERVENTION)) == 4


def test_apply_exact_counts_is_authoritative():
    sample = count_by_dimension([make_study(1, phases=["PHASE2"])], Dimension.PHASE)
    out = {b.key: b for b in apply_exact_counts(sample, {"PHASE2": 40, "PHASE3": 7},
                                                Dimension.PHASE)}
    assert out["PHASE2"].count == 40 and len(out["PHASE2"].evidence) == 1
    assert out["PHASE3"].count == 7 and out["PHASE3"].label == "Phase 3"
    assert out["PHASE3"].evidence == []


def test_order_buckets():
    phases = [Bucket("PHASE3", "Phase 3", 1), Bucket("EARLY_PHASE1", "Early Phase 1", 9)]
    assert [b.key for b in order_buckets(phases, Dimension.PHASE)] == ["EARLY_PHASE1", "PHASE3"]
    years = [Bucket("2021", "2021", 1), Bucket("2019", "2019", 5)]
    assert [b.key for b in order_buckets(years, Dimension.START_YEAR)] == ["2019", "2021"]
    nominal = [Bucket("b", "B", 1), Bucket("a", "A", 5), Bucket("c", "C", 5)]
    assert [b.key for b in order_buckets(nominal, Dimension.COUNTRY)] == ["a", "c", "b"]


def test_top_keys_sums_across_series():
    buckets = [Bucket("x", "X", 5, "s1"), Bucket("y", "Y", 4, "s1"), Bucket("y", "Y", 4, "s2")]
    assert top_keys(buckets, 1) == ["y"]


def test_histogram_bins_cover_every_point_once():
    studies = [make_study(i, enrollment=v) for i, v in enumerate([*range(0, 200, 5), 5000])]
    studies.append(make_study(999))  # missing value
    points, missing = numeric_values(studies, NumericField.ENROLLMENT)
    assert missing == 1
    bins = histogram_bins(points)
    assert sum(b.count for b in bins) == len(points)
    assert bins[-1].overflow and bins[-1].end == 5000
    for a, b in zip(bins, bins[1:], strict=False):
        assert a.end == b.start  # contiguous
    for b in bins:
        for _, fv in b.evidence:
            x = float(fv.value)
            assert b.start <= x and (x < b.end or (b.overflow and x <= b.end))


def test_histogram_small_sample_has_no_overflow():
    points, _ = numeric_values([make_study(i, enrollment=v) for i, v in enumerate([10, 20, 30])],
                               NumericField.ENROLLMENT)
    bins = histogram_bins(points)
    assert not any(b.overflow for b in bins) and sum(b.count for b in bins) == 3


def test_drug_cooccurrence_network():
    studies = [
        make_study(1, drugs=["Gemcitabine", "Nab-paclitaxel", "Placebo"]),
        make_study(2, drugs=["Gemcitabine HCl", "nab paclitaxel"]),
        make_study(3, drugs=["Gemcitabine", "Cisplatin"]),
    ]
    g = cooccurrence_graph(studies, Dimension.DRUG, Dimension.DRUG, min_edge_trials=2,
                           max_nodes=10)
    assert [(e.source, e.target, len(e.evidence)) for e in g.edges] == [
        ("drug:gemcitabine", "drug:nab paclitaxel", 2)
    ]
    assert {n.id for n in g.nodes} == {"drug:gemcitabine", "drug:nab paclitaxel"}
    assert g.candidate_nodes == 3  # placebo excluded


def test_bipartite_network_namespaces_and_prunes():
    studies = [make_study(i, sponsor="Merck", drugs=["Pembrolizumab"]) for i in range(3)]
    studies += [make_study(10, sponsor="Roche", drugs=["Atezolizumab"])]
    g = cooccurrence_graph(studies, Dimension.SPONSOR, Dimension.DRUG, min_edge_trials=1,
                           max_nodes=2)
    assert [n.id for n in g.nodes] == ["drug:pembrolizumab", "sponsor:merck"]
    assert len(g.edges) == 1 and len(g.edges[0].evidence) == 3


def test_site_network_skips_pairs_for_huge_trials():
    huge = [f"Hospital {chr(65 + i // 26)}{chr(65 + i % 26)}" for i in range(MAX_PAIRED_VALUES + 1)]
    studies = [
        make_study(1, sites=["Mayo Clinic", "Dana-Farber Cancer Institute"]),
        make_study(2, sites=["Mayo Clinic", "Dana Farber Cancer Institute", "Research Site"]),
        make_study(3, sites=huge),
    ]
    g = cooccurrence_graph(studies, Dimension.SITE, Dimension.SITE, min_edge_trials=2,
                           max_nodes=10)
    assert [(e.source, e.target) for e in g.edges] == [
        ("site:dana farber cancer institute", "site:mayo clinic")
    ]
    assert g.unpaired_trials == 1
