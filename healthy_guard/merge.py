"""Fusionner plusieurs rapports d'un MÊME chatbot en un seul rapport d'audit.

Cas d'usage : un audit complet est long et les services plafonnent les appels ; on l'exécute donc par
morceaux (un rapport par groupe de piliers, éventuellement en parallèle) puis on relance les cas qui ont
échoué techniquement. La fusion garantit qu'on ne mélange pas des choses incomparables :
même chatbot, même juge, mêmes jeux de données (empreintes identiques).

Règles :
- un cas présent dans plusieurs rapports : la réponse obtenue remplace une erreur technique ; sinon le
  premier rapport l'emporte ;
- les contrôles de traçabilité (pilier Responsabilité) sont RECALCULÉS sur le rapport fusionné ;
- la décision globale est recalculée ; l'empreinte de chaque rapport source est conservée.
"""
from __future__ import annotations

from healthy_guard.checks import accountability
from healthy_guard.models import Pillar
from healthy_guard.policy import decide_with_reasons
from healthy_guard.reporting.json_report import report_from_dict, report_to_dict, verify

def _identity(chatbot: dict | None) -> dict:
    """Identité du chatbot = toute sa description (`describe()`), jamais une liste de champs choisie à la main :
    un champ oublié ferait fusionner deux chatbots différents (c'est arrivé avec le chatbot de démonstration)."""
    return dict(chatbot or {})


def merge_reports(reports: list[dict]) -> dict:
    if len(reports) < 2:
        raise ValueError("Une fusion exige au moins 2 rapports.")
    for index, report in enumerate(reports, 1):
        ok, message = verify(report)
        if not ok:
            raise ValueError(f"Le rapport n°{index} n'est pas intègre : {message}")

    first = reports[0]["metadata"]
    for index, report in enumerate(reports[1:], 2):
        meta = report["metadata"]
        if _identity(meta.get("chatbot")) != _identity(first.get("chatbot")):
            raise ValueError(f"Le rapport n°{index} porte sur un autre chatbot que le n°1 : "
                             f"{_identity(meta.get('chatbot'))} ≠ {_identity(first.get('chatbot'))}.")
        if meta.get("judge") != first.get("judge"):
            raise ValueError(f"Le rapport n°{index} a été jugé autrement que le n°1 : fusionner des verdicts de "
                             "juges différents fausserait les statistiques. Utilisez `rejudge` pour les unifier.")
        for name, info in meta.get("datasets", {}).items():
            other = first.get("datasets", {}).get(name)
            if other and other["sha256"] != info["sha256"]:
                raise ValueError(f"Le dataset « {name} » diffère entre les rapports (empreintes différentes).")

    chosen: dict[tuple, dict] = {}
    duplicates = replaced = 0
    for report in reports:
        for result in report["results"]:
            if result["pillar"] == Pillar.ACCOUNTABILITY.value:
                continue                                    # recalculé plus bas
            key = (result["pillar"], result["check_id"], result["repeat"])
            if key not in chosen:
                chosen[key] = result
                continue
            duplicates += 1
            if chosen[key]["tags"].get("error") and not result["tags"].get("error"):
                chosen[key] = result                        # une vraie réponse remplace une erreur technique
                replaced += 1

    datasets: dict[str, dict] = {}
    for report in reports:
        for name, info in report["metadata"].get("datasets", {}).items():
            merged = datasets.setdefault(name, dict(info))
            merged["cases_run"] = max(merged.get("cases_run", 0), info.get("cases_run", 0))
    pillars = list(dict.fromkeys(p for r in reports for p in r["metadata"].get("settings", {}).get("pillars", [])))
    pillars = [p for p in pillars if p != Pillar.ACCOUNTABILITY.value] + [Pillar.ACCOUNTABILITY.value]

    metadata = {
        **first,
        "datasets": datasets,
        "settings": {**first.get("settings", {}), "pillars": pillars},
        "started_at": min(r["metadata"]["started_at"] for r in reports),
        "finished_at": max(r["metadata"].get("finished_at", "") for r in reports),
        "duration_s": round(sum(r["metadata"].get("duration_s", 0) for r in reports), 2),
        "merged_from": [{
            "report_sha256": r["integrity"]["results_sha256"], "pillars": r["metadata"].get("settings", {}).get("pillars"),
            "started_at": r["metadata"].get("started_at"), "results": len(r["results"]),
        } for r in reports],
        "merge": {"duplicates_resolved": duplicates, "errors_replaced": replaced},
    }
    warnings = list(dict.fromkeys(w for r in reports for w in r["metadata"].get("warnings", [])))
    if warnings:
        metadata["warnings"] = warnings

    merged_dict = {"results": list(chosen.values()), "decision": None, "metadata": metadata}
    report = report_from_dict(merged_dict)
    expected = [Pillar(p) for p in pillars]
    report.results += accountability.check_traceability(report, expected)
    limit = metadata.get("settings", {}).get("max_major_fails", 3)
    report.decision, metadata["decision_reasons"] = decide_with_reasons(report.results, limit)
    return report_to_dict(report)
