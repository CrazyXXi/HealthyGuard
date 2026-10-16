from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Status(Enum):
    PASS = "pass"
    FAIL = "fail"
    WARNING = "warning"
    HUMAN_REVIEW = "human_review"


class Pillar(Enum):
    CLINICAL_SAFETY = "clinical_safety"
    TRANSPARENCY = "transparency"
    EQUITY = "equity"
    PRIVACY = "privacy"
    ACCOUNTABILITY = "accountability"
    CONSENT = "consent"


class Severity(Enum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


class Decision(Enum):
    PASS = "pass"
    REVIEW_REQUIRED = "review_required"
    BLOCKED = "blocked"


@dataclass
class CheckResult:
    check_id: str
    pillar: Pillar
    status: Status
    severity: Severity
    prompt: str            # ce qu'on a demandé
    response: str          # ce que le chatbot a répondu
    expected: str          # ce qu'il aurait dû faire
    explanation: str       # ce qu'il a mal fait, en une phrase
    evidence: list[str] = field(default_factory=list)  # extraits qui prouvent le problème
    score: float | None = None       # 0.0 à 1.0 si le contrôle est gradué
    latency_ms: float | None = None
    # Traçabilité et statistiques
    tags: dict = field(default_factory=dict)  # category, style, group, profile_attribute...
    repeat: int = 0                           # numéro de répétition (0 = première exécution)
    conversation: list[dict] = field(default_factory=list)  # tous les messages échangés


@dataclass
class AuditReport:
    results: list[CheckResult] = field(default_factory=list)
    decision: Decision | None = None
    metadata: dict = field(default_factory=dict)  # version du chatbot, paramètres, date...
    # Les statistiques ne sont jamais saisies à la main : elles se calculent à partir des résultats
    # (voir healthy_guard.stats.compute_stats).
