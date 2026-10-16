"""Mode MANUEL, sans aucune API : auditer un chatbot qu'on ne peut utiliser que par son interface (ChatGPT, Gemini, widget de site...).

1. `export_scenarios` écrit un tableur (CSV) : pour chaque conversation, les messages à envoyer, dans l'ordre ;
2. la personne ouvre un NOUVEAU chat par conversation, envoie les messages et COLLE chaque réponse dans la colonne prévue ;
3. `load_answers` relit le tableur : `AnswersAdapter` rend à l'audit les réponses collées, comme le ferait une API. Les juges, les statistiques et les
   recommandations sont exactement les mêmes.

Limites assumées (écrites aussi dans le rapport) : le copier-coller est long, donc l'échantillon est petit ; l'audit suppose que les réponses sont
fidèles ; le prompt système et les réglages de l'interface ne sont pas contrôlés ; l'équité (qui exige tous les profils) est exclue par défaut.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from healthy_guard.adapters.base import ChatbotAdapter, ChatResponse
from healthy_guard.adapters.web import AdapterError
from healthy_guard.auditor import PILLAR_MODULES, sample
from healthy_guard.datasets import load_dataset
from healthy_guard.models import Pillar

FIELDS = ["conversation", "pilier", "tour", "message_a_envoyer", "reponse_du_chatbot"]
MANUAL_PILLARS = [Pillar.CLINICAL_SAFETY, Pillar.TRANSPARENCY, Pillar.PRIVACY, Pillar.CONSENT]    # l'équité exige tous les profils
DEFAULT_LIMIT = 20


def _norm(text: str) -> str:
    """Un tableur change les retours à la ligne et les espaces : on compare sans en tenir compte."""
    return " ".join(str(text).split())


def export_scenarios(path: str | Path, pillars: list[Pillar] | None = None, limit: int | None = DEFAULT_LIMIT) -> dict:
    rows, cases = [], 0
    for pillar in pillars or MANUAL_PILLARS:
        for case in sample(load_dataset(PILLAR_MODULES[pillar].DATASET), limit):
            cases += 1
            conversations = ([(f"{case['id']}#1", case["plant_turns"]), (f"{case['id']}#2", case.get("turns") or [case["prompt"]])]
                             if case.get("plant_turns") else [(case["id"], case.get("turns") or [case["prompt"]])])
            for conversation, turns in conversations:
                rows += [[conversation, pillar.value, number, message, ""] for number, message in enumerate(turns, 1)]
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:            # utf-8-sig : Excel reconnaît les accents
        writer = csv.writer(handle, delimiter=";", quoting=csv.QUOTE_ALL)
        writer.writerow(FIELDS)
        writer.writerows(rows)
    return {"cases": cases, "conversations": len({r[0] for r in rows}), "messages": len(rows)}


class AnswersAdapter(ChatbotAdapter):
    """Rend les réponses collées à la main. Une conversation est reconnue par la suite des messages de l'utilisateur."""

    manual = True

    def __init__(self, answers: dict[tuple[str, ...], str], only_ids: dict[Pillar, set[str]], source: str = "réponses collées à la main"):
        self.answers = answers
        self.only_ids = only_ids
        self.source = source

    def describe(self) -> dict:
        return {"adapter": "AnswersAdapter", "mode": "manuel (sans API)", "source": self.source, "answers": len(self.answers)}

    def send(self, messages: list[dict]) -> ChatResponse:
        key = tuple(_norm(m["content"]) for m in messages if m["role"] == "user")
        if key not in self.answers:
            raise AdapterError(f"Aucune réponse n'a été fournie pour « {messages[-1]['content'][:60]} » : complétez le tableur.")
        return ChatResponse(self.answers[key], latency_ms=None, metadata={"manual": True})


def _read_rows(path: str | Path) -> list[dict]:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252")                           # Excel « CSV (séparateur : point-virgule) » enregistre en cp1252
    delimiter = ";" if text.splitlines()[0].count(";") >= text.splitlines()[0].count(",") else ","
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    names = [(n or "").strip().lower() for n in (reader.fieldnames or [])]
    missing = [f for f in FIELDS if f not in names]
    if missing:
        raise ValueError(f"Colonne(s) manquante(s) dans le tableur : {', '.join(missing)} (attendues : {', '.join(FIELDS)}).")
    return [{(k or "").strip().lower(): (v or "") for k, v in row.items()} for row in reader]


def load_answers(path: str | Path) -> AnswersAdapter:
    rows = _read_rows(path)
    if not rows:
        raise ValueError("Le tableur ne contient aucune ligne.")
    conversations: dict[str, list[dict]] = {}
    for row in rows:
        conversations.setdefault(row["conversation"].strip(), []).append(row)

    answers: dict[tuple[str, ...], str] = {}
    only_ids: dict[Pillar, set[str]] = {}
    for conversation, turns in conversations.items():
        try:
            turns.sort(key=lambda r: int(r["tour"]))
            pillar = Pillar(turns[0]["pilier"].strip())
        except ValueError:
            raise ValueError(f"Conversation « {conversation} » : numéro de tour ou pilier invalide.") from None
        only_ids.setdefault(pillar, set()).add(conversation.split("#")[0])
        sent: list[str] = []
        for turn in turns:
            sent.append(_norm(turn["message_a_envoyer"]))
            if turn["reponse_du_chatbot"].strip():
                answers[tuple(sent)] = turn["reponse_du_chatbot"].strip()
    return AnswersAdapter(answers, only_ids, source=Path(path).name)


def instructions(summary: dict, path: str | Path) -> str:
    return (f"{summary['conversations']} conversations ({summary['messages']} messages) à passer dans votre chatbot : {path}\n"
            "Pour CHAQUE numéro de conversation :\n"
            "  1. ouvrez un NOUVEAU chat (une conversation vierge) ;\n"
            "  2. envoyez les messages de la colonne « message_a_envoyer », dans l'ordre des tours ;\n"
            "  3. collez chaque réponse du chatbot dans la colonne « reponse_du_chatbot », sur la même ligne.\n"
            "Les conversations qui finissent par #1 puis #2 sont à faire dans DEUX chats différents (le second ne doit rien savoir du premier).\n"
            "Enregistrez en CSV (UTF-8 si possible), puis : healthy-guard run --adapter answers --answers <fichier>")
