"""L'API Python : auditer un chatbot COMPLET avant sa mise en ligne, en une ligne.

    import healthy_guard as hg

    bot = hg.openai_chatbot("gemini-3.1-flash-lite", base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                            api_key_env="GEMINI_API_KEY")
    release = hg.audit(bot, system_prompt=open("prompt_systeme.txt").read(), name="Assistant santé",
                       judge=hg.assisted_judge({"provider": "openai", "model": "<modèle>", "base_url": "https://<serveur>",
                                                "api_key_env": "MA_CLE_DE_JUGE"}),     # facultatif : un juge intégré est fourni
                       output_dir="reports")
    hg.gate(bot, system_prompt=...)      # lève AuditFailed si le chatbot ne peut pas être publié : à mettre dans vos tests ou votre CI

`audit` n'est JAMAIS lancé par un simple `import` : un audit appelle le réseau, coûte du crédit et prend du temps ; il se déclenche
explicitement (cette fonction, la commande `healthy-guard run`, ou `gate` dans un test).
"""
from __future__ import annotations

import os
from pathlib import Path

from healthy_guard.adapters.base import ChatbotAdapter
from healthy_guard.adapters.config import build_chatbot
from healthy_guard.adapters.plugin import CallableAdapter, CommandAdapter
from healthy_guard.adapters.web import _UNSET as UNSET
from healthy_guard.adapters.web import AnthropicAdapter, HttpJsonAdapter, OpenAICompatibleAdapter, RestBotAdapter
from healthy_guard.chatbot import Chatbot, as_chatbot
from healthy_guard.checkbot import Finding, diagnose
from healthy_guard.judges.assisted import AssistedJudge
from healthy_guard.judges.base import Judge
from healthy_guard.judges.config import adapter_from_profile, validate_profile
from healthy_guard.judges.keyword import KeywordJudge
from healthy_guard.judges.llm import LLMJudge
from healthy_guard.models import Pillar
from healthy_guard.pipeline import Release, run_release_audit
from healthy_guard.promptreview import PromptReview, review_prompt
from healthy_guard.recommendations import build_recommendations

__all__ = ["demo_chatbot", "audit", "gate", "check", "AuditFailed", "Chatbot", "Release", "openai_chatbot", "anthropic_chatbot", "rest_chatbot",
           "http_chatbot", "function_chatbot", "command_chatbot", "local_chatbot", "assisted_judge", "review_prompt", "build_recommendations"]


class AuditFailed(Exception):
    """Le chatbot ne peut pas être publié en l'état. `release` contient le rapport complet et les recommandations."""

    def __init__(self, release: Release, message: str):
        super().__init__(message)
        self.release = release


# --- construire un chatbot ------------------------------------------------------------------------------------------------------------

def openai_chatbot(model: str, *, base_url: str | None = None, api_key_env=UNSET, system: str | None = None,
                   temperature: float = 0.0, max_tokens: int | None = None, extra_body: dict | None = None, **network) -> OpenAICompatibleAdapter:
    """Modèle derrière une API de type OpenAI : OpenAI, Gemini, vLLM, Mistral, Ollama, LM Studio...

    Clé d'API : lue dans `api_key_env`. Par défaut OPENAI_API_KEY pour OpenAI, AUCUNE pour toute autre adresse (serveur local ou interne) ;
    `api_key_env=None` force l'absence de clé.
    """
    kwargs = {"model": model, "system": system, "temperature": temperature, "extra_body": extra_body, **network}
    if api_key_env is not UNSET:
        kwargs["api_key_env"] = api_key_env
    if base_url:
        kwargs["base_url"] = base_url
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    return OpenAICompatibleAdapter(**kwargs)


def local_chatbot(model: str, *, base_url: str = "http://localhost:11434/v1", **options) -> OpenAICompatibleAdapter:
    """Modèle LOCAL, sans clé d'API : Ollama (défaut), LM Studio (http://localhost:1234/v1), llama.cpp (http://localhost:8080/v1), vLLM...
    Le premier appel peut être long (chargement du modèle) : le délai par défaut est étendu."""
    options.setdefault("timeout", 300.0)
    return openai_chatbot(model, base_url=base_url, api_key_env=None, **options)


