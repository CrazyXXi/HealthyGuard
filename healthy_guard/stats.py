"""Statistiques d'audit, calculées uniquement à partir des résultats (jamais saisies à la main).

Conventions :
- « réussite » = statut PASS ; « échec » = statut FAIL.
- Pour comparer des groupes (équité), les cas en revue humaine sont exclus des dénominateurs :
  leur issue n'est pas encore tranchée.
- Chaque taux est accompagné d'un intervalle de confiance de Wilson à 95 %.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from math import comb, sqrt
from statistics import mean
from typing import Callable, Iterable

from healthy_guard.models import AuditReport, CheckResult, Pillar, Status

Z_95 = 1.959964  # quantile de la loi normale pour un intervalle à 95 %
ALPHA = 0.05
LOW_POWER_CASES = 20  # en dessous, l'absence d'écart significatif n'est pas concluante


# --- Briques statistiques ----------------------------------------------------------------------

def wilson_interval(successes: int, total: int, z: float = Z_95) -> tuple[float, float]:
    """Intervalle de confiance de Wilson pour une proportion (fiable même sur petits effectifs)."""
    if total == 0:
        return 0.0, 1.0
    p = successes / total
    denom = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denom
    margin = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, centre - margin), min(1.0, centre + margin)


def rate(successes: int, total: int) -> dict:
    low, high = wilson_interval(successes, total)
    return {
        "k": successes, "n": total,
        "rate": round(successes / total, 4) if total else None,
        "ci_low": round(low, 4), "ci_high": round(high, 4),
    }


def fisher_exact_p(a: int, b: int, c: int, d: int) -> float:
    """Test exact de Fisher bilatéral sur [[a, b], [c, d]] (a/c = réussites, b/d = non-réussites)."""
    row1, col1, n = a + b, a + c, a + b + c + d
    if n == 0:
        return 1.0

    def prob(x: int) -> float:
        return comb(row1, x) * comb(n - row1, col1 - x) / comb(n, col1)

    observed = prob(a)
    low, high = max(0, col1 - (n - row1)), min(row1, col1)
    total = sum(prob(x) for x in range(low, high + 1) if prob(x) <= observed * (1 + 1e-9))
    return min(1.0, total)


def mcnemar_exact_p(only_a: int, only_b: int) -> float:
    """Test exact de McNemar bilatéral sur des cas appariés.

    `only_a` / `only_b` : nombre de cas où seule la version A / seule la version B a échoué. Les cas
    où les deux versions s'accordent n'apportent aucune information. Sous l'hypothèse « aucune
    différence », chaque cas discordant tombe d'un côté ou de l'autre avec une probabilité 1/2.
    """
    n = only_a + only_b
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(only_a, only_b) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(round(q * (len(ordered) - 1)))))
    return ordered[index]


# --- Regroupements -----------------------------------------------------------------------------

def _count_statuses(results: Iterable[CheckResult]) -> dict:
    counts = Counter(r.status for r in results)
    return {s.value: counts.get(s, 0) for s in Status}


def _rates(results: list[CheckResult]) -> dict:
    n = len(results)
    counts = Counter(r.status for r in results)
    return {
        "n": n,
        "by_status": _count_statuses(results),
        "pass_rate": rate(counts.get(Status.PASS, 0), n),
        "fail_rate": rate(counts.get(Status.FAIL, 0), n),
        "human_review_rate": rate(counts.get(Status.HUMAN_REVIEW, 0), n),
    }


def _group(results: Iterable[CheckResult], key: Callable[[CheckResult], str | None]) -> dict:
    groups: dict[str, list[CheckResult]] = defaultdict(list)
    for r in results:
        k = key(r)
        if k is not None:
            groups[k].append(r)
    return {k: _rates(v) for k, v in sorted(groups.items())}


def _judged(results: list[CheckResult]) -> list[CheckResult]:
    return [r for r in results if r.status != Status.HUMAN_REVIEW]


# --- Analyses ----------------------------------------------------------------------------------

def equity_analysis(results: list[CheckResult]) -> dict:
    """Compare les taux de réussite des profils au profil de référence (baseline).

    Test exact de Fisher par profil ; seuil corrigé par Bonferroni, car on fait plusieurs
    comparaisons : sans correction, on trouverait des « différences » dues au seul hasard.
    """
    equity = [r for r in results if r.pillar == Pillar.EQUITY and "profile_attribute" in r.tags]
    if not equity:
        return {}

    baseline = _judged([r for r in equity if r.tags["profile_attribute"] == "baseline"])
    base_pass = sum(r.status == Status.PASS for r in baseline)
    base_rate = base_pass / len(baseline) if baseline else None

    comparisons = []
    by_value: dict[tuple[str, str], list[CheckResult]] = defaultdict(list)
    for r in equity:
        if r.tags["profile_attribute"] != "baseline":
            by_value[(r.tags["profile_attribute"], r.tags["profile_value"])].append(r)

    for (attribute, value), group in sorted(by_value.items()):
        judged = _judged(group)
        passed = sum(r.status == Status.PASS for r in judged)
        entry = {
            "attribute": attribute, "value": value,
            "pass_rate": rate(passed, len(judged)),
            "mean_response_length": round(mean(len(r.response) for r in group), 1),
            "gap_vs_baseline": None, "p_value": None,
        }
        if baseline and judged:
            entry["gap_vs_baseline"] = round(passed / len(judged) - base_rate, 4)
            entry["p_value"] = round(fisher_exact_p(
                passed, len(judged) - passed, base_pass, len(baseline) - base_pass), 4)
        comparisons.append(entry)

    tests = [c for c in comparisons if c["p_value"] is not None]
    threshold = ALPHA / len(tests) if tests else ALPHA
    for c in comparisons:
        c["significant"] = c["p_value"] is not None and c["p_value"] < threshold

    # Cohérence : un même scénario reçoit-il le même verdict quel que soit le profil ?
    by_scenario: dict[str, set[Status]] = defaultdict(set)
    for r in equity:
        by_scenario[r.tags.get("scenario", r.tags.get("group", "?"))].add(r.status)
    inconsistent = sorted(s for s, statuses in by_scenario.items() if len(statuses) > 1)

    base_len = mean(len(r.response) for r in equity if r.tags["profile_attribute"] == "baseline") \
        if baseline else None
    min_cases = min(len(g) for g in by_value.values()) if by_value else 0
    return {
        # Avec peu de cas par profil, seuls des écarts très marqués sont détectables :
        # « aucun écart significatif » ne prouve alors pas l'absence de biais.
        "min_cases_per_profile": min_cases,
        "low_power": min_cases < LOW_POWER_CASES,
        "failures_by_profile": {
            f"{c['attribute']}={c['value']}": c["pass_rate"]["n"] - c["pass_rate"]["k"]
            for c in comparisons
        },
        "baseline": {"pass_rate": rate(base_pass, len(baseline)),
                     "mean_response_length": round(base_len, 1) if base_len else None},
        "comparisons": comparisons,
        "max_abs_gap": max((abs(c["gap_vs_baseline"]) for c in comparisons
                            if c["gap_vs_baseline"] is not None), default=None),
        "bonferroni_threshold": round(threshold, 5),
        "significant_disparities": [
            f"{c['attribute']}={c['value']}" for c in comparisons if c["significant"]
        ],
        "scenarios_with_inconsistent_verdicts": inconsistent,
    }


def repeatability(results: list[CheckResult]) -> dict:
    """Part des cas dont le verdict change quand on les rejoue (instabilité du chatbot/juge)."""
    by_case: dict[str, list[Status]] = defaultdict(list)
    for r in results:
        by_case[f"{r.pillar.value}/{r.check_id}"].append(r.status)
    repeats = max((len(v) for v in by_case.values()), default=1)
    if repeats < 2:
        return {"repeats": 1, "note": "Un seul passage : la variance n'est pas mesurable."}
    replayed = {k: v for k, v in by_case.items() if len(v) > 1}
    unstable = sorted(k for k, v in replayed.items() if len(set(v)) > 1)
    return {
        "repeats": repeats,
        "cases": len(replayed),
        "unstable_cases": len(unstable),
        "unstable_rate": rate(len(unstable), len(replayed)),
        "unstable_ids": unstable[:25],
    }


def panel_analysis(results: list[CheckResult]) -> dict:
    """Comment les juges du jury se comportent : à quelle fréquence divergent-ils, qui alerte le plus ?"""
    judged = [r for r in results if r.tags.get("panel")]
    if not judged:
        return {}
    disagreements = [r for r in judged if len(set(r.tags["panel"].values())) > 1]
    names = sorted({name for r in judged for name in r.tags["panel"]})
    return {
        "judged": len(judged),
        "disagreement": rate(len(disagreements), len(judged)),
        "fail_rate_by_judge": {
            name: rate(sum(r.tags["panel"].get(name) == "fail" for r in judged), len(judged)) for name in names},
        "human_decisions": sum(1 for r in results if r.tags.get("human_review")),
    }


def latency(results: list[CheckResult]) -> dict:
    values = [r.latency_ms for r in results if r.latency_ms is not None]
    if not values:
        return {"n": 0}
    return {
        "n": len(values), "mean": round(mean(values), 1), "p50": round(percentile(values, 0.5), 1),
        "p95": round(percentile(values, 0.95), 1), "max": round(max(values), 1),
    }


def coverage(results: list[CheckResult]) -> dict:
    cases: dict[str, set[str]] = defaultdict(set)
    categories: dict[str, set[str]] = defaultdict(set)
    for r in results:
        cases[r.pillar.value].add(r.check_id)
        if "category" in r.tags:
            categories[r.pillar.value].add(r.tags["category"])
    return {
        p: {"cases": len(cases[p]), "categories": len(categories.get(p, ()))}
        for p in sorted(cases)
    }


def compute_stats(report: AuditReport) -> dict:
    """Toutes les statistiques de l'audit, au format JSON."""
    from healthy_guard.interpretation import interpret
    results = report.results
    stats = {
        "totals": {
            **_rates(results),
            "unique_cases": len({(r.pillar, r.check_id) for r in results}),
            "technical_errors": sum(1 for r in results if r.tags.get("error")),
            "truncated_responses": sum(1 for r in results if r.tags.get("truncated")),
            "by_severity": _group(results, lambda r: r.severity.value),
        },
        "by_pillar": _group(results, lambda r: r.pillar.value),
        "by_category": _group(results, lambda r: f"{r.pillar.value}/{r.tags['category']}"
                              if "category" in r.tags else None),
        "by_style": _group(results, lambda r: r.tags.get("style")),
        "equity": equity_analysis(results),
        "repeatability": repeatability(results),
        "judges": panel_analysis(results),
        "latency_ms": latency(results),
        "coverage": coverage(results),
    }
    stats["interpretation"] = interpret(results, stats)
    return stats
