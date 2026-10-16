import json

import pytest

from healthy_guard.adapters.demo import DemoChatbot
from healthy_guard.auditor import Auditor
from healthy_guard.cli import main
from healthy_guard.models import Decision, Status
from healthy_guard.reporting.json_report import read_json


# --- le chatbot de démonstration prouve que l'audit détecte de vrais défauts ---------------------

@pytest.fixture(scope="module")
def good_report():
    return Auditor(DemoChatbot(flawed=False)).run()


@pytest.fixture(scope="module")
def flawed_report():
    return Auditor(DemoChatbot(flawed=True)).run()


def failures(report):
    return [r for r in report.results if r.status == Status.FAIL]


def test_good_demo_chatbot_passes_the_whole_audit(good_report):
    assert good_report.decision == Decision.PASS
    assert len(good_report.results) > 700
    assert not failures(good_report)


def test_flawed_demo_chatbot_is_blocked(flawed_report):
    assert flawed_report.decision == Decision.BLOCKED


@pytest.mark.parametrize("pillar, category", [
    ("privacy", "injection"),                 # révèle son prompt système
    ("privacy", "donnees_utilisateur"),       # recopie les identifiants
    ("consent", "droits"),                    # prétend avoir supprimé les données
    ("consent", "donnees"),                   # promet que rien n'est conservé
    ("consent", "humain"),                    # affirme que personne ne lit
    ("transparency", "identite"),             # se dit médecin
    ("clinical_safety", "infarctus"),         # valide l'attente / minimise
])
def test_each_deliberate_flaw_is_caught(flawed_report, pillar, category):
    hits = [r for r in failures(flawed_report)
            if r.pillar.value == pillar and r.tags.get("category") == category]
    assert hits, f"défaut non détecté : {pillar}/{category}"


def test_equity_biases_are_visible_even_if_not_significant(flawed_report):
    from healthy_guard.stats import compute_stats

    equity = compute_stats(flawed_report)["equity"]
    assert equity["failures_by_profile"]["sexe=femme"] == 1
    assert equity["failures_by_profile"]["poids=obesite"] == 1
    assert equity["failures_by_profile"]["statut_social=sans_domicile"] == 1
    assert equity["low_power"] is True     # l'audit prévient que 5 cas par profil, c'est peu


def test_flawed_chatbot_equity_failures_have_a_clear_cause(flawed_report):
    stress = [r for r in failures(flawed_report)
              if r.pillar.value == "equity" and r.tags["profile_value"] == "femme"]
    assert stress and "stress" in stress[0].response and stress[0].evidence


# --- CLI -----------------------------------------------------------------------------------------

def run_cli(tmp_path, *extra, adapter="demo-good"):
    out = tmp_path / "audit.json"
    code = main(["audit", "--adapter", adapter, "--out", str(out), "--quiet", *extra])
    return code, out


def test_cli_exit_code_0_when_deployment_is_allowed(tmp_path, capsys):
    code, out = run_cli(tmp_path, "--limit", "10")
    assert code == 0
    assert "DÉPLOIEMENT AUTORISÉ" in capsys.readouterr().out
    assert read_json(out)["decision"] == "pass"


@pytest.mark.parametrize("adapter", ["demo-flawed", "mock-bad"])
def test_cli_exit_code_2_when_blocked(tmp_path, capsys, adapter):
    code, out = run_cli(tmp_path, "--limit", "40", adapter=adapter)
    assert code == 2
    assert "DÉPLOIEMENT BLOQUÉ" in capsys.readouterr().out


def test_cli_writes_html_and_verify_roundtrip(tmp_path, capsys):
    html = tmp_path / "r.html"
    code, out = run_cli(tmp_path, "--limit", "5", "--html", str(html))
    assert code == 0 and "<!doctype html>" in html.read_text(encoding="utf-8")
    assert main(["verify", str(out)]) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    data["results"][0]["status"] = "fail"                    # falsification
    out.write_text(json.dumps(data), encoding="utf-8")
    assert main(["verify", str(out)]) == 1
    assert "ALTÉRÉ" in capsys.readouterr().out


def test_cli_report_regenerates_html_from_json(tmp_path):
    _, out = run_cli(tmp_path, "--limit", "5")
    html = tmp_path / "again.html"
    assert main(["report", str(out), "--html", str(html)]) == 0
    assert "Résultats par pilier" in html.read_text(encoding="utf-8")


def test_cli_pillars_and_repeats(tmp_path):
    code, out = run_cli(tmp_path, "--pillars", "consent,accountability", "--repeats", "2", "--limit", "3")
    data = read_json(out)
    assert {r["pillar"] for r in data["results"]} == {"consent", "accountability"}
    assert data["stats"]["repeatability"]["repeats"] == 2


@pytest.mark.parametrize("argv, message", [
    (["audit", "--adapter", "http"], "--url"),
    (["audit", "--adapter", "demo-good", "--judge", "llm"], "--judge-provider"),
    (["audit", "--adapter", "demo-good", "--pillars", "magie"], "magie"),
    (["audit", "--adapter", "demo-good", "--repeats", "0"], "repeats"),
    (["audit", "--adapter", "anthropic"], "modèle"),
    (["audit", "--adapter", "http", "--url", "http://x", "--header-env", "X=VARIABLE_ABSENTE_XYZ"], "VARIABLE_ABSENTE_XYZ"),
])
def test_cli_configuration_errors_use_exit_code_64(argv, message, capsys):
    assert main(argv) == 64
    assert message in capsys.readouterr().err


def test_cli_network_options_reach_the_adapters_and_the_summary_flags_errors(tmp_path, capsys):
    from healthy_guard.cli import build_parser, make_chatbot

    args = build_parser().parse_args(["audit", "--adapter", "http", "--url", "http://x/chat", "--min-interval", "6",
                                      "--max-retries", "5", "--backoff", "2", "--timeout", "30"])
    bot = make_chatbot(args)
    assert (bot.min_interval, bot.max_retries, bot.backoff, bot.timeout) == (6.0, 5, 2.0, 30.0)
    assert args.min_interval == 6.0

    code = main(["audit", "--adapter", "anthropic", "--model", "m", "--out", str(tmp_path / "a.json"), "--quiet",
                 "--limit", "1", "--pillars", "consent"])
    assert code == 1
    out = capsys.readouterr().out
    assert "aucune réponse (erreur technique" in out and "--min-interval" in out


def test_cli_usage_error_is_not_confused_with_blocked(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main([])
    assert exit_info.value.code == 64


def test_cli_missing_api_key_is_reported_not_leaked(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    code = main(["audit", "--adapter", "anthropic", "--model", "m", "--out", str(tmp_path / "a.json"), "--quiet", "--limit", "1"])
    # sans clé, chaque appel échoue : l'audit ne plante pas, il demande une revue humaine
    assert code == 1
    data = read_json(tmp_path / "a.json")
    asked = [r for r in data["results"] if r["pillar"] != "accountability"]
    assert asked and all(r["status"] == "human_review" for r in asked)
    assert "ANTHROPIC_API_KEY" in asked[0]["explanation"]


def test_cli_does_not_claim_equity_without_comparisons(tmp_path, capsys):
    run_cli(tmp_path, "--pillars", "equity", "--limit", "1")
    out = capsys.readouterr().out
    assert "pas assez de données" in out and "aucun écart" not in out
