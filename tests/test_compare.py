import copy
import json

import pytest

from healthy_guard.adapters.demo import DemoChatbot
from healthy_guard.auditor import Auditor
from healthy_guard.cli import main
from healthy_guard.compare import case_outcomes, compare_reports
from healthy_guard.reporting.json_report import report_to_dict, results_sha256
from healthy_guard.stats import mcnemar_exact_p


def audit(flawed, limit=30):
    return report_to_dict(Auditor(DemoChatbot(flawed=flawed), limit=limit).run())


@pytest.fixture(scope="module")
def good():
    return audit(False)


@pytest.fixture(scope="module")
def flawed():
    return audit(True)


def forge(report, check_id, status):
    """Produit un rapport légitime (empreinte recalculée) où un cas change de statut."""
    out = copy.deepcopy(report)
    for result in out["results"]:
        if result["check_id"] == check_id:
            result["status"] = status
    out["integrity"]["results_sha256"] = results_sha256(out["results"])
    return out


# --- test exact de McNemar -----------------------------------------------------------------------

@pytest.mark.parametrize("only_a, only_b, expected", [
    (0, 5, 0.0625), (5, 0, 0.0625), (1, 5, 0.21875), (3, 3, 1.0), (0, 0, 1.0), (10, 0, 2 / 1024), (2, 8, 0.109375),
])
def test_mcnemar_known_values(only_a, only_b, expected):
    assert mcnemar_exact_p(only_a, only_b) == pytest.approx(expected)


# --- comparaison -------------------------------------------------------------------------------------

def test_fixing_a_flawed_chatbot_is_a_significant_improvement(flawed, good):
    result = compare_reports(flawed, good)
    assert result["verdict"] == "AMÉLIORATION"
    assert result["overall"]["regressed"] == 0 and result["overall"]["fixed"] > 10
    assert result["overall"]["p_value"] < 0.001 and result["new_critical_failures"] == []
    assert result["decision"] == {"a": "blocked", "b": "pass"}


def test_breaking_a_good_chatbot_is_a_regression_with_new_critical_failures(good, flawed):
    result = compare_reports(good, flawed)
    assert result["verdict"] == "RÉGRESSION"
    assert result["new_critical_failures"] and result["significant_regressions"]
    assert result["overall"]["fixed"] == 0 and len(result["regressions"]) == result["overall"]["regressed"]


def test_identical_versions_show_no_difference(good):
    result = compare_reports(good, good)
    assert result["verdict"] == "AUCUNE DIFFÉRENCE DÉTECTABLE"
    assert result["overall"]["p_value"] == 1.0 and not result["regressions"] and not result["fixes"]


def test_a_single_new_critical_failure_is_a_regression_even_if_not_significant(good):
    critical = next(r["check_id"] for r in good["results"] if r["severity"] == "critical" and r["pillar"] != "accountability")
    worse = forge(good, critical, "fail")
    result = compare_reports(good, worse)
    assert result["verdict"] == "RÉGRESSION" and len(result["new_critical_failures"]) == 1
    assert result["overall"]["p_value"] > 0.05                        # un seul cas : statistiquement non significatif
    assert result["significant_regressions"] == []                    # ... mais la règle de sécurité s'applique


def test_a_new_minor_or_major_failure_alone_is_not_flagged_as_regression(good):
    major = next(r["check_id"] for r in good["results"]
                 if r["severity"] == "major" and r["pillar"] not in ("accountability", "equity"))
    result = compare_reports(good, forge(good, major, "fail"))
    assert result["verdict"] == "AUCUNE DIFFÉRENCE DÉTECTABLE" and result["regressions"]
    assert "Peu de cas discordants" in result["power_note"]


def test_flaky_cases_count_as_failed():
    report = {"results": [
        {"pillar": "privacy", "check_id": "c1", "status": "pass", "severity": "major"},
        {"pillar": "privacy", "check_id": "c1", "status": "fail", "severity": "major"},
        {"pillar": "privacy", "check_id": "c2", "status": "pass", "severity": "major"},
        {"pillar": "accountability", "check_id": "internal", "status": "fail", "severity": "critical"},
    ]}
    outcomes = case_outcomes(report)
    assert outcomes[("privacy", "c1")] == {"failed": True, "severity": "major", "flaky": True}
    assert outcomes[("privacy", "c2")]["failed"] is False
    assert all(key[0] != "accountability" for key in outcomes)


def test_the_comparison_is_limited_to_common_cases_and_says_so(good):
    shrunk = copy.deepcopy(good)
    shrunk["results"] = [r for r in shrunk["results"] if not r["check_id"].startswith("urgence")]
    shrunk["integrity"]["results_sha256"] = results_sha256(shrunk["results"])
    result = compare_reports(good, shrunk)
    assert any("seulement dans A" in note for note in result["notes"])
    assert "clinical_safety" not in result["by_pillar"]


def test_different_datasets_are_flagged(good):
    other = copy.deepcopy(good)
    other["metadata"]["datasets"]["urgence"]["sha256"] = "0" * 64
    assert any("urgence" in note and "diffère" in note for note in compare_reports(good, other)["notes"])


def test_bonferroni_threshold_is_corrected_for_the_number_of_pillar_tests(flawed, good):
    result = compare_reports(flawed, good)
    tested = [p for p, b in result["by_pillar"].items() if b["fixed"] + b["regressed"] > 0]
    assert result["bonferroni_threshold"] == pytest.approx(0.05 / len(tested), abs=1e-5) and len(tested) > 1


def test_a_tampered_report_cannot_be_compared(good):
    forged = copy.deepcopy(good)
    forged["results"][0]["status"] = "fail"
    with pytest.raises(ValueError, match="intègre"):
        compare_reports(good, forged)


# --- CLI -----------------------------------------------------------------------------------------------

def write_pair(tmp_path, a, b):
    pa, pb = tmp_path / "a.json", tmp_path / "b.json"
    pa.write_text(json.dumps(a), encoding="utf-8")
    pb.write_text(json.dumps(b), encoding="utf-8")
    return str(pa), str(pb)


def test_cli_compare_exit_codes(tmp_path, good, flawed, capsys):
    pa, pb = write_pair(tmp_path, flawed, good)
    assert main(["compare", pa, pb, "--out", str(tmp_path / "cmp.json")]) == 0
    out = capsys.readouterr().out
    assert "== AMÉLIORATION ==" in out and "blocked -> pass" in out
    assert json.loads((tmp_path / "cmp.json").read_text(encoding="utf-8"))["verdict"] == "AMÉLIORATION"

    pa, pb = write_pair(tmp_path, good, flawed)
    assert main(["compare", pa, pb]) == 1                              # régression : bloque la chaîne de déploiement
    out = capsys.readouterr().out
    assert "== RÉGRESSION ==" in out and "NOUVEAUX ÉCHECS CRITIQUES" in out


def test_cli_compare_rejects_a_tampered_report(tmp_path, good, capsys):
    forged = copy.deepcopy(good)
    forged["results"][0]["status"] = "fail"
    pa, pb = write_pair(tmp_path, good, forged)
    assert main(["compare", pa, pb]) == 64
    assert "intègre" in capsys.readouterr().err


def test_cli_compare_works_on_reports_written_by_the_audit_command(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    assert main(["audit", "--adapter", "demo-flawed", "--limit", "20", "--out", str(a), "--quiet"]) == 2
    assert main(["audit", "--adapter", "demo-good", "--limit", "20", "--out", str(b), "--quiet"]) == 0
    assert main(["compare", str(a), str(b)]) == 0 and main(["compare", str(b), str(a)]) == 1
