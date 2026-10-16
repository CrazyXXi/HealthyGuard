"""Ligne de commande : python -m healthy_guard {audit,report,verify}

Codes de sortie de `audit` (utilisables pour bloquer un déploiement en intégration continue) :
    0 = déploiement autorisé · 1 = revue humaine requise · 2 = déploiement bloqué
    64 = erreur d'usage ou de configuration
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
import sys
from pathlib import Path

from healthy_guard.adapters.base import ChatbotAdapter
from healthy_guard.adapters.demo import DemoChatbot
from healthy_guard.adapters.mock import MockChatbot
from healthy_guard.adapters.web import (AdapterError, AnthropicAdapter, HttpJsonAdapter,
                                        OpenAICompatibleAdapter, RestBotAdapter)
from healthy_guard.adapters.config import build_chatbot, load_chatbots
from healthy_guard.adapters.plugin import CommandAdapter, load_callable, load_plugin
from healthy_guard.api import default_judge, llm_adapter_of
from healthy_guard.interpretation import format_interpretation
from healthy_guard.manual import DEFAULT_LIMIT, export_scenarios, instructions, load_answers
from healthy_guard.auditor import ALL_PILLARS, Auditor
from healthy_guard.chatbot import Chatbot
from healthy_guard.checkbot import diagnose, format_findings, is_usable
from healthy_guard.checks.base import MAX_CONCURRENCY
from healthy_guard.envfile import load_env_file
from healthy_guard.extra import load_extra_cases
from healthy_guard.judges.base import Judge
from healthy_guard.judges.keyword import KeywordJudge
from healthy_guard.calibration import build_label_items, calibrate
from healthy_guard.compare import compare_reports
from healthy_guard.merge import merge_reports
from healthy_guard.retry import retry_errors
from healthy_guard.rejudge import rejudge_report
from healthy_guard.judges.assisted import AssistedJudge
from healthy_guard.judges.config import adapter_from_profile, load_profiles, validate_profile
from healthy_guard.judges.llm import LLMJudge, VotingJudge
from healthy_guard.judges.panel import PanelJudge
from healthy_guard.pipeline import _prompt_recommendations as prompt_recommendations, run_release_audit
from healthy_guard.promptreview import review_prompt
from healthy_guard.recommendations import SEVERITY_RANK, build_recommendations, format_recommendations
from healthy_guard.review import apply_reviews, export_queue
from healthy_guard.models import Decision, Pillar
from healthy_guard.reporting.html_report import write_html
from healthy_guard.reporting.json_report import (read_json, report_from_dict, report_to_dict,
                                                 verify, write_json)

EXIT_CODES = {Decision.PASS: 0, Decision.REVIEW_REQUIRED: 1, Decision.BLOCKED: 2}
EXIT_USAGE = 64

DECISION_TEXT = {
    Decision.PASS: "DÉPLOIEMENT AUTORISÉ",
    Decision.REVIEW_REQUIRED: "REVUE HUMAINE REQUISE",
    Decision.BLOCKED: "DÉPLOIEMENT BLOQUÉ",
}
ADAPTERS = ("demo-good", "demo-flawed", "mock-bad", "http", "rest", "anthropic", "openai", "python", "command", "plugin", "answers")


class ConfigError(Exception):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message):  # 64 et non 2 : le code 2 signifie « déploiement bloqué »
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: erreur : {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(prog="healthy-guard", description="Audit de gouvernance et d'éthique de chatbots de santé.")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=Parser)

    audit = sub.add_parser("audit", help="auditer un chatbot")
    add_chatbot_arguments(audit)

    add_network_arguments(audit)
    add_env_argument(audit)

    add_judge_arguments(audit)

    scope = audit.add_argument_group("périmètre")
    scope.add_argument("--pillars", help=f"liste séparée par des virgules parmi : {', '.join(p.value for p in ALL_PILLARS)}")
    scope.add_argument("--repeats", type=int, default=1, help="rejouer chaque cas N fois (mesure de la variance)")
    scope.add_argument("--limit", type=int, help="échantillon de N cas par pilier (audit rapide)")
    scope.add_argument("--max-major-fails", type=int, default=3, help="nombre d'échecs majeurs qui bloque (défaut : 3)")
    scope.add_argument("--retry-errors", metavar="RAPPORT.json",
                       help="ne relancer QUE les cas sans réponse (erreur technique) d'un rapport existant, puis "
                            "les fusionner dans --out ; mêmes options de chatbot et de juge que l'audit d'origine")
    scope.add_argument("--extra-cases", metavar="FICHIER.json",
                       help="vos propres cas de test (red team, incidents réels) ajoutés à ceux du pilier ; "
                            "jamais échantillonnés par --limit ; fichier tracé dans le rapport")

    out = audit.add_argument_group("sorties")
    out.add_argument("--out", default="audit_report.json", help="rapport JSON (défaut : audit_report.json)")
    out.add_argument("--html", help="écrire aussi un rapport HTML")
    out.add_argument("--quiet", action="store_true", help="ne pas afficher la progression")

    report = sub.add_parser("report", help="produire le rapport HTML à partir d'un rapport JSON")
    report.add_argument("input")
    report.add_argument("--html", required=True)

    check = sub.add_parser("verify", help="vérifier qu'un rapport JSON n'a pas été modifié")
    check.add_argument("input")

    check_bot = sub.add_parser("check-bot", help="diagnostiquer un chatbot AVANT un long audit (branchement, sessions, isolation)")
    add_chatbot_arguments(check_bot)
    add_network_arguments(check_bot)
    add_env_argument(check_bot)

    run = sub.add_parser("run", help="parcours COMPLET avant publication : connexion, prompt système, audit, recommandations, rapport")
    add_chatbot_arguments(run)
    add_run_arguments(run)
    add_network_arguments(run)
    add_env_argument(run)
    add_judge_arguments(run)

    init = sub.add_parser("init", help="assistant : crée healthyguard.json (la description de votre chatbot, sans secret)")
    init.add_argument("--file", default="healthyguard.json")
    init.add_argument("--force", action="store_true", help="écraser un fichier existant")

    recommend = sub.add_parser("recommend", help="recommandations (et revue du prompt) à partir d'un rapport d'audit existant")
    recommend.add_argument("input", help="rapport JSON")
    recommend.add_argument("--system-file", help="prompt système à relire en même temps")
    recommend.add_argument("--out", help="écrire le rapport enrichi (JSON)")
    recommend.add_argument("--html", help="écrire le rapport enrichi (HTML)")
    recommend.add_argument("--top", type=int, default=8, help="nombre de recommandations affichées (défaut : 8)")

    scenarios = sub.add_parser("export-scenarios", help="MODE SANS API : exporter les messages à passer à la main dans un chatbot (tableur)")
    scenarios.add_argument("--out", default="scenarios.csv")
    scenarios.add_argument("--pillars", help="défaut : clinical_safety,transparency,privacy,consent (l'équité exige tous les profils)")
    scenarios.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"cas par pilier (défaut : {DEFAULT_LIMIT} ; le copier-coller est long)")

    labels = sub.add_parser("export-labels", help="préparer un fichier d'annotation en aveugle pour calibrer le juge")
    labels.add_argument("input", help="rapport JSON")
    labels.add_argument("--out", default="labels.json")
    labels.add_argument("--key", default=None, help="fichier où garder les verdicts automatiques (défaut : <out>.key.json)")
    labels.add_argument("--n", type=int, default=60, help="nombre de conversations à annoter (défaut : 60)")
    labels.add_argument("--seed", type=int, default=0)

    calibrate_cmd = sub.add_parser("calibrate", help="mesurer l'accord du juge avec des annotations humaines")
    calibrate_cmd.add_argument("labels", help="fichier d'annotation rempli (champ « label » : pass ou fail)")
    add_judge_arguments(calibrate_cmd)
    add_env_argument(calibrate_cmd)
    calibrate_cmd.add_argument("--out", help="écrire le résultat de la calibration en JSON")
    calibrate_cmd.add_argument("--min-kappa", type=float, help="code de sortie 1 si le kappa est inférieur")
    calibrate_cmd.add_argument("--max-missed", type=float,
                               help="code de sortie 1 si la part d'échecs ratés dépasse cette valeur (ex. 0.1)")

    compare = sub.add_parser("compare", help="comparer deux audits (deux versions du chatbot) : régression ?")
    compare.add_argument("a", help="rapport JSON de référence (version actuelle)")
    compare.add_argument("b", help="rapport JSON à comparer (nouvelle version)")
    compare.add_argument("--out", help="écrire la comparaison en JSON")

    merge = sub.add_parser("merge", help="fusionner des rapports d'un même chatbot (audit fait par morceaux)")
    merge.add_argument("inputs", nargs="+", help="rapports JSON, du plus prioritaire au moins prioritaire")
    merge.add_argument("--out", default="merged_report.json")
    merge.add_argument("--html")

    rejudge = sub.add_parser("rejudge", help="refaire juger les conversations d'un rapport, sans rappeler le chatbot")
    rejudge.add_argument("input", help="rapport JSON")
    add_judge_arguments(rejudge)
    add_network_arguments(rejudge)
    add_env_argument(rejudge)
    rejudge.add_argument("--statuses", help="ne rejuger que ces verdicts d'origine, ex. fail,warning,human_review "
                                            "(défaut : tous)")
    rejudge.add_argument("--out", default="rejudged_report.json")
    rejudge.add_argument("--html")
    rejudge.add_argument("--quiet", action="store_true")

    queue = sub.add_parser("review-export", help="exporter les cas à trancher par un humain")
    queue.add_argument("input", help="rapport JSON")
    queue.add_argument("--out", default="review_queue.json")

    apply = sub.add_parser("review-apply", help="appliquer les décisions humaines et recalculer la décision")
    apply.add_argument("input", help="rapport JSON d'origine")
    apply.add_argument("reviews", help="file de revue remplie (decision, reviewer, comment)")
    apply.add_argument("--out", default="reviewed_report.json")
    apply.add_argument("--html")
    return parser


def add_chatbot_arguments(parser) -> None:
    chatbot = parser.add_argument_group("chatbot audité")
    chatbot.add_argument("--adapter", choices=ADAPTERS,
                         help="demo-good / demo-flawed / mock-bad : sans réseau ; "
                              "http (API qui reçoit la liste des messages) ; rest (chatbot CLASSIQUE : dernier message + session, "
                              "ex. Rasa) ; anthropic / openai : modèles de langage ; python (une fonction Python) ; command (une "
                              "commande) ; plugin (votre propre classe). Ou --chatbot avec --chatbot-config.")
    chatbot.add_argument("--url", help="(http) URL de l'API du chatbot")
    chatbot.add_argument("--request-field", default="messages", help="(http) champ JSON qui reçoit les messages")
    chatbot.add_argument("--response-path", default=None,
                         help="(http, rest) chemin de la réponse dans le JSON : reply, choices.0.message.content ; "
                              "pour rest, « * » parcourt une liste (Rasa : *.text)")
    chatbot.add_argument("--body-template", metavar="JSON",
                         help="(rest) corps de la requête, avec {{message}} (dernier message), {{session}} (identifiant de "
                              "session) et au besoin {{messages}} (toute la conversation). Ex. Rasa : "
                              "'{\"sender\": \"{{session}}\", \"message\": \"{{message}}\"}'. @fichier.json accepté.")
    chatbot.add_argument("--header", action="append", default=[], metavar="NOM:VALEUR",
                         help="(http) en-tête fixe (évitez d'y mettre des secrets)")
    chatbot.add_argument("--header-env", action="append", default=[], metavar="NOM=VARIABLE",
                         help="(http) en-tête dont la valeur est lue dans une variable d'environnement")
    chatbot.add_argument("--model", help="(anthropic/openai) identifiant du modèle audité")
    chatbot.add_argument("--base-url", help="(anthropic/openai) URL de base de l'API")
    chatbot.add_argument("--api-key-env", help="variable d'environnement contenant la clé d'API (inutile pour un serveur local ou "
                                               "interne : sans cette option, aucune clé n'est envoyée à une adresse autre qu'OpenAI)")
    chatbot.add_argument("--no-api-key", action="store_true", help="n'envoyer aucune clé, même vers OpenAI (serveur compatible sans authentification)")
    chatbot.add_argument("--system", help="prompt système du chatbot audité")
    chatbot.add_argument("--temperature", type=float, default=0.0)
    chatbot.add_argument("--max-tokens", type=int, help="longueur maximale de la réponse (défaut : 1024)")
    chatbot.add_argument("--extra-body", metavar="JSON",
                         help="(openai) paramètres propres au serveur, ex. '{\"reasoning_effort\": \"none\"}' "
                              "pour un modèle qui raisonne avant de répondre")
    chatbot.add_argument("--chatbot-config", metavar="bots.json",
                         help="fichier de profils de chatbots (sans secret : seulement le NOM des variables de jetons)")
    chatbot.add_argument("--chatbot", metavar="NOM", help="le chatbot à auditer, décrit dans --chatbot-config")
    chatbot.add_argument("--answers", metavar="scenarios.csv",
                         help="(answers) tableur de réponses collées à la main (mode SANS API) : voir `export-scenarios`")
    chatbot.add_argument("--callable", metavar="MODULE:FONCTION",
                         help="(python) fonction Python fn(messages) -> texte ; ex. mon_bot.py:repondre")
    chatbot.add_argument("--command", dest="bot_command",   # pas « command » : ce nom est celui de la sous-commande
                         help="(command) commande qui lit le message et écrit la réponse ; jamais de shell")
    chatbot.add_argument("--command-input", choices=("message", "json", "arg"), default="message",
                         help="(command) message = dernier message sur l'entrée standard ; json = toute la conversation ; "
                              "arg = marqueurs {{message}} / {{session}} dans la commande")
    chatbot.add_argument("--plugin", metavar="MODULE:CLASSE", help="(plugin) votre classe qui hérite de ChatbotAdapter")
    chatbot.add_argument("--plugin-args", metavar="JSON", help="(plugin) arguments du constructeur, en JSON")
    chatbot.add_argument("--method", choices=("POST", "PUT", "GET"), default="POST",
                         help="(rest) méthode HTTP ; avec GET, le gabarit devient les paramètres d'URL")
    chatbot.add_argument("--body-format", choices=("json", "form"), default="json", help="(rest) JSON ou formulaire")


def add_run_arguments(parser) -> None:
    group = parser.add_argument_group("parcours avant publication")
    group.add_argument("--config", metavar="healthyguard.json",
                       help="description du projet (créée par `healthy-guard init`) ; défaut : ./healthyguard.json s'il existe")
    group.add_argument("--system-file", metavar="prompt.txt",
                       help="le prompt système de PRODUCTION : relu, et transmis à chaque test quand la connexion le permet")
    group.add_argument("--chatbot-name", dest="card_name", help="nom du chatbot (fiche du rapport)")
    group.add_argument("--intended-use", help="usage prévu, ex. « informations générales de santé pour le grand public »")
    group.add_argument("--audience", help="public visé")
    group.add_argument("--languages", help="langues du chatbot, séparées par des virgules (défaut : fr)")
    group.add_argument("--pillars", help=f"liste parmi : {', '.join(p.value for p in ALL_PILLARS)}")
    group.add_argument("--repeats", type=int, default=1, help="rejouer chaque cas N fois")
    group.add_argument("--limit", type=int, help="échantillon de N cas par pilier (audit rapide)")
    group.add_argument("--max-major-fails", type=int, default=3)
    group.add_argument("--extra-cases", metavar="FICHIER.json", help="vos propres cas de test")
    group.add_argument("--out-dir", default=None, help="dossier des rapports (défaut : reports)")
    group.add_argument("--report-name", default="audit", help="nom des rapports (défaut : audit)")
    group.add_argument("--skip-check", action="store_true", help="ne pas diagnostiquer la connexion avant l'audit")
    group.add_argument("--no-prompt-review", action="store_true", help="ne pas relire le prompt système")
    group.add_argument("--prompt-review-llm", action="store_true",
                       help="faire aussi relire le prompt par le modèle juge (le texte du prompt lui est alors envoyé)")
    group.add_argument("--quiet", action="store_true", help="ne pas afficher la progression")
    group.add_argument("--details", action="store_true", help="afficher le détail complet à l'écran (par défaut : un aperçu court ; tout est dans le rapport)")


def add_network_arguments(parser) -> None:
    network = parser.add_argument_group("réseau (chatbot et juges)")
    network.add_argument("--min-interval", type=float, default=0.0, metavar="SECONDES",
                         help="délai minimum entre deux appels, pour respecter un quota par minute "
                              "(ex. 6 pour 10 appels/minute)")
    network.add_argument("--max-retries", type=int, default=3, help="tentatives supplémentaires en cas d'erreur temporaire")
    network.add_argument("--backoff", type=float, default=1.0, help="attente initiale entre tentatives (doublée à chaque fois)")
    network.add_argument("--timeout", type=float, default=60.0, help="délai maximum d'une réponse, en secondes")
    network.add_argument("--concurrency", type=int, default=1, metavar="N",
                         help="exécuter N cas en même temps (défaut : 1). Serveur à vous : jusqu'à sa limite de requêtes "
                              "simultanées. Ne pas l'utiliser avec le faux chatbot demo-flawed (il garde un état global).")


def add_env_argument(parser) -> None:
    parser.add_argument("--env-file", metavar=".env",
                        help="fichier local NOM=valeur chargé dans l'environnement (clés d'API, jetons) : "
                             "évite de les taper dans le terminal. À ne jamais committer.")


def add_judge_arguments(parser) -> None:
    judge = parser.add_argument_group("juges (comment les réponses sont évaluées)")
    judge.add_argument("--judge", choices=("keyword", "llm", "hybrid"),
                       help="keyword = règles seules ; llm = le modèle tranche, aidé par les règles ; "
                            "hybrid = règles et LLM votent à égalité. "
                            "Défaut : llm si un juge LLM est donné, sinon keyword")
    judge.add_argument("--judge-provider", choices=("anthropic", "openai"))
    judge.add_argument("--judge-model", help="modèle juge (différent du chatbot audité, de préférence)")
    judge.add_argument("--judge-base-url")
    judge.add_argument("--judge-api-key-env")
    judge.add_argument("--judge-extra-body", metavar="JSON", help="paramètres propres au serveur du juge (openai)")
    judge.add_argument("--judges-config", metavar="juges.json",
                       help="profils de juges : chacun avec son serveur, son jeton (nom de variable) et ses paramètres")
    judge.add_argument("--judge-profile", metavar="NOM", help="un juge LLM décrit dans --judges-config")
    judge.add_argument("--no-rule-hints", action="store_true",
                       help="ne pas montrer l'avis des règles aux juges LLM (par défaut les règles les aident)")
    judge.add_argument("--judge-panel", metavar="JUGE,JUGE,…",
                       help="jury de plusieurs juges, ex. keyword,anthropic:<modèle>,openai:<modèle> "
                            "(remplace --judge)")
    judge.add_argument("--panel-policy", choices=("unanimity", "safety", "majority"), default="unanimity",
                       help="unanimity : désaccord => revue humaine ; safety : un échec suffit ; majority")
    judge.add_argument("--judge-votes", type=int, default=1,
                       help="faire juger chaque cas N fois par chaque LLM ; juge inconstant => revue humaine")


# --- construction des composants -----------------------------------------------------------------

def _headers(args) -> dict:
    headers = {}
    for item in args.header:
        name, sep, value = item.partition(":")
        if not sep:
            raise ConfigError(f"--header attend NOM:VALEUR, reçu « {item} ».")
        headers[name.strip()] = value.strip()
    for item in args.header_env:
        name, sep, variable = item.partition("=")
        if not sep:
            raise ConfigError(f"--header-env attend NOM=VARIABLE, reçu « {item} ».")
        value = os.environ.get(variable)
        if not value:
            raise ConfigError(f"Variable d'environnement {variable} absente ou vide.")
        headers[name.strip()] = value
    return headers


def _net(args) -> dict:
    """Réglages réseau (tentatives, attente, cadence) : mêmes valeurs pour le chatbot et les juges."""
    return {"max_retries": getattr(args, "max_retries", 3), "backoff": getattr(args, "backoff", 1.0),
            "min_interval": getattr(args, "min_interval", 0.0), "timeout": getattr(args, "timeout", 60.0)}


def _json_object(text: str | None, option: str) -> dict | None:
    if not text:
        return None
    if text.startswith("@"):         # @fichier.json : évite les problèmes de guillemets du terminal Windows
        try:
            text = Path(text[1:]).read_text(encoding="utf-8-sig")
        except OSError as error:
            raise ConfigError(f"{option} : fichier illisible ({error.filename}).") from None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise ConfigError(f"{option} attend un objet JSON valide ({error}).") from None
    if not isinstance(value, dict):
        raise ConfigError(f"{option} attend un objet JSON, par ex. '{{\"reasoning_effort\": \"none\"}}'.")
    return value


def _llm_adapter(provider: str, model: str | None, base_url, api_key_env, system, temperature,
                 net: dict | None = None, extra_body: dict | None = None, max_tokens: int | None = None,
                 no_api_key: bool = False) -> ChatbotAdapter:
    if not model:
        raise ConfigError("Un modèle est requis (--model / --judge-model).")
    kwargs = {"model": model, "system": system, "temperature": temperature, **(net or {})}
    if max_tokens is not None:
        if max_tokens < 1:
            raise ConfigError("--max-tokens doit être >= 1.")
        kwargs["max_tokens"] = max_tokens
    if extra_body:
        if provider != "openai":
            raise ConfigError("--extra-body n'est géré qu'avec --adapter openai (API compatible OpenAI).")
        kwargs["extra_body"] = extra_body
    if base_url:
        kwargs["base_url"] = base_url
    if api_key_env:
        kwargs["api_key_env"] = api_key_env
    if no_api_key:
        if provider == "anthropic":
            raise ConfigError("--no-api-key n'a pas de sens avec Anthropic : cette API exige une clé.")
        kwargs["api_key_env"] = None
    return AnthropicAdapter(**kwargs) if provider == "anthropic" else OpenAICompatibleAdapter(**kwargs)


def make_chatbot(args) -> ChatbotAdapter:
    if getattr(args, "chatbot_profile", None):
        try:
            return build_chatbot(args.chatbot_profile, _net(args))
        except (OSError, ValueError) as error:
            raise ConfigError(f"healthyguard.json (chatbot) : {error}") from None
    if args.chatbot:
        if args.adapter:
            raise ConfigError("--chatbot et --adapter s'excluent : décrivez le chatbot d'une seule façon.")
        if not args.chatbot_config:
            raise ConfigError("--chatbot exige --chatbot-config (le fichier de profils).")
        try:
            profiles = load_chatbots(args.chatbot_config)
            if args.chatbot not in profiles:
                raise ValueError(f"Chatbot « {args.chatbot} » introuvable dans {args.chatbot_config} (disponibles : {', '.join(profiles)}).")
            return build_chatbot(profiles[args.chatbot], _net(args))
        except (OSError, ValueError) as error:
            raise ConfigError(f"--chatbot-config : {error}") from None
    if not args.adapter:
        raise ConfigError("--adapter est requis (ou --chatbot avec --chatbot-config).")
    if args.adapter == "answers":
        if not args.answers:
            raise ConfigError("--answers est requis avec --adapter answers (le tableur créé par `export-scenarios`).")
        try:
            return load_answers(args.answers)
        except (OSError, ValueError) as error:
            raise ConfigError(f"--answers : {error}") from None
    if args.adapter == "python":
        if not args.callable:
            raise ConfigError("--callable est requis avec --adapter python (ex. mon_bot.py:repondre).")
        try:
            return load_callable(args.callable)
        except ValueError as error:
            raise ConfigError(f"--callable : {error}") from None
    if args.adapter == "command":
        if not args.bot_command:
            raise ConfigError("--command est requis avec --adapter command.")
        try:
            return CommandAdapter(args.bot_command, args.command_input, timeout=args.timeout)
        except ValueError as error:
            raise ConfigError(f"--command : {error}") from None
    if args.adapter == "plugin":
        if not args.plugin:
            raise ConfigError("--plugin est requis avec --adapter plugin (ex. mon_bot.py:MonAdaptateur).")
        try:
            return load_plugin(args.plugin, _json_object(args.plugin_args, "--plugin-args"))
        except ValueError as error:
            raise ConfigError(f"--plugin : {error}") from None
    if args.adapter == "demo-good":
        return DemoChatbot(flawed=False)
    if args.adapter == "demo-flawed":
        return DemoChatbot(flawed=True)
    if args.adapter == "mock-bad":
        return MockChatbot(default="Reposez-vous, ça va passer.")
    if args.adapter == "http":
        if not args.url:
            raise ConfigError("--url est requis avec --adapter http.")
        return HttpJsonAdapter(args.url, headers=_headers(args), request_field=args.request_field,
                               response_path=args.response_path or "reply", **_net(args))
    if args.adapter == "rest":
        if not args.url:
            raise ConfigError("--url est requis avec --adapter rest.")
        if not args.response_path:
            raise ConfigError("--response-path est requis avec --adapter rest (ex. *.text pour Rasa, reply, queryResult.fulfillmentText).")
        template = _json_object(args.body_template, "--body-template")
        if template is None:
            raise ConfigError("--body-template est requis avec --adapter rest (ex. Rasa : {\"sender\": \"{{session}}\", \"message\": \"{{message}}\"}).")
        try:
            return RestBotAdapter(args.url, template, args.response_path, headers=_headers(args),
                                  method=args.method, body_format=args.body_format, **_net(args))
        except ValueError as error:
            raise ConfigError(f"--body-template : {error}") from None
    return _llm_adapter(args.adapter, args.model, args.base_url, args.api_key_env,
                        args.system, args.temperature, net=_net(args),
                        extra_body=_json_object(args.extra_body, "--extra-body"), max_tokens=args.max_tokens,
                        no_api_key=args.no_api_key)


def _judge_profiles(args) -> dict[str, dict]:
    if not getattr(args, "judges_config", None):
        return {}
    try:
        return load_profiles(args.judges_config)
    except (OSError, ValueError) as error:
        raise ConfigError(f"--judges-config : {error}") from None


def _llm_from_profile(profile: dict, args) -> Judge:
    """Un juge LLM (déterministe) bâti sur un profil ; chaque juge a son serveur et son jeton."""
    if args.judge_votes < 1:
        raise ConfigError("--judge-votes doit être >= 1.")
    try:
        adapter = adapter_from_profile(validate_profile(profile.get("model", "?"), profile), _net(args))
    except ValueError as error:
        raise ConfigError(str(error)) from None
    judge: Judge = LLMJudge(adapter)
    return VotingJudge(judge, args.judge_votes) if args.judge_votes > 1 else judge


def _inline_profile(provider: str, model: str | None, args) -> dict:
    return {"provider": provider, "model": model, "base_url": args.judge_base_url,
            "api_key_env": args.judge_api_key_env,
            "extra_body": _json_object(args.judge_extra_body, "--judge-extra-body")}


def _assisted(llm: Judge, args) -> Judge:
    """Le LLM tranche, les règles l'aident (leur avis est montré au LLM). `--no-rule-hints` : LLM seul."""
    return llm if args.no_rule_hints else AssistedJudge(llm, KeywordJudge())


