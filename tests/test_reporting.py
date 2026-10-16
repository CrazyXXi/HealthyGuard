import copy

from healthy_guard.adapters.mock import MockChatbot
from healthy_guard.auditor import Auditor
from healthy_guard.models import Pillar
from healthy_guard.reporting.html_report import render_html, write_html
from healthy_guard.reporting.json_report import (read_json, report_from_dict, report_to_dict,
                                                 verify, write_json)
from tests.test_auditor import ALL_GOOD


def make_report(default=ALL_GOOD, **kwargs):
    kwargs.setdefault("limit", 10)
    return Auditor(MockChatbot(default=default), **kwargs).run()


def test_json_roundtrip_is_lossless(tmp_path):
    report = make_report()
    path = tmp_path / "audit.json"
    write_json(report, path)
    data = read_json(path)
    rebuilt = report_from_dict(data)
    assert report_to_dict(rebuilt)["results"] == data["results"]
    assert rebuilt.decision == report.decision
    assert verify(data)[0]


def test_json_contains_everything_needed_to_audit_a_decision(tmp_path):
    data = write_json(make_report("Reposez-vous."), tmp_path / "a.json")
    assert {"decision", "metadata", "stats", "results", "integrity"} <= set(data)
    first = data["results"][0]
    assert {"check_id", "pillar", "status", "severity", "conversation", "response",
            "expected", "explanation", "evidence", "tags", "latency_ms"} <= set(first)
    assert first["conversation"][0]["role"] == "user"
    assert data["metadata"]["decision_reasons"]


def test_tampering_with_a_result_is_detected(tmp_path):
    data = write_json(make_report("Reposez-vous."), tmp_path / "a.json")
    forged = copy.deepcopy(data)
    for r in forged["results"]:
        if r["status"] == "fail":
            r["status"] = "pass"          # on « blanchit » un échec
            break
    ok, message = verify(forged)
    assert not ok and "différente" in message


def test_missing_integrity_is_reported():
    ok, message = verify({"results": []})
    assert not ok and "empreinte" in message.lower()


def test_html_report_shows_decision_reasons_and_failures(tmp_path):
    report = make_report("Reposez-vous.")
    path = tmp_path / "audit.html"
    write_html(report, path)
    html = path.read_text(encoding="utf-8")
    assert "Déploiement bloqué" in html
    assert "échec(s) critique(s)" in html
    assert "Résultats par pilier" in html and "Traçabilité" in html
    assert "Aucune orientation vers les secours" in html


def test_html_report_for_a_clean_audit():
    html = render_html(make_report())
    assert "Déploiement autorisé" in html


def test_html_escapes_untrusted_chatbot_output():
    evil = "<script>alert('x')</script><img src=x onerror=alert(1)>"
    html = render_html(make_report(evil))
    assert "<script>alert" not in html and "<img src=x" not in html
    assert "&lt;script&gt;" in html


def test_html_includes_equity_section_when_the_pillar_ran():
    report = make_report(pillars=[Pillar.EQUITY], limit=None)
    html = render_html(report)
    assert "Équité entre profils" in html and "Test exact de Fisher" in html
