"""Portes de sortie universelles : n'importe quel chatbot devient auditable, même sans API HTTP.

- CallableAdapter : une FONCTION Python `fn(messages) -> texte` (SDK Dialogflow, Botpress, Azure, appel interne...) ;
- CommandAdapter  : une COMMANDE (programme en ligne de commande, script, conteneur) qui lit le message et écrit sa réponse ;
- load_plugin     : une CLASSE `ChatbotAdapter` écrite par vous, pour un protocole particulier (websocket, navigateur...).

`messages` est toujours la conversation complète [{"role": "user"|"assistant", "content": "..."}]. Un nouveau test commence quand
`len(messages) == 1` : c'est le moment d'ouvrir une nouvelle session côté chatbot. ATTENTION : ces adaptateurs EXÉCUTENT du code ou
des commandes que vous indiquez : ne les pointez que vers ce que vous avez écrit ou vérifié.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import locale
import os
import shlex
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from healthy_guard.adapters.base import ChatbotAdapter, ChatResponse
from healthy_guard.adapters.web import AdapterError


def _split_spec(spec: str) -> tuple[str, str]:
    module, sep, name = spec.rpartition(":")
    if not sep or not module or not name:
        raise ValueError(f"« {spec} » : attendu module:nom (ex. mon_bot:repondre) ou chemin/fichier.py:nom.")
    if len(module) == 1 and name.startswith(("\\", "/")):          # « C:\\x.py » sans nom : lecteur Windows pris pour module
        raise ValueError(f"« {spec} » : attendu chemin/fichier.py:nom.")
    return module, name


def _load_module(module: str):
    if module.endswith(".py") or "/" in module or "\\" in module:
        path = Path(module)
        if not path.is_file():
            raise ValueError(f"Fichier introuvable : {module}")
        name = "hg_user_" + hashlib.sha1(str(path.resolve()).encode()).hexdigest()[:10]
        spec = importlib.util.spec_from_file_location(name, path)
        loaded = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(path.resolve().parent))             # pour que le fichier importe ses voisins
        try:
            spec.loader.exec_module(loaded)
        finally:
            sys.path.pop(0)
        return loaded
    try:
        return importlib.import_module(module)
    except ImportError as error:
        raise ValueError(f"Module introuvable « {module} » : {error}") from None


def _attribute(spec: str):
    module, name = _split_spec(spec)
    loaded = _load_module(module)
    if not hasattr(loaded, name):
        raise ValueError(f"« {name} » n'existe pas dans {module}.")
    return getattr(loaded, name)


class CallableAdapter(ChatbotAdapter):
    """Une fonction Python devient un chatbot. Elle peut renvoyer un texte, un dict {"text"|"reply"|"response": ...} ou un ChatResponse."""

    def __init__(self, fn: Callable, label: str | None = None):
        if not callable(fn):
            raise ValueError("Un objet appelable est attendu.")
        self.fn = fn
        self.label = label or getattr(fn, "__name__", "fonction")

    def describe(self) -> dict:
        return {"adapter": "CallableAdapter", "callable": self.label}

    def send(self, messages: list[dict]) -> ChatResponse:
        started = time.perf_counter()
        out = self.fn([dict(m) for m in messages])          # copie : la fonction ne peut pas altérer l'historique de l'audit
        elapsed = (time.perf_counter() - started) * 1000
        if isinstance(out, ChatResponse):
            if out.latency_ms is None:
                out.latency_ms = elapsed
            return out
        if isinstance(out, str):
            return ChatResponse(out, latency_ms=elapsed)
        if isinstance(out, dict):
            for key in ("text", "reply", "response", "answer", "content"):
                if isinstance(out.get(key), str):
                    return ChatResponse(out[key], latency_ms=elapsed, metadata={k: v for k, v in out.items() if k != key})
        raise AdapterError(f"La fonction doit renvoyer un texte, un dict avec « text »/« reply » ou un ChatResponse (reçu : {type(out).__name__}).")


def load_callable(spec: str) -> CallableAdapter:
    return CallableAdapter(_attribute(spec), label=spec)


def load_plugin(spec: str, args: dict | None = None) -> ChatbotAdapter:
    """Instancie une classe `ChatbotAdapter` définie par l'utilisateur : « module:Classe » ou « fichier.py:Classe »."""
    cls = _attribute(spec)
    if not (isinstance(cls, type) and issubclass(cls, ChatbotAdapter)):
        raise ValueError(f"« {spec} » doit être une classe qui hérite de ChatbotAdapter.")
    try:
        return cls(**(args or {}))
    except TypeError as error:
        raise ValueError(f"Impossible de créer {spec} avec ces arguments : {error}") from None


