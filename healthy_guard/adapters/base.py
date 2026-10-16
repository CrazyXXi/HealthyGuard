from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ChatResponse:
    text: str
    latency_ms: float | None = None
    metadata: dict = field(default_factory=dict)  # modèle, version, paramètres...


class ChatbotAdapter(ABC):
    """Contrat commun : l'auditeur ne connaît que cette interface.

    `messages` est la conversation complète, au format
    [{"role": "user" | "assistant", "content": "..."}].
    """

    @abstractmethod
    def send(self, messages: list[dict]) -> ChatResponse:
        ...

    def describe(self) -> dict:
        """Identité et paramètres du chatbot, enregistrés dans le rapport (traçabilité).

        Ne jamais y mettre de secret (clé d'API, jeton).
        """
        return {"adapter": type(self).__name__}
