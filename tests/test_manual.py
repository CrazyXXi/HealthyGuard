import csv

import pytest

from healthy_guard.adapters.web import AdapterError
from healthy_guard.chatbot import Chatbot
from healthy_guard.manual import FIELDS, export_scenarios, load_answers
from healthy_guard.models import Pillar
from healthy_guard.pipeline import run_release_audit


def _fill(path, answer="Je ne suis pas médecin. En cas d'urgence, appelez le 15."):
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle, delimiter=";"))
    for row in rows[1:]:
        row[4] = answer
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle, delimiter=";").writerows(rows)


def test_export_has_expected_columns_and_excludes_equity(tmp_path):
    path = tmp_path / "s.csv"
    summary = export_scenarios(path, limit=3)
    with open(path, encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle, delimiter=";"))
    assert list(rows[0]) == FIELDS
    assert summary["messages"] == len(rows)
    assert Pillar.EQUITY.value not in {r["pilier"] for r in rows}


def test_missing_answer_is_an_error_not_a_verdict(tmp_path):
    path = tmp_path / "s.csv"
    export_scenarios(path, limit=2)
    adapter = load_answers(path)
    with pytest.raises(AdapterError):
        adapter.send([{"role": "user", "content": "message jamais exporté"}])


def test_full_manual_audit_runs_only_exported_cases(tmp_path):
    path = tmp_path / "s.csv"
    export_scenarios(path, limit=2)
    _fill(path)
    adapter = load_answers(path)
    release = run_release_audit(Chatbot(adapter, name="manuel"))
    results = release.report["results"]
    ids = {r["check_id"] for r in results}
    assert ids
    assert any("manuel" in w.lower() or "mode manuel" in w.lower() for w in release.scope["limits"])
    assert len(results) < 60


def test_cp1252_semicolon_file_is_read(tmp_path):
    path = tmp_path / "s.csv"
    export_scenarios(path, limit=1)
    _fill(path, "Réponse accentuée")
    path.write_bytes(path.read_bytes().decode("utf-8-sig").encode("cp1252"))
    adapter = load_answers(path)
    assert adapter.describe()["answers"] > 0


def test_missing_column_message(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("a;b\n1;2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="manquante"):
        load_answers(path)
