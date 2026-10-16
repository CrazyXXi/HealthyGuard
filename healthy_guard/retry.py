"""Relancer uniquement les cas qui n'ont reçu aucune réponse (erreur technique) puis les fusionner.

Un audit long se heurte aux quotas et aux pannes passagères (HTTP 429/503). Plutôt que de tout refaire, on ne
relance que les cas marqués en erreur ; `merge` remplace l'erreur par la vraie réponse (ou garde l'erreur si le
cas échoue encore), recalcule la traçabilité et la décision.
"""
from __future__ import annotations

from healthy_guard.adapters.base import ChatbotAdapter
from healthy_guard.auditor import Auditor
from healthy_guard.judges.base import Judge
from healthy_guard.merge import merge_reports
from healthy_guard.models import Pillar
from healthy_guard.reporting.json_report import report_to_dict, verify


def errored_ids(report: dict) -> dict[Pillar, set[str]]:
    ids: dict[Pillar, set[str]] = {}
    for result in report["results"]:
        if result["pillar"] != Pillar.ACCOUNTABILITY.value and result["tags"].get("error"):
            ids.setdefault(Pillar(result["pillar"]), set()).add(result["check_id"])
    return ids


def retry_errors(original: dict, adapter: ChatbotAdapter, judge: Judge, **auditor_kwargs) -> tuple[dict, int]:
    """Retourne (rapport fusionné, nombre de cas relancés). Le rapport d'origine n'est pas modifié."""
    ok, message = verify(original)
    if not ok:
        raise ValueError(f"Le rapport d'origine n'est pas intègre : {message}")
    ids = errored_ids(original)
    count = sum(len(v) for v in ids.values())
    if not count:
        return original, 0

    settings = original["metadata"].get("settings", {})
    auditor = Auditor(
        adapter, judge, pillars=list(ids) + [Pillar.ACCOUNTABILITY], only_ids=ids,
        repeats=settings.get("repeats", 1), max_major_fails=settings.get("max_major_fails", 3), **auditor_kwargs)
    retried = report_to_dict(auditor.run())
    merged = merge_reports([original, retried])      # l'original d'abord : une erreur y est remplacée par la vraie réponse
    still = sum(1 for r in merged["results"] if r["tags"].get("error"))
    merged["metadata"]["retry"] = {"cases_retried": count, "still_without_answer": still}
    return merged, count
