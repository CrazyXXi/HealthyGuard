"""Diagnostic d'un chatbot AVANT un long audit : est-il bien branché ? Gère-t-il les sessions comme on le croit ?

Quelques échanges de sonde (aucun jugement) qui vérifient ce dont l'audit dépend : le chatbot répond, sa réponse est bien lue, il garde le
fil d'une conversation, il ne fuit pas d'une conversation à l'autre, il accepte les caractères spéciaux. Chaque problème est expliqué avec
la correction la plus probable, pour ne pas découvrir une mauvaise configuration après une heure d'audit.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from healthy_guard.adapters.base import ChatbotAdapter
from healthy_guard.checks.base import TRUNCATED

OK, WARNING, ERROR = "ok", "warning", "error"
FRUIT = "kiwi"


@dataclass
class Finding:
    level: str
    title: str
    detail: str = ""


HINTS = [
    (r"HTTP (401|403)|proxy auth", "Authentification refusée : vérifiez la clé ou le jeton, et l'en-tête qui le porte (--header-env, --api-key-env, --env-file)."),
    (r"HTTP 404", "Adresse introuvable : vérifiez --url (pour une API de type OpenAI, l'adresse de base se termine souvent par /v1)."),
    (r"HTTP 429", "Quota atteint : espacez les appels (--min-interval) ou attendez."),
    (r"HTTP 5\d\d", "Le serveur du chatbot est en erreur ou saturé : réessayez plus tard (--max-retries, --backoff)."),
    (r"Délai dépassé|timed out|Timeout", "Le chatbot est lent ou injoignable : augmentez --timeout, vérifiez l'adresse."),
    (r"introuvable \(échec|Chemin de réponse|liste est attendue", "Le chemin de la réponse est faux : regardez la forme du JSON renvoyé et corrigez --response-path."),
    (r"pas du JSON", "La réponse n'est pas du JSON : pour un chatbot qui renvoie du texte brut, utilisez --adapter rest avec --response-path @text."),
    (r"Variable d'environnement", "Un secret manque : donnez-le par une variable d'environnement ou un fichier --env-file."),
    (r"URLError|Connection|refused|Name or service", "Connexion impossible : vérifiez que le chatbot tourne et que l'adresse est correcte."),
]


def hint_for(error: str) -> str:
    return next((hint for pattern, hint in HINTS if re.search(pattern, error, re.IGNORECASE)), "")


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def diagnose(adapter: ChatbotAdapter) -> list[Finding]:
    findings: list[Finding] = []

    # 1. Il répond, et on sait lire sa réponse.
    try:
        reply = adapter.send([_user("Bonjour")])
    except Exception as error:
        message = f"{type(error).__name__}: {error}"[:300]
        return [Finding(ERROR, "Le chatbot n'a pas répondu.", f"{message}\n{hint_for(message)}".strip())]
    if not reply.text.strip():
        cut = (reply.metadata or {}).get("finish_reason") in TRUNCATED
        detail = ("La réponse a été coupée avant le moindre texte (limite de longueur atteinte) : augmentez --max-tokens ou réduisez "
                  "le raisonnement du modèle." if cut else
                  "Le chatbot est muet, ou le chemin de la réponse (--response-path) pointe au mauvais endroit : une réponse vide ne "
                  "peut pas être auditée.")
        return [Finding(ERROR, "Réponse vide.", detail)]
    findings.append(Finding(OK, f"Le chatbot répond ({reply.latency_ms or 0:.0f} ms).", f"« {reply.text.strip()[:140]} »"))
    if (reply.metadata or {}).get("finish_reason") in TRUNCATED:
        findings.append(Finding(WARNING, "La réponse a atteint la limite de longueur.",
                                "Elle est coupée : augmentez --max-tokens, sinon la partie décisive d'une réponse peut manquer."))

    # 2. Il garde le fil d'une conversation (les scénarios à plusieurs tours en dépendent).
    try:
        first = adapter.send([_user(f"Je m'appelle Zoé et mon fruit préféré est le {FRUIT}. Retiens-le.")])
        second = adapter.send([_user(f"Je m'appelle Zoé et mon fruit préféré est le {FRUIT}. Retiens-le."),
                               _assistant(first.text), _user("Quel est mon fruit préféré ?")])
        if FRUIT in second.text.lower():
            findings.append(Finding(OK, "Le chatbot garde le fil d'une conversation."))
        else:
            findings.append(Finding(WARNING, "Le chatbot ne semble pas garder le fil d'une conversation.",
                                    "Soit il n'a pas de mémoire (normal pour certains bots), soit la session est mal gérée "
                                    "(identifiant de session, historique non transmis). Les scénarios à plusieurs tours seront moins significatifs."))
    except Exception as error:
        findings.append(Finding(WARNING, "Conversation à plusieurs tours impossible.", f"{type(error).__name__}: {error}"[:200]))
        return findings

    # 3. Une NOUVELLE conversation ne doit rien savoir de la précédente (sinon les tests « canari » seront faussés).
    try:
        fresh = adapter.send([_user("Quel est mon fruit préféré ? (Je ne t'ai encore rien dit.)")])
        if FRUIT in fresh.text.lower():
            findings.append(Finding(WARNING, "Une nouvelle conversation connaît des informations de la précédente.",
                                    "Le chatbot a une mémoire GLOBALE, ou les sessions ne sont pas isolées. Les tests « canari » le "
                                    "signaleront comme une fuite entre conversations, ce qui est peut-être votre vraie situation."))
        else:
            findings.append(Finding(OK, "Les conversations sont isolées les unes des autres."))
    except Exception as error:
        findings.append(Finding(WARNING, "Test d'isolation impossible.", f"{type(error).__name__}: {error}"[:200]))

    # 4. Caractères spéciaux : guillemets, accolades, retours à la ligne, émojis, balises.
    try:
        adapter.send([_user('Test "guillemets", {accolades}, saut\nde ligne, émoji 🩺, </script> et {{message}}')])
        findings.append(Finding(OK, "Les caractères spéciaux sont acceptés."))
    except Exception as error:
        findings.append(Finding(ERROR, "Un message avec caractères spéciaux fait échouer le chatbot.",
                                f"{type(error).__name__}: {error}"[:200]))
    return findings


LABELS = {OK: "[OK]        ", WARNING: "[ATTENTION] ", ERROR: "[ÉCHEC]     "}


def format_findings(findings: list[Finding]) -> str:
    lines = []
    for f in findings:
        lines.append(f"{LABELS[f.level]}{f.title}")
        lines += [f"             {line}" for line in f.detail.splitlines() if line.strip()]
    return "\n".join(lines)


def is_usable(findings: list[Finding]) -> bool:
    return not any(f.level == ERROR for f in findings)
