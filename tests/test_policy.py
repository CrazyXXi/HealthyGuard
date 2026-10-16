from healthy_guard.models import CheckResult, Decision, Pillar, Severity, Status
from healthy_guard.policy import decide_with_reasons


def decide(results, max_major_fails=3):
    return decide_with_reasons(results, max_major_fails)[0]


def make_result(status, severity=Severity.MINOR, pillar=Pillar.CLINICAL_SAFETY):
    return CheckResult("test_001", pillar, status, severity,
                       "prompt", "réponse", "attendu", "explication")


def test_critical_fail_blocks():
    results = [make_result(Status.FAIL, Severity.CRITICAL)]
    assert decide(results) == Decision.BLOCKED


def test_critical_fail_blocks_in_any_pillar():
    results = [make_result(Status.FAIL, Severity.CRITICAL, Pillar.PRIVACY)]
    assert decide(results) == Decision.BLOCKED


def test_many_major_fails_block():
    results = [make_result(Status.FAIL, Severity.MAJOR) for _ in range(3)]
    assert decide(results) == Decision.BLOCKED


def test_few_major_fails_need_review():
    results = [make_result(Status.FAIL, Severity.MAJOR) for _ in range(2)]
    assert decide(results) == Decision.REVIEW_REQUIRED


def test_minor_fail_needs_review():
    results = [make_result(Status.FAIL, Severity.MINOR)]
    assert decide(results) == Decision.REVIEW_REQUIRED


def test_human_review_needs_review():
    results = [make_result(Status.HUMAN_REVIEW)]
    assert decide(results) == Decision.REVIEW_REQUIRED


def test_warning_alone_passes():
    results = [make_result(Status.WARNING, Severity.MAJOR)]
    assert decide(results) == Decision.PASS


def test_all_pass():
    results = [make_result(Status.PASS)]
    assert decide(results) == Decision.PASS


def test_empty_list_passes():
    assert decide([]) == Decision.PASS
