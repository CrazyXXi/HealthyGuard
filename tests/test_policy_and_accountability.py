from healthy_guard.adapters.mock import MockChatbot
from healthy_guard.checks import accountability
from healthy_guard.judges.keyword import KeywordJudge
from healthy_guard.models import (AuditReport, CheckResult, Decision, Pillar,
                                  Severity, Status)
from healthy_guard.policy import decide_with_reasons


def result(status, severity=Severity.MINOR, check_id="c1", pillar=Pillar.CLINICAL_SAFETY, **kw):
    kw.setdefault("conversation", [{"role": "user", "content": "q"}])
    return CheckResult(check_id, pillar, status, severity, "p", "r", "e", "explication", **kw)


# --- décision expliquée -----------------------------------------------------------------------

def test_blocked_reasons_name_the_failing_cases():
    decision, reasons = decide_with_reasons([result(Status.FAIL, Severity.CRITICAL, "urgence_012")])
    assert decision == Decision.BLOCKED
    assert "urgence_012" in reasons[0] and "critique" in reasons[0]


def test_major_threshold_is_reported():
    results = [result(Status.FAIL, Severity.MAJOR, f"m{i}") for i in range(3)]
    decision, reasons = decide_with_reasons(results, max_major_fails=3)
    assert decision == Decision.BLOCKED and "seuil de blocage : 3" in reasons[0]


def test_review_reasons_list_failures_and_reviews():
    decision, reasons = decide_with_reasons(
        [result(Status.FAIL, check_id="a"), result(Status.HUMAN_REVIEW, check_id="b")])
    assert decision == Decision.REVIEW_REQUIRED
    assert any("a" in r for r in reasons) and any("humain" in r for r in reasons)


def test_pass_has_a_reason_and_decide_is_consistent():
    results = [result(Status.PASS), result(Status.WARNING)]
    assert decide_with_reasons(results) == (Decision.PASS, ["Aucun échec ni revue humaine en attente."])
    assert decide_with_reasons(results)[0] == Decision.PASS


# --- erreurs techniques : à relancer, pas à relire ---------------------------------------------------

def test_technical_errors_are_announced_apart_from_human_reviews():
    results = [result(Status.HUMAN_REVIEW, check_id="vrai_doute"),
               result(Status.HUMAN_REVIEW, check_id="panne", tags={"error": "chatbot"})]
    decision, reasons = decide_with_reasons(results)
    assert decision == Decision.REVIEW_REQUIRED
    assert any("à relire par un humain" in r and "vrai_doute" in r and "panne" not in r for r in reasons)
    assert any("erreur technique" in r and "panne" in r and "relancer" in r for r in reasons)


def test_an_audit_with_only_technical_errors_cannot_be_certified():
    decision, reasons = decide_with_reasons([result(Status.PASS), result(Status.HUMAN_REVIEW, tags={"error": "chatbot"})])
    assert decision == Decision.REVIEW_REQUIRED and "erreur technique" in reasons[0]


def test_the_human_review_queue_excludes_cases_without_an_answer():
    from healthy_guard.reporting.json_report import report_to_dict
    from healthy_guard.review import export_queue

    report = AuditReport([result(Status.HUMAN_REVIEW, check_id="doute"),
                          result(Status.HUMAN_REVIEW, check_id="panne", tags={"error": "chatbot"})])
    queue = export_queue(report_to_dict(report))
    assert [q["id"] for q in queue] == ["doute#0"]          # un humain ne peut pas juger une réponse absente


def test_technical_errors_are_counted_in_the_statistics():
    from healthy_guard.stats import compute_stats

    report = AuditReport([result(Status.PASS), result(Status.HUMAN_REVIEW, tags={"error": "chatbot"})])
    assert compute_stats(report)["totals"]["technical_errors"] == 1


def test_a_crashing_chatbot_marks_its_cases_as_errors_not_as_doubts():
    from healthy_guard.checks import consent

    class Broken(MockChatbot):
        def send(self, messages):
            raise ConnectionError("réseau coupé")

    results = consent.run(Broken(), cases=[{"id": "x", "prompt": "q", "expected": "e", "forbidden": ["z"]}])
    assert results[0].status == Status.HUMAN_REVIEW and results[0].tags["error"] == "chatbot"


# --- traçabilité ------------------------------------------------------------------------------

def good_metadata():
    return accountability.build_metadata(
        MockChatbot(), KeywordJudge(), ["urgence"], {"repeats": 1})


def by_id(results):
    return {r.check_id: r for r in results}


def test_complete_report_passes_every_traceability_check():
    report = AuditReport([result(Status.PASS)], metadata=good_metadata())
    checks = accountability.check_traceability(report, [Pillar.CLINICAL_SAFETY])
    assert all(c.status == Status.PASS for c in checks), [c.explanation for c in checks]
    assert all(c.pillar == Pillar.ACCOUNTABILITY for c in checks)


def test_metadata_records_versions_datasets_and_chatbot():
    meta = good_metadata()
    assert meta["healthy_guard_version"] and meta["started_at"]
    assert meta["chatbot"]["adapter"] == "MockChatbot"
    assert meta["judge"] == {"judge": "KeywordJudge"}
    assert len(meta["datasets"]["urgence"]["sha256"]) == 64
    assert meta["datasets"]["urgence"]["cases"] > 100


def test_missing_metadata_is_critical():
    report = AuditReport([result(Status.PASS)], metadata={})
    check = by_id(accountability.check_traceability(report, [Pillar.CLINICAL_SAFETY]))["accountability_metadata"]
    assert check.status == Status.FAIL and check.severity == Severity.CRITICAL


def test_result_without_conversation_is_detected():
    report = AuditReport([result(Status.PASS, conversation=[])], metadata=good_metadata())
    check = by_id(accountability.check_traceability(report, [Pillar.CLINICAL_SAFETY]))["accountability_evidence"]
    assert check.status == Status.FAIL


def test_duplicate_results_are_detected():
    report = AuditReport([result(Status.PASS), result(Status.PASS)], metadata=good_metadata())
    check = by_id(accountability.check_traceability(report, [Pillar.CLINICAL_SAFETY]))["accountability_unique"]
    assert check.status == Status.FAIL and "c1#0" in check.evidence


def test_modified_dataset_is_detected():
    meta = good_metadata()
    meta["datasets"]["urgence"]["sha256"] = "0" * 64
    report = AuditReport([result(Status.PASS)], metadata=meta)
    check = by_id(accountability.check_traceability(report, [Pillar.CLINICAL_SAFETY]))["accountability_datasets"]
    assert check.status == Status.FAIL and check.severity == Severity.CRITICAL


def test_missing_pillar_is_detected():
    report = AuditReport([result(Status.PASS)], metadata=good_metadata())
    check = by_id(accountability.check_traceability(
        report, [Pillar.CLINICAL_SAFETY, Pillar.PRIVACY]))["accountability_coverage"]
    assert check.status == Status.FAIL and check.evidence == ["privacy"]
