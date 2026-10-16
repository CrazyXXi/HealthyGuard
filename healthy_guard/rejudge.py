"""Refaire juger les conversations d'un rapport par un autre juge, sans rappeler le chatbot.

Un audit appelle le chatbot (lent, plafonné, parfois payant) ; le jugement ne dépend que des conversations
enregistrées. On peut donc auditer une fois avec les règles (gratuit), puis affiner avec un jury LLM, ou
changer de juge après coup. Le verdict d'origine reste dans le résultat (`tags.rejudged`), le rapport garde
l'empreinte de son parent, et la décision globale est recalculée.

Un cas dont la conversation n'existe pas (erreur technique) n'est pas rejugé. Un cas que le nouveau juge n'a
pas pu juger (quota, réseau) GARDE son verdict d'origine et est signalé : on ne remplace jamais un verdict
valable par une erreur.
"""
from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from healthy_guard.calibration import find_case
from healthy_guard.checks.accountability import now_iso
from healthy_guard.checks.base import MAX_CONCURRENCY
from healthy_guard.judges.base import Judge
from healthy_guard.models import Pillar
from healthy_guard.policy import decide_with_reasons
from healthy_guard.reporting.json_report import report_from_dict, report_to_dict, verify


def probe_messages(conversation: list[dict]) -> list[dict]:
    """La conversation sondée : tout ce qui suit la dernière rupture de session (canaris)."""
    start = max((i for i, m in enumerate(conversation) if m["role"] == "session"), default=-1) + 1
    return conversation[start:]


# Erreurs où le CHATBOT n'a rien répondu d'exploitable : il n'y a rien à rejuger, il faut relancer l'audit.
# (Une erreur du JUGE, elle, laisse une réponse intacte : on la rejuge.)
NO_ANSWER = ("chatbot", "truncated")


def rejudge_report(report: dict, judge: Judge, statuses: set[str] | None = None,
                   progress: Callable[[int, int], None] | None = None, concurrency: int = 1) -> dict:
    """Retourne un NOUVEAU rapport ; l'original n'est pas modifié. `statuses` limite aux verdicts d'origine donnés.

    `concurrency` > 1 fait juger plusieurs conversations en même temps (utile avec un serveur qui accepte des requêtes
    simultanées) ; l'ordre et le contenu du résultat sont identiques à la version séquentielle.
    """
    if not 1 <= concurrency <= MAX_CONCURRENCY:
        raise ValueError(f"concurrency doit être entre 1 et {MAX_CONCURRENCY}.")
    ok, message = verify(report)
    if not ok:
        raise ValueError(f"Le rapport d'origine n'est pas intègre : {message}")
    report = copy.deepcopy(report)

    todo = [r for r in report["results"]
            if r["pillar"] != Pillar.ACCOUNTABILITY.value and r["tags"].get("error") not in NO_ANSWER
            and (statuses is None or r["status"] in statuses)]
    jobs, unresolved = [], 0
    for result in todo:
        case = find_case(result["check_id"].split("~")[0])
        if case is None or not result["conversation"]:
            unresolved += 1                      # cas personnalisé (--extra-cases) : ses règles ne sont pas dans le package
        else:
            jobs.append((result, {**case, "pillar": result["pillar"]}))

    def judge_one(job):
        result, case = job
        try:
            return judge.judge_transcript(case, probe_messages(result["conversation"])), None
        except Exception as error:
            return None, error

    changed = unchanged = failed = 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        outcomes = pool.map(judge_one, jobs) if concurrency > 1 else map(judge_one, jobs)
        for done, ((result, _), (verdict, error)) in enumerate(zip(jobs, outcomes), 1):
            if error is not None:
                failed += 1
                result["tags"]["rejudge_error"] = f"{type(error).__name__}: {error}"[:200]
            else:
                before = result["status"]
                result["tags"]["rejudged"] = {"from_status": before, "from_explanation": result["explanation"]}
                result["tags"].pop("error", None)          # un cas dont le juge avait échoué est maintenant jugé
                result["tags"].pop("rejudge_error", None)
                if verdict.panel:
                    result["tags"]["panel"] = verdict.panel
                result["status"], result["explanation"], result["evidence"] = (
                    verdict.status.value, verdict.explanation, verdict.evidence)
                changed += before != verdict.status.value
                unchanged += before == verdict.status.value
            if progress:
                progress(done, len(jobs))

    rebuilt = report_from_dict(report)
    rebuilt.metadata["judge_before_rejudge"] = report["metadata"].get("judge")
    rebuilt.metadata["judge"] = judge.describe()
    rebuilt.metadata["parent_report_sha256"] = report["integrity"]["results_sha256"]
    rebuilt.metadata["rejudged"] = {"at": now_iso(), "examined": len(todo), "verdict_changed": changed,
                                    "verdict_confirmed": unchanged, "unresolved_cases": unresolved,
                                    "judge_failures_kept_original": failed, "concurrency": concurrency}
    limit = rebuilt.metadata.get("settings", {}).get("max_major_fails", 3)
    rebuilt.decision, rebuilt.metadata["decision_reasons"] = decide_with_reasons(rebuilt.results, limit)
    return report_to_dict(rebuilt)
