import pytest

from healthy_guard.adapters.mock import MockChatbot
from healthy_guard.auditor import ALL_PILLARS, Auditor, sample
from healthy_guard.models import Decision, Pillar, Status
from tests.test_pillars import GOOD

ALL_GOOD = " ".join(GOOD.values())


def test_sample_is_deterministic_and_spread():
    cases = list(range(100))
    picked = sample(cases, 10)
    assert picked == sample(cases, 10)
    assert len(picked) == 10 and picked[0] == 0 and picked[-1] >= 85  # couvre tout le jeu
    assert sample(cases, None) == cases and sample(cases, 500) == cases
    assert sample(cases, 0) == []


def test_full_audit_of_a_good_chatbot():
    report = Auditor(MockChatbot(default=ALL_GOOD), limit=20).run()
    assert {r.pillar for r in report.results} == set(ALL_PILLARS)
    assert report.decision == Decision.PASS, report.metadata["decision_reasons"]
    meta = report.metadata
    assert meta["finished_at"] and meta["duration_s"] >= 0
    assert meta["settings"]["limit_per_pillar"] == 20
    assert meta["datasets"]["urgence"]["cases_run"] == 20
    assert meta["decision_reasons"]


def test_full_audit_of_a_dangerous_chatbot_is_blocked():
    report = Auditor(MockChatbot(default="Reposez-vous, ça va passer."), limit=20).run()
    assert report.decision == Decision.BLOCKED
    assert "critique" in report.metadata["decision_reasons"][0]
    from healthy_guard.stats import compute_stats
    assert compute_stats(report)["by_pillar"]["clinical_safety"]["fail_rate"]["rate"] > 0.5


def test_a_crashing_chatbot_needs_human_review_not_a_crash():
    class Broken(MockChatbot):
        def send(self, messages):
            raise ConnectionError("réseau coupé")

    report = Auditor(Broken(), pillars=[Pillar.PRIVACY, Pillar.ACCOUNTABILITY], limit=5).run()
    interrogation = [r for r in report.results if r.pillar == Pillar.PRIVACY]
    assert all(r.status == Status.HUMAN_REVIEW for r in interrogation)
    assert report.decision == Decision.REVIEW_REQUIRED


def test_pillar_subset_only_runs_the_requested_pillars():
    report = Auditor(MockChatbot(default=ALL_GOOD), pillars=[Pillar.CONSENT], limit=5).run()
    assert {r.pillar for r in report.results} == {Pillar.CONSENT}
    assert list(report.metadata["datasets"]) == ["consentement"]


def test_repeats_multiply_results_and_enable_variance_stats():
    from healthy_guard.stats import compute_stats

    report = Auditor(MockChatbot(default=ALL_GOOD), pillars=[Pillar.CONSENT], limit=4,
                     repeats=3).run()
    assert len(report.results) == 12
    assert compute_stats(report)["repeatability"]["repeats"] == 3


def test_progress_callback_reports_every_case():
    seen = []
    Auditor(MockChatbot(default=ALL_GOOD), pillars=[Pillar.CONSENT], limit=4,
            progress=lambda pillar, done, total: seen.append((pillar, done, total))).run()
    assert seen[-1] == (Pillar.CONSENT, 4, 4) and len(seen) == 4


def test_traceability_results_are_part_of_the_report():
    report = Auditor(MockChatbot(default=ALL_GOOD), limit=5).run()
    ids = {r.check_id for r in report.results if r.pillar == Pillar.ACCOUNTABILITY}
    assert {"accountability_metadata", "accountability_evidence", "accountability_datasets",
            "accountability_coverage", "accountability_unique"} == ids


def test_invalid_repeats():
    with pytest.raises(ValueError):
        Auditor(MockChatbot(), repeats=0)
