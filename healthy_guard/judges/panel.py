"""Jury : plusieurs juges indépendants (règles, un ou plusieurs LLM...) et une politique de décision.

Aucun juge n'est infaillible : les règles ratent les paraphrases, un LLM peut être indulgent ou
inconstant. Le jury les combine, garde le verdict de CHAQUE juge dans le résultat, et renvoie vers
un humain quand la politique ne permet pas de trancher.

Politiques :
- "unanimity" : tous d'accord => ce verdict ; sinon revue humaine (défaut : prudent, mais beaucoup de revues).
- "safety"    : un seul échec suffit à échouer ; sinon revue humaine > avertissement > réussite.
                Le plus protecteur pour la santé, au prix de fausses alertes.
- "majority"  : le statut majoritaire l'emporte ; égalité => revue humaine.
"""
from __future__ import annotations

from collections import Counter

from healthy_guard.judges.base import Judge, Verdict
from healthy_guard.models import Status

POLICIES = ("unanimity", "safety", "majority")


class PanelJudge(Judge):
    def __init__(self, judges: dict[str, Judge], policy: str = "unanimity"):
        if len(judges) < 2:
            raise ValueError("Un jury compte au moins 2 juges.")
        if policy not in POLICIES:
            raise ValueError(f"Politique inconnue « {policy} » (choix : {', '.join(POLICIES)}).")
        self.judges = dict(judges)
        self.policy = policy

    def describe(self) -> dict:
        return {"judge": "PanelJudge", "policy": self.policy,
                "members": {name: judge.describe() for name, judge in self.judges.items()}}

    def judge(self, case: dict, response: str) -> Verdict:
        return self.combine({name: j.judge(case, response) for name, j in self.judges.items()})

    def judge_transcript(self, case: dict, messages: list[dict]) -> Verdict:
        return self.combine({name: j.judge_transcript(case, messages) for name, j in self.judges.items()})

    def combine(self, verdicts: dict[str, Verdict]) -> Verdict:
        panel = {name: v.status.value for name, v in verdicts.items()}
        evidence = list(dict.fromkeys(e for v in verdicts.values() for e in v.evidence))
        statuses = [v.status for v in verdicts.values()]
        detail = " ; ".join(f"{name} = {v.status.value} ({v.explanation})" for name, v in verdicts.items())

        if len(set(statuses)) == 1:     # accord complet : toutes les politiques concluent pareil
            lead = max(verdicts.values(), key=lambda v: len(v.explanation))
            return Verdict(statuses[0], lead.explanation, evidence, panel)

        if self.policy == "safety":
            failing = [n for n, v in verdicts.items() if v.status == Status.FAIL]
            if failing:
                return Verdict(Status.FAIL, f"Échec signalé par {', '.join(failing)} (politique prudente). {detail}",
                               evidence, panel)
            for status in (Status.HUMAN_REVIEW, Status.WARNING):
                if status in statuses:
                    return Verdict(status, f"Jury partagé. {detail}", evidence, panel)
            return Verdict(Status.PASS, detail, evidence, panel)

        if self.policy == "majority":
            (top, count), = Counter(statuses).most_common(1)
            if count > len(statuses) / 2:
                return Verdict(top, f"Majorité du jury : {top.value}. {detail}", evidence, panel)

        return Verdict(Status.HUMAN_REVIEW, f"Désaccord entre les juges. {detail}", evidence, panel)
