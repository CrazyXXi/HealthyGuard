"""Brancher un modèle hébergé par ses soins (serveur compatible OpenAI, jeton « ID.SECRET », paramètres du serveur)
et l'auditer en parallèle, sans jamais laisser fuiter le secret."""
import json
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

from healthy_guard.adapters.base import ChatResponse
from healthy_guard.adapters.demo import DemoChatbot
from healthy_guard.adapters.mock import MockChatbot
from healthy_guard.adapters.web import HttpJsonAdapter, OpenAICompatibleAdapter
from healthy_guard.auditor import Auditor
from healthy_guard.checks import consent
from healthy_guard.cli import main
from healthy_guard.envfile import load_env_file, parse_env_file
from healthy_guard.models import Pillar, Status
from tests.test_web_adapters import REQUESTS, Handler

SECRET = "ws-TOP-SECRET-VALUE-42"
TOKEN = f"wk-ID123.{SECRET}"


@pytest.fixture()
def server(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    REQUESTS.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()


# --- fichier .env -----------------------------------------------------------------------------------

def test_env_file_parsing(tmp_path):
    path = tmp_path / ".env"
    path.write_text('﻿# jetons\n\nMODAL_TOKEN="wk-1.ws-2"\nexport AUTRE=valeur avec espaces  \nSIMPLE=\'x\'\nVIDE=\n', encoding="utf-8")
    assert parse_env_file(path) == {"MODAL_TOKEN": "wk-1.ws-2", "AUTRE": "valeur avec espaces", "SIMPLE": "x", "VIDE": ""}


def test_env_file_never_overrides_and_returns_only_names(tmp_path, monkeypatch):
    monkeypatch.setenv("DEJA_LA", "valeur-existante")
    monkeypatch.delenv("NOUVELLE", raising=False)
    path = tmp_path / ".env"
    path.write_text("DEJA_LA=autre\nNOUVELLE=secret-xyz\n", encoding="utf-8")
    loaded = load_env_file(path)
    assert loaded == ["NOUVELLE"] and "secret-xyz" not in "".join(loaded)
    import os
    assert os.environ["DEJA_LA"] == "valeur-existante" and os.environ["NOUVELLE"] == "secret-xyz"
    monkeypatch.delenv("NOUVELLE")


def test_env_file_errors_quote_the_line_number_never_the_value(tmp_path):
    path = tmp_path / ".env"
    path.write_text("OK=1\nceci n'est pas valide secret-valeur\n", encoding="utf-8")
    with pytest.raises(ValueError) as error:
        parse_env_file(path)
    assert "ligne 2" in str(error.value) and "secret-valeur" not in str(error.value)


def test_gitignore_protects_env_files():
    from pathlib import Path
    ignored = Path(__file__).resolve().parent.parent.joinpath(".gitignore").read_text(encoding="utf-8").split()
    assert ".env" in ignored and "*.env" in ignored and "reports/" in ignored


# --- adaptateur : paramètres du serveur et jeton composite ------------------------------------------------

def test_server_parameters_and_composite_token_are_sent(server, monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN", TOKEN)
    adapter = OpenAICompatibleAdapter("zai-org/GLM-5.3-Flash", api_key_env="MODAL_TOKEN", base_url=server, max_tokens=2048,
                                      extra_body={"reasoning_effort": "none", "top_p": 0.9})
    assert adapter.send([{"role": "user", "content": "Bonjour"}]).text == "Réponse OpenAI"
    sent = REQUESTS[0]
    assert sent["headers"]["authorization"] == f"Bearer {TOKEN}"
    assert sent["payload"]["reasoning_effort"] == "none" and sent["payload"]["top_p"] == 0.9
    assert sent["payload"]["max_tokens"] == 2048 and sent["payload"]["model"] == "zai-org/GLM-5.3-Flash"


@pytest.mark.parametrize("given, expected", [
    ("https://labyrinth-n1--ep-glm.us-west.modal.direct", "https://labyrinth-n1--ep-glm.us-west.modal.direct/v1"),
    ("https://labyrinth-n1--ep-glm.us-west.modal.direct/", "https://labyrinth-n1--ep-glm.us-west.modal.direct/v1"),
    ("https://labyrinth-n1--ep-glm.us-west.modal.direct/v1", "https://labyrinth-n1--ep-glm.us-west.modal.direct/v1"),
    ("https://generativelanguage.googleapis.com/v1beta/openai", "https://generativelanguage.googleapis.com/v1beta/openai"),
    ("http://127.0.0.1:8000/mon/chemin/", "http://127.0.0.1:8000/mon/chemin"),
])
def test_a_bare_host_gets_the_standard_v1_route_and_other_paths_are_untouched(given, expected):
    assert OpenAICompatibleAdapter("m", base_url=given).base_url == expected


def test_a_bare_host_reaches_the_v1_route(server, monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN", TOKEN)
    bare = server.removesuffix("/v1")
    OpenAICompatibleAdapter("m", api_key_env="MODAL_TOKEN", base_url=bare).send([{"role": "user", "content": "x"}])
    assert REQUESTS[0]["path"] == "/v1/chat/completions"


def test_server_parameters_cannot_override_the_conversation():
    with pytest.raises(ValueError, match="messages"):
        OpenAICompatibleAdapter("m", extra_body={"messages": []})
    with pytest.raises(ValueError, match="model"):
        OpenAICompatibleAdapter("m", extra_body={"model": "autre"})


def test_description_records_server_parameters_but_not_the_token(monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN", TOKEN)
    description = json.dumps(OpenAICompatibleAdapter("m", api_key_env="MODAL_TOKEN",
                                                     extra_body={"reasoning_effort": "none"}).describe())
    assert "reasoning_effort" in description and SECRET not in description and "wk-ID123" not in description


# --- de bout en bout : .env -> audit -> rapport sans secret ----------------------------------------------------

def test_cli_audit_of_a_hosted_model_leaves_no_secret_behind(server, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("MODAL_TOKEN", raising=False)
    env = tmp_path / "modal.env"
    env.write_text(f'MODAL_TOKEN="{TOKEN}"\n', encoding="utf-8")
    out, html = tmp_path / "r.json", tmp_path / "r.html"
    code = main(["audit", "--adapter", "openai", "--base-url", server, "--model", "zai-org/GLM-5.3-Flash",
                 "--api-key-env", "MODAL_TOKEN", "--env-file", str(env), "--max-tokens", "2048",
                 "--extra-body", '{"reasoning_effort": "none"}', "--pillars", "consent", "--limit", "3",
                 "--concurrency", "3", "--out", str(out), "--html", str(html), "--quiet"])
    assert code in (0, 1, 2)
    assert len(REQUESTS) >= 3 and all(r["headers"]["authorization"] == f"Bearer {TOKEN}" for r in REQUESTS)
    assert all(r["payload"]["reasoning_effort"] == "none" for r in REQUESTS)
    captured = capsys.readouterr()
    everything = out.read_text(encoding="utf-8") + html.read_text(encoding="utf-8") + captured.out + captured.err
    assert SECRET not in everything and "wk-ID123" not in everything          # le secret n'est nulle part
    assert "MODAL_TOKEN" in captured.err                                      # on cite le NOM de la variable chargée
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["metadata"]["chatbot"]["extra_body"] == {"reasoning_effort": "none"}
    assert report["metadata"]["settings"]["concurrency"] == 3


@pytest.mark.parametrize("argv, message", [
    (["--extra-body", "pas du json"], "JSON valide"),
    (["--extra-body", "[1, 2]"], "objet JSON"),
    (["--max-tokens", "0"], "max-tokens"),
    (["--concurrency", "0"], "concurrency"),
    (["--concurrency", "500"], "concurrency"),
    (["--env-file", "n_existe_pas.env"], "env-file"),
])
def test_cli_hosted_model_configuration_errors(argv, message, capsys):
    base = ["audit", "--adapter", "openai", "--base-url", "http://127.0.0.1:9/v1", "--model", "m", "--quiet"]
    assert main(base + argv) == 64
    assert message in capsys.readouterr().err


def test_server_parameters_can_be_read_from_a_file(server, tmp_path, monkeypatch):
    monkeypatch.setenv("MODAL_TOKEN", TOKEN)
    params = tmp_path / "params.json"
    params.write_text('{"reasoning_effort": "none"}', encoding="utf-8")
    main(["audit", "--adapter", "openai", "--base-url", server, "--model", "m", "--api-key-env", "MODAL_TOKEN",
          "--extra-body", f"@{params}", "--pillars", "consent", "--limit", "1", "--out", str(tmp_path / "r.json"), "--quiet"])
    assert REQUESTS and REQUESTS[0]["payload"]["reasoning_effort"] == "none"
    assert main(["audit", "--adapter", "openai", "--model", "m", "--extra-body", "@absent.json", "--quiet"]) == 64


def test_extra_body_is_refused_for_providers_that_do_not_support_it(capsys):
    assert main(["audit", "--adapter", "anthropic", "--model", "m", "--extra-body", '{"a": 1}', "--quiet"]) == 64
    assert "--adapter openai" in capsys.readouterr().err


# --- parallélisme --------------------------------------------------------------------------------------------

def test_parallel_audit_gives_exactly_the_same_results_in_the_same_order():
    sequential = Auditor(DemoChatbot(), pillars=[Pillar.CONSENT, Pillar.TRANSPARENCY], limit=20).run()
    parallel = Auditor(DemoChatbot(), pillars=[Pillar.CONSENT, Pillar.TRANSPARENCY], limit=20, concurrency=8).run()
    key = lambda r: (r.pillar.value, r.check_id, r.repeat, r.status.value, r.response, r.explanation)  # noqa: E731
    assert [key(r) for r in sequential.results] == [key(r) for r in parallel.results]
    assert parallel.metadata["settings"]["concurrency"] == 8


def test_cases_really_run_at_the_same_time():
    class NeedsFourAtOnce(MockChatbot):
        barrier = threading.Barrier(4, timeout=10)

        def send(self, messages):
            self.barrier.wait()                    # ne passe que si 4 appels sont simultanés
            return ChatResponse("ok", latency_ms=1.0)

    cases = [{"id": f"c{i}", "prompt": f"q{i}", "expected": "e", "forbidden": ["z"], "severity": "minor"} for i in range(4)]
    results = consent.run(NeedsFourAtOnce(), cases=cases, concurrency=4)
    assert [r.check_id for r in results] == ["c0", "c1", "c2", "c3"] and all(r.status == Status.PASS for r in results)


def test_a_failure_in_one_parallel_case_does_not_affect_the_others():
    class FailsOnOne(MockChatbot):
        def send(self, messages):
            if "q2" in messages[0]["content"]:
                raise TimeoutError("503")
            return ChatResponse("ok")

    cases = [{"id": f"c{i}", "prompt": f"q{i}", "expected": "e", "forbidden": ["z"], "severity": "minor"} for i in range(5)]
    results = consent.run(FailsOnOne(), cases=cases, concurrency=5)
    assert [r.status for r in results] == [Status.PASS, Status.PASS, Status.HUMAN_REVIEW, Status.PASS, Status.PASS]
    assert results[2].tags["error"] == "chatbot"


def test_progress_is_reported_once_per_case_in_order():
    seen = []
    cases = [{"id": f"c{i}", "prompt": f"q{i}", "expected": "e", "forbidden": ["z"], "severity": "minor"} for i in range(6)]
    consent.run(MockChatbot(default="ok"), cases=cases, concurrency=3, progress=lambda done, total: seen.append((done, total)))
    assert seen == [(i, 6) for i in range(1, 7)]


def test_pacing_holds_even_when_many_threads_call_at_once():
    adapter = HttpJsonAdapter("http://x", min_interval=0.05)
    stamps = []

    def call():
        adapter._pace()
        stamps.append(time.monotonic())

    threads = [threading.Thread(target=call) for _ in range(6)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stamps.sort()
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    assert min(gaps) >= 0.04                              # jamais deux appels collés, malgré 6 threads simultanés
    assert time.monotonic() - start >= 0.24
