"""Profils de juges : plusieurs modèles, chacun avec son propre serveur et son propre jeton.

Un jury peut mêler un modèle hébergé chez vous (GLM sur Modal) et un service (Gemini) : chacun a son adresse, sa
variable de jeton et ses paramètres. Ils se déclarent dans un fichier JSON (sans secret : seulement le NOM de la variable
d'environnement qui contient le jeton) :

    {"glm":    {"provider": "openai", "model": "zai-org/GLM-5.3-Flash",
                "base_url": "https://mon-endpoint.modal.direct", "api_key_env": "MODAL_TOKEN",
                "extra_body": {"reasoning_effort": "low"}, "max_tokens": 2048},
     "gemini": {"provider": "openai", "model": "gemini-3.1-flash-lite",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "api_key_env": "GEMINI_API_KEY"}}

puis `--judges-config juges.json --judge-panel keyword,glm,gemini`.
"""
from __future__ import annotations

import json
from pathlib import Path

from healthy_guard.adapters.base import ChatbotAdapter
from healthy_guard.adapters.web import AnthropicAdapter, OpenAICompatibleAdapter

ALLOWED = {"provider", "model", "base_url", "api_key_env", "extra_body", "max_tokens", "temperature"}
PROVIDERS = ("anthropic", "openai")


def validate_profile(name: str, profile: object) -> dict:
    if not isinstance(profile, dict):
        raise ValueError(f"Profil « {name} » : un objet JSON est attendu.")
    unknown = set(profile) - ALLOWED
    if unknown:
        raise ValueError(f"Profil « {name} » : champ(s) inconnu(s) {', '.join(sorted(unknown))} "
                         f"(autorisés : {', '.join(sorted(ALLOWED))}).")
    if profile.get("provider") not in PROVIDERS:
        raise ValueError(f"Profil « {name} » : « provider » doit valoir {' ou '.join(PROVIDERS)}.")
    if not profile.get("model"):
        raise ValueError(f"Profil « {name} » : « model » est requis.")
    if profile.get("extra_body") is not None and profile["provider"] != "openai":
        raise ValueError(f"Profil « {name} » : « extra_body » n'est géré qu'avec provider « openai ».")
    if profile.get("extra_body") is not None and not isinstance(profile["extra_body"], dict):
        raise ValueError(f"Profil « {name} » : « extra_body » doit être un objet JSON.")
    return profile


def load_profiles(path: str | Path) -> dict[str, dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} n'est pas un JSON valide : {error}") from None
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path} doit contenir un objet {{nom: profil}} non vide.")
    return {name: validate_profile(name, profile) for name, profile in data.items()}


def adapter_from_profile(profile: dict, net: dict | None = None) -> ChatbotAdapter:
    """Construit l'adaptateur du profil. Le juge est déterministe par défaut (température 0)."""
    kwargs = {"model": profile["model"], "temperature": profile.get("temperature", 0.0), **(net or {})}
    for source, target in (("base_url", "base_url"), ("max_tokens", "max_tokens")):
        if profile.get(source) is not None:
            kwargs[target] = profile[source]
    if "api_key_env" in profile:                           # null explicite = aucune clé (juge local) ; absent = règle par défaut
        kwargs["api_key_env"] = profile["api_key_env"]
    if profile["provider"] == "anthropic":
        return AnthropicAdapter(**kwargs)
    if profile.get("extra_body"):
        kwargs["extra_body"] = profile["extra_body"]
    return OpenAICompatibleAdapter(**kwargs)