def anthropic_chatbot(model: str, *, api_key_env: str = "ANTHROPIC_API_KEY", system: str | None = None, temperature: float = 0.0,
                      max_tokens: int | None = None, **network) -> AnthropicAdapter:
    kwargs = {"model": model, "api_key_env": api_key_env, "system": system, "temperature": temperature, **network}
    if max_tokens:
        kwargs["max_tokens"] = max_tokens
    return AnthropicAdapter(**kwargs)


def rest_chatbot(url: str, body_template: dict, response_path: str, **options) -> RestBotAdapter:
    """Chatbot classique (Rasa, Botpress, Dialogflow, maison) : dernier message + session. Voir RestBotAdapter."""
    return RestBotAdapter(url, body_template, response_path, **options)


def http_chatbot(url: str, **options) -> HttpJsonAdapter:
    return HttpJsonAdapter(url, **options)


def demo_chatbot(kind: str = "flawed") -> ChatbotAdapter:
    """Un faux chatbot pour essayer HealthyGuard sans clé, sans réseau : « flawed » (avec défauts) ou « good » (sans défaut)."""
    from healthy_guard.adapters.demo import DemoChatbot
    if kind not in ("good", "flawed"):
        raise ValueError("kind doit valoir « good » ou « flawed ».")
    return DemoChatbot(kind)


def function_chatbot(fn) -> CallableAdapter:
    """Une fonction fn(messages) -> texte devient un chatbot."""
    return CallableAdapter(fn)


def command_chatbot(command, **options) -> CommandAdapter:
    return CommandAdapter(command, **options)


def assisted_judge(profile: dict | ChatbotAdapter, *, rules: bool = True, escalate_critical: bool = True, **network) -> Judge:
    """Un modèle de langage juge les réponses, aidé par les règles. `profile` : {"provider", "model", "base_url", "api_key_env", ...}
    ou directement un adaptateur. Utilisez un modèle DIFFÉRENT du chatbot audité."""
    adapter = profile if isinstance(profile, ChatbotAdapter) else adapter_from_profile(validate_profile("juge", profile), network or None)
    llm = LLMJudge(adapter)
    return AssistedJudge(llm, KeywordJudge(), escalate_critical=escalate_critical) if rules else llm


# Le juge intégré : un service hébergé pour HealthyGuard (voir server/judge_gateway.py). Le package ne contient AUCUN jeton ni nom de modèle :
# l'adresse de la passerelle est publique et sans secret. Elle se règle une fois déployée (ou par HEALTHYGUARD_JUDGE_URL).
HOSTED_JUDGE_URL = ""
JUDGE_ALIAS = "healthyguard-judge"


def default_judge() -> Judge | None:
    """Le juge utilisé quand on n'en précise pas. L'utilisateur n'a rien à configurer : ni modèle, ni jeton, ni serveur.

    Retourne None si aucune passerelle n'est configurée : l'audit se fait alors par règles seules, avec un avertissement.
    Facultatif : HEALTHYGUARD_API_KEY si votre passerelle exige une clé.
    """
    url = (os.environ.get("HEALTHYGUARD_JUDGE_URL") or HOSTED_JUDGE_URL).strip()
    if not url:
        return None
    profile = {"provider": "openai", "model": JUDGE_ALIAS, "base_url": url, "max_tokens": 2048,
               "api_key_env": "HEALTHYGUARD_API_KEY" if os.environ.get("HEALTHYGUARD_API_KEY") else None}
    return assisted_judge(profile, timeout=180)


# --- auditer -------------------------------------------------------------------------------------------------------------------------------

def _pillars(pillars) -> list[Pillar] | None:
    return None if pillars is None else [p if isinstance(p, Pillar) else Pillar(p) for p in pillars]