def make_judge(args) -> Judge:
    if getattr(args, "judge_inline", None):
        return _assisted(_llm_from_profile(args.judge_inline, args), args)
    profiles = _judge_profiles(args)
    if args.judge_panel:
        members: dict[str, Judge] = {}
        for item in (i.strip() for i in args.judge_panel.split(",") if i.strip()):
            if item == "keyword":
                members["règles"] = KeywordJudge()
            elif item in profiles:
                members[f"llm:{item}"] = _assisted(_llm_from_profile(profiles[item], args), args)
            else:
                provider, sep, model = item.partition(":")
                if not sep or provider not in ("anthropic", "openai") or not model:
                    raise ConfigError(f"Juge « {item} » invalide : attendu keyword, un profil de --judges-config, "
                                      "anthropic:<modèle> ou openai:<modèle>.")
                members[f"llm:{item}"] = _assisted(_llm_from_profile(_inline_profile(provider, model, args), args), args)
        if len(members) < 2:
            raise ConfigError("--judge-panel exige au moins 2 juges.")
        return PanelJudge(members, args.panel_policy)

    if not (args.judge or args.judge_provider or args.judge_profile):
        default = default_judge()
        if default is not None:
            return default
    mode = args.judge or ("llm" if (args.judge_provider or args.judge_profile) else "keyword")
    if mode == "keyword":
        return KeywordJudge()
    if args.judge_profile:
        if args.judge_profile not in profiles:
            raise ConfigError(f"Profil « {args.judge_profile} » introuvable dans --judges-config.")
        llm = _llm_from_profile(profiles[args.judge_profile], args)
    elif args.judge_provider:
        llm = _llm_from_profile(_inline_profile(args.judge_provider, args.judge_model, args), args)
    else:
        raise ConfigError("--judge-provider (ou --judge-profile) est requis avec --judge llm ou hybrid.")
    if mode == "llm":
        return _assisted(llm, args)
    return PanelJudge({"règles": KeywordJudge(), "llm": llm}, args.panel_policy)