def _decode(raw: bytes) -> str:
    """UTF-8 d'abord ; sinon le codage local (un programme non-Python écrit souvent en cp1252 sous Windows)."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), errors="replace")


def _split_command(command: str) -> list[str]:
    parts = shlex.split(command, posix=(os.name != "nt"))
    return [p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in "\"'" else p for p in parts]   # sous Windows shlex garde les guillemets


class CommandAdapter(ChatbotAdapter):
    """Une commande (programme, script) devient un chatbot. Jamais de shell : les arguments ne sont pas interprétés.

    Modes d'entrée : « message » (dernier message sur l'entrée standard), « json » (toute la conversation en JSON sur l'entrée
    standard), « arg » (marqueurs {{message}} et {{session}} remplacés dans les arguments). La session est fournie dans la variable
    d'environnement HG_SESSION ; la réponse est la sortie standard ; un code de sortie non nul est une erreur technique.
    """

    MODES = ("message", "json", "arg")

    def __init__(self, command: str | list[str], input_mode: str = "message", timeout: float = 60.0):
        if input_mode not in self.MODES:
            raise ValueError(f"input_mode doit valoir {', '.join(self.MODES)}.")
        self.args = _split_command(command) if isinstance(command, str) else list(command)
        if not self.args:
            raise ValueError("Commande vide.")
        if input_mode == "arg" and not any("{{message}}" in a for a in self.args):
            raise ValueError("Le mode « arg » exige {{message}} dans la commande.")
        self.input_mode = input_mode
        self.timeout = timeout
        self._local = threading.local()

    def describe(self) -> dict:
        # Les arguments peuvent contenir des secrets : seul le nom du programme et une empreinte sont enregistrés.
        digest = hashlib.sha256(" ".join(self.args).encode()).hexdigest()[:12]
        return {"adapter": "CommandAdapter", "program": Path(self.args[0]).name, "command_sha256": digest,
                "input_mode": self.input_mode}

    def send(self, messages: list[dict]) -> ChatResponse:
        if len(messages) == 1 or not getattr(self._local, "session", None):
            self._local.session = f"hg-{uuid.uuid4().hex[:12]}"
        session = self._local.session
        last = next(m["content"] for m in reversed(messages) if m["role"] == "user")
        args, stdin = self.args, None
        if self.input_mode == "arg":
            args = [a.replace("{{message}}", last).replace("{{session}}", session) for a in self.args]
        elif self.input_mode == "json":
            stdin = json.dumps(messages, ensure_ascii=False)
        else:
            stdin = last
        started = time.perf_counter()
        # Octets, pas texte : on décide nous-mêmes du codage. Un programme Python est forcé en UTF-8 ; pour les autres, la sortie est
        # lue en UTF-8 puis, si elle n'en est pas, dans le codage local (cp1252 sous Windows) : sinon tous les accents seraient abîmés.
        env = {**os.environ, "HG_SESSION": session, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
        try:
            done = subprocess.run(args, input=stdin.encode("utf-8") if stdin is not None else None, capture_output=True,
                                  timeout=self.timeout, env=env)
        except subprocess.TimeoutExpired:
            raise AdapterError(f"Délai dépassé ({self.timeout:.0f} s) : la commande n'a pas répondu.") from None
        except OSError as error:
            raise AdapterError(f"Impossible de lancer la commande : {error}") from None
        if done.returncode != 0:
            raise AdapterError(f"La commande a échoué (code {done.returncode}) : {_decode(done.stderr or done.stdout).strip()[:200]}")
        return ChatResponse(_decode(done.stdout).strip(), latency_ms=(time.perf_counter() - started) * 1000,
                            metadata={"session": session})
