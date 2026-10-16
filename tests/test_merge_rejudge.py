import copy
import json

import pytest

from healthy_guard.adapters.demo import DemoChatbot
from healthy_guard.adapters.mock import MockChatbot
from healthy_guard.auditor import Auditor
from healthy_guard.cli import main
from healthy_guard.judges.base import Judge, Verdict
from healthy_guard.judges.keyword import KeywordJudge
from healthy_guard.judges.llm import LLMJudge
from healthy_guard.judges.panel import PanelJudge
from healthy_guard.merge import merge_reports
from healthy_guard.models import Pillar, Status
from healthy_guard.reporting.json_report import read_json, report_to_dict, results_sha256, verify
from healthy_guard.rejudge import rejudge_report


def audit(pillars, flawed=True, limit=15, judge=None):
    return report_to_dict(Auditor(DemoChatbot(flawed=flawed), judge, pillars=pillars, limit=limit).run())


def forge(report, mutate):
    """Rapport légitime (empreinte recalculée) dont on a modifié des résultats."""
    out = copy.deepcopy(report)
    mutate(out["results"])
    out["integrity"]["results_sha256"] = results_sha256(out["results"])
    return out


class Always(Judge):
    def __init__(self, status):
        self.status = status

    def judge(self, case, response):
        return Verdict(self.status, f"toujours {self.status.value}")


@pytest.fixture(scope="module")
def clinical():
    return audit([Pillar.CLINICAL_SAFETY, Pillar.ACCOUNTABILITY])


@pytest.fixture(scope="module")
def privacy():
    return audit([Pillar.PRIVACY, Pillar.ACCOUNTABILITY])


def interrogation(report):
    return [r for r in report["results"] if r["pillar"] != "accountability"]


# --- fusion ---------------------------------------------------------------------------------------

def test_merging_pieces_gives_the_same_audit_as_running_it_in_one_go(clinical, privacy):
    merged = merge_reports([clinical, privacy])
    whole = audit([Pillar.CLINICAL_SAFETY, Pillar.PRIVACY, Pillar.ACCOUNTABILITY])
    key = lambda r: (r["pillar"], r["check_id"], r["status"])  # noqa: E731
    assert sorted(map(key, interrogation(merged))) == sorted(map(key, interrogation(whole)))
    assert merged["decision"] == whole["decision"] and verify(merged)[0]


def test_traceability_is_recomputed_on_the_merged_report(clinical, privacy):
    merged = merge_reports([clinical, privacy])
    internal = [r for r in merged["results"] if r["pillar"] == "accountability"]
    assert len(internal) == 5 and all(r["status"] == "pass" for r in internal)      # pas 10 : recalculés, pas additionnés
    assert set(merged["metadata"]["datasets"]) == {"urgence", "confidentialite"}
    assert merged["metadata"]["settings"]["pillars"] == ["clinical_safety", "privacy", "accountability"]
    assert [m["report_sha256"] for m in merged["metadata"]["merged_from"]] == \
        [clinical["integrity"]["results_sha256"], privacy["integrity"]["results_sha256"]]


def test_a_real_answer_replaces_a_technical_error(privacy):
    def break_one(results):
        victim = next(r for r in results if r["pillar"] == "privacy")
        victim.update(status="human_review", response="", explanation="Erreur d'appel au chatbot")
        victim["tags"]["error"] = "chatbot"
        break_one.id = victim["check_id"]

    broken = forge(privacy, break_one)
    for order in ([broken, privacy], [privacy, broken]):
        merged = merge_reports(order)
        target = next(r for r in merged["results"] if r["check_id"] == break_one.id)
        assert not target["tags"].get("error") and target["response"]
        assert merged["metadata"]["merge"]["duplicates_resolved"] == len(interrogation(privacy))
    assert merge_reports([broken, privacy])["metadata"]["merge"]["errors_replaced"] == 1
    assert merge_reports([privacy, broken])["metadata"]["merge"]["errors_replaced"] == 0   # le 1er l'emporte s'il est valable


@pytest.mark.parametrize("mutate, message", [
    (lambda r: r["metadata"].update(chatbot={"adapter": "AutreBot", "model": "x"}), "autre chatbot"),
    (lambda r: r["metadata"].update(judge={"judge": "LLMJudge"}), "jugé autrement"),
    (lambda r: r["metadata"]["datasets"]["confidentialite"].update(sha256="0" * 64), "diffère"),
])
def test_incomparable_reports_cannot_be_merged(clinical, privacy, mutate, message):
    other = copy.deepcopy(privacy)
    mutate(other)
    other["integrity"]["results_sha256"] = results_sha256(other["results"])
    with pytest.raises(ValueError, match=message):
        merge_reports([clinical, other]) if "diffère" not in message else merge_reports([privacy, other])


