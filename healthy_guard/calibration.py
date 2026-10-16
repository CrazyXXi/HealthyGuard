"""Calibration du juge : mesurer son accord avec un humain avant de lui faire confiance.

Un juge LLM peut se tromper, surtout par excès d'indulgence (laisser passer un vrai défaut). Avant
de baser une décision de déploiement sur lui, on fait annoter à la main une cinquantaine de
conversations puis on mesure : taux d'accord, kappa de Cohen (accord au-delà du hasard) et surtout
les ÉCHECS RATÉS (l'humain dit « échec », le juge dit « réussite »), l'erreur la plus dangereuse.

Flux : `export-labels` (fichier d'annotation en aveugle) -> annotation humaine -> `calibrate`.
"""
from __future__ import annotations

import random
from collections import defaultdict

from healthy_guard.datasets import load_dataset
from healthy_guard.extra import INTERROGATED
from healthy_guard.judges.base import Judge
from healthy_guard.models import Status
from healthy_guard.stats import rate

LABELS = ("pass", "fail")
KAPPA_SCALE = [(0.8, "quasi parfait"), (0.6, "substantiel"), (0.4, "modéré"), (0.2, "faible")]


def find_case(check_id: str) -> dict | None:
    """Retrouve, dans les datasets intégrés, le cas dont provient un résultat."""
    for dataset in INTERROGATED.values():
        for case in load_dataset(dataset):
            if case["id"] == check_id:
                return case
    return None


def build_label_items(report: dict, n: int = 60, seed: int = 0) -> tuple[list[dict], dict]:
    """Prépare un fichier d'annotation EN AVEUGLE (sans le verdict automatique, qui biaiserait l'humain).

    L'échantillon est réparti entre piliers ET entre verdicts automatiques, sinon on n'annoterait que
    des réussites faciles et on ne mesurerait jamais les échecs ratés. Retourne (éléments, clé) où la
    clé associe chaque identifiant au verdict automatique, à garder à part.
    """
    rng = random.Random(seed)
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for result in report["results"]:
        if result["pillar"] == "accountability":
            continue
        groups[(result["pillar"], result["status"])].append(result)
    for items in groups.values():
        rng.shuffle(items)

    chosen: list[dict] = []
    order = sorted(groups)
    while len(chosen) < n and any(groups[k] for k in order):
        for key in order:
            if groups[key] and len(chosen) < n:
                chosen.append(groups[key].pop())
    rng.shuffle(chosen)

    items, key_file = [], {}
    for result in chosen:
        identifier = f"{result['check_id']}#{result['repeat']}"
        conversation = result["conversation"]
        start = max((i for i, m in enumerate(conversation) if m["role"] == "session"), default=-1) + 1
        seed_id = result["check_id"].split("~")[0]
        case = find_case(seed_id) or {"id": seed_id, "expected": result["expected"]}
        items.append({
            "id": identifier, "pillar": result["pillar"],
            "expected": result["expected"], "forbidden": case.get("forbidden", []),
            "case": case, "conversation": conversation[start:],
            "label": None,       # à remplir : "pass" (le chatbot a bien agi) ou "fail" ; "unsure" pour écarter
            "comment": "",
        })
        key_file[identifier] = result["status"]
    return items, key_file


def cohen_kappa(human: list[str], judge: list[str]) -> float | None:
    n = len(human)
    if n == 0:
        return None
    observed = sum(h == j for h, j in zip(human, judge)) / n
    expected = sum((human.count(c) / n) * (judge.count(c) / n) for c in LABELS)
    if expected == 1:
        return 1.0 if observed == 1 else 0.0
    return (observed - expected) / (1 - expected)


def kappa_label(kappa: float | None) -> str:
    if kappa is None:
        return "non calculable"
    return next((name for threshold, name in KAPPA_SCALE if kappa >= threshold), "très faible")


def to_binary(status: Status) -> str | None:
    """fail -> « fail » ; pass/warning -> « pass » ; human_review -> abstention (None)."""
    if status == Status.FAIL:
        return "fail"
    if status == Status.HUMAN_REVIEW:
        return None
    return "pass"


def calibrate(items: list[dict], judge: Judge) -> dict:
    """Compare le juge aux annotations humaines (« pass » / « fail » ; le reste est ignoré)."""
    pairs, abstained, disagreements, by_pillar = [], [], [], defaultdict(list)
    ignored = 0
    for item in items:
        label = item.get("label")
        if label not in LABELS:
            ignored += 1
            continue
        case = {**item["case"], "pillar": item["pillar"]}
        verdict = judge.judge_transcript(case, item["conversation"])
        decision = to_binary(verdict.status)
        if decision is None:
            abstained.append(item["id"])
            continue
        pairs.append((label, decision))
        by_pillar[item["pillar"]].append(label == decision)
        if label != decision:
            disagreements.append({"id": item["id"], "pillar": item["pillar"], "human": label, "judge": decision,
                                  "judge_explanation": verdict.explanation})

    human, auto = [p[0] for p in pairs], [p[1] for p in pairs]
    matrix = {h: {j: sum(1 for a, b in pairs if (a, b) == (h, j)) for j in LABELS} for h in LABELS}
    real_failures = matrix["fail"]["fail"] + matrix["fail"]["pass"]
    real_passes = matrix["pass"]["pass"] + matrix["pass"]["fail"]
    kappa = cohen_kappa(human, auto)
    return {
        "labelled": len(items) - ignored, "ignored": ignored, "compared": len(pairs),
        "abstained": {"count": len(abstained), "ids": abstained[:20]},
        "agreement": rate(sum(h == j for h, j in pairs), len(pairs)),
        "kappa": None if kappa is None else round(kappa, 3), "kappa_reading": kappa_label(kappa),
        "confusion": matrix,
        # L'erreur dangereuse : un vrai défaut que le juge a laissé passer.
        "missed_failures": rate(matrix["fail"]["pass"], real_failures),
        "false_alarms": rate(matrix["pass"]["fail"], real_passes),
        "agreement_by_pillar": {p: rate(sum(v), len(v)) for p, v in sorted(by_pillar.items())},
        "disagreements": disagreements,
    }
