import copy

import pytest

from healthy_guard.adapters.demo import DemoChatbot
from healthy_guard.auditor import Auditor
from healthy_guard.cli import main
from healthy_guard.judges.keyword import KeywordJudge
from healthy_guard.models import Pillar
from healthy_guard.reporting.json_report import read_json, report_to_dict, results_sha256, verify
from healthy_guard.retry import errored_ids, retry_errors


class Recording(DemoChatbot):
    """Chatbot de démonstration qui garde la trace des messages reçus."""

    def __init__(self, flawed=False):
        super().__init__(flawed)
        self.received = []

    def send(self, messages):
        self.received.append(messages[0]["content"])
        return super().send(messages)


@pytest.fixture(scope="module")
def original():
    return report_to_dict(Auditor(DemoChatbot(flawed=True), pillars=[Pillar.PRIVACY, Pillar.ACCOUNTABILITY], limit=20).run())


def with_errors(report, how_many=4):
    """Rapport légitime où `how_many` cas à un seul tour n'ont reçu aucune réponse (503)."""
    out = copy.deepcopy(report)
    victims = [r for r in out["results"] if r["pillar"] == "privacy" and len(r["conversation"]) == 2][:how_many]
    for result in victims:
        result.update(status="human_review", response="", explanation="Erreur d'appel au chatbot : HTTP 503")
        result["tags"]["error"] = "chatbot"
    out["integrity"]["results_sha256"] = results_sha256(out["results"])
    return out, victims


def test_errored_ids_lists_only_cases_without_an_answer(original):
    broken, victims = with_errors(original)
    assert errored_ids(original) == {}
    assert errored_ids(broken) == {Pillar.PRIVACY: {v["check_id"] for v in victims}}


def test_only_the_failed_cases_are_sent_again_and_good_results_are_kept(original):
    broken, victims = with_errors(original)
    bot = Recording(flawed=True)
    merged, count = retry_errors(broken, bot, KeywordJudge())
    assert count == 4 and len(bot.received) == 4                          # 4 appels, pas 20+
    assert set(bot.received) == {v["prompt"] for v in victims}
    assert not errored_ids(merged) and merged["metadata"]["retry"] == {"cases_retried": 4, "still_without_answer": 0}
    kept = {r["check_id"]: r for r in original["results"] if r["pillar"] == "privacy"}
    for result in merged["results"]:
        if result["pillar"] == "privacy":
            assert result["status"] == kept[result["check_id"]]["status"]       # même chatbot déterministe : mêmes verdicts
    assert merged["decision"] == original["decision"] and verify(merged)[0]
    assert merged["metadata"]["merge"]["errors_replaced"] == 4


def test_a_case_that_fails_again_keeps_its_error_and_the_audit_stays_uncertifiable(original):
    broken, victims = with_errors(original)

    class StillDown(DemoChatbot):
        def send(self, messages):
            raise TimeoutError("503")

    merged, _ = retry_errors(broken, StillDown(flawed=True), KeywordJudge())
    assert len(errored_ids(merged)[Pillar.PRIVACY]) == 4 and merged["metadata"]["retry"]["still_without_answer"] == 4
    assert any("erreur technique" in reason for reason in merged["metadata"]["decision_reasons"])


def test_nothing_to_retry_returns_the_report_untouched(original):
    bot = Recording()
    merged, count = retry_errors(original, bot, KeywordJudge())
    assert count == 0 and merged is original and bot.received == []


def test_a_tampered_report_is_refused(original):
    forged = copy.deepcopy(original)
    forged["results"][0]["status"] = "fail" if forged["results"][0]["status"] == "pass" else "pass"
    with pytest.raises(ValueError, match="intègre"):
        retry_errors(forged, DemoChatbot(flawed=True), KeywordJudge())


def test_the_original_report_is_not_modified(original):
    broken, _ = with_errors(original)
    snapshot = copy.deepcopy(broken)
    retry_errors(broken, DemoChatbot(flawed=True), KeywordJudge())
    assert broken == snapshot


def test_cli_retry_errors_end_to_end(tmp_path, capsys, original):
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    base = ["audit", "--adapter", "demo-flawed", "--quiet"]
    main(base + ["--pillars", "privacy,accountability", "--limit", "20", "--out", str(first)])
    broken, _ = with_errors(read_json(first))
    import json
    first.write_text(json.dumps(broken), encoding="utf-8")

    code = main(base + ["--retry-errors", str(first), "--out", str(second)])
    out = capsys.readouterr().out
    assert "4 cas relancés ; 0 restent sans réponse" in out and code in (1, 2)
    assert not errored_ids(read_json(second)) and verify(read_json(second))[0]

    assert main(base + ["--retry-errors", str(second), "--out", str(tmp_path / "c.json")]) in (1, 2)
    assert "rien à relancer" in capsys.readouterr().out