def test_tampered_or_single_reports_are_refused(clinical, privacy):
    forged = copy.deepcopy(privacy)
    forged["results"][0]["status"] = "fail" if forged["results"][0]["status"] == "pass" else "pass"
    with pytest.raises(ValueError, match="intègre"):
        merge_reports([clinical, forged])
    with pytest.raises(ValueError, match="au moins 2"):
        merge_reports([clinical])


# --- rejuger ----------------------------------------------------------------------------------------

def test_rejudging_with_the_same_judge_changes_nothing(clinical, privacy):
    """Garantit que juger après coup est équivalent à juger pendant l'audit (même chaîne de règles)."""
    for report in (clinical, privacy, audit([Pillar.TRANSPARENCY, Pillar.CONSENT, Pillar.EQUITY], limit=40)):
        again = rejudge_report(report, KeywordJudge())
        before = {(r["pillar"], r["check_id"]): r["status"] for r in interrogation(report)}
        after = {(r["pillar"], r["check_id"]): r["status"] for r in interrogation(again)}
        assert before == after
        assert again["decision"] == report["decision"] and again["metadata"]["rejudged"]["verdict_changed"] == 0


def test_a_new_judge_replaces_verdicts_and_keeps_the_trace(privacy):
    updated = rejudge_report(privacy, Always(Status.PASS))
    assert all(r["status"] == "pass" for r in interrogation(updated)) and updated["decision"] == "pass"
    failed_before = [r for r in interrogation(privacy) if r["status"] == "fail"]
    assert failed_before                                            # le chatbot de démo avait bien des défauts
    sample = next(r for r in interrogation(updated) if r["tags"]["rejudged"]["from_status"] == "fail")
    assert sample["tags"]["rejudged"]["from_explanation"] and sample["explanation"] == "toujours pass"
    meta = updated["metadata"]
    assert meta["judge"]["judge"] == "Always" and meta["judge_before_rejudge"] == privacy["metadata"]["judge"]
    assert meta["parent_report_sha256"] == privacy["integrity"]["results_sha256"]
    assert meta["rejudged"]["verdict_changed"] == len(failed_before) and verify(updated)[0]


def test_the_original_report_is_never_modified(privacy):
    before = copy.deepcopy(privacy)
    rejudge_report(privacy, Always(Status.PASS))
    assert privacy == before


def test_rejudging_can_be_limited_to_some_verdicts(privacy):
    updated = rejudge_report(privacy, Always(Status.PASS), statuses={"fail"})
    for old, new in zip(interrogation(privacy), interrogation(updated)):
        if old["status"] == "fail":
            assert new["status"] == "pass" and "rejudged" in new["tags"]
        else:
            assert new["status"] == old["status"] and "rejudged" not in new["tags"]


def test_cases_without_an_answer_are_not_rejudged(privacy):
    broken = forge(privacy, lambda results: results[0].update(status="human_review", response="") or results[0]["tags"].update(error="chatbot"))
    updated = rejudge_report(broken, Always(Status.PASS))
    assert updated["results"][0]["status"] == "human_review" and "rejudged" not in updated["results"][0]["tags"]
    assert updated["metadata"]["rejudged"]["examined"] == len(interrogation(privacy)) - 1


def test_a_judge_that_fails_never_overwrites_a_valid_verdict(privacy):
    class Broken(Judge):
        def judge(self, case, response):
            raise TimeoutError("quota dépassé")

    updated = rejudge_report(privacy, Broken())
    assert [r["status"] for r in interrogation(updated)] == [r["status"] for r in interrogation(privacy)]
    assert updated["metadata"]["rejudged"]["judge_failures_kept_original"] == len(interrogation(privacy))
    assert "TimeoutError" in interrogation(updated)[0]["tags"]["rejudge_error"]


def test_custom_cases_without_known_rules_are_kept_and_counted(privacy):
    custom = forge(privacy, lambda results: results[0].update(check_id="mon_cas_perso"))
    updated = rejudge_report(custom, Always(Status.PASS))
    assert updated["results"][0]["status"] == privacy["results"][0]["status"]
    assert updated["metadata"]["rejudged"]["unresolved_cases"] == 1


def test_rules_and_llm_jury_can_overturn_rules_false_alarms(privacy):
    lenient_llm = LLMJudge(MockChatbot(default=json.dumps({"status": "pass", "explanation": "ok", "evidence": []})))
    updated = rejudge_report(privacy, PanelJudge({"règles": KeywordJudge(), "llm": lenient_llm}, "unanimity"))
    disputed = [r for r in interrogation(updated) if r["tags"]["rejudged"]["from_status"] == "fail"]
    contested = [r for r in disputed if r["tags"]["panel"]["llm"] == "pass"]
    assert contested and all(r["status"] == "human_review" for r in contested)       # désaccord => un humain tranche
    # Une donnée sensible qui fuit est un fait : le LLM indulgent ne peut pas l'effacer, le cas reste un échec.
    leaks = [r for r in disputed if r["tags"]["panel"]["llm"] == "fail"]
    assert leaks and all(r["status"] == "fail" and r["evidence"] for r in leaks)
    assert updated["metadata"]["judge"]["judge"] == "PanelJudge"


