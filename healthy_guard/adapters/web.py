"""Adaptateurs vers de vrais chatbots, en bibliothèque standard (aucune dépendance).

- HttpJsonAdapter          : n'importe quelle API HTTP JSON (le chatbot du développeur)
- AnthropicAdapter         : API Messages d'Anthropic
- OpenAICompatibleAdapter  : API « chat/completions » (OpenAI et services compatibles)

Les clés d'API sont lues dans des variables d'environnement. Elles ne sont jamais écrites
dans `describe()`, donc jamais dans le rapport, ni dans les messages d'erreur.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from healthy_guard.adapters.base import ChatbotAdapter, ChatResponse

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


class AdapterError(RuntimeError):
    """Erreur d'appel au chatbot (réseau, authentification, format de réponse...)."""


def dig(data, path: str):
    """Lit une valeur dans du JSON imbriqué : « choices.0.message.content »."""
    current = data
    for part in path.split("."):
        try:
            current = current[int(part)] if isinstance(current, list) else current[part]
        except (KeyError, IndexError, ValueError, TypeError):
            raise AdapterError(f"Chemin de réponse « {path} » introuvable (échec à « {part} »).") from None
    return current


MAX_SERVER_DELAY = 120.0  # on n'attend jamais plus longtemps qu'un délai demandé par le serveur


def _retry_delay_in_body(body: str) -> float | None:
    """Délai d'attente suggéré dans le corps d'une erreur (ex. « retryDelay »: « 27s »)."""
    match = re.search(r'retryDelay"\s*:\s*"(\d+(?:\.\d+)?)s"', body) or \
        re.search(r"retry in (\d+(?:\.\d+)?)s", body, re.IGNORECASE)
    return min(float(match.group(1)), MAX_SERVER_DELAY) if match else None


class _HttpAdapter(ChatbotAdapter):
    def __init__(self, timeout: float = 60.0, max_retries: int = 3, backoff: float = 1.0,
                 min_interval: float = 0.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.min_interval = min_interval  # cadence : secondes minimum entre deux appels (quotas par minute)
        self._sleep = sleep  # injectable pour les tests
        self._clock = clock
        self._next_slot: float | None = None
        self._lock = threading.Lock()  # l'adaptateur est partagé entre les threads d'un audit parallèle

    def _pace(self) -> None:
        """Réserve un créneau d'appel : au moins `min_interval` secondes entre deux appels, même en parallèle."""
        if not self.min_interval:
            return
        with self._lock:
            now = self._clock()
            slot = now if self._next_slot is None else max(now, self._next_slot)
            self._next_slot = slot + self.min_interval
        if slot > now:
            self._sleep(slot - now)

    def _post(self, url: str, headers: dict, payload: dict, method: str = "POST", form: bool = False,
              raw_text: bool = False) -> dict:
        """Envoie la requête (POST JSON par défaut ; GET avec paramètres d'URL ; POST/PUT en formulaire) et lit la réponse :
        JSON, ou texte brut (`raw_text`) rendu sous la forme {"text": ...}."""
        flat = {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False) for k, v in payload.items()}
        head = dict(headers)
        if method == "GET":
            query = urllib.parse.urlencode(flat)
            url = url + ("&" if "?" in url else "?") + query if query else url
            body = None
        elif form:
            body = urllib.parse.urlencode(flat).encode("utf-8")
            head = {"Content-Type": "application/x-www-form-urlencoded", **headers}
        else:
            body = json.dumps(payload).encode("utf-8")
            head = {"Content-Type": "application/json", **headers}
        request = urllib.request.Request(url, data=body, method=method, headers=head)
        last_error = "erreur inconnue"
        for attempt in range(self.max_retries + 1):
            self._pace()
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    text = response.read().decode("utf-8")
                    return {"text": text} if raw_text else json.loads(text)
            except urllib.error.HTTPError as error:
                full_body = error.read().decode("utf-8", "replace")
                last_error = f"HTTP {error.code} : {full_body[:200]}"
                if error.code not in RETRYABLE_STATUS:
                    raise AdapterError(last_error) from None
                retry_after = error.headers.get("Retry-After") if error.headers else None
                if retry_after and retry_after.isdigit():
                    delay = float(retry_after)
                elif (hinted := _retry_delay_in_body(full_body)) is not None:
                    delay = hinted  # certains services (Google) indiquent le délai dans le corps de l'erreur
                else:
                    delay = self.backoff * 2 ** attempt
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                last_error = f"{type(error).__name__} : {getattr(error, 'reason', error)}"
                delay = self.backoff * 2 ** attempt
            except json.JSONDecodeError:
                raise AdapterError("La réponse du chatbot n'est pas du JSON valide.") from None
            if attempt < self.max_retries:
                self._sleep(delay)
        raise AdapterError(f"Échec après {self.max_retries + 1} tentatives. Dernière erreur : {last_error}")

    @staticmethod
    def _api_key(env_name: str) -> str:
        key = os.environ.get(env_name)
        if not key:
            raise AdapterError(f"Variable d'environnement {env_name} absente ou vide (clé d'API).")
        return key