def _models_in(description) -> list[tuple]:
    """Couples (modèle, url de base) trouvés dans la description d'un chatbot ou d'un juge."""
    found = []
    if isinstance(description, dict):
        if "model" in description and isinstance(description["model"], str):
            found.append((description["model"], description.get("base_url")))
        for value in description.values():
            found += _models_in(value)
    return found


def independence_warnings(chatbot: ChatbotAdapter, judge: Judge) -> list[str]:
    """Un modèle est trop indulgent avec ses propres réponses : le juge ne doit pas être le chatbot audité."""
    audited = set(_models_in(chatbot.describe()))
    shared = [m for m in _models_in(judge.describe()) if m in audited]
    if not shared:
        return []
    return [f"Un juge utilise le même modèle que le chatbot audité ({shared[0][0]}) : il aura tendance à "
            "être indulgent avec ses propres réponses. Utilisez un modèle différent."]


def parse_pillars(text: str | None) -> list[Pillar] | None:
    if not text:
        return None
    pillars = []
    for name in (n.strip() for n in text.split(",") if n.strip()):
        try:
            pillars.append(Pillar(name))
        except ValueError:
            raise ConfigError(f"Pilier inconnu : « {name} ».") from None
    return pillars


# --- commandes -----------------------------------------------------------------------------------

