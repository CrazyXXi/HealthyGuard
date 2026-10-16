"""Passerelle du juge HealthyGuard : le SEUL endroit qui connaît le vrai modèle, son adresse et ses identifiants.

Le package installé par les utilisateurs ne contient ni jeton, ni nom de modèle, ni adresse de serveur d'inférence : il parle à cette passerelle,
qui relaie vers le modèle hébergé en ajoutant les identifiants côté serveur. Publier les identifiants dans un package public reviendrait à les donner
à tout le monde (ils seraient copiés, utilisés à vos frais, puis révoqués) ; ici ils ne quittent jamais le serveur.

Déploiement (une fois, sur Modal) :

    pip install modal
    modal secret create healthyguard-judge UPSTREAM_URL=https://<adresse du modèle> UPSTREAM_TOKEN=<clé>.<secret> UPSTREAM_MODEL=<nom du modèle>
    modal deploy server/judge_gateway.py

Modal affiche l'adresse publique de la passerelle : c'est elle (et elle seule, sans secret) qu'on met dans `HOSTED_JUDGE_URL` (healthy_guard/api.py)
ou dans la variable HEALTHYGUARD_JUDGE_URL. Facultatif : ALLOWED_KEYS="cle1,cle2" dans le secret pour exiger une clé (en-tête Authorization: Bearer).

Cette passerelle n'enregistre AUCUN contenu : les conversations d'un audit peuvent contenir des données de santé.
"""
import os
import threading
import time

import modal

ALIAS = "healthyguard-judge"                 # le seul nom de modèle que voient les utilisateurs et les rapports
MAX_TOKENS = 2048
MAX_BODY_CHARS = 60_000
RATE_PER_MINUTE = 60                          # par adresse IP

image = modal.Image.debian_slim().pip_install("fastapi[standard]", "httpx")
app = modal.App("healthyguard-judge-gateway", image=image)


@app.function(secrets=[modal.Secret.from_name("healthyguard-judge")], min_containers=0, scaledown_window=300, timeout=300)
@modal.asgi_app()
def gateway():
    import httpx
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse

    api = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    upstream = os.environ["UPSTREAM_URL"].rstrip("/")
    token = os.environ["UPSTREAM_TOKEN"]
    model = os.environ["UPSTREAM_MODEL"]
    allowed = {k.strip() for k in os.environ.get("ALLOWED_KEYS", "").split(",") if k.strip()}
    hits: dict[str, list[float]] = {}
    lock = threading.Lock()

    def limited(who: str) -> bool:
        now = time.monotonic()
        with lock:
            recent = [t for t in hits.get(who, []) if now - t < 60]
            recent.append(now)
            hits[who] = recent
            return len(recent) > RATE_PER_MINUTE

    @api.post("/v1/chat/completions")
    async def completions(request: Request):
        if allowed and request.headers.get("authorization", "").removeprefix("Bearer ").strip() not in allowed:
            raise HTTPException(401, "Clé HealthyGuard requise.")
        who = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
        if limited(who):
            raise HTTPException(429, "Trop de requêtes : réessayez dans une minute.")
        raw = await request.body()
        if len(raw) > MAX_BODY_CHARS:
            raise HTTPException(413, "Requête trop volumineuse.")
        try:
            body = await request.json()
            messages = body["messages"]
            assert isinstance(messages, list) and messages
        except Exception:
            raise HTTPException(400, "Requête invalide.") from None
        payload = {"model": model, "messages": messages, "temperature": min(float(body.get("temperature", 0.0)), 1.0),
                   "max_tokens": min(int(body.get("max_tokens", MAX_TOKENS)), MAX_TOKENS), "reasoning_effort": "low"}
        async with httpx.AsyncClient(timeout=240) as client:
            reply = await client.post(f"{upstream}/v1/chat/completions", json=payload, headers={"Authorization": f"Bearer {token}"})
        try:
            data = reply.json()
        except ValueError:
            raise HTTPException(502, "Le juge est momentanément indisponible.") from None
        if reply.status_code != 200:
            code = 503 if reply.status_code >= 500 else reply.status_code
            return JSONResponse({"error": {"message": "Le juge est momentanément indisponible.", "code": code}}, status_code=code)
        data["model"] = ALIAS                 # le vrai nom du modèle ne sort jamais
        return JSONResponse(data)

    @api.get("/health")
    async def health():
        return {"status": "ok"}

    return api
