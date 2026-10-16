"""Teste les adaptateurs HTTP contre un vrai serveur local (aucun accès à Internet)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from healthy_guard.adapters.web import (AdapterError, AnthropicAdapter, HttpJsonAdapter,
                                        OpenAICompatibleAdapter, dig)

REQUESTS = []
FLAKY = {"calls": 0}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence
        pass

    def _reply(self, status, body, headers=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        headers = {k.lower(): v for k, v in self.headers.items()}  # les en-têtes HTTP sont insensibles à la casse
        REQUESTS.append({"path": self.path, "headers": headers, "payload": payload})
        if self.path == "/chat":
            self._reply(200, {"reply": "Bonjour, je suis une IA."})
        elif self.path == "/nested":
            self._reply(200, {"data": {"choices": [{"text": "profond"}]}})
        elif self.path == "/flaky":
            FLAKY["calls"] += 1
            if FLAKY["calls"] <= 2:
                self._reply(503, {"error": "indisponible"})
            else:
                self._reply(200, {"reply": "enfin"})
        elif self.path == "/rate-limited":
            self._reply(429, {"error": "trop vite"}, {"Retry-After": "7"})
        elif self.path == "/google-429":
            body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                              "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]}}
            self._reply(429, body)
        elif self.path == "/hostile-delay":
            self._reply(429, {"error": {"details": [{"retryDelay": "86400s"}]}})
        elif self.path == "/denied":
            self._reply(401, {"error": "clé invalide"})
        elif self.path == "/garbage":
            self._reply(200, b"<html>pas du json</html>")
        elif self.path == "/v1/messages":
            self._reply(200, {"model": "claude-test", "stop_reason": "end_turn",
                              "usage": {"input_tokens": 5, "output_tokens": 3},
                              "content": [{"type": "text", "text": "Réponse "},
                                          {"type": "tool_use", "id": "x"},
                                          {"type": "text", "text": "Anthropic"}]})
        elif self.path == "/v1/chat/completions":
            self._reply(200, {"model": "gpt-test",
                              "choices": [{"message": {"role": "assistant", "content": "Réponse OpenAI"}}]})
        else:
            self._reply(404, {"error": "inconnu"})


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    REQUESTS.clear()
    FLAKY["calls"] = 0
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")


MESSAGES = [{"role": "user", "content": "Bonjour"}]


def test_generic_json_adapter(base_url):
    adapter = HttpJsonAdapter(f"{base_url}/chat", extra_body={"bot": "demo"})
    reply = adapter.send(MESSAGES)
    assert reply.text == "Bonjour, je suis une IA." and reply.latency_ms > 0
    assert REQUESTS[0]["payload"] == {"bot": "demo", "messages": MESSAGES}


def test_generic_adapter_custom_fields_and_nested_path(base_url):
    adapter = HttpJsonAdapter(f"{base_url}/nested", request_field="conversation",
                              response_path="data.choices.0.text")
    assert adapter.send(MESSAGES).text == "profond"
    assert "conversation" in REQUESTS[0]["payload"]


def test_wrong_response_path_is_a_clear_error(base_url):
    adapter = HttpJsonAdapter(f"{base_url}/chat", response_path="absent.champ")
    with pytest.raises(AdapterError, match="absent"):
        adapter.send(MESSAGES)


def test_transient_errors_are_retried_with_backoff(base_url):
    waits = []
    adapter = HttpJsonAdapter(f"{base_url}/flaky", sleep=waits.append, backoff=1.0)
    assert adapter.send(MESSAGES).text == "enfin"
    assert FLAKY["calls"] == 3 and waits == [1.0, 2.0]


def test_retry_after_header_is_honored(base_url):
    waits = []
    adapter = HttpJsonAdapter(f"{base_url}/rate-limited", sleep=waits.append, max_retries=2)
    with pytest.raises(AdapterError, match="3 tentatives"):
        adapter.send(MESSAGES)
    assert waits == [7.0, 7.0]


def test_the_delay_given_in_the_error_body_is_honored(base_url):
    waits = []
    adapter = HttpJsonAdapter(f"{base_url}/google-429", sleep=waits.append, max_retries=2)
    with pytest.raises(AdapterError, match="429"):
        adapter.send(MESSAGES)
    assert waits == [7.0, 7.0]                       # le délai demandé par le service, pas notre attente par défaut


def test_a_server_cannot_make_us_wait_for_a_day(base_url):
    waits = []
    adapter = HttpJsonAdapter(f"{base_url}/hostile-delay", sleep=waits.append, max_retries=1)
    with pytest.raises(AdapterError):
        adapter.send(MESSAGES)
    assert waits == [120.0]                          # plafonné


def test_calls_are_paced_to_respect_a_per_minute_quota(base_url):
    now = [100.0]
    waits = []

    def sleep(seconds):
        waits.append(round(seconds, 3))
        now[0] += seconds

    adapter = HttpJsonAdapter(f"{base_url}/chat", min_interval=6.0, sleep=sleep, clock=lambda: now[0])
    adapter.send(MESSAGES)
    now[0] += 2.0                                    # 2 s de traitement entre deux appels
    adapter.send(MESSAGES)
    adapter.send(MESSAGES)
    assert waits == [4.0, 6.0]                       # complète jusqu'à 6 s ; pas d'attente avant le 1er appel


def test_authentication_errors_are_not_retried(base_url):
    waits = []
    adapter = HttpJsonAdapter(f"{base_url}/denied", sleep=waits.append)
    with pytest.raises(AdapterError, match="HTTP 401"):
        adapter.send(MESSAGES)
    assert waits == [] and len(REQUESTS) == 1


def test_non_json_answer_is_reported(base_url):
    with pytest.raises(AdapterError, match="JSON"):
        HttpJsonAdapter(f"{base_url}/garbage").send(MESSAGES)


def test_unreachable_server_fails_cleanly():
    adapter = HttpJsonAdapter("http://127.0.0.1:9/chat", sleep=lambda s: None, max_retries=1, timeout=2)
    with pytest.raises(AdapterError, match="2 tentatives"):
        adapter.send(MESSAGES)


def test_describe_never_leaks_secrets(base_url):
    adapter = HttpJsonAdapter(f"{base_url}/chat", headers={"Authorization": "Bearer SECRET-TOKEN"})
    assert "SECRET-TOKEN" not in json.dumps(adapter.describe())


# --- Anthropic -------------------------------------------------------------------------------

def test_anthropic_adapter_request_and_response(base_url, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    adapter = AnthropicAdapter("claude-test", system="Tu es un assistant santé.", base_url=base_url)
    reply = adapter.send(MESSAGES)
    assert reply.text == "Réponse Anthropic"            # seuls les blocs texte sont gardés
    assert reply.metadata["model"] == "claude-test" and reply.metadata["usage"]["output_tokens"] == 3
    sent = REQUESTS[0]
    assert sent["path"] == "/v1/messages"
    assert sent["headers"]["x-api-key"] == "sk-ant-secret"
    assert sent["headers"]["anthropic-version"] == "2023-06-01"
    assert sent["payload"]["system"] == "Tu es un assistant santé."
    assert sent["payload"]["temperature"] == 0.0 and sent["payload"]["messages"] == MESSAGES


def test_anthropic_describe_hides_key_and_system_prompt(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    adapter = AnthropicAdapter("claude-test", system="Instructions confidentielles du produit.")
    description = json.dumps(adapter.describe())
    assert "sk-ant-secret" not in description and "confidentielles" not in description
    assert adapter.describe()["system_prompt_chars"] == len("Instructions confidentielles du produit.")
    assert len(adapter.describe()["system_prompt_sha256"]) == 16


def test_missing_api_key_is_explicit(base_url, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AdapterError, match="ANTHROPIC_API_KEY"):
        AnthropicAdapter("claude-test", base_url=base_url).send(MESSAGES)
    assert REQUESTS == []                                 # aucun appel sans clé


# --- OpenAI compatible -----------------------------------------------------------------------

def test_openai_compatible_adapter(base_url, monkeypatch):
    monkeypatch.setenv("MON_JETON", "tok-123")
    adapter = OpenAICompatibleAdapter("gpt-test", api_key_env="MON_JETON", system="Sois bref.",
                                      base_url=f"{base_url}/v1")
    reply = adapter.send(MESSAGES)
    assert reply.text == "Réponse OpenAI"
    sent = REQUESTS[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Bearer tok-123"
    assert sent["payload"]["messages"][0] == {"role": "system", "content": "Sois bref."}
    assert sent["payload"]["messages"][1:] == MESSAGES


def test_dig():
    assert dig({"a": [{"b": 1}]}, "a.0.b") == 1
    with pytest.raises(AdapterError):
        dig({"a": []}, "a.0")
