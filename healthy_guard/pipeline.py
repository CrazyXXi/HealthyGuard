"""Le parcours complet AVANT PUBLICATION : connexion, prompt système, audit du comportement, recommandations, rapport.

    1. Connexion   : le chatbot répond-il, sa réponse est-elle bien lue, garde-t-il le fil, fuit-il entre conversations ? (checkbot)
    2. Prompt      : le texte du prompt système a-t-il les garde-fous ? contient-il des instructions dangereuses ? (promptreview)
    3. Audit       : cinq piliers, centaines de scénarios, prompt de production appliqué, jugés par les règles puis par un modèle (auditor)
    4. Conseils    : chaque échec devient une action concrète, avec un extrait de prompt à relire (recommendations)
    5. Rapport     : décision (autorisé / revue humaine / bloqué), recommandations, périmètre couvert et non couvert (JSON + HTML)

Aucune étape n'est dans le chatbot : tout se fait par ses réponses (boîte noire).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from healthy_guard.adapters.base import ChatbotAdapter
from healthy_guard.auditor import Auditor
from healthy_guard.chatbot import Chatbot
from healthy_guard.checkbot import Finding, diagnose, format_findings, is_usable
from healthy_guard.judges.base import Judge
from healthy_guard.models import Decision, Pillar
from healthy_guard.promptreview import PromptFinding, review_prompt, review_with_llm
from healthy_guard.recommendations import SEVERITY_RANK, SUGGESTION_NOTE, build_recommendations, format_recommendations
from healthy_guard.reporting.html_report import write_html
from healthy_guard.reporting.json_report import report_to_dict, write_json

EXIT_CODES = {Decision.PASS: 0, Decision.REVIEW_REQUIRED: 1, Decision.BLOCKED: 2}
EXIT_NOT_READY = 64


@dataclass
class Release:
    """Résultat du parcours complet. `report` est None si le chatbot n'était pas joignable (l'audit n'a pas eu lieu)."""
    chatbot: Chatbot
    connection: list[Finding]
    prompt_review: dict | None
    report: dict | None = None
    recommendations: list[dict] = field(default_factory=list)
    paths: dict = field(default_factory=dict)
    scope: dict = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.report is not None

    @property
    def decision(self) -> str | None:
        return self.report["decision"] if self.report else None

    @property
    def passed(self) -> bool:
        return self.decision == Decision.PASS.value

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[Decision(self.decision)] if self.report else EXIT_NOT_READY

    def summary(self, max_recommendations: int = 5) -> str:
        lines = [f"== {self.chatbot.name} =="]
        lines += ["", "Connexion :", format_findings(self.connection)]
        if not self.ready:
            return "\n".join(lines + ["", "L'audit n'a pas eu lieu : corrigez d'abord la connexion."])
        if self.prompt_review is not None:
            bad = [f for f in self.prompt_review["findings"] if f["level"] in ("dangerous", "missing")]
            lines += ["", f"Prompt système : {len(self.prompt_review['present'])} garde-fou(s) présent(s), {len(bad)} constat(s) à traiter."]
        decision = {"pass": "DÉPLOIEMENT POSSIBLE", "review_required": "REVUE HUMAINE REQUISE AVANT DÉPLOIEMENT",
                    "blocked": "DÉPLOIEMENT BLOQUÉ"}[self.decision]
        lines += ["", f"== {decision} =="]
        lines += [f"  - {reason}" for reason in self.report["metadata"].get("decision_reasons", [])]
        totals = self.report["stats"]["totals"]
        lines.append(f"\n{totals['unique_cases']} scénarios, {totals['by_status']['fail']} échec(s), {totals['by_status']['human_review']} à relire.")
        lines += ["", "Recommandations (les plus urgentes) :", format_recommendations(self.recommendations, max_recommendations)]
        if self.scope.get("limits"):
            lines += ["", "Limites de cet audit :"] + [f"  - {x}" for x in self.scope["limits"]]
        if self.paths:
            lines += ["", "Rapports : " + " ; ".join(str(p) for p in self.paths.values())]
        return "\n".join(lines)


# Un garde-fou absent du prompt est la cause racine de plusieurs thèmes d'échec : « orientation vers les secours » absente explique les
# échecs d'urgence simples, sous pression et suicidaires.
ROOT_CAUSES = {"urgence": ("urgence", "urgence_pression", "suicide", "sur_alerte"), "diagnostic": ("diagnostic",), "identite": ("identite",),
               "tiers": ("tiers", "injection"), "memoire": ("memoire",), "equite": ("equite",), "donnees": ("donnees", "capacites", "accueil"),
               "injection": ("injection",)}


