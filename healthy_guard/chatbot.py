"""Le chatbot COMPLET : tout ce qui le définit, passé en un seul objet à l'audit.

Un chatbot de santé n'est pas seulement un modèle : c'est un modèle + un PROMPT SYSTÈME + une API (paramètres, réglages) + un usage prévu +
une langue + parfois des outils. L'audit prend l'ensemble, l'examine sous plusieurs angles (le texte du prompt, la configuration de l'API,
le comportement réel sur des centaines de scénarios), et le rapport dit CE QUI A ÉTÉ COUVERT ET CE QUI NE L'A PAS ÉTÉ : une boîte noire
ne voit ni les bases de connaissances, ni les outils, ni l'infrastructure.

    chatbot = Chatbot(adapter, system_prompt=open("prompt.txt").read(), name="Assistant santé", languages=("fr",),
                      intended_use="Informations générales de santé pour le grand public", tools=[{"name": "prise_rdv", "side_effects": True}])
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from healthy_guard.adapters.base import ChatbotAdapter
from healthy_guard.adapters.plugin import CallableAdapter, load_callable
from healthy_guard.adapters.web import AnthropicAdapter, OpenAICompatibleAdapter

DATASET_LANGUAGES = ("fr",)          # les scénarios intégrés sont écrits en français
INJECTABLE = (OpenAICompatibleAdapter, AnthropicAdapter)


@dataclass
class Chatbot:
    adapter: ChatbotAdapter
    system_prompt: str | None = None
    name: str = "chatbot"
    description: str = ""
    intended_use: str = ""
    audience: str = ""
    languages: tuple[str, ...] = ("fr",)
    tools: list[dict] = field(default_factory=list)       # [{"name": ..., "description": ..., "side_effects": True|False}]

    def audited_adapter(self) -> tuple[ChatbotAdapter, bool]:
        """L'adaptateur à interroger, avec le prompt système de production appliqué quand la connexion le permet.

        Retourne (adaptateur, prompt_transmis). Pour une API de modèle de langage, le prompt est transmis à chaque appel (copie : l'objet de
        l'appelant n'est pas modifié). Pour un chatbot classique, une fonction ou une commande, le prompt vit côté serveur : on le relit
        (revue statique) mais on ne peut pas le transmettre ; on teste alors le prompt tel que le serveur l'applique.
        """
        if self.system_prompt and isinstance(self.adapter, INJECTABLE):
            adapter = copy.copy(self.adapter)
            adapter.system = self.system_prompt
            return adapter, True
        return self.adapter, False

    def card(self) -> dict:
        from healthy_guard.promptreview import review_prompt
        review = review_prompt(self.system_prompt) if self.system_prompt else None
        return {
            "name": self.name, "description": self.description, "intended_use": self.intended_use, "audience": self.audience,
            "languages": list(self.languages), "tools": [{"name": t.get("name"), "side_effects": bool(t.get("side_effects"))} for t in self.tools],
            "system_prompt": {"provided": bool(self.system_prompt), "sha256": review.sha256 if review else None,
                              "chars": review.chars if review else 0},
        }

    def scope(self, prompt_transmitted: bool) -> dict:
        """Ce que l'audit a couvert, ce qu'il n'a PAS couvert, et les limites de ses conclusions."""
        covered = [
            "Comportement observé du chatbot (ses réponses) sur cinq piliers : sécurité clinique, transparence, équité, confidentialité, consentement.",
            "Fiabilité de l'API observée pendant l'audit : erreurs, saturation, latence, réponses coupées.",
        ]
        if self.system_prompt:
            covered.append("Revue du texte du prompt système fourni (garde-fous présents ou absents, instructions dangereuses).")
            covered.append("Prompt système appliqué à chaque test." if prompt_transmitted else
                           "Prompt système relu, mais NON transmis par ce type de connexion : on suppose qu'il est identique à celui du serveur.")
        not_covered = [
            "Contenu des bases de connaissances et des sources (RAG) : seul leur effet sur les réponses est observé.",
            "Garde-fous situés hors du modèle (filtres en amont ou en aval) : vus seulement par leurs effets.",
            "Journalisation, durée de conservation, sécurité de l'infrastructure, hébergement : conformité RGPD, dispositif médical, "
            "à examiner par ailleurs.",
            "Qualité clinique détaillée des conseils : une relecture par un professionnel de santé reste indispensable.",
            "Utilisateurs réels et populations particulières : les scénarios sont écrits, pas observés.",
        ]
        limits = []
        if getattr(self.adapter, "manual", False):
            limits.append("Mode manuel (sans API) : les réponses ont été collées à la main depuis une interface. L'audit suppose qu'elles sont "
                          "fidèles ; le prompt système et les réglages de l'interface ne sont pas contrôlés ; l'échantillon est petit.")
        if not self.system_prompt:
            limits.append("Aucun prompt système fourni : l'audit vise le modèle brut, pas votre produit.")
        side_effects = [t["name"] for t in self.tools if t.get("side_effects")]
        if self.tools:
            not_covered.insert(1, "Outils et actions déclenchés : " + (", ".join(t.get("name", "?") for t in self.tools)) + " (non testés).")
        if side_effects:
            limits.append("Outils avec effets de bord déclarés (" + ", ".join(side_effects) + ") : une boîte noire ne vérifie pas leurs "
                          "confirmations ni leurs autorisations.")
        foreign = [lang for lang in self.languages if lang not in DATASET_LANGUAGES]
        if foreign:
            limits.append("Langues déclarées non couvertes par les scénarios intégrés (français uniquement) : " + ", ".join(foreign)
                          + ". Les verdicts ne disent rien du comportement dans ces langues.")
        return {"covered": covered, "not_covered": not_covered, "limits": limits}


def as_chatbot(thing, system_prompt: str | None = None, system_prompt_file: str | None = None, **card) -> Chatbot:
    """Normalise ce qu'on passe à l'audit : un Chatbot, un adaptateur, une fonction Python, un profil (dict) ou « fichier.py:fonction »."""
    if system_prompt is None and system_prompt_file:
        system_prompt = Path(system_prompt_file).read_text(encoding="utf-8-sig")
    if isinstance(thing, Chatbot):
        if system_prompt and not thing.system_prompt:
            thing = copy.copy(thing)
            thing.system_prompt = system_prompt
        return thing
    if isinstance(thing, ChatbotAdapter):
        adapter = thing
    elif isinstance(thing, dict):
        from healthy_guard.adapters.config import build_chatbot
        adapter = build_chatbot(thing)
    elif isinstance(thing, str):
        adapter = load_callable(thing)
    elif callable(thing):
        adapter = CallableAdapter(thing)
    else:
        raise TypeError("Un chatbot est : un objet Chatbot, un ChatbotAdapter, une fonction fn(messages) -> texte, un profil (dict) "
                        f"ou « fichier.py:fonction » (reçu : {type(thing).__name__}).")
    return Chatbot(adapter, system_prompt=system_prompt, **card)
