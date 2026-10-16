from __future__ import annotations

from healthy_guard.checks import clinical_safety
from healthy_guard.checks.base import evaluate_rules
from healthy_guard.judges.base import Judge, Verdict
from healthy_guard.models import Pillar


class KeywordJudge(Judge):
    """Juge par règles : mots-clés et expressions régulières. Rapide et déterministe.

    - sécurité clinique : logique dédiée (renvoi vers les secours, sur-alerte) ;
    - autres piliers : règles génériques pilotées par les champs du cas
      (`requires`, `forbidden`, `leak_patterns`).
    """

    def judge_transcript(self, case: dict, messages: list[dict]) -> Verdict:
        from healthy_guard.checks.transcript import evaluate_transcript  # import tardif : évite un cycle

        replies = [m["content"] for m in messages if m["role"] == "assistant"]
        return Verdict(*evaluate_transcript(case, replies))

    def judge(self, case: dict, response: str) -> Verdict:
        pillar = case.get("pillar", Pillar.CLINICAL_SAFETY.value)
        if pillar == Pillar.CLINICAL_SAFETY.value:
            status, explanation, evidence = clinical_safety.evaluate(case, response)
        else:
            status, explanation, evidence = evaluate_rules(case, response)
        return Verdict(status, explanation, evidence)
