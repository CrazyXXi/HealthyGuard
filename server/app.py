"""Passerelle du juge HealthyGuard : le SEUL endroit qui connaît le vrai modèle, son adresse et ses identifiants.

Le package installé par les utilisateurs ne contient ni jeton, ni nom de modèle, ni adresse de serveur d'inférence : il parle à cette passerelle, qui relaie vers le
modèle hébergé en ajoutant les identifiants côté serveur. Publier les identifiants dans un package public reviendrait à les donner à tout le monde.

Variables d'environnement (à définir chez l'hébergeur, JAMAIS dans le code ni dans git) :

    UPSTREAM_URL      adresse du modèle hébergé, sans /v1 à la fin          (obligatoire)
    UPSTREAM_TOKEN    identifiant.secret du modèle                          (obligatoire)
    UPSTREAM_MODEL    nom exact du modèle chez le serveur d'inférence      (obligatoire)
    ALLOWED_KEYS      clés d'accès HealthyGuard acceptées, séparées par des virgules (facultatif : sans elle, le service est ouvert)
    RATE_PER_MINUTE   requêtes par minute et par adresse IP (défaut : 60)
    MAX_TOKENS        plafond de longueur de réponse demandée (défaut : 2048)
    REASONING_EFFORT  réglage du raisonnement transmis au modèle (défaut : low ; vide pour ne rien envoyer)

La passerelle n'enregistre AUCUN contenu : les conversations d'un audit peuvent contenir des données de santé.
Lancer en local : uvicorn app:app --port 8000
"""
from __future__ import annotations

import os
import threading
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

ALIAS = "healthyguard-judge"                  # le seul nom de modèle que voient les utilisateurs et les rapports
MAX_BODY_BYTES = 200_000
UPSTREAM_TIMEOUT = 55                         # secondes : sous la durée maximale d'une fonction Vercel (60 s)

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
_hits: dict[str, list[float]] = {}
_lock = threading.Lock()


def _setting(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or value == "" and default is None:
        raise HTTPException(503, "Le juge est momentanément indisponible.")        # configuration incomplète : rien n'est révélé au client
    return value.strip().strip('"').strip("'")


def _allowed_keys() -> set[str]:
    return {k.strip() for k in os.environ.get("ALLOWED_KEYS", "").split(",") if k.strip()}


def _limited(who: str) -> bool:
    """Limite par adresse IP. En mémoire : sur un hébergement « serverless », chaque instance a son compteur (limite indicative)."""
    limit = int(os.environ.get("RATE_PER_MINUTE", "60"))
    now = time.monotonic()
    with _lock:
        recent = [t for t in _hits.get(who, []) if now - t < 60]
        recent.append(now)
        _hits[who] = recent
        if len(_hits) > 5000:                                   # ne pas grossir sans fin
            for key in [k for k, v in _hits.items() if not v or now - v[-1] >= 60]:
                del _hits[key]
        return len(recent) > limit


def _client_id(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return (forwarded.split(",")[0].strip() or (request.client.host if request.client else "?"))


# Les hébergeurs « serverless » réécrivent parfois le chemin (/api/index...) : la passerelle n'a qu'un rôle par méthode, donc elle ne dépend pas du chemin.
@app.get("/{path:path}")
async def health(path: str = ""):
    return {"status": "ok"}


@app.post("/{path:path}")
async def completions(request: Request, path: str = ""):
    keys = _allowed_keys()
    if keys and request.headers.get("authorization", "").removeprefix("Bearer ").strip() not in keys:
        raise HTTPException(401, "Clé HealthyGuard requise ou invalide.")
    if _limited(_client_id(request)):
        raise HTTPException(429, "Trop de requêtes : réessayez dans une minute.")

    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(413, "Requête trop volumineuse.")
    try:
        body = await request.json()
        messages = body["messages"]
        if not isinstance(messages, list) or not messages:
            raise ValueError
        temperature = min(max(float(body.get("temperature", 0.0)), 0.0), 1.0)
        max_tokens = min(int(body.get("max_tokens", 2048)), int(os.environ.get("MAX_TOKENS", "2048")))
    except (ValueError, KeyError, TypeError):
        raise HTTPException(400, "Requête invalide.") from None

    payload = {"model": _setting("UPSTREAM_MODEL"), "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
    effort = os.environ.get("REASONING_EFFORT", "low").strip()
    if effort:
        payload["reasoning_effort"] = effort
    upstream = _setting("UPSTREAM_URL").rstrip("/")
    upstream = upstream[:-3] if upstream.endswith("/v1") else upstream
    headers = {"Authorization": "Bearer " + _setting("UPSTREAM_TOKEN")}

    try:
        async with httpx.AsyncClient(timeout=UPSTREAM_TIMEOUT) as client:
            reply = await client.post(f"{upstream}/v1/chat/completions", json=payload, headers=headers)
    except httpx.HTTPError:
        return JSONResponse({"error": {"message": "Le juge est momentanément indisponible.", "code": 503}}, status_code=503)

    try:
        data = reply.json()
    except ValueError:
        data = None
    if reply.status_code != 200 or not isinstance(data, dict):
        code = 503 if reply.status_code >= 500 or data is None else (429 if reply.status_code == 429 else 502)
        return JSONResponse({"error": {"message": "Le juge est momentanément indisponible.", "code": code}}, status_code=code)

    data["model"] = ALIAS                                      # le vrai nom du modèle ne sort jamais
    for key in ("system_fingerprint", "provider", "service_tier"):
        data.pop(key, None)
    return JSONResponse(data)
