"""Profils de chatbots : décrire n'importe quel chatbot dans un fichier JSON, sans secret.

    {"gemini": {"adapter": "openai", "model": "gemini-3.1-flash-lite", "api_key_env": "GEMINI_API_KEY",
                "base_url": "https://generativelanguage.googleapis.com/v1beta/openai"},
     "rasa":   {"adapter": "rest", "url": "http://localhost:5005/webhooks/rest/webhook",
                "body_template": {"sender": "{{session}}", "message": "{{message}}"}, "response_path": "*.text",
                "headers_env": {"X-Api-Key": "RASA_KEY"}},
     "interne": {"adapter": "python", "callable": "mon_bot.py:repondre"}}

puis `--chatbot-config bots.json --chatbot rasa`. Les secrets ne figurent jamais dans le fichier : seulement le NOM de la variable
d'environnement (`api_key_env`, `headers_env`).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from healthy_guard.adapters.base import ChatbotAdapter
from healthy_guard.adapters.plugin import CommandAdapter, load_callable, load_plugin
from healthy_guard.adapters.web import (AnthropicAdapter, HttpJsonAdapter, OpenAICompatibleAdapter, RestBotAdapter)

COMMON = {"adapter"}
KEYS = {
    "openai": {"base_url", "model", "api_key_env", "system", "temperature", "max_tokens", "extra_body"},
    "anthropic": {"base_url", "model", "api_key_env", "system", "temperature", "max_tokens"},
    "http": {"url", "headers", "headers_env", "request_field", "response_path", "extra_body"},
    "rest": {"url", "body_template", "response_path", "headers", "headers_env", "method", "body_format"},
    "python": {"callable"},
    "command": {"command", "input_mode"},
    "plugin": {"class", "args"},
}
REQUIRED = {"openai": {"model"}, "anthropic": {"model"}, "http": {"url"}, "rest": {"url", "body_template", "response_path"},
            "python": {"callable"}, "command": {"command"}, "plugin": {"class"}}


def validate_chatbot(name: str, profile: object) -> dict:
    if not isinstance(profile, dict):
        raise ValueError(f"Chatbot « {name} » : un objet JSON est attendu.")
    kind = profile.get("adapter")
    if kind not in KEYS:
        raise ValueError(f"Chatbot « {name} » : « adapter » doit valoir {', '.join(sorted(KEYS))} (reçu : {kind!r}).")
    unknown = set(profile) - KEYS[kind] - COMMON
    if unknown:
        raise ValueError(f"Chatbot « {name} » ({kind}) : champ(s) inconnu(s) {', '.join(sorted(unknown))} "
                         f"(autorisés : {', '.join(sorted(KEYS[kind]))}).")
    missing = REQUIRED[kind] - set(profile)
    if missing:
        raise ValueError(f"Chatbot « {name} » ({kind}) : champ(s) requis manquant(s) : {', '.join(sorted(missing))}.")
    return profile


def load_chatbots(path: str | Path) -> dict[str, dict]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} n'est pas un JSON valide : {error}") from None
    if not isinstance(data, dict) or not data:
        raise ValueError(f"{path} doit contenir un objet {{nom: profil}} non vide.")
    return {name: validate_chatbot(name, profile) for name, profile in data.items()}


def _headers(profile: dict) -> dict:
    headers = dict(profile.get("headers") or {})
    for header, variable in (profile.get("headers_env") or {}).items():
        value = os.environ.get(variable)
        if not value:
            raise ValueError(f"Variable d'environnement {variable} absente ou vide (en-tête {header}).")
        headers[header] = value
    return headers


def build_chatbot(profile: dict, net: dict | None = None) -> ChatbotAdapter:
    """Construit l'adaptateur d'un profil. `net` : réglages réseau (max_retries, backoff, min_interval, timeout)."""
    validate_chatbot(profile.get("model") or profile.get("url") or profile.get("callable") or "?", profile)
    kind, net = profile["adapter"], dict(net or {})
    if kind in ("openai", "anthropic"):
        kwargs = {"model": profile["model"], "system": profile.get("system"), "temperature": profile.get("temperature", 0.0), **net}
        for key in ("base_url", "max_tokens"):
            if profile.get(key) is not None:
                kwargs[key] = profile[key]
        if "api_key_env" in profile:                       # null explicite = aucune clé ; absent = règle par défaut de l'adaptateur
            kwargs["api_key_env"] = profile["api_key_env"]
        if kind == "anthropic":
            return AnthropicAdapter(**kwargs)
        if profile.get("extra_body"):
            kwargs["extra_body"] = profile["extra_body"]
        return OpenAICompatibleAdapter(**kwargs)
    if kind == "http":
        return HttpJsonAdapter(profile["url"], headers=_headers(profile), request_field=profile.get("request_field", "messages"),
                               response_path=profile.get("response_path", "reply"), extra_body=profile.get("extra_body"), **net)
    if kind == "rest":
        return RestBotAdapter(profile["url"], profile["body_template"], profile["response_path"], headers=_headers(profile),
                              method=profile.get("method", "POST"), body_format=profile.get("body_format", "json"), **net)
    if kind == "python":
        return load_callable(profile["callable"])
    if kind == "command":
        return CommandAdapter(profile["command"], profile.get("input_mode", "message"), timeout=net.get("timeout", 60.0))
    return load_plugin(profile["class"], profile.get("args"))

