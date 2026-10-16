"""Des personnes n'utilisent pas de clé d'API : modèle local (Ollama, LM Studio, llama.cpp), serveur interne, juge local."""
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

import healthy_guard as hg
from healthy_guard.adapters.web import AdapterError, OpenAICompatibleAdapter
from healthy_guard.adapters.config import build_chatbot
from healthy_guard.cli import main
from healthy_guard.judges.config import adapter_from_profile
from healthy_guard.reporting.json_report import read_json
from tests.test_web_adapters import REQUESTS, Handler

MESSAGES = [{"role": "user", "content": "Bonjour"}]


@pytest.fixture()
def local_server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    REQUESTS.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


def test_a_local_server_gets_no_authorization_header_and_needs_no_variable(local_server):
    reply = OpenAICompatibleAdapter("llama3", base_url=local_server).send(MESSAGES)
    assert reply.text == "Réponse OpenAI" and "authorization" not in REQUESTS[0]["headers"]


def test_the_default_openai_address_still_requires_its_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(AdapterError, match="OPENAI_API_KEY"):
        OpenAICompatibleAdapter("gpt").send(MESSAGES)


def test_asking_for_a_key_that_is_missing_is_still_an_error_even_locally(local_server, monkeypatch):
    monkeypatch.delenv("MA_CLE_OUBLIEE", raising=False)
    with pytest.raises(AdapterError, match="MA_CLE_OUBLIEE"):
        OpenAICompatibleAdapter("m", base_url=local_server, api_key_env="MA_CLE_OUBLIEE").send(MESSAGES)
    assert REQUESTS == []                                         # on n'envoie rien en ayant oublié une clé demandée


def test_a_key_that_is_given_is_sent(local_server, monkeypatch):
    monkeypatch.setenv("MA_CLE", "jeton-123")
    OpenAICompatibleAdapter("m", base_url=local_server, api_key_env="MA_CLE").send(MESSAGES)
    assert REQUESTS[0]["headers"]["authorization"] == "Bearer jeton-123"


@pytest.mark.parametrize("kwargs, expected", [
    ({"base_url": "http://localhost:11434/v1"}, "aucune"),
    ({"base_url": "http://localhost:11434/v1", "api_key_env": None}, "aucune"),
    ({"base_url": "http://localhost:11434/v1", "api_key_env": ""}, "aucune"),
    ({}, "jeton lu dans une variable d'environnement"),
    ({"base_url": "https://x.example", "api_key_env": "K"}, "jeton lu dans une variable d'environnement"),
    ({"api_key_env": None}, "aucune"),
])
def test_the_report_states_how_the_chatbot_was_authenticated(kwargs, expected):
    assert OpenAICompatibleAdapter("m", **kwargs).describe()["authentication"] == expected


def test_local_chatbot_shortcut_targets_ollama_without_a_key_and_waits_for_model_loading():
    bot = hg.local_chatbot("llama3")
    assert bot.base_url == "http://localhost:11434/v1" and bot.api_key_env is None and bot.timeout == 300.0
    assert hg.local_chatbot("m", base_url="http://localhost:1234/v1", timeout=30).timeout == 30
    assert hg.openai_chatbot("m", base_url="http://localhost:8080/v1").api_key_env is None
    assert hg.openai_chatbot("m").api_key_env == "OPENAI_API_KEY"
    assert hg.openai_chatbot("m", api_key_env="AUTRE").api_key_env == "AUTRE"


def test_profiles_support_keyless_chatbots_and_judges():
    keyless = build_chatbot({"adapter": "openai", "model": "m", "base_url": "http://localhost:11434/v1"})
    explicit = build_chatbot({"adapter": "openai", "model": "m", "api_key_env": None})
    keyed = build_chatbot({"adapter": "openai", "model": "m", "base_url": "https://h.example", "api_key_env": "K"})
    assert keyless.api_key_env is None and explicit.api_key_env is None and keyed.api_key_env == "K"
    judge = adapter_from_profile({"provider": "openai", "model": "j", "base_url": "http://localhost:11434/v1"})
    assert judge.api_key_env is None and judge.temperature == 0.0


def test_cli_audit_of_a_local_model_without_any_key(local_server, tmp_path):
    out = tmp_path / "r.json"
    code = main(["audit", "--adapter", "openai", "--base-url", local_server, "--model", "llama3", "--pillars", "consent", "--limit", "3",
                 "--out", str(out), "--quiet"])
    assert code in (0, 1, 2) and REQUESTS and all("authorization" not in r["headers"] for r in REQUESTS)
    assert read_json(out)["metadata"]["chatbot"]["authentication"] == "aucune"


def test_cli_no_api_key_flag(tmp_path, capsys):
    assert main(["check-bot", "--adapter", "anthropic", "--model", "m", "--no-api-key"]) == 64
    assert "exige une clé" in capsys.readouterr().err


def test_a_keyless_local_model_can_be_the_judge_through_the_cli(local_server, tmp_path):
    audit = tmp_path / "a.json"
    main(["audit", "--adapter", "demo-flawed", "--pillars", "consent", "--limit", "3", "--out", str(audit), "--quiet"])
    profiles = tmp_path / "juges.json"
    profiles.write_text(json.dumps({"local": {"provider": "openai", "model": "llama3", "base_url": local_server}}), encoding="utf-8")
    REQUESTS.clear()
    main(["rejudge", str(audit), "--judges-config", str(profiles), "--judge-profile", "local", "--out", str(tmp_path / "rj.json"), "--quiet"])
    assert REQUESTS and all("authorization" not in r["headers"] for r in REQUESTS)


def test_init_wizard_does_not_ask_for_a_key_variable_for_a_local_model(tmp_path, monkeypatch):
    answers = iter(["1", "llama3", "http://localhost:11434/v1", "", "", "", "Assistant", "", "fr", "", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    config = json.loads((tmp_path / "healthyguard.json").read_text(encoding="utf-8"))
    assert config["chatbot"] == {"adapter": "openai", "model": "llama3", "base_url": "http://localhost:11434/v1"}
    assert not (tmp_path / ".env.example").exists()          # aucun secret à renseigner