# --- CLI ----------------------------------------------------------------------------------------------

def test_cli_merge_then_rejudge_workflow(tmp_path, capsys):
    a, b, merged = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "m.json"
    codes = {"review_required": 1, "blocked": 2}
    for path, pillars in ((a, "clinical_safety,accountability"), (b, "privacy,accountability")):
        assert main(["audit", "--adapter", "demo-flawed", "--pillars", pillars, "--limit", "25",
                     "--out", str(path), "--quiet"]) in (1, 2)
    code = main(["merge", str(a), str(b), "--out", str(merged), "--html", str(tmp_path / "m.html")])
    out = capsys.readouterr().out
    assert "2 rapports fusionnés" in out
    data = read_json(merged)
    assert code == codes[data["decision"]]                            # le code de sortie suit la décision fusionnée
    assert {r["pillar"] for r in data["results"]} == {"clinical_safety", "privacy", "accountability"}

    rejudged = tmp_path / "rj.json"
    assert main(["rejudge", str(merged), "--statuses", "fail", "--out", str(rejudged), "--quiet"]) == code
    assert "0 verdict(s) modifié(s)" in capsys.readouterr().out         # même juge : rien ne change
    assert main(["rejudge", str(merged), "--statuses", "magie", "--out", str(rejudged)]) == 64


def test_cli_merge_refuses_incomparable_reports(tmp_path, capsys):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    main(["audit", "--adapter", "demo-flawed", "--pillars", "consent", "--limit", "3", "--out", str(a), "--quiet"])
    main(["audit", "--adapter", "demo-good", "--pillars", "privacy", "--limit", "3", "--out", str(b), "--quiet"])
    assert main(["merge", str(a), str(b), "--out", str(tmp_path / "m.json")]) == 64
    assert "autre chatbot" in capsys.readouterr().err


# --- rejuger en parallèle, et reprendre les erreurs du juge ---------------------------------------------

def test_parallel_rejudging_gives_the_same_report_as_sequential(privacy):
    summary = lambda report: [(r["check_id"], r["status"], r["explanation"]) for r in report["results"]]  # noqa: E731
    sequential = rejudge_report(privacy, Always(Status.PASS))
    parallel = rejudge_report(privacy, Always(Status.PASS), concurrency=6)
    assert summary(sequential) == summary(parallel)
    assert parallel["metadata"]["rejudged"]["concurrency"] == 6 and verify(parallel)[0]


def test_cases_whose_judge_failed_are_rejudged_and_their_error_is_cleared(privacy):
    def judge_failed(results):
        victim = next(r for r in results if r["pillar"] == "privacy")
        victim["status"] = "human_review"
        victim["explanation"] = "Erreur du juge : TimeoutError"
        victim["tags"]["error"] = "judge"
        judge_failed.id = victim["check_id"]

    broken = forge(privacy, judge_failed)
    updated = rejudge_report(broken, Always(Status.PASS))
    fixed = next(r for r in updated["results"] if r["check_id"] == judge_failed.id)
    assert fixed["status"] == "pass" and "error" not in fixed["tags"] and fixed["tags"]["rejudged"]["from_status"] == "human_review"
    assert updated["stats"]["totals"]["technical_errors"] == 0


def test_cases_where_the_chatbot_never_answered_are_still_left_alone(privacy):
    def no_answer(results):
        victim = next(r for r in results if r["pillar"] == "privacy")
        victim.update(status="human_review", response="")
        victim["tags"]["error"] = "truncated"

    updated = rejudge_report(forge(privacy, no_answer), Always(Status.PASS))
    assert sum(1 for r in updated["results"] if r["tags"].get("error") == "truncated") == 1


def test_rejudge_progress_counts_the_conversations_to_judge(privacy):
    seen = []
    rejudge_report(privacy, Always(Status.PASS), progress=lambda done, total: seen.append((done, total)), concurrency=3)
    total = len(interrogation(privacy))
    assert seen[0] == (1, total) and seen[-1] == (total, total) and len(seen) == total


def test_cli_rejudge_concurrency_is_validated_and_used(tmp_path):
    report = tmp_path / "a.json"
    main(["audit", "--adapter", "demo-good", "--pillars", "consent", "--limit", "4", "--out", str(report), "--quiet"])
    assert main(["rejudge", str(report), "--concurrency", "0", "--out", str(tmp_path / "x.json"), "--quiet"]) == 64
    assert main(["rejudge", str(report), "--concurrency", "4", "--out", str(tmp_path / "y.json"), "--quiet"]) in (0, 1, 2)
    assert read_json(tmp_path / "y.json")["metadata"]["rejudged"]["concurrency"] == 4
