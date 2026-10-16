"""Revue humaine : le juge final.

Les cas que les juges automatiques ne peuvent pas trancher (statut `human_review`) sont exportés
dans une file ; un relecteur NOMMÉ décide « pass » ou « fail » ; la décision est appliquée au
rapport. On ne réécrit pas l'historique : le statut d'origine, le relecteur, la date et le
commentaire restent dans le résultat, et le rapport garde l'empreinte de son parent.
"""
from __future__ import annotations

import copy

from healthy_guard.checks.accountability import now_iso
from healthy_guard.models import Status
from healthy_guard.policy import decide_with_reasons
from healthy_guard.reporting.json_report import report_from_dict, report_to_dict, verify

DECISIONS = ("pass", "fail")


def export_queue(report: dict) -> list[dict]:
    """Cas à trancher par un humain (ordre : gravité décroissante), sans rien qui oriente la décision."""
    order = {"critical": 0, "major": 1, "minor": 2}
    pending = [r for r in report["results"]
               if r["status"] == Status.HUMAN_REVIEW.value and not r["tags"].get("error")]  # rien à lire sans réponse
    pending.sort(key=lambda r: (order[r["severity"]], r["check_id"]))
    return [{
        "id": f"{r['check_id']}#{r['repeat']}", "pillar": r["pillar"], "severity": r["severity"],
        "expected": r["expected"], "why_flagged": r["explanation"], "judges": r["tags"].get("panel", {}),
        "conversation": r["conversation"],
        "decision": None,        # à remplir : "pass" ou "fail"
        "reviewer": "",          # à remplir : nom du relecteur (obligatoire : on doit savoir qui a décidé)
        "comment": "",
    } for r in pending]


def apply_reviews(report: dict, reviews: list[dict], max_major_fails: int | None = None) -> dict:
    """Applique les décisions humaines ; retourne un NOUVEAU rapport (dictionnaire) recalculé."""
    ok, message = verify(report)
    if not ok:
        raise ValueError(f"Le rapport d'origine n'est pas intègre : {message}")
    report = copy.deepcopy(report)          # le rapport d'origine n'est jamais modifié

    by_id = {f"{r['check_id']}#{r['repeat']}": r for r in report["results"]}
    decided: dict[str, dict] = {}
    for review in reviews:
        identifier = review.get("id")
        if review.get("decision") is None:
            continue                                       # pas encore tranché : on le laisse en revue
        if identifier not in by_id:
            raise ValueError(f"Cas inconnu dans le rapport : « {identifier} ».")
        if by_id[identifier]["status"] != Status.HUMAN_REVIEW.value:
            raise ValueError(f"« {identifier} » n'est pas en revue humaine (statut {by_id[identifier]['status']}).")
        if review["decision"] not in DECISIONS:
            raise ValueError(f"« {identifier} » : décision « {review['decision']} » invalide (pass ou fail).")
        if not str(review.get("reviewer", "")).strip():
            raise ValueError(f"« {identifier} » : le nom du relecteur est obligatoire.")
        if identifier in decided:
            raise ValueError(f"« {identifier} » est décidé deux fois.")
        decided[identifier] = review

    when = now_iso()
    for identifier, review in decided.items():
        result = by_id[identifier]
        result["tags"]["human_review"] = {
            "reviewer": review["reviewer"].strip(), "decision": review["decision"],
            "comment": review.get("comment", ""), "original_status": result["status"], "reviewed_at": when,
        }
        who = review["reviewer"].strip()
        result["explanation"] = (f"Décision humaine ({who}) : {review['decision']}"
                                 f"{' — ' + review['comment'] if review.get('comment') else ''}. "
                                 f"Avant revue : {result['explanation']}")[:600]
        result["status"] = review["decision"]

    rebuilt = report_from_dict(report)
    limit = max_major_fails or report["metadata"].get("settings", {}).get("max_major_fails", 3)
    rebuilt.metadata["decision_before_review"] = report["decision"]
    rebuilt.metadata["parent_report_sha256"] = report["integrity"]["results_sha256"]
    rebuilt.decision, rebuilt.metadata["decision_reasons"] = decide_with_reasons(rebuilt.results, limit)
    rebuilt.metadata["human_reviews"] = {
        "applied": len(decided), "still_pending": sum(r.status == Status.HUMAN_REVIEW for r in rebuilt.results),
        "reviewers": sorted({r["reviewer"].strip() for r in decided.values()}), "applied_at": when,
    }
    return report_to_dict(rebuilt)