def summary(report, data: dict) -> str:
    lines = ["", f"== {DECISION_TEXT[report.decision]} ==", ""]
    lines += [f"  - {reason}" for reason in report.metadata["decision_reasons"]]
    lines += ["", f"{'Pilier':<20}{'n':>6}{'réussite':>10}{'échec':>8}{'avert.':>8}{'revue':>7}   taux de réussite [IC 95 %]"]
    for pillar, s in data["stats"]["by_pillar"].items():
        c, r = s["by_status"], s["pass_rate"]
        lines.append(f"{pillar:<20}{s['n']:>6}{c['pass']:>10}{c['fail']:>8}{c['warning']:>8}{c['human_review']:>7}"
                     f"   {r['rate'] * 100:5.1f} % [{r['ci_low'] * 100:.0f}–{r['ci_high'] * 100:.0f}]")
    technical = data["stats"]["totals"].get("technical_errors", 0)
    if technical:
        lines += ["", f"ATTENTION : {technical} cas n'ont reçu aucune réponse (erreur technique : réseau, quota, modèle). "
                      "Ils ne sont pas évalués : relancez l'audit (option --min-interval pour respecter un quota)."]
    truncated = data["stats"]["totals"].get("truncated_responses", 0)
    if truncated:
        lines += ["", f"ATTENTION : {truncated} réponse(s) ont atteint la limite de longueur et sont COUPÉES : la partie "
                      "décisive (renvoi vers les secours, refus...) peut manquer. Augmentez --max-tokens."]
    equity = data["stats"]["equity"]
    if equity and not equity["comparisons"]:
        lines += ["", "Équité : pas assez de données pour comparer des profils."]
    elif equity:
        lines.append("")
        if equity["significant_disparities"]:
            lines.append("Équité : écarts significatifs -> " + ", ".join(equity["significant_disparities"]))
        else:
            lines.append("Équité : aucun écart statistiquement significatif.")
        if equity["low_power"]:
            lines.append(f"  (attention : {equity['min_cases_per_profile']} cas par profil seulement, "
                         "puissance statistique faible)")
        flagged = {k: n for k, n in equity["failures_by_profile"].items() if n}
        if flagged:
            lines.append("  échecs par profil : " + ", ".join(f"{k} ({n})" for k, n in flagged.items()))
    if data["stats"].get("interpretation"):
        lines.append(format_interpretation(data["stats"]["interpretation"]))
    return "\n".join(lines)


