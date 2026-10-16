"""Cas de test fournis par l'utilisateur (red team, incidents réels, prompts d'attaquants...).

Un dataset fixe ne couvre jamais les prompts que rédigent de vrais attaquants : ce module permet
d'ajouter ses propres cas dans un fichier JSON, sans toucher au code. Les cas sont validés (une
erreur de syntaxe dans une regex ne doit pas faire planter l'audit au milieu) et le fichier est
tracé dans le rapport (nom, empreinte SHA-256, nombre de cas).

Format : une liste de cas, avec les mêmes champs que les datasets intégrés, plus `pillar`.
Exemple minimal :
    [{"id": "mon_cas_1", "pillar": "privacy",
      "prompt": "…message subtil…",
      "expected": "Refuser de divulguer.",
      "requires": [["ne peux pas", "confidenti"]],
      "forbidden": ["voici son dossier"]}]
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from healthy_guard.datasets import load_dataset
from healthy_guard.models import Pillar, Severity

# Piliers qui interrogent le chatbot (le pilier Responsabilité n'a pas de cas).
INTERROGATED = {
    Pillar.CLINICAL_SAFETY: "urgence", Pillar.TRANSPARENCY: "transparence", Pillar.EQUITY: "equite",
    Pillar.PRIVACY: "confidentialite", Pillar.CONSENT: "consentement",
}
CRITERIA = ("requires", "forbidden", "leak_patterns", "forbidden_regex")


def _problem(case: dict, index: int, message: str) -> ValueError:
    return ValueError(f"Cas n°{index + 1} ({case.get('id', 'sans id')}) : {message}")


def validate_case(case: dict, index: int, taken_ids: set[str]) -> Pillar:
    if not isinstance(case, dict):
        raise ValueError(f"Cas n°{index + 1} : un objet JSON est attendu.")
    for field in ("id", "pillar", "expected"):
        if not case.get(field):
            raise _problem(case, index, f"champ « {field} » manquant.")
    try:
        pillar = Pillar(case["pillar"])
    except ValueError:
        raise _problem(case, index, f"pilier inconnu « {case['pillar']} ».") from None
    if pillar not in INTERROGATED:
        raise _problem(case, index, f"le pilier « {pillar.value} » ne prend pas de cas de test.")
    if case["id"] in taken_ids:
        raise _problem(case, index, "identifiant déjà utilisé (dans ce fichier ou dans un dataset intégré).")
    if not (case.get("prompt") or case.get("turns")):
        raise _problem(case, index, "« prompt » ou « turns » est requis.")
    if case.get("turns"):
        if not all(isinstance(t, str) and t for t in case["turns"]):
            raise _problem(case, index, "« turns » doit être une liste de messages non vides.")
        case.setdefault("prompt", case["turns"][-1])
    if case.get("plant_turns") and not case.get("leak_patterns"):
        raise _problem(case, index, "un cas avec « plant_turns » (canari) exige « leak_patterns ».")
    try:
        Severity(case.get("severity", "major"))
    except ValueError:
        raise _problem(case, index, f"sévérité inconnue « {case['severity']} ».") from None
    if pillar != Pillar.CLINICAL_SAFETY and not any(case.get(k) for k in CRITERIA):
        raise _problem(case, index, "au moins un critère est requis parmi " + ", ".join(CRITERIA) + ".")
    patterns = [p for group in case.get("requires", []) for p in group]
    patterns += case.get("leak_patterns", []) + case.get("forbidden_regex", [])
    for pattern in patterns:
        try:
            re.compile(pattern)
        except re.error as error:
            raise _problem(case, index, f"expression régulière invalide « {pattern} » ({error}).") from None
    return pillar


def load_extra_cases(path: str | Path) -> tuple[dict[Pillar, list[dict]], dict]:
    """Charge et valide un fichier de cas ; retourne (cas par pilier, informations de traçabilité)."""
    raw = Path(path).read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"Le fichier de cas n'est pas un JSON valide : {error}") from None
    if not isinstance(data, list) or not data:
        raise ValueError("Le fichier de cas doit contenir une liste non vide de cas.")

    taken = {c["id"] for dataset in INTERROGATED.values() for c in load_dataset(dataset)}
    by_pillar: dict[Pillar, list[dict]] = {}
    for index, case in enumerate(data):
        pillar = validate_case(case, index, taken)
        taken.add(case["id"])
        case.setdefault("category", "personnalise")
        case.setdefault("style", "personnalise")
        case.setdefault("severity", "major")
        by_pillar.setdefault(pillar, []).append(case)
    info = {"file": Path(path).name, "sha256": hashlib.sha256(raw).hexdigest(), "cases": len(data),
            "by_pillar": {p.value: len(c) for p, c in by_pillar.items()}}
    return by_pillar, info
