from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from healthy_guard.models import Status


@dataclass
class Verdict:
    status: Status
    explanation: str
    evidence: list[str] = field(default_factory=list)
    panel: dict = field(default_factory=dict)  # jury : {nom du juge: statut}, pour savoir qui a dit quoi


class Judge(ABC):
    """Lit la réponse d'un chatbot à un cas de test et rend un verdict.

    Le contrôle (quoi demander) est séparé du juge (comment lire la réponse) :
    on peut changer de juge sans toucher aux contrôles ni aux datasets.
    """

    @abstractmethod
    def judge(self, case: dict, response: str) -> Verdict:
        ...

    def judge_transcript(self, case: dict, messages: list[dict]) -> Verdict:
        """Juge une conversation entière (mode adaptatif). Par défaut : la dernière réponse seule."""
        replies = [m["content"] for m in messages if m["role"] == "assistant"]
        return self.judge(case, replies[-1] if replies else "")

    def describe(self) -> dict:
        """Identité du juge, enregistrée dans le rapport (traçabilité)."""
        return {"judge": type(self).__name__}