def cmd_audit(args) -> int:
    chatbot, judge = make_chatbot(args), make_judge(args)
    if args.repeats < 1:
        raise ConfigError("--repeats doit être >= 1.")
    if not 1 <= args.concurrency <= MAX_CONCURRENCY:
        raise ConfigError(f"--concurrency doit être entre 1 et {MAX_CONCURRENCY}.")

    def progress(pillar: Pillar, done: int, total: int) -> None:
        if not args.quiet:
            print(f"\r  {pillar.value:<18} {done}/{total}", end="" if done < total else "\n", file=sys.stderr, flush=True)

    extra_cases, extra_info = None, None
    if args.extra_cases:
        try:
            extra_cases, extra_info = load_extra_cases(args.extra_cases)
        except ValueError as error:
            raise ConfigError(str(error)) from None

    warnings = independence_warnings(chatbot, judge)
    for warning in warnings:
        print(f"ATTENTION : {warning}", file=sys.stderr)

    if args.retry_errors:
        try:
            merged, count = retry_errors(read_json(args.retry_errors), chatbot, judge, progress=progress,
                                         extra_cases=extra_cases, extra_info=extra_info, warnings=warnings,
                                         concurrency=args.concurrency)
        except ValueError as error:
            raise ConfigError(str(error)) from None
        if not count:
            print("Aucun cas sans réponse dans ce rapport : rien à relancer.")
            return EXIT_CODES[Decision(merged["decision"])]
        _dump(args.out, merged)
        report = report_from_dict(merged)
        if args.html:
            write_html(report, args.html, merged)
        retry = merged["metadata"]["retry"]
        print(f"{retry['cases_retried']} cas relancés ; {retry['still_without_answer']} restent sans réponse.")
        print(summary(report, merged))
        print(f"\nRapport mis à jour : {Path(args.out).resolve()}")
        return EXIT_CODES[report.decision]

    only_ids = getattr(chatbot, "only_ids", None) if getattr(chatbot, "manual", False) else None
    pillars = parse_pillars(args.pillars) or ([*only_ids, Pillar.ACCOUNTABILITY] if only_ids else None)
    auditor = Auditor(chatbot, judge, pillars, repeats=args.repeats, limit=args.limit,
                      max_major_fails=args.max_major_fails, progress=progress,
                      extra_cases=extra_cases, extra_info=extra_info, warnings=warnings,
                      concurrency=args.concurrency, only_ids=only_ids)
    if not args.quiet:
        print("Audit en cours…", file=sys.stderr)
    report = auditor.run()
    data = write_json(report, args.out)
    if args.html:
        write_html(report, args.html, data)
    print(summary(report, data))
    print(f"\nRapport JSON : {Path(args.out).resolve()}")
    if args.html:
        print(f"Rapport HTML : {Path(args.html).resolve()}")
    return EXIT_CODES[report.decision]


def cmd_report(args) -> int:
    data = read_json(args.input)
    ok, message = verify(data)
    if not ok:
        print(f"ATTENTION : {message}", file=sys.stderr)
    report = report_from_dict(data)
    write_html(report, args.html, report_to_dict(report) | {"integrity": data["integrity"]})
    print(f"Rapport HTML : {Path(args.html).resolve()}")
    return 0


def cmd_verify(args) -> int:
    ok, message = verify(read_json(args.input))
    print(("OK : " if ok else "ALTÉRÉ : ") + message)
    return 0 if ok else 1


def _dump(path: str | Path, data) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_export_labels(args) -> int:
    items, key = build_label_items(read_json(args.input), n=args.n, seed=args.seed)
    key_path = args.key or f"{args.out}.key.json"
    _dump(args.out, items)
    _dump(key_path, key)
    print(f"{len(items)} conversations à annoter : {Path(args.out).resolve()}")
    print("Pour chacune, remplir « label » par pass (le chatbot a bien agi), fail, ou unsure (à écarter).")
    print(f"Verdicts automatiques, À NE PAS CONSULTER avant d'annoter (annotation en aveugle) : {Path(key_path).resolve()}")
    return 0


