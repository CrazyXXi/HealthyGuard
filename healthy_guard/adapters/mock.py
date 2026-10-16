from __future__ import annotations

from healthy_guard.adapters.base import ChatbotAdapter, ChatResponse


class MockChatbot(ChatbotAdapter):
    """Faux chatbot pour tester l'auditeur sans réseau.

    `rules` associe un mot-clé (cherché dans le dernier message de
    l'utilisateur) à une réponse. Sans correspondance, `default` est renvoyé.
    """

    def __init__(self, rules: dict[str, str] | None = None,
                 default: str = "Je ne sais pas."):
        self.rules = rules or {}
        self.default = default
        self.calls: list[list[dict]] = []  # historique, utile pour les tests

    def describe(self) -> dict:
        return {"adapter": "MockChatbot", "mock": True, "rules": len(self.rules)}

    def send(self, messages: list[dict]) -> ChatResponse:
        self.calls.append([dict(m) for m in messages])  # copie : la liste de l'appelant continue d'évoluer
        last = messages[-1]["content"].lower()
        for keyword, answer in self.rules.items():
            if keyword.lower() in last:
                return ChatResponse(answer, latency_ms=0.0, metadata={"mock": True})
        return ChatResponse(self.default, latency_ms=0.0, metadata={"mock": True})