class HttpJsonAdapter(_HttpAdapter):
    """Chatbot exposé par une API HTTP JSON quelconque.

    Envoie `{request_field: messages, **extra_body}` et lit la réponse au chemin `response_path`
    (ex. « reply » ou « choices.0.message.content »).
    """

    def __init__(self, url: str, headers: dict | None = None, request_field: str = "messages",
                 response_path: str = "reply", extra_body: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.url = url
        self.headers = headers or {}
        self.request_field = request_field
        self.response_path = response_path
        self.extra_body = extra_body or {}

    def describe(self) -> dict:
        # Les en-têtes (souvent des jetons) ne sont volontairement pas enregistrés.
        return {"adapter": "HttpJsonAdapter", "url": self.url, "request_field": self.request_field,
                "response_path": self.response_path, "extra_body": self.extra_body}

    def send(self, messages: list[dict]) -> ChatResponse:
        started = time.perf_counter()
        data = self._post(self.url, self.headers, {**self.extra_body, self.request_field: messages})
        text = dig(data, self.response_path)
        if not isinstance(text, str):
            raise AdapterError(f"La valeur à « {self.response_path} » n'est pas du texte.")
        return ChatResponse(text, latency_ms=(time.perf_counter() - started) * 1000)


class _LlmApiAdapter(_HttpAdapter):
    def __init__(self, model: str, api_key_env: str, system: str | None, temperature: float,
                 max_tokens: int, base_url: str, **kwargs):
        super().__init__(**kwargs)
        self.model = model
        self.api_key_env = api_key_env
        self.system = system
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = base_url.rstrip("/")

    def describe(self) -> dict:
        info = {"adapter": type(self).__name__, "model": self.model, "base_url": self.base_url,
                "temperature": self.temperature, "max_tokens": self.max_tokens}
        if self.system:  # empreinte seulement : le prompt système peut être confidentiel
            info["system_prompt_sha256"] = hashlib.sha256(self.system.encode()).hexdigest()[:16]
            info["system_prompt_chars"] = len(self.system)
        return info


class AnthropicAdapter(_LlmApiAdapter):
    def __init__(self, model: str, api_key_env: str = "ANTHROPIC_API_KEY", system: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 1024,
                 base_url: str = "https://api.anthropic.com", **kwargs):
        super().__init__(model, api_key_env, system, temperature, max_tokens, base_url, **kwargs)

    def send(self, messages: list[dict]) -> ChatResponse:
        headers = {"x-api-key": self._api_key(self.api_key_env), "anthropic-version": "2023-06-01"}
        payload = {"model": self.model, "max_tokens": self.max_tokens,
                   "temperature": self.temperature, "messages": messages}
        if self.system:
            payload["system"] = self.system
        started = time.perf_counter()
        data = self._post(f"{self.base_url}/v1/messages", headers, payload)
        blocks = data.get("content")
        if not isinstance(blocks, list):
            raise AdapterError("Réponse Anthropic sans bloc « content ».")
        text = "".join(b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        return ChatResponse(
            text, latency_ms=(time.perf_counter() - started) * 1000,
            metadata={"model": data.get("model"), "usage": data.get("usage"),
                      "stop_reason": data.get("stop_reason"),
                      # même vocabulaire que les API compatibles OpenAI : « length » = réponse coupée
                      "finish_reason": "length" if data.get("stop_reason") == "max_tokens" else data.get("stop_reason")})


_UNSET = object()
OPENAI_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatibleAdapter(_LlmApiAdapter):
    """API « chat/completions » : OpenAI, mais aussi les modèles hébergés par vos soins ou LOCAUX (Ollama, LM Studio, llama.cpp, vLLM...).

    SANS CLÉ D'API : un serveur local ou interne n'en demande pas. La règle : pour l'adresse d'OpenAI, la clé est lue dans OPENAI_API_KEY ;
    pour toute autre adresse, AUCUNE clé n'est envoyée sauf si vous indiquez `api_key_env` ; `api_key_env=None` force l'absence de clé.
    Si vous indiquez une variable et qu'elle est absente, c'est une erreur (on ne veut pas oublier une clé sans le savoir).

    `extra_body` ajoute des paramètres propres au serveur (ex. {"reasoning_effort": "none", "top_p": 0.9}).
    Le jeton, s'il y en a un, se lit dans la variable `api_key_env` ; il peut avoir la forme « IDENTIFIANT.SECRET ».
    """

    RESERVED = {"model", "messages"}

    def __init__(self, model: str, api_key_env=_UNSET, system: str | None = None,
                 temperature: float = 0.0, max_tokens: int = 1024,
                 base_url: str = OPENAI_BASE_URL, extra_body: dict | None = None, **kwargs):
        if api_key_env is _UNSET:
            api_key_env = "OPENAI_API_KEY" if base_url.rstrip("/") == OPENAI_BASE_URL else None
        super().__init__(model, api_key_env or None, system, temperature, max_tokens, base_url, **kwargs)
        # Adresse « nue » (https://hote) : la route standard est /v1/chat/completions, on ajoute /v1.
        # Une adresse qui a déjà un chemin (/v1, /v1beta/openai...) n'est jamais modifiée.
        if urllib.parse.urlparse(self.base_url).path in ("", "/"):
            self.base_url = self.base_url.rstrip("/") + "/v1"
        if reserved := self.RESERVED & set(extra_body or {}):
            raise ValueError(f"extra_body ne peut pas redéfinir : {', '.join(sorted(reserved))}.")
        self.extra_body = dict(extra_body or {})

    def describe(self) -> dict:
        info = super().describe()
        info["authentication"] = "aucune" if self.api_key_env is None else "jeton lu dans une variable d'environnement"
        if self.extra_body:
            info["extra_body"] = self.extra_body    # paramètres du serveur : jamais de secret ici
        return info

    def send(self, messages: list[dict]) -> ChatResponse:
        headers = {} if self.api_key_env is None else {"Authorization": f"Bearer {self._api_key(self.api_key_env)}"}
        full = ([{"role": "system", "content": self.system}] if self.system else []) + messages
        payload = {"model": self.model, "messages": full,
                   "temperature": self.temperature, "max_tokens": self.max_tokens, **self.extra_body}
        started = time.perf_counter()
        data = self._post(f"{self.base_url}/chat/completions", headers, payload)
        text = dig(data, "choices.0.message.content")
        if text is None:
            text = ""   # un modèle qui raisonne rend un contenu nul quand son budget de réponse est épuisé
        if not isinstance(text, str):
            raise AdapterError("Réponse sans contenu texte.")
        return ChatResponse(
            text, latency_ms=(time.perf_counter() - started) * 1000,
            metadata={"model": data.get("model"), "usage": data.get("usage"),
                      "finish_reason": data["choices"][0].get("finish_reason")})


# --- chatbots classiques (à règles, à intentions...) exposés en REST ---------------------------------------

def _substitute(template, message: str, session: str, messages: list[dict]):
    """Remplace {{message}}, {{session}} et {{messages}} dans un gabarit JSON, sans jamais fabriquer de JSON à la main :
    on travaille sur des objets Python, donc un message contenant guillemets, retours à la ligne ou accolades reste intact."""
    if isinstance(template, str):
        if template == "{{messages}}":
            return messages
        if template == "{{message}}":
            return message
        if template == "{{session}}":
            return session
        # une seule passe : un message qui contiendrait lui-même « {{session}} » ne doit pas être re-substitué
        values = {"{{message}}": message, "{{session}}": session}
        return re.sub(r"\{\{(?:message|session)\}\}", lambda m: values[m.group(0)], template)
    if isinstance(template, list):
        return [_substitute(item, message, session, messages) for item in template]
    if isinstance(template, dict):
        return {key: _substitute(value, message, session, messages) for key, value in template.items()}
    return template


def _extract_texts(data, parts: list[str], path: str) -> list[str]:
    """Textes trouvés au chemin « a.0.b » ; « * » parcourt une liste (ex. « *.text » pour la liste de messages de Rasa)."""
    if not parts:
        return [data] if isinstance(data, str) else []
    head, rest = parts[0], parts[1:]
    if head == "*":
        if not isinstance(data, list):
            raise AdapterError(f"Chemin de réponse « {path} » : une liste est attendue à l'endroit du « * ».")
        found: list[str] = []
        for item in data:
            try:
                found += _extract_texts(item, rest, path)
            except AdapterError:
                continue          # élément sans texte (image, bouton...) : ignoré
        return found
    try:
        child = data[int(head)] if isinstance(data, list) else data[head]
    except (KeyError, IndexError, ValueError, TypeError):
        raise AdapterError(f"Chemin de réponse « {path} » introuvable (échec à « {head} »).") from None
    return _extract_texts(child, rest, path)


class RestBotAdapter(_HttpAdapter):
    """Chatbot CLASSIQUE (à règles, à intentions : Rasa, Botpress, Dialogflow, widget maison...) exposé en REST.

    Différence avec un modèle de langage : il ne reçoit PAS toute la conversation à chaque appel, seulement le dernier
    message, et retrouve le fil grâce à un identifiant de SESSION. L'adaptateur ouvre donc une nouvelle session à chaque
    nouvelle conversation (un cas de test, ou la sonde d'un canari) et la garde pour les tours suivants.

    `body_template` : objet JSON avec des marqueurs {{message}} (dernier message), {{session}} (identifiant de session),
    {{messages}} (toute la conversation, pour les bots qui la réclament). Exemple Rasa :
        {"sender": "{{session}}", "message": "{{message}}"}      et      response_path = "*.text"
    Un chemin avec « * » concatène tous les textes d'une liste ; une liste vide donne une réponse vide (le bot s'est tu).
    `response_path="@text"` : la réponse est le corps brut (texte, pas du JSON). `method` : POST (défaut), PUT ou GET (le gabarit devient
    les paramètres d'URL) ; `body_format="form"` : formulaire application/x-www-form-urlencoded au lieu de JSON.
    Les secrets vont dans les en-têtes (--header-env), jamais dans le gabarit : il est enregistré dans le rapport.
    """

    METHODS = ("POST", "PUT", "GET")
    FORMATS = ("json", "form")

    def __init__(self, url: str, body_template: dict, response_path: str, headers: dict | None = None,
                 method: str = "POST", body_format: str = "json", **kwargs):
        super().__init__(**kwargs)
        if method not in self.METHODS:
            raise ValueError(f"method doit valoir {', '.join(self.METHODS)}.")
        if body_format not in self.FORMATS:
            raise ValueError(f"body_format doit valoir {', '.join(self.FORMATS)}.")
        if not isinstance(body_template, dict):
            raise ValueError("body_template doit être un objet JSON.")
        if "{{message}}" not in json.dumps(body_template) and "{{messages}}" not in json.dumps(body_template):
            raise ValueError("body_template doit contenir {{message}} (ou {{messages}}) : sinon le bot ne reçoit jamais la question.")
        self.url = url
        self.body_template = body_template
        self.response_path = response_path
        self.headers = headers or {}
        self.method = method
        self.body_format = body_format
        self._local = threading.local()      # une session par thread : chaque cas d'un audit parallèle a la sienne

    def describe(self) -> dict:
        return {"adapter": "RestBotAdapter", "url": self.url, "body_template": self.body_template,
                "response_path": self.response_path, "method": self.method, "body_format": self.body_format}

    def send(self, messages: list[dict]) -> ChatResponse:
        if len(messages) == 1 or not getattr(self._local, "session", None):
            self._local.session = f"hg-{uuid.uuid4().hex[:12]}"       # nouvelle conversation = nouvelle session
        last = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        body = _substitute(self.body_template, last, self._local.session, messages)
        started = time.perf_counter()
        plain = self.response_path == "@text"            # le bot répond par du texte brut, pas du JSON
        data = self._post(self.url, self.headers, body, method=self.method, form=self.body_format == "form", raw_text=plain)
        text = data["text"].strip() if plain else "\n".join(
            _extract_texts(data, self.response_path.split("."), self.response_path))
        return ChatResponse(text, latency_ms=(time.perf_counter() - started) * 1000,
                            metadata={"session": self._local.session})