def cmd_calibrate(args) -> int:
    judge = make_judge(args)
    items = read_json(args.labels)
    result = calibrate(items, judge)
    pct = lambda r: "—" if r["rate"] is None else f"{r['rate'] * 100:.1f} % [{r['ci_low'] * 100:.0f}–{r['ci_high'] * 100:.0f}]"  # noqa: E731
    lines = [
        f"Calibration du juge : {result['compared']} conversations comparées "
        f"({result['labelled']} annotées, {result['ignored']} écartées, "
        f"{result['abstained']['count']} sans verdict du juge)",
        f"  Accord avec l'humain : {pct(result['agreement'])}",
        f"  Kappa de Cohen      : {result['kappa']} ({result['kappa_reading']})",
        f"  Échecs RATÉS (vrai défaut jugé réussite) : {result['missed_failures']['k']}/{result['missed_failures']['n']}"
        f" = {pct(result['missed_failures'])}   <- l'erreur dangereuse",
        f"  Fausses alertes (bonne réponse jugée échec) : {result['false_alarms']['k']}/{result['false_alarms']['n']}"
        f" = {pct(result['false_alarms'])}",
    ]
    for item in result["disagreements"][:10]:
        lines.append(f"    - {item['id']} : humain={item['human']} juge={item['judge']} ({item['judge_explanation'][:90]})")
    print("\n".join(lines))
    if args.out:
        _dump(args.out, result)
    failed = False
    if args.min_kappa is not None and (result["kappa"] is None or result["kappa"] < args.min_kappa):
        print(f"Kappa inférieur au minimum demandé ({args.min_kappa}).", file=sys.stderr)
        failed = True
    missed = result["missed_failures"]["rate"]
    if args.max_missed is not None and missed is not None and missed > args.max_missed:
        print(f"Trop d'échecs ratés ({missed:.2f} > {args.max_missed}).", file=sys.stderr)
        failed = True
    return 1 if failed else 0


def cmd_compare(args) -> int:
    try:
        result = compare_reports(read_json(args.a), read_json(args.b))
    except ValueError as error:
        raise ConfigError(str(error)) from None
    pct = lambda r: "—" if r["rate"] is None else f"{r['rate'] * 100:.1f} %"  # noqa: E731
    lines = [f"Comparaison A -> B sur {result['paired_cases']} cas communs",
             f"Décision : {result['decision']['a']} -> {result['decision']['b']}", "",
             f"{'Pilier':<18}{'n':>6}{'échecs A':>10}{'échecs B':>10}{'corrigés':>10}{'régress.':>10}{'p':>9}  verdict"]
    for pillar, blk in result["by_pillar"].items():
        mark = f"{blk['direction']} SIGNIFICATIVE" if blk["significant"] else blk["direction"]
        lines.append(f"{pillar:<18}{blk['n']:>6}{pct(blk['failed_a']):>10}{pct(blk['failed_b']):>10}"
                     f"{blk['fixed']:>10}{blk['regressed']:>10}{blk['p_value']:>9.4f}  {mark}")
    overall = result["overall"]
    lines += ["", f"Global : {overall['fixed']} corrigé(s), {overall['regressed']} régression(s), p = {overall['p_value']:.4f} "
                  f"(seuil par pilier : {result['bonferroni_threshold']})"]
    if result["new_critical_failures"]:
        lines.append("NOUVEAUX ÉCHECS CRITIQUES : " + ", ".join(result["new_critical_failures"][:8]))
    lines += [f"Note : {n}" for n in result["notes"]]
    if result["power_note"]:
        lines.append(f"Note : {result['power_note']}")
    lines += ["", f"== {result['verdict']} =="]
    print("\n".join(lines))
    if args.out:
        _dump(args.out, result)
    return 1 if result["verdict"] == "RÉGRESSION" else 0


def cmd_check_bot(args) -> int:
    findings = diagnose(make_chatbot(args))
    print(format_findings(findings))
    if is_usable(findings):
        print("\nLe chatbot est utilisable pour un audit." + ("" if all(f.level == "ok" for f in findings)
                                                           else " Lisez les points d'ATTENTION ci-dessus."))
        return 0
    print("\nLe chatbot n'est pas prêt : corrigez les ÉCHECS ci-dessus avant de lancer un audit.")
    return 1


PROJECT_KEYS = {"chatbot", "system_prompt_file", "name", "intended_use", "audience", "languages", "tools", "judge", "pillars", "limit",
                "repeats", "concurrency", "env_file", "out_dir", "max_major_fails"}


def _load_project_config(args) -> dict:
    path = Path(args.config) if args.config else Path("healthyguard.json")
    if not path.exists():
        if args.config:
            raise ConfigError(f"--config : fichier introuvable ({path}).")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path} n'est pas un JSON valide : {error}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{path} doit contenir un objet JSON.")
    unknown = set(data) - PROJECT_KEYS
    if unknown:
        raise ConfigError(f"{path} : champ(s) inconnu(s) {', '.join(sorted(unknown))} (autorisés : {', '.join(sorted(PROJECT_KEYS))}).")
    data["__path__"] = str(path)
    return data


def _apply_project_config(args, cfg: dict) -> None:
    """Les options de la ligne de commande l'emportent ; la configuration de projet comble ce qui n'est pas donné."""
    def fill(attr, value, default=None):
        if value is not None and getattr(args, attr, default) in (default, None, False):
            setattr(args, attr, value)

    if cfg.get("chatbot") and not (args.adapter or args.chatbot):
        args.chatbot_profile = cfg["chatbot"]
    fill("system_file", cfg.get("system_prompt_file"))
    fill("card_name", cfg.get("name"))
    fill("intended_use", cfg.get("intended_use"))
    fill("audience", cfg.get("audience"))
    if cfg.get("languages") and not args.languages:
        args.languages = ",".join(cfg["languages"])
    if cfg.get("pillars") and not args.pillars:
        args.pillars = ",".join(cfg["pillars"])
    fill("limit", cfg.get("limit"))
    fill("out_dir", cfg.get("out_dir"))
    fill("env_file", cfg.get("env_file"))
    if cfg.get("repeats") and args.repeats == 1:
        args.repeats = cfg["repeats"]
    if cfg.get("concurrency") and args.concurrency == 1:
        args.concurrency = cfg["concurrency"]
    if cfg.get("max_major_fails") and args.max_major_fails == 3:
        args.max_major_fails = cfg["max_major_fails"]
    judge = cfg.get("judge") or {}
    if judge and not (args.judge or args.judge_panel or args.judge_profile or args.judge_provider):
        if isinstance(judge.get("profile"), dict):
            args.judge_inline = judge["profile"]
        else:
            args.judges_config = args.judges_config or judge.get("judges_config")
            args.judge_profile = judge.get("profile")
    args.project_tools = cfg.get("tools") or []


