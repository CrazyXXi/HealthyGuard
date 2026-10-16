"""Charger des secrets depuis un fichier `.env` local, sans jamais les afficher.

Pourquoi : une clé collée dans un terminal, un message ou un script finit dans un historique. Un fichier
`.env` (ignoré par git, jamais envoyé ailleurs) évite cela. Format : une ligne `NOM=valeur` ; les lignes
vides et celles qui commencent par `#` sont ignorées ; `export NOM=valeur` et les guillemets sont acceptés.
Une variable déjà définie dans l'environnement n'est PAS écrasée.

Les messages d'erreur citent le numéro de ligne, jamais la valeur.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_env_file(path: str | Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").lstrip()
        name, sep, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if not sep or not NAME.match(name):
            raise ValueError(f"{path} ligne {number} : attendu NOM=valeur.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


def load_env_file(path: str | Path) -> list[str]:
    """Ajoute les variables du fichier à l'environnement du processus ; retourne les NOMS chargés (jamais les valeurs)."""
    loaded = []
    for name, value in parse_env_file(path).items():
        if name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded
