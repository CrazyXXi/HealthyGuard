"""Rapport JSON : complet, vérifiable (empreinte d'intégrité) et relisible."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from healthy_guard.models import (AuditReport, CheckResult, Decision, Pillar, Severity,
                                  Status)
from healthy_guard.stats import compute_stats

SCHEMA_VERSION = 1


def result_to_dict(r: CheckResult) -> dict:
    return {
        "check_id": r.check_id, "pillar": r.pillar.value, "status": r.status.value,
        "severity": r.severity.value, "repeat": r.repeat, "tags": r.tags,
        "prompt": r.prompt, "conversation": r.conversation, "response": r.response,
        "expected": r.expected, "explanation": r.explanation, "evidence": r.evidence,
        "score": r.score, "latency_ms": r.latency_ms,
    }


def result_from_dict(d: dict) -> CheckResult:
    return CheckResult(
        check_id=d["check_id"], pillar=Pillar(d["pillar"]), status=Status(d["status"]),
        severity=Severity(d["severity"]), prompt=d["prompt"], response=d["response"],
        expected=d["expected"], explanation=d["explanation"], evidence=d.get("evidence", []),
        score=d.get("score"), latency_ms=d.get("latency_ms"), tags=d.get("tags", {}),
        repeat=d.get("repeat", 0), conversation=d.get("conversation", []),
    )


def results_sha256(results: list[dict]) -> str:
    """Empreinte d'une liste de résultats (JSON canonique : même contenu => même hash)."""
    canonical = json.dumps(results, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def report_to_dict(report: AuditReport, extras: dict | None = None) -> dict:
    """`extras` (recommandations, revue du prompt, diagnostic de connexion) s'ajoutent au rapport. L'empreinte d'intégrité ne couvre QUE les
    résultats : ce sont eux qui font foi ; les recommandations en sont déduites et peuvent être régénérées (`recommend`)."""
    results = [result_to_dict(r) for r in report.results]
    data = {
        "schema_version": SCHEMA_VERSION,
        "decision": report.decision.value if report.decision else None,
        "metadata": report.metadata,
        "stats": compute_stats(report),
        "results": results,
        "integrity": {"algorithm": "sha256", "results_sha256": results_sha256(results)},
    }
    data.update(extras or {})
    return data


def report_from_dict(data: dict) -> AuditReport:
    decision = data.get("decision")
    return AuditReport(
        results=[result_from_dict(r) for r in data["results"]],
        decision=Decision(decision) if decision else None,
        metadata=data.get("metadata", {}),
    )


def write_json(report: AuditReport, path: str | Path, extras: dict | None = None) -> dict:
    data = report_to_dict(report, extras)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def read_json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def verify(data: dict) -> tuple[bool, str]:
    """Vrai si les résultats n'ont pas été modifiés depuis l'écriture du rapport."""
    integrity = data.get("integrity", {})
    expected = integrity.get("results_sha256")
    if not expected:
        return False, "Aucune empreinte d'intégrité dans le rapport."
    actual = results_sha256(data.get("results", []))
    if actual != expected:
        return False, f"Empreinte différente : attendue {expected[:12]}…, calculée {actual[:12]}…"
    return True, f"Rapport intègre (sha256 {expected[:12]}…)."
