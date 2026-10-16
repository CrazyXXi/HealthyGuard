import pytest

from healthy_guard.models import (AuditReport, CheckResult, Pillar, Severity,
                                  Status)
from healthy_guard.stats import (compute_stats, equity_analysis, fisher_exact_p,
                                 percentile, repeatability, wilson_interval)


def result(status=Status.PASS, pillar=Pillar.CLINICAL_SAFETY, check_id="c1",
           severity=Severity.MAJOR, repeat=0, response="réponse", **tags):
    return CheckResult(check_id, pillar, status, severity, "p", response, "e", "x",
                       tags=tags, repeat=repeat)


# --- briques ---------------------------------------------------------------------------------

def test_wilson_known_values():
    low, high = wilson_interval(50, 100)
    assert low == pytest.approx(0.4038, abs=1e-3)
    assert high == pytest.approx(0.5962, abs=1e-3)


def test_wilson_is_not_degenerate_on_extremes():
    low, high = wilson_interval(10, 10)
    assert high == pytest.approx(1.0) and 0.65 < low < 0.75  # 10/10 ne veut pas dire « 100 % sûr »
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wide_interval_on_small_samples():
    small = wilson_interval(9, 10)
    large = wilson_interval(900, 1000)
    assert (small[1] - small[0]) > 5 * (large[1] - large[0])


def test_fisher_known_value():
    # Exemple classique : [[3, 1], [1, 3]] -> p bilatéral = 0.4857
    assert fisher_exact_p(3, 1, 1, 3) == pytest.approx(0.4857, abs=1e-3)


def test_fisher_identical_groups_and_extreme_groups():
    assert fisher_exact_p(10, 10, 10, 10) == pytest.approx(1.0)
    assert fisher_exact_p(20, 0, 0, 20) < 1e-6


def test_percentile():
    values = list(range(1, 101))
    assert percentile(values, 0.5) in (50, 51)
    assert percentile(values, 0.95) in (95, 96)
    assert percentile([7], 0.95) == 7


# --- agrégats --------------------------------------------------------------------------------

def test_totals_and_pillars():
    report = AuditReport([
        result(Status.PASS), result(Status.PASS), result(Status.FAIL, check_id="c2"),
        result(Status.HUMAN_REVIEW, check_id="c3", pillar=Pillar.PRIVACY),
    ])
    stats = compute_stats(report)
    assert stats["totals"]["n"] == 4
    assert stats["totals"]["by_status"] == {"pass": 2, "fail": 1, "warning": 0, "human_review": 1}
    assert stats["totals"]["pass_rate"]["rate"] == 0.5
    assert stats["by_pillar"]["privacy"]["human_review_rate"]["k"] == 1
    assert stats["totals"]["by_severity"]["major"]["n"] == 4


def test_breakdown_by_category_and_style():
    report = AuditReport([
        result(category="infarctus", style="direct"),
        result(Status.FAIL, category="infarctus", style="minimise", check_id="c2"),
    ])
    stats = compute_stats(report)
    assert stats["by_category"]["clinical_safety/infarctus"]["n"] == 2
    assert stats["by_style"]["direct"]["pass_rate"]["rate"] == 1.0
    assert stats["by_style"]["minimise"]["fail_rate"]["rate"] == 1.0


def test_empty_report_does_not_crash():
    stats = compute_stats(AuditReport([]))
    assert stats["totals"]["n"] == 0
    assert stats["totals"]["pass_rate"]["rate"] is None
    assert stats["equity"] == {}
    assert stats["latency_ms"] == {"n": 0}


# --- équité ----------------------------------------------------------------------------------

def equity_result(attribute, value, status, i, response="réponse"):
    return result(status, Pillar.EQUITY, check_id=f"e{i}", response=response,
                  profile_attribute=attribute, profile_value=value, scenario="s1", group="s1")


def test_equity_detects_a_real_disparity():
    results = []
    for i in range(30):
        results.append(equity_result("baseline", "neutre", Status.PASS, i))
        results.append(equity_result("sexe", "femme", Status.FAIL if i < 20 else Status.PASS, 100 + i))
        results.append(equity_result("sexe", "homme", Status.PASS, 200 + i))
    analysis = equity_analysis(results)
    assert "sexe=femme" in analysis["significant_disparities"]
    assert "sexe=homme" not in analysis["significant_disparities"]
    femme = next(c for c in analysis["comparisons"] if c["value"] == "femme")
    assert femme["gap_vs_baseline"] == pytest.approx(-0.6667, abs=1e-3)
    assert analysis["max_abs_gap"] == pytest.approx(0.6667, abs=1e-3)


def test_equity_does_not_cry_wolf_on_noise():
    results = []
    for i in range(10):
        results.append(equity_result("baseline", "neutre", Status.PASS, i))
        # un seul échec sur 10 : écart apparent de 10 points, non significatif
        results.append(equity_result("age", "82_ans", Status.FAIL if i == 0 else Status.PASS, 100 + i))
    analysis = equity_analysis(results)
    assert analysis["significant_disparities"] == []


def test_equity_ignores_human_review_in_denominators():
    results = [equity_result("baseline", "neutre", Status.PASS, i) for i in range(5)]
    results += [equity_result("age", "82_ans", Status.PASS, 10),
                equity_result("age", "82_ans", Status.HUMAN_REVIEW, 11)]
    comparison = equity_analysis(results)["comparisons"][0]
    assert comparison["pass_rate"]["n"] == 1


def test_equity_response_length_gap_is_reported():
    results = [equity_result("baseline", "neutre", Status.PASS, 0, "x" * 400),
               equity_result("age", "82_ans", Status.PASS, 1, "x" * 100)]
    analysis = equity_analysis(results)
    assert analysis["baseline"]["mean_response_length"] == 400.0
    assert analysis["comparisons"][0]["mean_response_length"] == 100.0


# --- répétabilité et latence -----------------------------------------------------------------

def test_repeatability_single_pass():
    assert repeatability([result()])["repeats"] == 1


def test_repeatability_detects_unstable_cases():
    results = [
        result(Status.PASS, check_id="stable", repeat=0), result(Status.PASS, check_id="stable", repeat=1),
        result(Status.PASS, check_id="flaky", repeat=0), result(Status.FAIL, check_id="flaky", repeat=1),
    ]
    out = repeatability(results)
    assert out["repeats"] == 2 and out["cases"] == 2
    assert out["unstable_cases"] == 1
    assert out["unstable_ids"] == ["clinical_safety/flaky"]


def test_latency_summary():
    results = [result(check_id=str(i)) for i in range(4)]
    for r, ms in zip(results, [100, 200, 300, 400]):
        r.latency_ms = ms
    summary = compute_stats(AuditReport(results))["latency_ms"]
    assert summary["n"] == 4 and summary["mean"] == 250.0 and summary["max"] == 400.0
