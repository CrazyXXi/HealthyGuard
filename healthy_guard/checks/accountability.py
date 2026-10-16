"""Pilier Responsabilité & Traçabilité.

Contrairement aux autres piliers, celui-ci n'interroge pas le chatbot : il vérifie que
l'AUDIT lui-même est complet et vérifiable (versions, paramètres, preuves, empreintes).
Chaque décision ou erreur du chatbot doit pouvoir être retrouvée et expliquée.
"""
from __future__ import annotations

import platform
import sys
from datetime import datetime, timezone

import healthy_guard
from healthy_guard.adapters.base import ChatbotAdapter
from healthy_guard.datasets import dataset_sha256, load_dataset
from healthy_guard.judges.base import Judge
from healthy_guard.models import AuditReport, CheckResult, Pillar, Severity, Status

PILLAR = Pillar.ACCOUNTABILITY

REQUIRED_METADATA = (
    "healthy_guard_version", "started_at", "chatbot", "judge", "datasets", "settings",
)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def build_metadata(adapter: ChatbotAdapter, judge: Judge, dataset_names: list[str],
                   settings: dict) -> dict:
    """Rassemble tout ce qu'il faut pour reproduire et auditer l'exécution."""
    return {
        "healthy_guard_version": healthy_guard.__version__,
        "started_at": now_iso(),
        "chatbot": adapter.describe(),
        "judge": judge.describe(),
        "datasets": {
            name: {"cases": len(load_dataset(name)), "sha256": dataset_sha256(name)}
            for name in dataset_names
        },
        "settings": settings,
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
    }


def _result(check_id: str, status: Status, severity: Severity, expected: str,
            explanation: str, evidence: list[str] | None = None) -> CheckResult:
    return CheckResult(
        check_id=check_id, pillar=PILLAR, status=status, severity=severity,
        prompt="(contrôle interne de l'audit)", response="", expected=expected,
        explanation=explanation, evidence=evidence or [],
        tags={"category": "traceabilite", "style": "interne"},
    )


def check_traceability(report: AuditReport, expected_pillars: list[Pillar]) -> list[CheckResult]:
    """Vérifie la complétude et l'intégrité de l'audit (aucun appel au chatbot)."""
    results = []
    interrogation = [r for r in report.results if r.pillar != PILLAR]

    # 1. Métadonnées : versions, paramètres, juge, datasets.
    missing = [k for k in REQUIRED_METADATA if not report.metadata.get(k)]
    results.append(_result(
        "accountability_metadata", Status.FAIL if missing else Status.PASS, Severity.CRITICAL,
        "Le rapport consigne version, chatbot, juge, datasets et paramètres.",
        f"Métadonnées manquantes : {', '.join(missing)}." if missing
        else "Toutes les métadonnées de traçabilité sont présentes.",
        missing,
    ))

    # 2. Preuves : chaque résultat garde la conversation et une explication.
    incomplete = [r.check_id for r in interrogation
                  if not r.conversation or not r.explanation.strip()]
    results.append(_result(
        "accountability_evidence", Status.FAIL if incomplete else Status.PASS, Severity.MAJOR,
        "Chaque résultat conserve la conversation complète et une explication.",
        f"{len(incomplete)} résultat(s) sans preuve complète." if incomplete
        else "Chaque résultat est accompagné de sa conversation et de son explication.",
        incomplete[:10],
    ))

    # 3. Unicité : un même (cas, répétition) ne doit pas apparaître deux fois.
    keys = [(r.check_id, r.repeat) for r in interrogation]
    duplicates = sorted({f"{k[0]}#{k[1]}" for k in keys if keys.count(k) > 1})
    results.append(_result(
        "accountability_unique", Status.FAIL if duplicates else Status.PASS, Severity.MAJOR,
        "Chaque résultat est identifiable de façon unique.",
        f"{len(duplicates)} identifiant(s) en double." if duplicates
        else "Tous les résultats sont identifiables de façon unique.",
        duplicates[:10],
    ))

    # 4. Intégrité des datasets : l'empreinte enregistrée correspond au fichier actuel.
    changed = []
    for name, info in report.metadata.get("datasets", {}).items():
        try:
            if dataset_sha256(name) != info.get("sha256"):
                changed.append(name)
        except OSError:
            changed.append(name)
    results.append(_result(
        "accountability_datasets", Status.FAIL if changed else Status.PASS, Severity.CRITICAL,
        "Les datasets utilisés sont identiques à ceux enregistrés dans le rapport.",
        f"Dataset(s) modifié(s) ou introuvable(s) : {', '.join(changed)}." if changed
        else "Les empreintes des datasets sont conformes.",
        changed,
    ))

    # 5. Couverture : chaque pilier demandé a bien produit des résultats.
    covered = {r.pillar for r in interrogation}
    empty = [p.value for p in expected_pillars if p != PILLAR and p not in covered]
    results.append(_result(
        "accountability_coverage", Status.FAIL if empty else Status.PASS, Severity.CRITICAL,
        "Tous les piliers demandés ont été évalués.",
        f"Piliers sans résultat : {', '.join(empty)}." if empty
        else "Tous les piliers demandés ont produit des résultats.",
        empty,
    ))
    return results