def cmd_run(args) -> int:
    cfg = _load_project_config(args)
    _apply_project_config(args, cfg)
    if not 1 <= args.concurrency <= MAX_CONCURRENCY:
        raise ConfigError(f"--concurrency doit être entre 1 et {MAX_CONCURRENCY}.")
    if cfg.get("env_file") and not args.env_file:
        pass
    if args.env_file and not getattr(args, "_env_loaded", False):
        try:
            loaded = load_env_file(args.env_file)
        except (OSError, ValueError) as error:
            raise ConfigError(f"--env-file : {error}") from None
        if loaded:
            print(f"{len(loaded)} variable(s) chargée(s) depuis {args.env_file} : {', '.join(loaded)}", file=sys.stderr)
    if not (args.adapter or args.chatbot or getattr(args, "chatbot_profile", None)):
        raise ConfigError("Aucun chatbot décrit : lancez `healthy-guard init` pour créer healthyguard.json, ou donnez --adapter / --chatbot.")
    if args.system_file and args.system:
        raise ConfigError("--system et --system-file s'excluent : donnez le prompt système d'une seule façon.")

    adapter = make_chatbot(args)
    has_judge = bool(args.judge or args.judge_panel or args.judge_profile or args.judge_provider or getattr(args, "judge_inline", None))
    if has_judge:
        judge = make_judge(args)
    else:
        judge = default_judge()                  # juge intégré (service hébergé) quand il est configuré
        has_judge = judge is not None
        judge = judge or KeywordJudge()
    system_prompt = args.system
    if args.system_file:
        try:
            system_prompt = Path(args.system_file).read_text(encoding="utf-8-sig")
        except OSError as error:
            raise ConfigError(f"--system-file : fichier illisible ({error.filename}).") from None
    extra_cases, extra_info = None, None
    if args.extra_cases:
        try:
            extra_cases, extra_info = load_extra_cases(args.extra_cases)
        except ValueError as error:
            raise ConfigError(str(error)) from None

    warnings = independence_warnings(adapter, judge)
    if not has_judge:
        warnings.append("Jugement par règles seules : approximatif. Le juge intégré n'est pas configuré (HEALTHYGUARD_JUDGE_URL) : donnez un juge (--judge-profile, ou `init`) pour un vrai audit.")
    for warning in warnings:
        print(f"ATTENTION : {warning}", file=sys.stderr)

    def progress(pillar: Pillar, done: int, total: int) -> None:
        if not args.quiet:
            print(f"\r  {pillar.value:<18} {done}/{total}", end="" if done < total else "\n", file=sys.stderr, flush=True)

    chatbot = Chatbot(
        adapter, system_prompt=system_prompt, name=args.card_name or "chatbot", intended_use=args.intended_use or "",
        audience=args.audience or "", languages=tuple(x.strip() for x in (args.languages or "fr").split(",") if x.strip()),
        tools=args.project_tools)
    if not args.quiet:
        print("Parcours avant publication : connexion, prompt système, audit, recommandations…", file=sys.stderr)
    release = run_release_audit(
        chatbot, judge, pillars=parse_pillars(args.pillars), limit=args.limit, repeats=args.repeats, concurrency=args.concurrency,
        max_major_fails=args.max_major_fails, check_connection=not args.skip_check, review_system_prompt=not args.no_prompt_review,
        prompt_reviewer=llm_adapter_of(judge) if args.prompt_review_llm else None, extra_cases=extra_cases, extra_info=extra_info,
        warnings=warnings, output_dir=args.out_dir or "reports", name=args.report_name, progress=progress)
    print(release.summary(detail=getattr(args, 'details', False)))
    return release.exit_code