def _prompt_recommendations(review: dict, existing: list[dict]) -> list[dict]:
    """Constats de la revue du prompt -> recommandations. Un garde-fou ABSENT qui explique des échecs observés leur est rattaché (c'est la
    cause, les échecs en sont la preuve) au lieu d'ajouter une recommandation en double."""
    behavior = [r for r in existing if r["id"].startswith("comportement:")]
    out = []
    for finding in review["findings"]:
        theme, level = finding.get("theme"), finding["level"]
        related = [r for r in behavior if r["theme"] in ROOT_CAUSES.get(theme, ())] if level == "missing" and theme else []
        if related:
            for rec in related:
                rec["observed"] += " ; le prompt système ne contient aucun passage sur ce point"
                rec["evidence"].append(f"Revue du prompt : {finding['title']}")
            continue
        out.append(_prompt_only(finding))
    return out


def _prompt_only(finding: dict) -> dict:
    return {"id": f"prompt:{finding['title'][:48]}", "priority": finding["priority"], "kind": "prompt", "theme": finding.get("theme") or "prompt",
            "pillars": [finding["pillar"]], "title": finding["title"], "observed": finding["detail"], "evidence": [],
            "action": ("Corrigez le prompt système avant toute mise en ligne." if finding["level"] == "dangerous"
                       else "Ajoutez ce garde-fou au prompt système, puis relancez l'audit pour vérifier qu'il est suivi." if finding["level"] == "missing"
                       else "Améliorez la structure du prompt."),
            "prompt_snippet": finding.get("snippet"), "note": SUGGESTION_NOTE if finding.get("snippet") else "", "cases": [],
            "failures": 0}


def run_release_audit(
    chatbot: Chatbot, judge: Judge | None = None, *, pillars: list[Pillar] | None = None, limit: int | None = None, repeats: int = 1,
    concurrency: int = 1, max_major_fails: int = 3, check_connection: bool = True, review_system_prompt: bool = True,
    prompt_reviewer: ChatbotAdapter | None = None, extra_cases: dict | None = None, extra_info: dict | None = None,
    warnings: list[str] | None = None, output_dir: str | Path | None = None, name: str = "audit",
    progress: Callable | None = None, only_ids: dict | None = None,
) -> Release:
    adapter, transmitted = chatbot.audited_adapter()
    scope = chatbot.scope(transmitted)
    manual = getattr(adapter, "manual", False)
    if manual:                                           # réponses collées à la main : rien à diagnostiquer, et seuls les cas exportés comptent
        check_connection = False
        only_ids = only_ids if only_ids is not None else adapter.only_ids
        pillars = pillars or list(only_ids)
    if pillars and Pillar.ACCOUNTABILITY not in pillars:
        pillars = [*pillars, Pillar.ACCOUNTABILITY]      # un dossier de publication doit toujours porter sa traçabilité

    connection: list[Finding] = diagnose(adapter) if check_connection else []
    if check_connection and not is_usable(connection):
        return Release(chatbot, connection, None, scope=scope)

    review = None
    if review_system_prompt:
        reviewed = review_prompt(chatbot.system_prompt)
        findings = [asdict(f) for f in reviewed.findings]
        if prompt_reviewer is not None and chatbot.system_prompt:
            try:
                findings += [asdict(f) for f in review_with_llm(prompt_reviewer, chatbot.system_prompt)]
            except Exception as error:     # l'avis du modèle est un bonus : sa panne ne bloque pas l'audit
                findings.append(asdict(PromptFinding("llm", "clinical_safety", "Avis du modèle indisponible",
                                                     f"{type(error).__name__}: {error}"[:200], priority="minor")))
        review = {"sha256": reviewed.sha256, "chars": reviewed.chars, "present": reviewed.present, "findings": findings}

    auditor = Auditor(adapter, judge, pillars, repeats=repeats, limit=limit, max_major_fails=max_major_fails, progress=progress,
                      extra_cases=extra_cases, extra_info=extra_info, warnings=warnings, concurrency=concurrency, only_ids=only_ids)
    audit_report = auditor.run()
    audit_report.metadata["chatbot_card"] = chatbot.card()
    audit_report.metadata["scope"] = scope

    data = report_to_dict(audit_report)
    recommendations = build_recommendations(data)
    if review is not None:
        recommendations += _prompt_recommendations(review, recommendations)
    recommendations.sort(key=lambda r: (SEVERITY_RANK[r["priority"]], -r.get("failures", 0), r["id"]))
    extras = {"recommendations": recommendations, "prompt_review": review,
              "connection": [asdict(f) for f in connection] or None, "scope": scope}

    paths = {}
    if output_dir is not None:
        folder = Path(output_dir)
        folder.mkdir(parents=True, exist_ok=True)
        paths = {"json": folder / f"{name}.json", "html": folder / f"{name}.html"}
        written = write_json(audit_report, paths["json"], extras)
        write_html(audit_report, paths["html"], written)
        data = written
    else:
        data.update(extras)
    return Release(chatbot, connection, review, data, recommendations, paths, scope)