def llm_adapter_of(judge) -> ChatbotAdapter | None:
    """L'adaptateur du modèle qui juge (pour lui faire aussi relire le prompt)."""
    for attribute in ("adapter", "llm", "inner"):
        inner = getattr(judge, attribute, None)
        if isinstance(inner, ChatbotAdapter):
            return inner
        if inner is not None and (found := llm_adapter_of(inner)) is not None:
            return found
    return None


def audit(chatbot, *, system_prompt: str | None = None, system_prompt_file: str | Path | None = None, judge: Judge | None = None,
          pillars=None, limit: int | None = None, repeats: int = 1, concurrency: int = 1, max_major_fails: int = 3,
          output_dir: str | Path | None = None, name: str = "audit", verbose: bool = True, check_connection: bool = True,
          review_system_prompt: bool = True, review_prompt_with_judge: bool = False, extra_cases: dict | None = None,
          extra_info: dict | None = None, **card) -> Release:
    """Audite le chatbot COMPLET et retourne un `Release` (décision, recommandations, rapports).

    `chatbot` : un `Chatbot`, un adaptateur (`openai_chatbot(...)`), une fonction Python, un profil (dict) ou « fichier.py:fonction ».
    `card` : nom, description, usage prévu, public, langues, outils : `Chatbot(name=..., intended_use=..., languages=..., tools=...)`.
    Sans `judge`, le jugement se fait par règles seules : gratuit mais approximatif ; donnez un modèle (`assisted_judge`) pour un vrai audit.
    """
    subject = as_chatbot(chatbot, system_prompt, str(system_prompt_file) if system_prompt_file else None, **card)
    warnings = []
    if judge is None:
        judge = default_judge()
    if judge is None:
        judge = KeywordJudge()
        warnings.append("Jugement par règles seules : approximatif (faux positifs et faux négatifs). Le juge intégré n'est pas configuré : "
                        "définissez HEALTHYGUARD_JUDGE_URL ou donnez un juge (`judge=`) pour un vrai audit.")
    release = run_release_audit(
        subject, judge, pillars=_pillars(pillars), limit=limit, repeats=repeats, concurrency=concurrency, max_major_fails=max_major_fails,
        check_connection=check_connection, review_system_prompt=review_system_prompt,
        prompt_reviewer=llm_adapter_of(judge) if review_prompt_with_judge else None, extra_cases=extra_cases, extra_info=extra_info,
        warnings=warnings, output_dir=output_dir, name=name)
    if verbose:
        print(release.summary(detail=verbose == 'detail'))
    return release


def gate(chatbot, *, allow_review: bool = False, **options) -> Release:
    """Comme `audit`, mais LÈVE `AuditFailed` si le chatbot ne peut pas être publié : à mettre dans un test ou dans l'intégration continue.

    Par défaut, seule la décision « autorisé » passe ; `allow_review=True` accepte aussi « revue humaine requise » (à vos risques).
    """
    options.setdefault("verbose", False)
    release = audit(chatbot, **options)
    accepted = {"pass", "review_required"} if allow_review else {"pass"}
    if release.decision in accepted:
        return release
    if not release.ready:
        raise AuditFailed(release, "Le chatbot n'est pas joignable : l'audit n'a pas eu lieu.\n" + release.summary(0))
    top = release.recommendations[:3]
    advice = "\n".join(f"  {i}. [{r['priority']}] {r['title']}" for i, r in enumerate(top, 1)) or "  (voir le rapport)"
    raise AuditFailed(release, f"Chatbot non publiable : décision « {release.decision} ».\nRecommandations prioritaires :\n{advice}")


def check(chatbot, **card) -> list[Finding]:
    """Diagnostic rapide du branchement (répond-il, garde-t-il le fil, fuit-il entre conversations...), sans audit."""
    return diagnose(as_chatbot(chatbot, **card).audited_adapter()[0])


def review_system_prompt_text(text: str) -> PromptReview:
    return review_prompt(text)


__all__ += ["review_system_prompt_text", "build_chatbot"]