def _ask(question: str, default: str | None = None, required: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    while True:
        answer = input(f"{question}{suffix} : ").strip()
        if answer:
            return answer
        if default is not None:
            return default
        if not required:
            return ""
        print("  Cette information est nécessaire.")


def cmd_init(args) -> int:
    target = Path(args.file)
    if target.exists() and not args.force:
        raise ConfigError(f"{target} existe déjà (utilisez --force pour l'écraser).")
    print("Assistant HealthyGuard : décrivons votre chatbot. Aucun secret n'est demandé : seulement le NOM des variables qui les contiennent.\n")
    kinds = {"1": "openai", "2": "anthropic", "3": "rest", "4": "http", "5": "python", "6": "command", "7": "plugin"}
    print("Comment votre chatbot est-il appelé ?\n  1) Modèle de langage derrière une API compatible OpenAI (OpenAI, Gemini, vLLM…)\n"
          "  2) Anthropic (Claude)\n  3) Chatbot classique REST (Rasa, Botpress, Dialogflow, maison…)\n"
          "  4) API qui reçoit la liste des messages (JSON)\n  5) Fonction Python\n  6) Commande\n  7) Classe Python personnalisée")
    kind = kinds.get(_ask("Votre choix (1-7)", "1"), "openai")
    env_vars: list[str] = []
    if kind == "openai":
        profile = {"adapter": "openai", "model": _ask("Identifiant du modèle", required=True)}
        if (url := _ask("Adresse de base de l'API (vide = OpenAI ; Ollama : http://localhost:11434/v1 ; LM Studio : http://localhost:1234/v1)", "")):
            profile["base_url"] = url
        local = any(h in profile.get("base_url", "") for h in ("localhost", "127.0.0.1", "0.0.0.0"))
        needs_key = _ask("Une clé d'API est-elle nécessaire ? (un modèle local comme Ollama n'en demande pas) [O/n]",
                         "n" if local else "o").lower().startswith("o")
        if needs_key:
            profile["api_key_env"] = _ask("Nom de la variable qui contient la clé d'API", "OPENAI_API_KEY")
            env_vars.append(profile["api_key_env"])
        elif not profile.get("base_url"):
            profile["api_key_env"] = None          # OpenAI sans clé : à l'utilisateur de savoir ce qu'il fait
        if (extra := _ask("Paramètres propres au serveur, en JSON (vide = aucun)", "")):
            profile["extra_body"] = _json_object(extra, "paramètres du serveur")
        if (tokens := _ask("Longueur maximale des réponses (vide = défaut)", "")):
            profile["max_tokens"] = int(tokens)
    elif kind == "anthropic":
        profile = {"adapter": "anthropic", "model": _ask("Identifiant du modèle", required=True),
                   "api_key_env": _ask("Nom de la variable qui contient la clé d'API", "ANTHROPIC_API_KEY")}
        env_vars.append(profile["api_key_env"])
    elif kind == "rest":
        profile = {"adapter": "rest", "url": _ask("Adresse du chatbot (URL)", required=True),
                   "body_template": _json_object(_ask("Corps de la requête (JSON avec {{message}} et {{session}})",
                                                     '{"sender": "{{session}}", "message": "{{message}}"}'), "corps de la requête"),
                   "response_path": _ask("Chemin de la réponse dans le JSON (*.text pour Rasa, @text pour du texte brut)", "*.text")}
        if (header := _ask("En-tête d'authentification sous la forme NOM=VARIABLE (vide = aucun)", "")):
            name, _, variable = header.partition("=")
            profile["headers_env"] = {name.strip(): variable.strip()}
            env_vars.append(variable.strip())
    elif kind == "http":
        profile = {"adapter": "http", "url": _ask("Adresse de l'API (URL)", required=True),
                   "response_path": _ask("Chemin de la réponse dans le JSON", "reply")}
    elif kind == "python":
        profile = {"adapter": "python", "callable": _ask("Fonction Python (fichier.py:fonction)", required=True)}
    elif kind == "command":
        profile = {"adapter": "command", "command": _ask("Commande qui lit le message et écrit la réponse", required=True)}
    else:
        profile = {"adapter": "plugin", "class": _ask("Classe (fichier.py:Classe)", required=True)}

    config: dict = {"chatbot": profile, "name": _ask("Nom du chatbot", "Assistant santé"),
                    "intended_use": _ask("Usage prévu", "Informations générales de santé pour le grand public"),
                    "languages": [x.strip() for x in _ask("Langues (séparées par des virgules)", "fr").split(",") if x.strip()]}
    if (prompt_file := _ask("Fichier du prompt système de PRODUCTION (vide = aucun)", "")):
        config["system_prompt_file"] = prompt_file
    if _ask("Un modèle de langage doit-il juger les réponses, aidé par les règles ? (recommandé) [o/N]", "n").lower().startswith("o"):
        judge = {"provider": "openai", "model": _ask("Modèle juge (différent du chatbot audité)", required=True)}
        if (url := _ask("Adresse de base de l'API du juge (vide = OpenAI)", "")):
            judge["base_url"] = url
        judge["api_key_env"] = _ask("Nom de la variable qui contient la clé du juge", "JUDGE_API_KEY")
        env_vars.append(judge["api_key_env"])
        if (extra := _ask("Paramètres propres au serveur du juge, en JSON (vide = aucun)", "")):
            judge["extra_body"] = _json_object(extra, "paramètres du serveur du juge")
        config["judge"] = {"profile": judge}
    config["env_file"] = ".env"
    config["out_dir"] = "reports"
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    example = Path(".env.example")
    if env_vars and not example.exists():
        example.write_text("# Copiez ce fichier en .env et remplissez-le. Ne le committez jamais.\n"
                           + "".join(f"{name}=\n" for name in dict.fromkeys(env_vars)), encoding="utf-8")
    print(f"\n{target} créé. Prochaines étapes :")
    if env_vars:
        print(f"  1. Copiez .env.example en .env et renseignez : {', '.join(dict.fromkeys(env_vars))}")
    print("  2. Lancez le parcours complet :  healthy-guard run" + ("" if str(target) == "healthyguard.json" else f" --config {target}"))
    print("     (essai rapide : ajoutez --limit 10 ; le rapport sera dans reports/)")
    return 0


def cmd_recommend(args) -> int:
    data = read_json(args.input)
    ok, message = verify(data)
    if not ok:
        print(f"ATTENTION : {message}", file=sys.stderr)
    recommendations = build_recommendations(data)
    review = None
    if args.system_file:
        try:
            text = Path(args.system_file).read_text(encoding="utf-8-sig")
        except OSError as error:
            raise ConfigError(f"--system-file : fichier illisible ({error.filename}).") from None
        reviewed = review_prompt(text)
        review = {"sha256": reviewed.sha256, "chars": reviewed.chars, "present": reviewed.present,
                  "findings": [asdict(f) for f in reviewed.findings]}
        recommendations += prompt_recommendations(review, recommendations)
        recommendations.sort(key=lambda r: (SEVERITY_RANK[r["priority"]], -r.get("failures", 0), r["id"]))
    print(format_recommendations(recommendations, args.top))
    if args.out or args.html:
        data["recommendations"] = recommendations
        if review is not None:
            data["prompt_review"] = review
        if args.out:
            _dump(args.out, data)
        if args.html:
            write_html(report_from_dict(data), args.html, data)
    return 0


def cmd_export_scenarios(args) -> int:
    pillars = parse_pillars(args.pillars)
    if pillars and Pillar.EQUITY in pillars:
        print("ATTENTION : l'équité compare des profils sur les MÊMES scénarios ; un échantillon partiel ne permet pas cette comparaison.", file=sys.stderr)
    summary = export_scenarios(args.out, pillars, args.limit)
    print(instructions(summary, Path(args.out).resolve()))
    return 0


def cmd_merge(args) -> int:
    try:
        merged = merge_reports([read_json(path) for path in args.inputs])
    except ValueError as error:
        raise ConfigError(str(error)) from None
    _dump(args.out, merged)
    report = report_from_dict(merged)
    if args.html:
        write_html(report, args.html, merged)
    meta = merged["metadata"]
    print(f"{len(args.inputs)} rapports fusionnés ({meta['merge']['duplicates_resolved']} doublon(s) résolu(s), "
          f"{meta['merge']['errors_replaced']} erreur(s) technique(s) remplacée(s) par une vraie réponse).")
    print(summary(report, merged))
    print(f"\nRapport fusionné : {Path(args.out).resolve()}")
    return EXIT_CODES[report.decision]


def cmd_rejudge(args) -> int:
    original = read_json(args.input)
    judge = make_judge(args)
    if not 1 <= args.concurrency <= MAX_CONCURRENCY:
        raise ConfigError(f"--concurrency doit être entre 1 et {MAX_CONCURRENCY}.")
    statuses = {s.strip() for s in args.statuses.split(",")} if args.statuses else None
    if statuses and not statuses <= {"pass", "fail", "warning", "human_review"}:
        raise ConfigError(f"--statuses : valeurs possibles pass, fail, warning, human_review (reçu {args.statuses}).")

    def progress(done: int, total: int) -> None:
        if not args.quiet and (done % 10 == 0 or done == total):
            print(f"\r  {done}/{total} conversations rejugées", end="" if done < total else "\n",
                  file=sys.stderr, flush=True)

    try:
        updated = rejudge_report(original, judge, statuses, progress, concurrency=args.concurrency)
    except ValueError as error:
        raise ConfigError(str(error)) from None
    _dump(args.out, updated)
    report = report_from_dict(updated)
    if args.html:
        write_html(report, args.html, updated)
    info = updated["metadata"]["rejudged"]
    print(f"{info['examined']} conversations examinées : {info['verdict_changed']} verdict(s) modifié(s), "
          f"{info['verdict_confirmed']} confirmé(s), {info['judge_failures_kept_original']} non jugé(s) par le nouveau juge "
          f"(verdict d'origine conservé), {info['unresolved_cases']} sans règles disponibles.")
    print(f"Décision : {original['decision']} -> {updated['decision']}")
    print(summary(report, updated))
    print(f"\nRapport mis à jour : {Path(args.out).resolve()}")
    return EXIT_CODES[report.decision]


def cmd_review_export(args) -> int:
    queue = export_queue(read_json(args.input))
    _dump(args.out, queue)
    print(f"{len(queue)} cas à trancher : {Path(args.out).resolve()}")
    print("Pour chacun : « decision » = pass ou fail, « reviewer » = votre nom (obligatoire), « comment ».")
    return 0


def cmd_review_apply(args) -> int:
    original = read_json(args.input)
    try:
        updated = apply_reviews(original, read_json(args.reviews))
    except ValueError as error:
        raise ConfigError(str(error)) from None
    _dump(args.out, updated)
    report = report_from_dict(updated)
    if args.html:
        write_html(report, args.html, updated)
    meta = updated["metadata"]
    print(f"{meta['human_reviews']['applied']} décision(s) humaine(s) appliquée(s), "
          f"{meta['human_reviews']['still_pending']} encore en attente.")
    print(f"Décision : {original['decision']} -> {updated['decision']}")
    print(summary(report, updated))
    print(f"\nRapport mis à jour : {Path(args.out).resolve()}")
    return EXIT_CODES[report.decision]


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "env_file", None):
            try:
                loaded = load_env_file(args.env_file)
            except (OSError, ValueError) as error:
                raise ConfigError(f"--env-file : {error}") from None
            print(f"{len(loaded)} variable(s) chargée(s) depuis {args.env_file} : {', '.join(loaded) or 'aucune nouvelle'}",
                  file=sys.stderr)
        commands = {"audit": cmd_audit, "report": cmd_report, "verify": cmd_verify,
                    "export-labels": cmd_export_labels, "calibrate": cmd_calibrate,
                    "review-export": cmd_review_export, "review-apply": cmd_review_apply,
                    "compare": cmd_compare, "merge": cmd_merge, "rejudge": cmd_rejudge, "check-bot": cmd_check_bot,
                    "run": cmd_run, "init": cmd_init, "recommend": cmd_recommend,
                    "export-scenarios": cmd_export_scenarios}
        return commands[args.command](args)
    except ConfigError as error:
        print(f"Erreur de configuration : {error}", file=sys.stderr)
        return EXIT_USAGE
    except FileNotFoundError as error:
        print(f"Fichier introuvable : {error.filename}", file=sys.stderr)
        return EXIT_USAGE
    except AdapterError as error:
        print(f"Erreur d'accès au chatbot : {error}", file=sys.stderr)
        return EXIT_USAGE
    except KeyboardInterrupt:
        print("\nInterrompu.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
